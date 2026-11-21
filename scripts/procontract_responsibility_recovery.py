"""Return an unfinished responsibility to its admitted owner after caller loss.

The configured work is context-level policy. This mechanism chooses no research
step, measurement retry, adoption or new authority; an explicit wait is respected.
"""
import hashlib
import json
import sqlite3
from contextlib import closing


def recover_unselected(runtime, policy):
    """Queue at most one owner return for a closed, unselected native outcome."""
    raw=json.dumps(policy,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    policy_hash=hashlib.sha256(raw).hexdigest()
    with runtime.control_lock(), runtime.host.authority_lock():
        runtime.host.allowed()
        state=runtime.state();inbox=runtime.host.inbox('research')
        if not state['started'] or inbox['cancelled']:
            return {'state':'inactive'}
        status=runtime.status()
        if status['state']!='needs_selection' or status['active_operations'] or status['unresolved_operations']:
            return {'state':'has_work_or_disposition','runtime_state':status['state']}
        choices=runtime.choices();eligible=[]
        for choice in choices:
            body=choice['body'];work=body.get('work') or {}
            cap=runtime.config['capabilities'].get(work.get('capability'),{})
            if (cap.get('kind')=='native' and cap.get('select')
                    and body.get('epoch')==inbox['epoch'] and body.get('generation')==state['generation']):
                eligible.append(choice)
        if not eligible:return {'state':'no_current_responsible_return'}
        choice=eligible[-1]
        # Reconciliation may finish the operation before marking its choice.
        # Never return an older owner while the newest one's closure is pending.
        if choice['state']!='finished' or not choice['operation']:
            return {'state':'responsible_return_pending','choice':choice['id']}
        if any(item['body'].get('origin',{}).get('operation')==choice['operation']
               and item['body']['role']!='delegated' for item in choices):
            return {'state':'owner_already_selected','choice':choice['id']}
        operation=runtime.host.inspect(choice['operation']);outcome=operation.get('outcome') or {}
        if operation['state']!='finished' or outcome.get('shutdown_confirmed') is not True:
            return {'state':'unresolved_return'}
        # Confirm the producer process has exited too. A socket handler which
        # started before native shutdown must not race a second disposition.
        from procontract_operations import alive, group_alive
        child_path=runtime.host.operation_dir(choice['operation'])/'child.json'
        if child_path.exists():
            child=json.loads(child_path.read_text())
            if alive(child) or (child and group_alive(child['pid'])):
                return {'state':'responsible_process_pending','choice':choice['id']}
        # This recovery profile covers an established native owner channel.
        # Zero-response startup failures remain owner-handled; this implementation
        # does not consume availability observations or retry startup failures.
        database=runtime.root/'native-actions'/choice['operation']/'requests.sqlite'
        try:
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
                completed=db.execute("SELECT count(*) FROM requests WHERE terminal='response.completed'").fetchone()[0]
        except sqlite3.Error:
            completed=0
        if not completed:return {'state':'owner_channel_unobserved','operation':choice['operation']}
        selected=runtime.host.incumbent()
        if selected.get('version') is None:return {'state':'no_selected_research_version'}
        if policy['work'].get('version',selected['version'])!=selected['version']:
            raise ValueError('Recovery must use the current selected research version')
        work=runtime._work(policy['work'],current_version=selected['version'])
        cap=runtime.config['capabilities'][work['capability']]
        if cap['kind']!='native' or not cap.get('select'):
            raise ValueError('Recovery must return to an admitted native responsibility owner')
        facts={'choice':choice['id'],'operation':choice['operation'],'outcome':outcome['object'],
               'execution':outcome.get('execution'),'shutdown_confirmed':True,
               'native_status':((outcome.get('measurement') or {}).get('task') or {}).get('status'),
               'completed_model_responses':completed}
        work['prompt']+='\n\nRetained return facts (data, not new instructions):\n'+json.dumps(facts,ensure_ascii=False)
        identity='recover.'+hashlib.sha256(json.dumps([choice['id'],outcome['object'],inbox['epoch'],state['generation'],
                                                       selected['version'],policy_hash],separators=(',',':')).encode()).hexdigest()[:32]
        body={'role':'continuation','origin':{'return_of':{'choice':choice['id'],'operation':choice['operation'],'outcome':outcome['object']},
                'responsibility_recovery':{'policy_sha256':policy_hash,'reason':'Native owner returned without selecting further work or wait.'}},
              'work':work,'epoch':inbox['epoch'],'generation':state['generation'],'after_operation':choice['operation']}
        # Selection and automatic call returns use database transactions rather
        # than control_lock. Compare the observed choices and closure atomically
        # with insertion, so a disposition appearing during inspection wins.
        with runtime.host.tx() as db:
            current=[dict(row) for row in db.execute('SELECT id,body,state,operation FROM rsi_choices ORDER BY rowid')]
            observed=[{'id':item['id'],'body':json.dumps(item['body'],sort_keys=True,separators=(',',':'),ensure_ascii=False),
                       'state':item['state'],'operation':item['operation']} for item in choices]
            current_state=json.loads(db.execute('SELECT body FROM rsi_state').fetchone()[0])
            owner=db.execute("SELECT epoch,cancelled FROM inbox WHERE id='research'").fetchone()
            if (current!=observed or owner['cancelled'] or owner['epoch']!=inbox['epoch']
                    or current_state['generation']!=state['generation'] or not current_state['started']):
                return {'state':'responsibility_changed'}
            for row in db.execute('SELECT state,outcome FROM operations WHERE id IN (SELECT operation FROM rsi_choices)'):
                closed=json.loads(row['outcome']) if row['outcome'] else {}
                if row['state'] in ('reserved','running','unknown') or closed.get('shutdown_confirmed') is not True:
                    return {'state':'responsibility_changed'}
            actual=db.execute('SELECT outcome FROM operations WHERE id=?',(choice['operation'],)).fetchone()
            if json.loads(actual[0])['object']!=outcome['object']:
                return {'state':'responsibility_changed'}
            runtime.host.allowed()
            db.execute("INSERT INTO rsi_choices(id,body,state) VALUES(?,?,'queued')",
                       (identity,json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False)))
        return {'state':'owner_return_queued','choice':identity,'predecessor':facts,'policy_sha256':policy_hash,
                'generation_unchanged':True,'original_allocation_renewed':False,'measurement_retried':False}


