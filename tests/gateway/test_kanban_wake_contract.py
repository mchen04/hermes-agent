import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.run import GatewayRunner
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_notify as kbn

CHAT = "chat-1"
LONG_REASON = "Should we wait for interactive sign-in on the mini, or drop its successful-turn requirement? " * 6


class RecordingAdapter:
    def __init__(self):
        self.sent, self.handled = [], []

    async def send(self, chat_id, text, metadata=None):
        self.sent.append(text)

    async def handle_message(self, event):
        self.handled.append(event)
        event._gateway_accepted = True


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "wake.db"))
    for var in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_SESSION_PLATFORM", "HERMES_SESSION_CHAT_ID"):
        monkeypatch.delenv(var, raising=False)
    kb.init_db()
    return tmp_path


async def _tick(monkeypatch, runner):
    real_sleep = asyncio.sleep

    async def fake_sleep(delay):
        if delay == 5:
            return
        runner._running = False
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    runner._running = True
    await runner._kanban_notifier_watcher(interval=1)


def _runner(adapter):
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._kanban_sub_fail_counts = {}
    runner._kanban_dispatcher_lock_handle = object()

    async def no_upload(**kwargs):
        return None

    runner._deliver_kanban_artifacts = no_upload
    return runner


def _wakes(monkeypatch, runner, adapter):
    before = len(adapter.handled)
    asyncio.run(_tick(monkeypatch, runner))
    return [getattr(e, "text", "") for e in adapter.handled[before:]]


def _card(conn, title="release the app"):
    tid = kb.create_task(conn, title=title, assignee="forge", session_id="agent:main:telegram:dm:chat-1")
    kbn.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id=CHAT, chat_type="dm", delivery_mode="wake")
    return tid


def _as_worker(monkeypatch, conn, tid):
    kb.claim_task(conn, tid)
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    return kb.get_task(conn, tid).current_run_id


def test_blocked_wake_names_kind_reason_cause_and_whether_a_human_is_needed(board, monkeypatch):
    with kbc.connect_closing() as conn:
        asks, waits = _card(conn, "asks"), _card(conn, "waits")
        kb.block_task(conn, asks, reason=LONG_REASON, kind="needs_input",
                      expected_run_id=_as_worker(monkeypatch, conn, asks))
        kb.block_task(conn, waits, reason="Mini has 10 GiB free; admission needs 40 GiB.", kind="capacity",
                      expected_run_id=_as_worker(monkeypatch, conn, waits))
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    adapter = RecordingAdapter()

    wakes = _wakes(monkeypatch, _runner(adapter), adapter)

    ask = next(w for w in wakes if asks in w)
    assert "event: blocked · caused_by worker · " in ask
    block_line = next(line for line in ask.splitlines() if line.startswith("block: needs_input — "))
    assert len(block_line) <= len("block: needs_input — ") + 300
    assert "needs_human: yes — Should we wait for interactive sign-in on the mini" in ask
    wait = next(w for w in wakes if waits in w)
    assert "block: capacity — Mini has 10 GiB free; admission needs 40 GiB." in wait
    assert "needs_human: no" in wait


def test_completed_wake_carries_the_result_up_to_the_configured_length(board, monkeypatch):
    summary = "Merged PR #157 and verified the rollout.\n" + "Every check passed on the mini. " * 30
    with kbc.connect_closing() as conn:
        tid = _card(conn)
        run_id = _as_worker(monkeypatch, conn, tid)
        kb._append_event(conn, tid, "pr_acceptance", {
            "pr_url": "https://github.com/example/app/pull/157", "head_sha": "c5fb7cd6c1662698d1ab",
            "detail": "PR merged; the repository requires no checks."})
        kb.complete_task(conn, tid, summary=summary, expected_run_id=run_id)
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    adapter = RecordingAdapter()

    (wake,) = _wakes(monkeypatch, _runner(adapter), adapter)

    result = next(line for line in wake.splitlines() if line.startswith("Result: "))
    assert result.startswith("Result: Merged PR #157 and verified the rollout. Every check passed")
    assert 400 < len(result) - len("Result: ") <= 500
    assert "publish: PR https://github.com/example/app/pull/157 PR merged" in wake
    assert "head c5fb7cd6c166" in wake


