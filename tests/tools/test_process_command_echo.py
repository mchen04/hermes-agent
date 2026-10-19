import hashlib
import json
import time

import pytest

from tools.process_registry import ProcessRegistry, ProcessSession

INSTRUCTION = "Round 7 for the coder: " + "re-run the targeted tests, fix each reproduced failure, report. " * 45
LONG_COMMAND = f"python3 fleetturn.py send --session t_x-coder --message {json.dumps(INSTRUCTION)} --wait"


def _session(sid: str, command: str) -> ProcessSession:
    return ProcessSession(id=sid, command=command, task_id="t1", started_at=time.time(), exited=True,
                          exit_code=0, output_buffer="sent: turn 7 accepted\n")


@pytest.fixture
def registry(monkeypatch):
    from tools import process_registry as pr

    reg = ProcessRegistry()
    for sid, command in (("proc_long", LONG_COMMAND), ("proc_short", "python3 fleetturn.py --status")):
        reg._finished[sid] = _session(sid, command)
    monkeypatch.setattr(pr, "process_registry", reg)
    return pr


@pytest.mark.parametrize("action", ["poll", "wait", "log", "kill"])
def test_status_and_wait_results_return_a_hash_instead_of_the_instruction(registry, action) -> None:
    out = json.loads(registry._handle_process({"action": action, "session_id": "proc_long", "timeout": 1}))

    assert INSTRUCTION not in json.dumps(out)
    assert len(out["command"]) <= 201
    assert out["command"].startswith("python3 fleetturn.py send --session t_x-coder")
    assert out["command_sha256"] == hashlib.sha256(LONG_COMMAND.encode()).hexdigest()
    assert out["command_chars"] == len(LONG_COMMAND)


def test_short_commands_and_the_list_view(registry) -> None:
    short = json.loads(registry._handle_process({"action": "poll", "session_id": "proc_short"}))
    listed = json.loads(registry._handle_process({"action": "list"}))["processes"]

    assert short["command"] == "python3 fleetturn.py --status" and "command_sha256" not in short
    assert all(INSTRUCTION not in json.dumps(entry) for entry in listed)
