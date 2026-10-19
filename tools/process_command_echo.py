from __future__ import annotations

import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


def command_echo_limit() -> int:
    from hermes_cli.config import DEFAULT_CONFIG, cfg_get, read_raw_config

    try:
        value = cfg_get(read_raw_config(), "terminal", "process_result_command_chars")
        return max(0, int(DEFAULT_CONFIG["terminal"]["process_result_command_chars"] if value is None else value))
    except Exception:
        logger.warning("process results: cannot read terminal.process_result_command_chars", exc_info=True)
        return int(DEFAULT_CONFIG["terminal"]["process_result_command_chars"])


def compact_command(result: Any, limit: int) -> Any:
    if not isinstance(result, dict) or not limit:
        return result
    out = dict(result)
    command = out.get("command")
    if isinstance(command, str) and len(command) > limit:
        out["command"] = command[:limit].rstrip() + "…"
        out["command_sha256"] = hashlib.sha256(command.encode("utf-8")).hexdigest()
        out["command_chars"] = len(command)
    if isinstance(out.get("processes"), list):
        out["processes"] = [compact_command(p, limit) for p in out["processes"]]
    return out