def test_the_subscribers_own_stop_and_cancel_do_not_wake_it(board, monkeypatch):
    from gateway import session_context

    session = {"HERMES_SESSION_PLATFORM": "telegram", "HERMES_SESSION_CHAT_ID": CHAT}
    monkeypatch.setattr(session_context, "get_session_env", lambda name, default="": session.get(name, default))
    with kbc.connect_closing() as conn:
        own, other = _card(conn, "own"), _card(conn, "other")
        kb.block_task(conn, own, reason="Michael said stop", kind="needs_input")
        kb.complete_task(conn, own, summary="Cancelled at Michael's request.")
        session["HERMES_SESSION_CHAT_ID"] = "another-chat"
        kb.complete_task(conn, other, summary="Cancelled from another chat.")
    adapter = RecordingAdapter()

    wakes = _wakes(monkeypatch, _runner(adapter), adapter)

    assert len(wakes) == 1 and other in wakes[0]
    assert "caused_by operator" in wakes[0]
    with kbc.connect_closing() as conn:
        assert kbn.unseen_events_for_sub(conn, task_id=own, platform="telegram", chat_id=CHAT,
                                         kinds=("blocked", "completed"))[1] == []


def test_late_crash_replays_are_dropped_and_a_late_gave_up_still_wakes(board, monkeypatch):
    old = int(time.time()) - 600
    with kbc.connect_closing() as conn:
        replay, parked = _card(conn, "replay"), _card(conn, "parked")
        for tid, kinds in ((replay, ("crashed",)), (parked, ("crashed", "gave_up"))):
            for kind in kinds:
                conn.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
                             (tid, kind, json.dumps({"error": "pid 83937 exited with code 130"}), old))
        conn.commit()
    adapter = RecordingAdapter()

    wakes = _wakes(monkeypatch, _runner(adapter), adapter)

    assert len(wakes) == 1 and parked in wakes[0]
    assert "event: gave_up · caused_by restart replay · 10m ago" in wakes[0]
    assert "event: crashed" not in wakes[0]


def test_configured_drop_kinds_skip_retried_crashes_and_a_gave_up_still_wakes(board, monkeypatch):
    now = int(time.time())
    with kbc.connect_closing() as conn:
        retried, parked = _card(conn, "retried"), _card(conn, "parked")
        for tid, kinds in ((retried, ("crashed",)), (parked, ("crashed", "gave_up"))):
            for kind in kinds:
                conn.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
                             (tid, kind, json.dumps({"error": "pid 1 exited with code 1"}), now))
        conn.commit()
    adapter = RecordingAdapter()
    runner = _runner(adapter)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"kanban": {"wake": {"drop_kinds": ["crashed"]}}})

    wakes = _wakes(monkeypatch, runner, adapter)

    assert len(wakes) == 1 and parked in wakes[0] and retried not in wakes[0]
    assert "event: crashed" not in wakes[0]
    assert "event: gave_up" in wakes[0]


def test_repeat_wakes_on_one_card_merge_inside_the_window(board, monkeypatch):
    with kbc.connect_closing() as conn:
        tid = _card(conn)
        kb.block_task(conn, tid, reason="first question?", kind="needs_input",
                      expected_run_id=_as_worker(monkeypatch, conn, tid))
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    adapter = RecordingAdapter()
    runner = _runner(adapter)
    assert len(_wakes(monkeypatch, runner, adapter)) == 1

    with kbc.connect_closing() as conn:
        kb.unblock_task(conn, tid)
        kb.add_comment(conn, tid, "default", "Answer: yes.")
        kb.block_task(conn, tid, reason="second question?", kind="needs_input",
                      expected_run_id=_as_worker(monkeypatch, conn, tid))
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    assert _wakes(monkeypatch, runner, adapter) == []

    key = next(iter(runner._kanban_last_wake))
    runner._kanban_last_wake[key] -= 121
    (merged,) = _wakes(monkeypatch, runner, adapter)
    assert "block: needs_input — second question?" in merged
    assert "new since last wake: comments default #" in merged and "events unblocked" in merged


