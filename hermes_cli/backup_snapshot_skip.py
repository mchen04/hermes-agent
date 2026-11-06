import logging
import sqlite3
from contextlib import closing
from pathlib import Path

from hermes_cli.sizefmt import format_bytes
from hermes_state_holders import read_only_db_uri
from hermes_state_repair import _disk_budget

logger = logging.getLogger(__name__)


def _database_image_size(src: Path) -> int | None:
    try:
        with closing(sqlite3.connect(read_only_db_uri(src), uri=True, timeout=0.0)) as conn:
            page_count = conn.execute("PRAGMA page_count").fetchone()[0]
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            return page_count * page_size
    except sqlite3.Error:
        return None


def snapshot_skips(src: Path, rel_name: str, destination: Path, max_file_size: int | None) -> bool:
    reason = None
    if max_file_size is not None:
        try:
            size = src.stat().st_size
        except OSError:
            size = None
        if size is not None and size > max_file_size:
            reason = f"{format_bytes(size)} exceeds {format_bytes(max_file_size)} limit"
    if reason is None and src.suffix == ".db":
        error, need, free, headroom = _disk_budget(src, "snapshot copy", destination)
        if error is not None:
            reason = error
        else:
            image_size = _database_image_size(src)
            if image_size is not None:
                need = image_size
            if free - need < headroom:
                reason = (f"needs {format_bytes(need)} plus {format_bytes(headroom)} headroom, "
                          f"{format_bytes(free)} free")
    if reason is None:
        return False
    print(f"  ⚠ Snapshot: skipping {rel_name} ({reason})")
    logger.warning("Quick snapshot skipped %s: %s", rel_name, reason)
    return True
