"""Audit history survives busy polling without retaining terminal rows forever."""

from datetime import datetime, timedelta, timezone


def test_busy_history_keeps_recent_failures_receipts_and_inflight(monkeypatch, tmp_path):
    from cron import executions

    monkeypatch.setattr(executions, "EXECUTIONS_FILE", tmp_path / "cron" / "executions.db")
    now = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(executions, "_hermes_now", lambda: now)
    recent = (now - timedelta(days=29)).isoformat()
    expired = (now - timedelta(days=31)).isoformat()
    running = executions.create_execution("running", source="builtin")
    executions.mark_execution_running(running["id"])
    pending = executions.create_execution("handoff", source="builtin")
    executions.mark_execution_handoff_pending(pending["id"])
    with executions._transaction() as conn:
        conn.executemany(
            "INSERT INTO executions (id,job_id,source,process_id,pid,status,claimed_at,finished_at,"
            "delivery_outcome,error) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(f"recent-{i}", "poll", "builtin", "owner", 1, "completed", recent, recent, None, None)
             for i in range(1100)]
            + [("receipt", "digest", "builtin", "owner", 1, "completed", recent, recent, "delivered", None),
               ("failure", "digest", "builtin", "owner", 1, "failed", recent, recent, "failed", "fixture failure")]
            + [(f"old-{status}", "old", "builtin", "owner", 1, status, expired, expired, None, None)
               for status in ("completed", "failed", "unknown")],
        )
        conn.execute("UPDATE executions SET claimed_at=? WHERE status IN ('running','claimed')", (expired,))
    trigger = executions.create_execution("trigger", source="builtin")
    executions.finish_execution(trigger["id"], success=True)

    with executions._transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM executions WHERE id LIKE 'recent-%'").fetchone()[0] == 1100
        assert conn.execute("SELECT COUNT(*) FROM executions WHERE id LIKE 'old-%'").fetchone()[0] == 0
    assert executions.get_execution("receipt")["delivery_outcome"] == "delivered"
    assert executions.get_execution("failure")["error"] == "fixture failure"
    assert executions.get_execution(running["id"])["status"] == "running"
    assert executions.get_execution(pending["id"])["handoff_pending"] == 1


def test_age_uses_finished_time_and_respects_timestamp_offsets(monkeypatch, tmp_path):
    from cron import executions

    monkeypatch.setattr(executions, "EXECUTIONS_FILE", tmp_path / "cron" / "executions.db")
    now = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(executions, "_hermes_now", lambda: now)
    long_running = executions.create_execution("long-running", source="builtin")
    boundary = executions.create_execution("boundary", source="builtin")
    executions.finish_execution(boundary["id"], success=True)
    cutoff = now - timedelta(days=30)
    with executions._transaction() as conn:
        conn.execute("UPDATE executions SET claimed_at=? WHERE id=?",
                     ((now - timedelta(days=60)).isoformat(), long_running["id"]))
        conn.execute("UPDATE executions SET finished_at=? WHERE id=?",
                     ((cutoff - timedelta(seconds=1)).astimezone(timezone(timedelta(hours=10))).isoformat(), boundary["id"]))

    assert executions.finish_execution(long_running["id"], success=True)["status"] == "completed"
    assert executions.get_execution(boundary["id"]) is None
