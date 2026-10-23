import asyncio
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.kanban_watchers_notifier import _WAKE_KINDS, diagnostic_event
from gateway.run import GatewayRunner
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_notify as kbn

CHAT = "chat-1"
STALL = {
    "stall": "guard_hold",
    "reason": "held 47 min: the dispatcher will not start a worker while PR https://github.com/o/r/pull/2 is open",
    "next": "post Michael's go-ahead as a comment, then kanban_resume_authorized_pr with that comment id",
    "actor": "stall alarm",
}


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
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "stall.db"))
    for var in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_SESSION_PLATFORM", "HERMES_SESSION_CHAT_ID"):
        monkeypatch.delenv(var, raising=False)
    kb.init_db()
    return tmp_path


def _runner(adapter):
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._kanban_sub_fail_counts = {}
    runner._kanban_dispatcher_lock_handle = object()

    async def no_upload(**kwargs):
        return None

    runner._deliver_kanban_artifacts = no_upload
    return runner


def _tick(monkeypatch, runner):
    real_sleep = asyncio.sleep

    async def fake_sleep(delay):
        if delay == 5:
            return
        runner._running = False
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    runner._running = True
    asyncio.run(runner._kanban_notifier_watcher(interval=1))


def _stalled_card(mode):
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="publish the router", assignee="forge",
                             session_id="agent:main:telegram:dm:chat-1")
        kbn.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id=CHAT, chat_type="dm", delivery_mode=mode)
        with kb.write_txn(conn):
            kb._append_event(conn, tid, "stalled", STALL)
    return tid


def test_a_stalled_card_wakes_the_chat_that_follows_it_with_the_stall_and_its_next_step(board, monkeypatch):
    tid = _stalled_card("wake")
    adapter = RecordingAdapter()

    _tick(monkeypatch, _runner(adapter))

    assert adapter.sent == []
    (event,) = adapter.handled
    wake = event.text
    assert f"Task {tid} stalled; no worker is moving it" in wake
    assert "event: stalled · caused_by operator (stall alarm) · " in wake
    assert f"stall: guard_hold — {STALL['reason']}" in wake
    assert "needs_human: no" in wake
    assert f"next: {STALL['next']}" in wake
    assert (event.metadata or {}).get("notification_category", "result") == "result"


def test_a_stalled_card_is_claimed_once(board, monkeypatch):
    _stalled_card("wake")
    adapter = RecordingAdapter()
    runner = _runner(adapter)

    _tick(monkeypatch, runner)
    runner._kanban_last_wake = {}
    _tick(monkeypatch, runner)

    assert len(adapter.handled) == 1


def test_a_notify_subscription_gets_one_line_naming_the_stall(board, monkeypatch):
    tid = _stalled_card("notify")
    adapter = RecordingAdapter()

    _tick(monkeypatch, _runner(adapter))

    assert adapter.handled == []
    (line,) = adapter.sent
    assert tid in line and "stalled" in line and "held 47 min" in line


def test_a_stall_is_a_wake_kind_and_never_a_muted_diagnostic():
    assert "stalled" in _WAKE_KINDS
    assert not diagnostic_event(SimpleNamespace(kind="stalled", payload=STALL))
