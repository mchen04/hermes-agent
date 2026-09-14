import json
import pytest
from types import SimpleNamespace
from hermes_cli import kanban_db as kb, kanban_db_connect as kbc, kanban_db_workspace as ws
from hermes_cli import kanban_ops
from hermes_cli.kanban_retention import archive_research, digest, verify_archive


@pytest.mark.parametrize("mode", ["parent", "gc"])
def test_deferred_parent_and_gc_keep_unarchived_research(tmp_path,monkeypatch,mode):
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    monkeypatch.setattr(ws,'_cleanup_worker_tmux',lambda *args:None)
    with kbc.connect_closing() as conn:
        root=tmp_path/'kanban/workspaces'/mode;root.mkdir(parents=True)
        # The marker may be lost; retained source evidence still identifies research.
        (root/'sources.md').write_text('https://example.invalid/source')
        (root/'raw.txt').write_text('original source')
        tid=kb.create_task(conn,title='research',workspace_kind='scratch',workspace_path=str(root))
        if mode=='parent':
            child=kb.create_task(conn,title='dependent',parents=[tid])
            assert kb.complete_task(conn,tid,summary='research evidence',fire_lifecycle_hook=False)
            assert root.exists()
            assert kb.complete_task(conn,child,result='read evidence',fire_lifecycle_hook=False)
        else:
            with kb.write_txn(conn):conn.execute("UPDATE tasks SET status='archived' WHERE id=?",(tid,))
            kanban_ops._cmd_gc(SimpleNamespace(event_retention_days=30,log_retention_days=30))
        assert root.exists(), mode
        entries=[dict(path='raw.txt',kind='raw_source',sha256=digest(root/'raw.txt'),url='https://example.invalid/source',publisher='Fixture',published_at='2026-09-01',retrieved_at='2026-09-13'),
                 dict(path='sources.md',kind='evidence',sha256=digest(root/'sources.md'))]
        (root/'evidence-manifest.json').write_text(json.dumps({'entries':entries}))
        receipt=archive_research(root,tmp_path/f'{mode}.zip')
        if mode=='gc':kanban_ops._cmd_gc(SimpleNamespace(event_retention_days=30,log_retention_days=30))
        else:ws._try_cleanup_parent_workspaces(conn,child)
        assert not root.exists()
        assert verify_archive(receipt['archive'],receipt['sha256'])['entries']==entries

def test_research_task_identity_survives_missing_marker(tmp_path,monkeypatch):
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    monkeypatch.setattr(ws,'_cleanup_worker_tmux',lambda *args:None)
    root=tmp_path/'kanban/workspaces/research';root.mkdir(parents=True)
    (root/'original.txt').write_text('irreplaceable research evidence')
    with kbc.connect_closing() as conn:
        tid=kb.create_task(conn,title='research',skills=['argus-research'],workspace_kind='scratch',workspace_path=str(root))
        ws._cleanup_workspace(conn,tid)
        assert root.exists()
