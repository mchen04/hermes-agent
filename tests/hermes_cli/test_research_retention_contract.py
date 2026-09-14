import json
from types import SimpleNamespace
from hermes_cli import kanban_db as kb, kanban_db_connect as kbc, kanban_db_workspace as ws
from hermes_cli.kanban_retention import archive_research, digest, verify_archive
from gateway.kanban_watchers_notifier import _fmt_completed


def test_raw_evidence_must_survive_real_scratch_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    monkeypatch.setattr(ws,'_cleanup_worker_tmux',lambda *args:None)
    conn=kbc.connect()
    root=tmp_path/'kanban/workspaces/research-fixture';root.mkdir(parents=True)
    (root/'retention-required.json').write_text('{}')
    tid=kb.create_task(conn,title='fixture',workspace_kind='scratch',workspace_path=str(root))
    try:
        ws._cleanup_workspace(conn,tid)
        assert root.exists(), 'missing archive must retain original sources'
        (root/'raw.txt').write_text('retained original source')
        (root/'sources.md').write_text('https://example.invalid/source')
        entries=[{'path':'raw.txt','kind':'raw_source','sha256':digest(root/'raw.txt'),
                  'url':'https://example.invalid/source','publisher':'Fixture','published_at':'2026-09-01','retrieved_at':'2026-09-13'}]
        entries.append({'path':'sources.md','kind':'evidence','sha256':digest(root/'sources.md')})
        (root/'evidence-manifest.json').write_text(json.dumps({'entries':entries}))
        receipt=archive_research(root,tmp_path/'retained.zip')
        ws._cleanup_workspace(conn,tid)
        assert not root.exists()
        assert verify_archive(receipt['archive'],receipt['sha256'])['entries']==entries
    finally: conn.close()


def test_budget_exception_notification_never_claims_objective_success(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    monkeypatch.setattr(ws,'_cleanup_worker_tmux',lambda *args:None)
    with kbc.connect_closing() as conn:
        tid=kb.create_task(conn,title='research fixture',assignee='owner')
        assert kb.complete_task(conn,tid,summary='0/5 survivors',metadata={'outcome':'budget_exhausted'},fire_lifecycle_hook=False)
        row=conn.execute("SELECT * FROM task_events WHERE task_id=? AND kind='completed'",(tid,)).fetchone()
        ev=kb.Event.from_row(row)
        n=SimpleNamespace(task=kb.get_task(conn,tid),head='fixture',title='research')
        text,handoff,_=_fmt_completed(ev,n)
        assert 'Objective unmet' in text and '0/5' in text
        assert 'budget_exhausted' in handoff
