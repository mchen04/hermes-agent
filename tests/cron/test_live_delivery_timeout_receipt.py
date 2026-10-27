import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cron import executions, scheduler, scheduler_delivery
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import SendResult
from tests.waiting import wait_until


@pytest.mark.parametrize("run_success", [True, False])
@pytest.mark.parametrize("no_agent", [False, True])
@pytest.mark.parametrize("via_queue", [False, True])
@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize("receipt_before_bookkeeping", [True, False])
def test_timeout_stays_unknown_until_the_adapter_receipt(success, receipt_before_bookkeeping, via_queue, no_agent, run_success, monkeypatch, caplog):
    from cron.jobs import create_job
    from cron.scheduler_bookkeeping import finish_completed_run

    from hermes_constants import get_hermes_home
    from cron.jobs import get_job

    script = get_hermes_home() / "scripts" / "receipt.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("printf result\n")
    job = create_job("brief", "every 24h", repeat=2, no_agent=no_agent, script="receipt.sh" if no_agent else None,
                     deliver="origin", origin={"platform": "discord", "chat_id": "123"})
    execution = executions.create_execution(job["id"], source="test")
    executions.mark_execution_running(execution["id"])
    job["execution_id"] = execution["id"]
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    release = asyncio.Event()
    finished = threading.Event()
    seed_threads = []
    adapter = MagicMock()

    async def send(*args, **kwargs):
        await release.wait()
        return SendResult(success=success, message_id="receipt-123" if success else None, error=None if success else "send failed")

    adapter.send = send
    config = MagicMock(platforms={Platform.DISCORD: PlatformConfig(enabled=True)})
    config.get_home_channel.return_value = None
    standalone = AsyncMock()
    monkeypatch.setattr(scheduler_delivery, "_LIVE_SEND_CONFIRM_TIMEOUT_SECS", 0.01)
    def seed(*args):
        seed_threads.append(threading.current_thread())
        finished.set()

    monkeypatch.setattr(scheduler_delivery, "_seed_live_delivery_sessions", seed)
    try:
        with (
            caplog.at_level("INFO"),
            patch("gateway.config.load_gateway_config", return_value=config),
            patch("cron.scheduler.load_config", return_value={"cron": {"wrap_response": False}}),
            patch("tools.send_message_tool._send_to_platform", standalone),
        ):
            if via_queue:
                from cron import delivery_queue

                delivery_queue.enqueue(execution["id"], job, "Brief text", for_failure=not run_success)
                assert delivery_queue.drain(lambda queued, content, failure: scheduler._deliver_result(
                    queued, content, adapters={Platform.DISCORD: adapter}, loop=loop, for_failure=failure,
                )) == 1
                assert delivery_queue.get_status(execution["id"])["status"] == "unknown"
                job["_live_delivery_unknown"] = True
                error = None
            else:
                error = scheduler._deliver_result(job, "Brief text", adapters={Platform.DISCORD: adapter}, loop=loop,
                                                      for_failure=not run_success)
            assert error is None
            assert "delivered to" not in caplog.text
            assert "message_id=-" not in caplog.text
            standalone.assert_not_awaited()
            if receipt_before_bookkeeping:
                loop.call_soon_threadsafe(release.set)
                wait_until(lambda: "adapter receipt confirmed" in caplog.text or "adapter receipt failed" in caplog.text,
                           what="adapter receipt")
            delivery = scheduler._RunDelivery(job=job, success=run_success, error=None if run_success else "script failed",
                                               should_deliver=True, delivery_error=error)
            finish_completed_run(delivery, None, execution["id"])
            if not receipt_before_bookkeeping:
                pending = executions.get_execution(execution["id"])
                assert pending["status"] == "unknown"
                assert pending["delivery_outcome"] == "unknown"
                loop.call_soon_threadsafe(release.set)
            expected = "delivered" if success else "failed"
            wait_until(lambda: executions.get_execution(execution["id"])["delivery_outcome"] == expected,
                       what="durable adapter receipt")
            final = executions.get_execution(execution["id"])
            assert final["status"] == ("completed" if success and run_success else "failed")
            expected_status = "error" if not run_success else "ok" if success else "delivery_failed"
            wait_until(lambda: get_job(job["id"])["last_status"] == expected_status,
                       what="job delivery receipt")
            assert get_job(job["id"])["repeat"]["completed"] == 1
            if success:
                wait_until(finished.is_set, what="confirmed delivery session seed")
                assert seed_threads[0] is not thread
                assert "receipt-123" in caplog.text
            assert "message_id=-" not in caplog.text
    finally:
        loop.call_soon_threadsafe(release.set)
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()


def test_late_receipt_keeps_the_owning_profile_scope(tmp_path, monkeypatch):
    from concurrent.futures import Future
    from dataclasses import dataclass

    from cron.live_delivery_receipts import track
    from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override

    @dataclass
    class Target:
        job: dict
        where: str

    seen = []
    finished = threading.Event()

    def seed(*args):
        seen.append(get_hermes_home())
        finished.set()

    monkeypatch.setattr(scheduler_delivery, "_seed_live_delivery_sessions", seed)
    owner = tmp_path / "owner"
    future = Future()
    token = set_hermes_home_override(owner)
    try:
        track(Target({"id": "receipt"}, "discord:123"), future)
    finally:
        reset_hermes_home_override(token)
    future.set_result(SendResult(success=True, message_id="receipt-123"))
    wait_until(finished.is_set, what="profile-bound receipt")
    assert seen == [owner]


def test_a_cancelled_confirmation_is_still_unknown(monkeypatch):
    from concurrent.futures import Future
    from dataclasses import dataclass

    from agent import memory_provider
    from cron import live_delivery_receipts

    @dataclass
    class Target:
        job: dict
        where: str

    workers = []
    spawn = memory_provider.spawn_context_thread

    def record_worker(*args, **kwargs):
        worker = spawn(*args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(memory_provider, "spawn_context_thread", record_worker)
    job = {"id": "cancelled"}
    future = Future()
    live_delivery_receipts.track(Target(job, "discord:123"), future)
    assert future.cancel()
    for worker in workers:
        worker.join(5)
        assert not worker.is_alive()
    assert live_delivery_receipts.outcome(job) == "unknown"
    assert job["_live_delivery_receipts"][0]["status"] == "unknown"
