import json
import time
from hermes_cli import kanban_db as kb, kanban_db_connect as kbc
from hermes_cli.kanban_db_unblock import unblock_task


def test_automatic_resume_cannot_bypass_recovery_or_hold(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    now = int(time.time())
    dependency = dict(provider='anthropic', account='fable', resource='tokens', owner='owner', authorized=True, retry_not_before=now+18000)
    with kbc.connect_closing() as conn:
        protected=[]
        for reason in [json.dumps({'recovery':dependency}), json.dumps({'recovery':dict(dependency,retry_not_before=now-1)}),
                       json.dumps({'recovery':dict(dependency,provider=None)}), 'HOLD for approval', 'STOP automatic work']:
            tid=kb.create_task(conn,title='recovery fixture',assignee='owner')
            assert kb.block_task(conn,tid,kind='transient',reason=reason,resume_after=1)
            with kb.write_txn(conn):conn.execute("UPDATE task_events SET created_at=? WHERE task_id=? AND kind='blocked'",(now-7200,tid))
            protected.append(tid)
        ordinary=kb.create_task(conn,title='ordinary transient',assignee='owner')
        kb.block_task(conn,ordinary,kind='transient',reason='temporary socket timeout',resume_after=1)
        with kb.write_txn(conn):conn.execute("UPDATE task_events SET created_at=? WHERE task_id=? AND kind='blocked'",(now-7200,ordinary))
        assert [r['task_id'] for r in kb.resume_stranded_blocks(conn,now=now)] == [ordinary]
        for tid in protected:
            eid=conn.execute("SELECT MAX(id) FROM task_events WHERE task_id=? AND kind='blocked'",(tid,)).fetchone()[0]
            assert not unblock_task(conn,tid,auto_resume={'blocked_event_id':eid,'trigger':'answered'})
            assert kb.get_task(conn,tid).status=='blocked'


def test_answered_domain_questions_resume_but_real_capacity_blocks_do_not(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    now = int(time.time())
    questions = ['Which credit-spread series should I use?', 'What capacity should the disk have?']
    protected = ['HOLD for approval', 'STOP automatic work', 'Provider credits exhausted', 'Insufficient credits',
                 'Provider capacity exhausted', 'Quota exceeded', 'rate limit reached', 'HTTP 429']
    with kbc.connect_closing() as conn:
        ids = []
        for reason in questions + protected:
            tid = kb.create_task(conn, title='synthetic needs-input', assignee='owner')
            kb.block_task(conn, tid, kind='needs_input', reason=reason)
            with kb.write_txn(conn):
                conn.execute("UPDATE task_events SET created_at=? WHERE task_id=? AND kind='blocked'", (now-10, tid))
            kb.add_comment(conn, tid, author='human', body='Use the specified series.')
            ids.append(tid)
        assert {r['task_id'] for r in kb.resume_stranded_blocks(conn, now=now+1)} == set(ids[:len(questions)])
        assert all(kb.get_task(conn, tid).status == 'blocked' for tid in ids[len(questions):])
