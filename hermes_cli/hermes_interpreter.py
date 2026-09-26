"""LOCAL-PATCH launcher-venv-python: the interpreter that can run ``-m hermes_cli.main``.

Since 0.21.5 the gateway runs on the managed launcher interpreter (``tools/python-3.x/bin/python3 -I``)
with Hermes put on ``sys.path`` by hand. That interpreter cannot import ``hermes_cli`` on its own, so
child processes built as ``[sys.executable, "-m", "hermes_cli.main"]`` (kanban workers, /restart,
/update) fail with ``ModuleNotFoundError``. This resolves the environment venv whose site-packages the
running process already imports from (no file reads beyond an ``is_file``), then the legacy project
venv, and only then ``sys.executable``.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _running_in_venv() -> bool:
    return getattr(sys, "prefix", "") != getattr(sys, "base_prefix", "")


def _venv_pythons_on_sys_path() -> list[Path]:
    found: list[Path] = []
    for entry in sys.path:
        marker = "/lib/python"
        if "/venv" in entry and marker in entry and "site-packages" in entry:
            venv = Path(entry.split(marker, 1)[0])
            candidate = venv / "bin" / "python"
            if candidate not in found:
                found.append(candidate)
    return found


def hermes_python() -> str:
    """Absolute path of an interpreter that can import ``hermes_cli``."""
    if _running_in_venv():
        return sys.executable
    root = Path(__file__).resolve().parents[1]
    candidates = _venv_pythons_on_sys_path() + [root / "venv" / "bin" / "python"]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return sys.executable
