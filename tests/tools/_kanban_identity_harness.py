"""One Hermes interpreter acting as a managed Kanban worker, or as the unbound front door.

Test-only harness for ``test_kanban_worker_tool_identity.py`` (LOCAL-PATCH kanban-worker-tool-identity).
``python _kanban_identity_harness.py <plan.json>`` runs the plan's ops in order against a scratch
HERMES_HOME and prints ``HARNESS=<json list of results>`` last. A managed worker interpreter runs one
frame, so each worker is its own harness process; ``bind`` is the real ``bind_worker_context``.
"""
import json
import os
import shlex
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LABEL_KEYS = ("HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_CLAIM_LOCK",
              "HERMES_DELEGATED_CHILD_CONTEXT")
PROBE = ("import json, os\n"
         f"print('LABELS=' + json.dumps({{k: os.environ.get(k) for k in {LABEL_KEYS!r}}}))\n")
# A labeled child is still a fenced descendant: its own card's tool and CLI completion must refuse,
# and the resume guard must not treat it as the dispatcher-owned run.
MUTATE = (PROBE + "import subprocess, sys\n"
          f"sys.path.insert(0, {str(ROOT)!r})\n"
          "from tools import kanban_tools as kt\n"
          "from hermes_cli.kanban_resume_guard import _dispatcher_owned_run\n"
          "task = os.environ['HERMES_KANBAN_TASK']\n"
          "tool = json.loads(kt._handle_complete({'task_id': task, 'summary': 'must refuse'}))\n"
          "cli = subprocess.run([sys.executable, '-m', 'hermes_cli.main', 'kanban', 'complete', task, '--result', 'must refuse'],"
          f" cwd={str(ROOT)!r}, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)\n"
          "print('MUTATE=' + json.dumps({'tool': tool, 'cli_rc': cli.returncode, 'resume_exempt': _dispatcher_owned_run()}))\n")


def _rows(output, tag):
    return [json.loads(line.split(tag, 1)[1]) for line in output.splitlines() if line.startswith(tag)]


@contextmanager
def _scope(name):
    from agent.delegation_context import delegated_child_context, non_dispatcher_owned_context
    if name == "delegate":
        with delegated_child_context("identity-delegate"):
            yield
    elif name == "cron":
        with non_dispatcher_owned_context():
            yield
    else:
        yield


def _script(work, name, source):
    path = Path(work, name)
    path.write_text(source, encoding="utf-8")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(path))}"


def _probe(op, work):
    """Labels seen by a child of the real terminal (foreground/background) or execute_code tool."""
    from tools.process_registry import process_registry
    from tools.terminal_tool import terminal_tool
    command = _script(work, "probe.py", PROBE)
    task = op.get("task", "identity-session")
    with _scope(op.get("scope", "worker")):
        if op["via"] == "terminal":
            output = json.loads(terminal_tool(command=command, task_id=task))["output"]
        elif op["via"] == "background":
            started = json.loads(terminal_tool(command=command, background=True, task_id=task))
            output = process_registry.wait(started["session_id"], timeout=60)["output"]
        elif op["via"] == "spawn_env":  # a caller-supplied env for the spawned process
            spawned = process_registry.spawn_local(command, cwd=work, env_vars=op["env"])
            output = process_registry.wait(spawned.id, timeout=60)["output"]
        else:  # kernel: the cell's own env and a program it starts
            from tools.code_execution_tool import execute_code
            code = PROBE + f"import subprocess, sys\nsubprocess.run({shlex.split(command)!r}, check=True)\n"
            with patch("tools.code_execution_tool._load_config", return_value={"mode": "strict", "timeout": 60}):
                output = json.loads(execute_code(code, task_id=task))["output"]
    rows = _rows(output, "LABELS=")
    assert rows, output
    return rows


def _mutate(work):
    from tools.terminal_tool import terminal_tool
    output = json.loads(terminal_tool(command=_script(work, "mutate.py", MUTATE), task_id="identity-mutate"))["output"]
    return {"labels": _rows(output, "LABELS="), "mutate": _rows(output, "MUTATE=")}


def _reclaim(op):  # the operator/parent reclaims the card under the bound worker
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing
    with connect_closing(Path(op["db"])) as conn:
        return kb.reclaim_task(conn, op["task_id"], reason="identity test")


def _run(op, work):
    kind = op["op"]
    if kind == "bind":
        from gateway.session_kanban import bind_worker_context
        try:
            bind_worker_context({"policy": {"source": "kanban", "kanban_json": json.dumps(op["context"])}})
        except Exception as exc:
            return {"error": str(exc)}
        return {"ok": True}
    if kind == "probe":
        return _probe(op, work)
    if kind == "mutate":
        return _mutate(work)
    if kind == "shell":  # a worker command whose exports persist into the session snapshot
        from tools.terminal_tool import terminal_tool
        return json.loads(terminal_tool(command=op["command"], task_id="identity-session"))["exit_code"]
    if kind == "complete":  # the worker completes its own card through the real tool
        from tools import kanban_tools
        return json.loads(kanban_tools._handle_complete({"summary": "identity test done"}))
    if kind == "barrier":  # every concurrent worker is bound before any of them spawns
        Path(op["dir"], op["name"]).touch()
        deadline = time.monotonic() + 60
        while len(os.listdir(op["dir"])) < op["count"]:
            assert time.monotonic() < deadline, "barrier timed out"
            time.sleep(0.05)
        return True
    return _reclaim(op)


def main():
    plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    results = []
    try:
        for op in plan["ops"]:
            results.append(_run(op, plan["work"]))
    finally:
        from tools.code_kernel import shutdown_all_kernels
        from tools.process_registry import process_registry
        shutdown_all_kernels()
        process_registry.kill_all()
        print("HARNESS=" + json.dumps(results))
        sys.stdout.flush()
    os._exit(0)  # the bound interpreter's submitter watch and tool threads are daemons of this run


if __name__ == "__main__":
    main()
