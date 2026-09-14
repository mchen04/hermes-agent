import time

import pytest

from hermes_cli import kanban_db as kb, kanban_db_connect as kbc, kanban_db_notify as notify
from hermes_cli.kanban_db_unblock import unblock_task


@pytest.mark.parametrize('reason', ['Codex usage-limit reached', 'provider rate-limited',
                                  'provider rate limited', 'HTTP429', 'HTTP 429', 'Too Many Requests'])
def test_quota_wording_never_resumes_by_timer_or_comment(tmp_path, monkeypatch, reason):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    now = int(time.time())
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title='quota fixture', assignee='owner')
        assert kb.block_task(conn, tid, kind='transient', reason=reason, resume_after=1)
        with kb.write_txn(conn):
            conn.execute("UPDATE task_events SET created_at=? WHERE task_id=? AND kind='blocked'", (now-7200, tid))
        event_id = conn.execute("SELECT MAX(id) FROM task_events WHERE task_id=?", (tid,)).fetchone()[0]
        assert kb.resume_stranded_blocks(conn, now=now) == []
        assert not unblock_task(conn, tid, auto_resume={'blocked_event_id': event_id, 'trigger': 'answered'})
        assert kb.get_task(conn, tid).status == 'blocked'


@pytest.mark.parametrize('legacy', [False, True])
def test_late_subscription_only_coalesces_observed_blockers(tmp_path, monkeypatch, legacy):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(kb, 'BLOCK_RECURRENCE_LIMIT', 30)
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title='late subscription', assignee='owner')
        assert kb.block_task(conn, tid, reason='quota exhausted', kind='transient', resume_after=3600)
        key = dict(task_id=tid, platform='discord', chat_id='late')
        notify.add_notify_sub(conn, **key, delivery_mode='notify+wake')
        if legacy:
            with kb.write_txn(conn):
                conn.execute('ALTER TABLE kanban_notify_subs DROP COLUMN start_event_id')
            kbc._migrate_add_optional_columns(conn)
        assert not notify.claim_unseen_events_for_sub(conn, **key, kinds=['blocked'])[2]
        for reason, count in [('quota exhausted', 1), ('quota exhausted', 0), ('changed quota', 1)]:
            assert unblock_task(conn, tid)
            assert kb.block_task(conn, tid, reason=reason, kind='transient', resume_after=3600)
            old, new, events = notify.claim_unseen_events_for_sub(conn, **key, kinds=['blocked'])
            assert len(events) == count
            if events:
                # A failed transport must be retryable before observation is accepted.
                assert notify.rewind_notify_cursor(conn, **key, old_cursor=old, claimed_cursor=new)
                assert notify.claim_unseen_events_for_sub(conn, **key, kinds=['blocked'])[2] == events
            notify.add_notify_sub(conn, **key)  # An idempotent re-registration preserves observation.
            kbc._migrate_add_optional_columns(conn)
        assert notify.remove_notify_sub(conn, **key)
        notify.add_notify_sub(conn, **key)
        assert unblock_task(conn, tid)
        assert kb.block_task(conn, tid, reason='changed quota', kind='transient', resume_after=3600)
        assert len(notify.claim_unseen_events_for_sub(conn, **key, kinds=['blocked'])[2]) == 1
