"""LOCAL-PATCH unbacked-claim-gate: a final answer that claims a change ("Updated the TO DO
LIST item ...") with no tool call after the user's latest message gets one nudge to make the
change or withdraw the claim.

Seen live: a follow-up that arrived mid-turn was folded into the running turn; the model
answered "Updated ..." without calling a tool, and the note never changed.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

_CLAIM_RE = re.compile(
    r"^\W*(?:done[.!,:]|(?:i(?:'|’)ve |i have |i )?(?:updated|added|created|deleted|removed|"
    r"changed|renamed|moved|sent|scheduled|saved|edited|fixed|marked|cleared|archived|"
    r"replied|booked|cancel(?:l)?ed|rescheduled|appended|replaced)\b)",
    re.IGNORECASE | re.MULTILINE,
)

NUDGE = (
    "Your reply says a change was made, but no tool ran after the user's latest message. "
    "If the user asked for a change, make it now with a tool and check the result, then reply. "
    "If no change is needed, reply without claiming one."
)


def claim_gate_enabled() -> bool:
    return os.environ.get("HERMES_CLAIM_GATE", "1").strip().lower() not in {"0", "false", "off", "no"}


def _is_synthetic(msg: Dict[str, Any]) -> bool:
    return any(k.startswith("_") and k.endswith("_synthetic") and v for k, v in msg.items())


def tool_ran_since_last_user(messages: List[Dict[str, Any]]) -> bool:
    for msg in reversed(messages):
        role = msg.get("role")
        if role == "tool" or (role == "assistant" and msg.get("tool_calls")):
            return True
        if role == "user" and not _is_synthetic(msg):
            return False
    return False


def claims_change(text: Any) -> bool:
    return bool(_CLAIM_RE.search(str(text or "")[:400]))


def build_claim_nudge(*, final_response: Any, messages: List[Dict[str, Any]], has_tools: bool,
                      attempts: int) -> Optional[str]:
    if attempts >= 1 or not has_tools or not claim_gate_enabled():
        return None
    if not claims_change(final_response) or tool_ran_since_last_user(messages):
        return None
    return NUDGE
