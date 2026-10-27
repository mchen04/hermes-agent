from __future__ import annotations

import contextvars
import logging
import threading
from dataclasses import replace

logger = logging.getLogger("cron.scheduler")
_lock = threading.RLock()


def outcome(job: dict) -> str | None:
    with _lock:
        receipts = job.get("_live_delivery_receipts", [])
        if receipts:
            states = {receipt["status"] for receipt in receipts}
            return "unknown" if "unknown" in states else "failed" if "failed" in states else "delivered"
    if job.get("_live_delivery_unknown"):
        from cron.delivery_queue import get_status

        row = get_status(str(job.get("execution_id") or ""))
        return row["status"] if row and row["status"] in ("delivered", "failed") else "unknown"
    return None


def error(job: dict) -> str | None:
    with _lock:
        errors = [receipt["error"] for receipt in job.get("_live_delivery_receipts", []) if receipt.get("error")]
    if errors:
        return "; ".join(errors)
    if job.get("_live_delivery_unknown"):
        from cron.delivery_queue import get_status

        row = get_status(str(job.get("execution_id") or ""))
        if row and row["status"] == "failed":
            return row.get("error") or "adapter receipt failed"
    return None


def settle(job: dict) -> None:
    execution_id = job.get("execution_id")
    result = outcome(job)
    if not execution_id or result not in ("delivered", "failed"):
        return
    from cron.delivery_outcome import _settle_job_record
    from cron.delivery_queue import resolve_live_receipt
    from cron.executions import settle_delivery_outcome

    detail = error(job)
    resolve_live_receipt(str(execution_id), result, detail)
    if settle_delivery_outcome(str(execution_id), result, error=detail):
        _settle_job_record(job["id"], str(execution_id), detail, outcome=result)


def track(target, future) -> None:
    job = target.job
    with _lock:
        job.setdefault("_live_delivery_receipts", [])
    target = replace(target, job=dict(job))
    receipt = {"where": target.where, "status": "unknown"}
    with _lock:
        receipts = target.job.setdefault("_live_delivery_receipts", [])
        receipts.append(receipt)
        job = {"id": job["id"], "execution_id": job.get("execution_id"), "_live_delivery_receipts": receipts}
    scope = contextvars.copy_context()

    def receive(completed):
        from cron.scheduler_delivery import _confirm_adapter_delivery, _result_field, _seed_live_delivery_sessions

        confirmed = False
        try:
            result = completed.result()
            if not _confirm_adapter_delivery(result, job["id"]):
                raise RuntimeError(_result_field(result, "error") or "adapter returned an unconfirmed receipt")
        except Exception as exc:
            detail = str(exc) or type(exc).__name__
            with _lock:
                receipt.update(status="failed", error=detail)
            logger.warning("Job '%s': adapter receipt failed for %s: %s", job["id"], target.where, detail, exc_info=True)
        else:
            message_id = _result_field(result, "message_id")
            with _lock:
                receipt.update(status="delivered", message_id=message_id)
            logger.info("Job '%s': adapter receipt confirmed delivery to %s message_id=%s",
                        job["id"], target.where, message_id)
            confirmed = True
        settle(job)
        if confirmed:
            _seed_live_delivery_sessions(target, message_id)

    def dispatch(completed):
        from agent.memory_provider import spawn_context_thread

        scope.run(spawn_context_thread, receive, name="cron-delivery-receipt", args=(completed,)).start()

    future.add_done_callback(dispatch)