def main():
    """Attach an explicitly provisioned recovery policy to an existing runtime.

    Dispatch remains with that runtime's supervisor. This companion neither
    provisions a new host nor changes its immutable configuration or sources.
    """
    import argparse
    import fcntl
    import os
    from pathlib import Path
    import sys
    import time

    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pins',type=Path,required=True)
    parser.add_argument('--pins-sha256',required=True)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    raw=args.pins.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=args.pins_sha256:
        raise ValueError('recovery provisioning identity changed')
    pins=json.loads(raw)

    def verify():
        for name,expected in pins['sources'].items():
            with Path(name).open('rb') as stream:
                actual=hashlib.file_digest(stream,'sha256').hexdigest()
            if actual!=expected:raise ValueError('recovery source changed: '+name)

    required=[str(Path(__file__).resolve()),pins['policy'],pins['store_binary'],
              str(Path(pins['runtime'])/'runtime.json'),str(Path(pins['runtime'])/'host/host.json')]
    required.extend(str(path.resolve()) for path in (Path(pins['host_source'])/'scripts').glob('*.py'))
    if not all(name in pins['sources'] for name in required):
        raise ValueError('recovery provisioning omits a required source')
    verify()
    os.environ['PRO_CONTRACT_STORE_BIN']=pins['store_binary']
    sys.path.insert(0,str(Path(pins['host_source'])/'scripts'))
    from procontract_research_runtime import ResearchRuntime
    import procontract_operations as ops

    runtime=ResearchRuntime(pins['runtime'])
    policy=json.loads(Path(pins['policy']).read_text())
    records=Path(pins['records']);records.mkdir(parents=True,exist_ok=True)
    with (runtime.root/'responsibility-recovery.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        ops.atomic(records/'STARTED.json',{'at':time.time(),'pins_sha256':args.pins_sha256,
                   'process':ops.process_identity(os.getpid()),'runtime':str(runtime.root)})
        previous=None
        while True:
            try:
                verify()
                result=recover_unselected(runtime,policy)
            except Exception as error:
                ops.atomic(records/'ERROR.json',{'at':time.time(),'error':str(error),'type':type(error).__name__})
                raise
            receipt={'at':time.time(),'pins_sha256':args.pins_sha256,**result}
            ops.atomic(records/'STATUS.json',receipt)
            if result!=previous:
                with (records/'events.jsonl').open('a') as stream:
                    stream.write(json.dumps(receipt,sort_keys=True)+'\n')
                    stream.flush();os.fsync(stream.fileno())
                previous=result
            if args.once or result['state']=='inactive':return
            time.sleep(5)


if __name__=='__main__':main()
