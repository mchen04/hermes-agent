import logging
from pathlib import Path

from hermes_cli.sizefmt import format_bytes
from hermes_state_repair import _disk_budget

logger = logging.getLogger(__name__)


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
        elif free - need < headroom:
            reason = (f"needs {format_bytes(need)} plus {format_bytes(headroom)} headroom, "
                      f"{format_bytes(free)} free")
    if reason is None:
        return False
    print(f"  ⚠ Snapshot: skipping {rel_name} ({reason})")
    logger.warning("Quick snapshot skipped %s: %s", rel_name, reason)
    return True
