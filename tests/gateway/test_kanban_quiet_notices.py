"""LOCAL-PATCH kanban-quiet-notices.

``kanban.notify_delivery_mode: wake`` subscribes chat sessions wake-only, so the
woken agent tells the user instead of the raw notice being posted. Wake-only
push subscriptions must still upload the handoff's files, exactly once.
"""

import asyncio

from gateway.config import Platform
from gateway.run import GatewayRunner
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_notify as kbn


class RecordingAdapter:
    def __init__(self, fail_wake=False):
        self.sent = []
        self.handled = []
        self.fail_wake = fail_wake

    async def send(self, chat_id, text, metadata=None):
        self.sent.append(text)

    async def handle_message(self, event):
        self.handled.append(event)
        if self.fail_wake:
            raise RuntimeError("simulated wake failure")
        event._gateway_accepted = True


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


def _runner(adapter, uploads):
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._kanban_sub_fail_counts = {}
    runner._kanban_dispatcher_lock_handle = object()

    async def record_upload(**kwargs):
        uploads.append(kwargs["event_payload"])

    runner._deliver_kanban_artifacts = record_upload
    return runner


def _completed_task(delivery_mode):
    conn = kbc.connect()
    try:
        tid = kb.create_task(conn, title="quiet", assignee="worker",
                             session_id="agent:main:telegram:dm:chat-1")
        kbn.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1",
                           chat_type="dm", delivery_mode=delivery_mode)
        kb.complete_task(conn, tid, summary="report at /tmp/report.html")
        return tid
    finally:
        conn.close()


def test_wake_only_posts_no_notice_but_uploads_files(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "quiet.db"))
    kb.init_db()
    _completed_task("wake")
    adapter, uploads = RecordingAdapter(), []
    asyncio.run(_tick(monkeypatch, _runner(adapter, uploads)))

    assert adapter.sent == []
    assert len(adapter.handled) == 1
    assert len(uploads) == 1


def test_wake_retry_does_not_upload_files_twice(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "retry.db"))
    kb.init_db()
    _completed_task("wake")
    adapter, uploads = RecordingAdapter(fail_wake=True), []
    runner = _runner(adapter, uploads)
    asyncio.run(_tick(monkeypatch, runner))
    asyncio.run(_tick(monkeypatch, runner))

    assert len(adapter.handled) == 2
    assert len(uploads) == 1


def test_notify_wake_still_posts_notice(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "loud.db"))
    kb.init_db()
    _completed_task("notify+wake")
    adapter, uploads = RecordingAdapter(), []
    asyncio.run(_tick(monkeypatch, _runner(adapter, uploads)))

    assert len(adapter.sent) == 1
    assert len(adapter.handled) == 1
    assert len(uploads) == 1


def _config(monkeypatch, kanban):
    from hermes_cli import config
    monkeypatch.setattr(config, "load_config", lambda: {"kanban": kanban})


def test_chat_delivery_mode_reads_config(monkeypatch):
    _config(monkeypatch, {"notify_delivery_mode": "wake"})
    assert kbn.chat_delivery_mode() == "wake"
    _config(monkeypatch, {"notify_delivery_mode": "notify"})
    assert kbn.chat_delivery_mode() == "notify+wake"
    _config(monkeypatch, {})
    assert kbn.chat_delivery_mode() == "notify+wake"


def test_auto_subscribe_target_uses_configured_mode(monkeypatch):
    from gateway import session_context
    from tools import kanban_tools

    session = {"HERMES_SESSION_PLATFORM": "discord", "HERMES_SESSION_CHAT_ID": "123",
               "HERMES_SESSION_CHAT_TYPE": "group", "HERMES_SESSION_PROFILE": "default"}
    monkeypatch.setattr(session_context, "get_session_env", lambda k, d="": session.get(k, d))
    _config(monkeypatch, {"notify_delivery_mode": "wake"})
    assert kanban_tools._resolve_notify_target()["delivery_mode"] == "wake"
    _config(monkeypatch, {})
    assert kanban_tools._resolve_notify_target()["delivery_mode"] == "notify+wake"