def test_wake_cursor_reads_only_what_came_since_the_last_wake(board, monkeypatch):
    from tools import kanban_tools as kt

    with kbc.connect_closing() as conn:
        tid = _card(conn)
        kb.block_task(conn, tid, reason="first?", kind="needs_input", expected_run_id=_as_worker(monkeypatch, conn, tid))
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    adapter = RecordingAdapter()
    runner = _runner(adapter)
    _wakes(monkeypatch, runner, adapter)
    with kbc.connect_closing() as conn:
        kb.add_comment(conn, tid, "default", "Old note before the answer.")
        kb.unblock_task(conn, tid)
        kb.block_task(conn, tid, reason="second?", kind="needs_input",
                      expected_run_id=_as_worker(monkeypatch, conn, tid))
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    runner._kanban_last_wake.clear()

    (wake,) = _wakes(monkeypatch, runner, adapter)

    line = next(line for line in wake.splitlines() if line.startswith("delta read: kanban_show("))
    cursor = json.loads(line[line.index("cursor=") + len("cursor="):-1])
    delta = json.loads(kt._handle_show({"task_id": tid, "cursor": cursor}))
    assert [c["body"] for c in delta["comments"]] == ["Old note before the answer."]
    assert [e["kind"] for e in delta["events"]][-1] == "blocked"
    assert "body" not in delta["task"]


def test_evidence_files_stay_paths_and_deliverables_upload(board, monkeypatch, tmp_path):
    from gateway.kanban_wake_contract import contract_lines, wake_settings
    from gateway.platforms.base import BasePlatformAdapter

    monkeypatch.setenv("HERMES_MEDIA_ALLOW_DIRS", str(tmp_path))
    files = [tmp_path / name for name in ("rulebook.pdf", "evidence.zip", "receipt.json", "run.log")]
    for path in files:
        path.write_bytes(b"x")
    uploaded: list = []

    async def send_document(chat_id, file_path, metadata=None, **_kw):
        uploaded.append(file_path)

    adapter = SimpleNamespace(send_document=send_document, send_video=None, send_multiple_images=None,
                              extract_local_files=BasePlatformAdapter.extract_local_files)
    runner = GatewayRunner.__new__(GatewayRunner)
    payload = {"summary": "done", "artifacts": [str(p) for p in files]}

    asyncio.run(runner._deliver_kanban_artifacts(adapter=adapter, chat_id=CHAT, metadata={},
                                                 event_payload=payload, task=None))

    assert [p.rsplit("/", 1)[-1] for p in uploaded] == ["rulebook.pdf"]
    event = SimpleNamespace(id=1, kind="completed", payload=payload, created_at=int(time.time()), run_id=None)
    lines = contract_lines([event], {}, settings=wake_settings())
    evidence = next(line for line in lines if line.startswith("evidence (paths, not uploaded): "))
    assert "evidence.zip" in evidence and "receipt.json" in evidence and "run.log" in evidence
    assert "rulebook.pdf" not in evidence


def test_a_stop_rule_block_wakes_as_a_result_not_a_diagnostic():
    from gateway.kanban_watchers_notifier import diagnostic_event

    stop = SimpleNamespace(kind="blocked", payload={"kind": "stop-rule", "reason": "Stop met"})
    capacity = SimpleNamespace(kind="blocked", payload={"kind": "capacity", "reason": "mini busy"})
    assert diagnostic_event(stop) is False
    assert diagnostic_event(capacity) is True
