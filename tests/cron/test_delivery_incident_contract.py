from cron import executions, incidents


def test_generation_success_does_not_hide_delivery_failure(tmp_path, monkeypatch):
    from cron.scheduler_outcomes import _classify_delivery_outcome
    from cron import scheduler, jobs
    from pathlib import Path
    monkeypatch.setattr(executions, 'EXECUTIONS_FILE', tmp_path / 'executions.db')
    monkeypatch.setattr(scheduler, 'mark_job_run', lambda *a, **k: True)
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    monkeypatch.setattr(jobs,'OUTPUT_DIR',tmp_path/'cron/output')
    monkeypatch.setattr(jobs,'CRON_DIR',tmp_path/'cron')
    monkeypatch.setattr(scheduler,'_deliver_result',lambda job,*a,**k: None if job['deliver']=='origin' else 'transport refused')
    prior_ids=[]
    for unresolved in [False,True,False]:
        run = executions.create_execution('synthetic-decisions', source='test')
        d=scheduler._RunDelivery(job={'id':run['job_id'],'deliver':'origin' if unresolved else 'discord:fixture'},success=True,error=None)
        scheduler._save_compose_deliver(d,scheduler._FireOwnership(d.job,None),'original immutable approval list','original immutable approval list',adapters=None,loop=None,verbose=False,execution_token=None)
        output=Path(d.output_file)
        assert output.read_text()=='original immutable approval list'
        scheduler._finish_completed_run(d,None,run['id'])
        rows=incidents.list_incidents('detected')
        assert len(rows)==1
        incident=rows[0]
        assert incident['failure_type']=='delivery' and run['id'] in incident['error']
        assert incident['output_file']==str(output)
        assert incident['id'] not in prior_ids
        assert executions.get_execution(run['id'])['status']=='completed'
        expected=_classify_delivery_outcome(delivery_error=d.delivery_error,should_deliver=True,unresolved_origin=unresolved,normalized_deliver=d.job['deliver'],incident_acked=False,success=True)
        assert executions.get_execution(run['id'])['delivery_outcome']==expected
        assert executions.finish_execution(run['id'], success=True, delivery_outcome='delivered') is None
        assert incidents.list_incidents('detected')==rows
        assert incidents.ack_incident(incident['id'])
        prior_ids.append(incident['id'])


def test_receipt_absence_stays_unverified_in_execution_and_incident(tmp_path, monkeypatch):
    from cron.scheduler_delivery import _record_delivery_verification
    from cron.scheduler_outcomes import _classify_delivery_outcome
    from cron import jobs, scheduler
    monkeypatch.setattr(executions, 'EXECUTIONS_FILE', tmp_path / 'executions.db')
    monkeypatch.setattr(jobs, 'update_job', lambda *a: None)
    job = {'id': 'synthetic-unverified'}
    _record_delivery_verification(job, ['discord:fixture'])
    outcome = _classify_delivery_outcome(delivery_error=None, should_deliver=True,
        unresolved_origin=False, normalized_deliver='discord:fixture', incident_acked=False,
        success=True, delivery_unverified=job['last_delivery_unverified'])
    assert outcome == 'unverified'
    run = executions.create_execution(job['id'], source='test')
    executions.finish_execution(run['id'], success=True, delivery_outcome=outcome)
    assert executions.list_executions()[0]['delivery_outcome'] == 'unverified'
    assert incidents.list_incidents('detected')[0]['failure_type'] == 'delivery'
    for delivery_error,expected in [(None,'unverified'),('transport failed during shutdown','failed')]:
        run=executions.create_execution(job['id'],source='test')
        d=scheduler._RunDelivery(job=dict(job,deliver='discord:fixture'),success=False,error='shutdown',should_deliver=True,delivery_error=delivery_error)
        scheduler._finish_interrupted_run(d,run['id'])
        record=executions.get_execution(run['id'])
        assert record['delivery_outcome']==expected and record['status']=='failed'
        assert any(run['id'] in row['error'] for row in incidents.list_incidents('detected'))
