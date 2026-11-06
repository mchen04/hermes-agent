import json
import shutil
import sqlite3
from collections import namedtuple
from contextlib import closing
from pathlib import Path

import pytest

from hermes_cli import backup, update_cmd_config, update_cmd_maint

GIB = 1 << 30
_Usage = namedtuple("_Usage", "total used free")


def _db(path: Path, rows: int, *, logical_bytes: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, body TEXT)")
    conn.executemany("INSERT INTO t(body) VALUES (?)", [(f"row {i}",) for i in range(rows)])
    conn.commit()
    conn.close()
    if logical_bytes:
        with open(path, "r+b") as handle:
            handle.truncate(logical_bytes)
    return path


def _rows(path: Path) -> int:
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT count(*) FROM t").fetchone()[0]
    finally:
        conn.close()


def _manifest(home: Path, snapshot_id: str) -> dict:
    return json.loads((home / "state-snapshots" / snapshot_id / "manifest.json").read_text())


def _no_free_space_reading(_path):
    raise OSError("statfs failed")


def test_update_snapshot_copies_profile_databases_larger_than_the_old_cap(tmp_path, monkeypatch):
    root = tmp_path / "home"
    forge = root / "profiles" / "forge"
    forge.mkdir(parents=True)
    (root / "config.yaml").write_text("model: {}\n")
    (forge / "config.yaml").write_text("model: {}\n")
    past_cap = update_cmd_maint._PRE_UPDATE_SNAPSHOT_MAX_FILE_SIZE + GIB // 2
    state = _db(forge / "state.db", 3, logical_bytes=past_cap)
    _db(forge / "kanban" / "boards" / "ops" / "kanban.db", 2, logical_bytes=past_cap)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(update_cmd_config, "_LAST_SIBLING_SNAPSHOTS", {})
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: _Usage(400 * GIB, 100 * GIB, 300 * GIB))
    live = sqlite3.connect(state)
    try:
        live.execute("INSERT INTO t(body) VALUES ('only in the wal')")
        live.commit()
        update_cmd_maint._run_quick_snapshots()
    finally:
        live.close()

    snapshot_id = update_cmd_config._LAST_SIBLING_SNAPSHOTS["forge"]
    manifest = _manifest(forge, snapshot_id)
    assert manifest["oversized_skipped"] == []
    assert manifest["failed_dbs"] == []
    snapshot = forge / "state-snapshots" / snapshot_id
    assert _rows(snapshot / "state.db") == 4
    assert _rows(snapshot / "kanban" / "boards" / "ops" / "kanban.db") == 2
    assert (snapshot / "state.db").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("repeated_updates,copied", [
    (True, True),
    (False, True),
    (False, False),
], ids=["repeated-wal-updates-fit", "growth-in-wal-fits", "growth-in-wal-short"])
def test_snapshot_budgets_the_committed_database_image(
        tmp_path, monkeypatch, repeated_updates, copied):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("model: {}\n")
    database = home / "state.db"
    live = sqlite3.connect(database)
    try:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("PRAGMA wal_autocheckpoint=0")
        live.execute("CREATE TABLE t(body BLOB)")
        if repeated_updates:
            live.execute("INSERT INTO t(body) VALUES (?)", (bytes(65536),))
        live.commit()
        live.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if repeated_updates:
            for i in range(20):
                live.execute("UPDATE t SET body=?", (bytes([i]) * 65536,))
                live.commit()
        else:
            live.executemany("INSERT INTO t(body) VALUES (?)", [("x" * 1000,)] * 1000)
            live.commit()

        measure = tmp_path / "measure.db"
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(measure)) as destination:
                source.backup(destination)
        image = measure.stat().st_size
        main = database.stat().st_size
        wal = database.with_name("state.db-wal")
        assert wal.is_file()
        if repeated_updates:
            assert wal.stat().st_size > 2 * image
        else:
            assert main < image // 4
        sidecars = [database.with_name(f"state.db{suffix}") for suffix in ("-wal", "-shm")]
        raw = main + sum(path.stat().st_size for path in sidecars if path.exists())
        reserve = GIB // 4
        free = reserve + ((image + raw) // 2 if copied else (main + image) // 2)
        monkeypatch.setattr(shutil, "disk_usage", lambda _path: _Usage(GIB, GIB - free, free))

        snapshot_id = backup.create_quick_snapshot(label="pre-update", hermes_home=home)

        manifest = _manifest(home, snapshot_id)
        snapshot = home / "state-snapshots" / snapshot_id / "state.db"
        assert ("state.db" in manifest["files"]) == copied
        assert snapshot.is_file() == copied
        assert manifest["oversized_skipped"] == ([] if copied else ["state.db"])
        if copied:
            standalone = tmp_path / "standalone.db"
            shutil.copy2(snapshot, standalone)
            with closing(sqlite3.connect(standalone)) as restored:
                assert restored.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
                if repeated_updates:
                    assert restored.execute("SELECT body FROM t").fetchone()[0][0] == 19
                else:
                    assert restored.execute("SELECT count(*) FROM t").fetchone()[0] == 1000
    finally:
        live.close()


@pytest.mark.parametrize("disk_usage", [
    lambda _path: _Usage(400 * GIB, 399 * GIB, GIB),
    _no_free_space_reading,
], ids=["free-space-below-headroom", "free-space-unknown"])
def test_snapshot_without_disk_headroom_records_skipped_databases_and_prunes_nothing(
        tmp_path, monkeypatch, disk_usage):
    home = tmp_path / "forge"
    home.mkdir()
    (home / "config.yaml").write_text("model: {}\n")
    _db(home / "state.db", 3)
    _db(home / "kanban" / "boards" / "ops" / "kanban.db", 2)
    older = backup.create_quick_snapshot(label="complete", hermes_home=home)
    monkeypatch.setattr(shutil, "disk_usage", disk_usage)

    newer = backup.create_quick_snapshot(label="pre-update", hermes_home=home, keep=1)

    manifest = _manifest(home, newer)
    assert sorted(manifest["oversized_skipped"]) == ["kanban/boards/ops/kanban.db", "state.db"]
    assert "config.yaml" in manifest["files"]
    assert not {"state.db", "kanban/boards/ops/kanban.db"} & set(manifest["files"])
    assert {s["id"] for s in backup.list_quick_snapshots(hermes_home=home)} == {older, newer}
    assert _rows(home / "state-snapshots" / older / "state.db") == 3
    assert _rows(home / "state-snapshots" / older / "kanban" / "boards" / "ops" / "kanban.db") == 2


@pytest.mark.parametrize("destination_free,source_free,copied", [
    (300 * GIB, 0, True),
    (0, 300 * GIB, False),
], ids=["destination-has-room", "destination-has-no-room"])
def test_snapshot_budgets_space_on_the_destination_volume(
        tmp_path, monkeypatch, destination_free, source_free, copied):
    home = tmp_path / "home"
    destination = tmp_path / "backup-volume"
    home.mkdir()
    destination.mkdir()
    (home / "state-snapshots").symlink_to(destination, target_is_directory=True)
    (home / "config.yaml").write_text("model: {}\n")
    _db(home / "state.db", 3)

    def disk_usage(path):
        resolved = Path(path).resolve()
        free = destination_free if resolved.is_relative_to(destination.resolve()) else source_free
        return _Usage(400 * GIB, 400 * GIB - free, free)

    monkeypatch.setattr(shutil, "disk_usage", disk_usage)

    snapshot_id = backup.create_quick_snapshot(label="pre-update", hermes_home=home)

    manifest = _manifest(home, snapshot_id)
    assert ("state.db" in manifest["files"]) == copied
    assert (destination / snapshot_id / "state.db").is_file() == copied
    assert manifest["oversized_skipped"] == ([] if copied else ["state.db"])


@pytest.mark.parametrize("free,copied", [
    (300 * GIB, True),
    (GIB // 4, False),
], ids=["database-copied", "database-skipped"])
def test_snapshot_excludes_board_sidecars_and_preserves_committed_wal_rows(
        tmp_path, monkeypatch, free, copied):
    home = tmp_path / "home"
    board_rel = "kanban/boards/ops/kanban.db"
    board = _db(home / board_rel, 2)
    (home / "config.yaml").write_text("model: {}\n")
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: _Usage(400 * GIB, 400 * GIB - free, free))
    live = sqlite3.connect(board)
    try:
        live.execute("PRAGMA wal_autocheckpoint=0")
        live.executemany("INSERT INTO t(body) VALUES (?)", [("wal row 1",), ("wal row 2",)])
        live.commit()
        wal = board.with_name("kanban.db-wal")
        assert wal.is_file()
        assert wal.stat().st_size > 0

        snapshot_id = backup.create_quick_snapshot(label="pre-update", hermes_home=home)

        snapshot = home / "state-snapshots" / snapshot_id
        manifest = _manifest(home, snapshot_id)
        sidecars = (".db-wal", ".db-shm", ".db-journal")
        assert not [path for path in snapshot.rglob("*") if path.name.endswith(sidecars)]
        assert not [name for name in manifest["files"] if name.endswith(sidecars)]
        assert (board_rel in manifest["files"]) == copied
        assert (snapshot / board_rel).is_file() == copied
        assert manifest["oversized_skipped"] == ([] if copied else [board_rel])
        if copied:
            standalone = tmp_path / "standalone.db"
            shutil.copy2(snapshot / board_rel, standalone)
            assert _rows(standalone) == 4
            restored = sqlite3.connect(standalone)
            try:
                assert restored.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            finally:
                restored.close()
    finally:
        live.close()
