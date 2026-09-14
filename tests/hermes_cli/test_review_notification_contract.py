from hermes_cli import kanban_db as kb, kanban_db_connect as kbc, kanban_db_notify as notify
from hermes_cli.kanban_db_unblock import unblock_task


def test_real_reblocks_ignore_bookkeeping_but_preserve_changes(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(kb, 'BLOCK_RECURRENCE_LIMIT', 20)
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title='notification fixture', assignee='owner')
        key = dict(task_id=tid, platform='discord', chat_id='fixture')
        notify.add_notify_sub(conn, **key, delivery_mode='notify+wake')
        for reason, delay, expected in [('quota unavailable',3600,1), ('quota unavailable',3600,0),
                                         ('different account unavailable',3600,1), ('different account unavailable',7200,1)]:
            assert kb.block_task(conn, tid, reason=reason, kind='transient', resume_after=delay)
            old, new, events = notify.claim_unseen_events_for_sub(conn, **key, kinds=['blocked','completed'])
            assert new > old and len(events) == expected
            assert unblock_task(conn, tid)
        assert kb.complete_task(conn, tid, result='finished', fire_lifecycle_hook=False)
        assert [e.kind for e in notify.claim_unseen_events_for_sub(conn, **key, kinds=['blocked','completed'])[2]] == ['completed']


def test_failure_counter_does_not_change_gave_up_notification_identity(tmp_path,monkeypatch):
    from hermes_cli.kanban_db_dispatch import _record_task_failure
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    with kbc.connect_closing() as conn:
        tid=kb.create_task(conn,title='breaker fixture',assignee='owner')
        key=dict(task_id=tid,platform='discord',chat_id='fixture')
        notify.add_notify_sub(conn,**key,delivery_mode='notify+wake')
        for counter,error,expected in [(0,'quota exhausted',1),(3,'quota exhausted',0),(4,'different blocker',1)]:
            with kb.write_txn(conn):conn.execute('UPDATE tasks SET consecutive_failures=? WHERE id=?',(counter,tid))
            assert _record_task_failure(conn,tid,error,outcome='failed',force_trip=True)
            assert len(notify.claim_unseen_events_for_sub(conn,**key,kinds=['gave_up'])[2])==expected
            assert unblock_task(conn,tid)
