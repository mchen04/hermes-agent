import json
import time
from hermes_cli import kanban_db as kb, kanban_db_connect as kbc, kanban_db_notify as kbn
from hermes_cli.kanban_db_unblock import unblock_task


def test_capacity_recovery_preserves_dependency_deadline_and_owner(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    conn=kbc.connect()
    try:
        tid=kb.create_task(conn,title='synthetic quota block',assignee='same-owner')
        now=int(time.time())
        dep={'provider':'openai-codex','account':'account-a','resource':'codex',
             'owner':'same-owner','authorized':True,'retry_not_before':now+3600}
        payload={'recovery':dep,'resume_after':3600}
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='blocked' WHERE id=?",(tid,))
            kb._append_event(conn,tid,'blocked',payload)
        ev=conn.execute('SELECT MAX(id) FROM task_events WHERE task_id=?',(tid,)).fetchone()[0]
        capacity=dict(dep,available=True,checked_at=now)
        assert not unblock_task(conn,tid,capacity=capacity,expected_event_id=ev)
        dep['retry_not_before']=now-1
        with kb.write_txn(conn):
            conn.execute('UPDATE task_events SET payload=?,created_at=? WHERE id=?',(json.dumps(payload),now-3601,ev))
        for field,value in [('provider','anthropic'),('account','account-b'),('resource','other'),('available',False),('checked_at',now-100)]:
            assert not unblock_task(conn,tid,capacity=dict(capacity,**{field:value}),expected_event_id=ev)
        assert not unblock_task(conn,tid,capacity=capacity,expected_event_id=ev-1)
        with kb.write_txn(conn):
            payload['reason']='HOLD until approved'
            conn.execute('UPDATE task_events SET payload=? WHERE id=?',(json.dumps(payload),ev))
        assert not unblock_task(conn,tid,capacity=capacity,expected_event_id=ev)
        with kb.write_txn(conn):
            payload.pop('reason')
            conn.execute('UPDATE task_events SET payload=? WHERE id=?',(json.dumps(payload),ev))
        assert unblock_task(conn,tid,capacity=capacity,expected_event_id=ev)
        assert kb.get_task(conn,tid).assignee=='same-owner'
        assert not unblock_task(conn,tid,capacity=capacity,expected_event_id=ev)
    finally:
        conn.close()


def test_unchanged_blocker_is_consumed_before_notification_and_wake(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    conn=kbc.connect()
    try:
        tid=kb.create_task(conn,title='quiet block',assignee='owner')
        key={'task_id':tid,'platform':'discord','chat_id':'synthetic-chat'}
        kbn.add_notify_sub(conn,**key,delivery_mode='notify+wake')
        def append(kind,payload):
            with kb.write_txn(conn): kb._append_event(conn,tid,kind,payload)
        payload={'reason':'Fable quota exhausted','resume_after':3600}
        append('blocked',payload)
        assert len(kbn.claim_unseen_events_for_sub(conn,**key)[2])==1
        append('blocked',payload)
        old,new,events=kbn.claim_unseen_events_for_sub(conn,**key)
        assert new>old and events==[]
        assert kbn.claim_unseen_events_for_sub(conn,**key)[2]==[]
        append('blocked',dict(payload,reason='Needs a human decision'))
        assert len(kbn.claim_unseen_events_for_sub(conn,**key)[2])==1
    finally:
        conn.close()
