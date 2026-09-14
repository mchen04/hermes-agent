import pytest

from hermes_cli import kanban_db as kb, kanban_db_connect as kbc, kanban_db_notify as notify
from hermes_cli.kanban_db_unblock import unblock_task


@pytest.mark.parametrize('horizon_present', [False, True])
def test_legacy_text_cursor_rebuild_preserves_subscription_horizon(tmp_path, monkeypatch, horizon_present):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    path = tmp_path/'legacy.db'
    with kbc.connect_closing(path) as conn:
        task = kb.create_task(conn, title='legacy subscription', assignee='owner')
        kb.block_task(conn, task, reason='quota exhausted', kind='transient', resume_after=3600)
        key = dict(task_id=task, platform='discord', chat_id='fixture')
        notify.add_notify_sub(conn, **key)
        cursor = conn.execute('SELECT last_event_id FROM kanban_notify_subs').fetchone()[0]
        with kb.write_txn(conn):
            conn.execute('ALTER TABLE kanban_notify_subs RENAME TO old_subs')
            conn.execute('CREATE TABLE kanban_notify_subs(task_id TEXT, platform TEXT, chat_id TEXT, thread_id TEXT, last_event_id TEXT, created_at TEXT, delivery_mode TEXT' + (', start_event_id INTEGER' if horizon_present else '') + ', PRIMARY KEY(task_id,platform,chat_id))')
            columns = 'task_id,platform,chat_id,thread_id,last_event_id,created_at,delivery_mode' + (',start_event_id' if horizon_present else '')
            conn.execute(f'INSERT INTO kanban_notify_subs({columns}) SELECT {columns} FROM old_subs')
            conn.execute('DROP TABLE old_subs')
    kbc._INITIALIZED_PATHS.discard(str(path.resolve()))
    with kbc.connect_closing(path) as conn:
        row = conn.execute('SELECT last_event_id,start_event_id FROM kanban_notify_subs').fetchone()
        assert tuple(row) == (cursor, cursor)
        assert not notify.claim_unseen_events_for_sub(conn, **key, kinds=['blocked'])[2]
        assert unblock_task(conn, task)
        kb.block_task(conn, task, reason='quota exhausted', kind='transient', resume_after=3600)
        assert len(notify.claim_unseen_events_for_sub(conn, **key, kinds=['blocked'])[2]) == 1
