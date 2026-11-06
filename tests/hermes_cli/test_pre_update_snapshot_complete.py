import json
import shutil
import sqlite3
from collections import namedtuple
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
