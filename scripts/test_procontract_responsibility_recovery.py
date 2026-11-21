"""Fault/lifecycle controls for owner re-entry; no model inference."""
import json
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_procontract_research_runtime as fixtures
from procontract_responsibility_recovery import recover_unselected
from procontract_store import contract


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.RuntimeTest();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.rt=self.f.runtime
        self.policy={'work':{'capability':'research','prompt':'Inspect the actual return and choose useful work or wait.'}}

    def closed_owner(self,completed=True,shutdown=True):
        self.f.finish(shutdown=shutdown)
        directory=self.rt.root/'native-actions'/self.f.operation;directory.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(directory/'requests.sqlite') as db:
            db.execute('create table requests(terminal text)')
            if completed:db.execute("insert into requests values('response.completed')")
        self.rt.tick()

    def test_closed_unselected_return_is_once_and_preserves_kernel_and_grants(self):
        self.f.start();self.closed_owner()
        before=contract(self.rt.host.store,'research');inc=self.rt.host.incumbent()
        original=self.rt.host.inspect(self.f.operation);generation=self.rt.state()['generation']
        result=recover_unselected(self.rt,self.policy)
        self.assertEqual(result['state'],'owner_return_queued')
        count=len(self.rt.choices())
        self.assertEqual(recover_unselected(self.rt,self.policy)['state'],'has_work_or_disposition')
        self.assertEqual(len(self.rt.choices()),count)
        choice=next(c for c in self.rt.choices() if c['id']==result['choice'])
        self.assertEqual(choice['body']['after_operation'],self.f.operation)
        self.assertEqual(choice['body']['origin']['return_of']['outcome'],original['outcome']['object'])
        self.assertEqual(self.rt.returned_context(choice['body'])[0]['outcome'],original['outcome']['object'])
        self.assertEqual(self.rt.host.inspect(self.f.operation),original)
        self.assertEqual(contract(self.rt.host.store,'research'),before)
        self.assertEqual(self.rt.host.incumbent(),inc)
        self.assertEqual(self.rt.state()['generation'],generation)
        self.assertEqual(choice['body']['work']['version'],inc['version'])

    def test_explicit_inert_wait_is_not_overridden(self):
        view=self.f.start();self.f.select(view,wait={'condition':{'event':False}});self.closed_owner()
        self.assertEqual(recover_unselected(self.rt,self.policy)['state'],'has_work_or_disposition')

    def test_cancelled_responsibility_is_not_resumed(self):
        self.f.start();self.closed_owner();self.rt.control('cancel','human-stop')
        count=len(self.rt.choices())
        self.assertEqual(recover_unselected(self.rt,self.policy)['state'],'inactive')
        self.assertEqual(len(self.rt.choices()),count)

    def test_live_work_and_unknown_effects_are_not_replayed(self):
        self.f.start()
        self.assertEqual(recover_unselected(self.rt,self.policy)['state'],'has_work_or_disposition')
        self.closed_owner(shutdown=False)
        self.assertEqual(recover_unselected(self.rt,self.policy)['state'],'has_work_or_disposition')

    def test_zero_response_startup_failure_waits_for_availability(self):
        self.f.start();self.closed_owner(completed=False)
        count=len(self.rt.choices())
        self.assertEqual(recover_unselected(self.rt,self.policy)['state'],'owner_channel_unobserved')
        self.assertEqual(len(self.rt.choices()),count)

    def test_old_generation_does_not_authorize_a_recovery(self):
        self.f.start();self.closed_owner()
        with self.rt.host.tx() as db:
            state=json.loads(db.execute('select body from rsi_state').fetchone()[0]);state['generation']+=1
            db.execute('update rsi_state set body=?',(json.dumps(state),))
        self.assertEqual(recover_unselected(self.rt,self.policy)['state'],'no_current_responsible_return')

    def test_leaf_cannot_receive_the_remaining_responsibility(self):
        self.f.start();self.closed_owner()
        with self.assertRaisesRegex(ValueError,'responsibility owner'):
            recover_unselected(self.rt,{'work':{'capability':'edit','prompt':'No selection authority'}})

    def test_concurrent_controllers_queue_one_return(self):
        self.f.start();self.closed_owner()
        barrier=threading.Barrier(2)
        def recover():
            barrier.wait()
            return recover_unselected(self.rt,self.policy)['state']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:recover(),range(2)))
        self.assertCountEqual(results,['owner_return_queued','has_work_or_disposition'])
        self.assertEqual(sum(c['id'].startswith('recover.') for c in self.rt.choices()),1)

    def test_reconciliation_window_never_replays_older_owner(self):
        view=self.f.start()
        selected=self.f.select(view,work=self.policy['work'])
        self.closed_owner()
        next_choice=next(c for c in self.rt.choices() if c['id']==selected['choice'])
        self.f.finish(next_choice['operation'])
        # Operation has closed but tick has not marked its choice finished yet.
        self.assertEqual(self.rt.status()['state'],'needs_selection')
        result=recover_unselected(self.rt,self.policy)
        self.assertEqual(result['state'],'responsible_return_pending')
        self.assertEqual(result['choice'],next_choice['id'])

    def test_original_authority_exhaustion_does_not_queue_new_work(self):
        self.f.start();self.closed_owner();count=len(self.rt.choices())
        with patch.object(self.rt.host,'allowed',side_effect=ValueError('original host allocation exhausted')):
            with self.assertRaisesRegex(ValueError,'allocation exhausted'):
                recover_unselected(self.rt,self.policy)
        self.assertEqual(len(self.rt.choices()),count)

    def test_recovery_cannot_choose_another_version(self):
        self.f.start();self.closed_owner()
        with self.assertRaisesRegex(ValueError,'current selected research version'):
            recover_unselected(self.rt,{'work':{**self.policy['work'],'version':'other'}})

    def test_leaf_closed_before_automatic_return_does_not_replay_caller(self):
        view=self.f.start()
        selected=self.f.select(view,call={'work':{'capability':'measure'},'return_to':self.policy['work']})
        self.closed_owner()
        leaf=next(c for c in self.rt.choices() if c['id']==selected['choice'])
        self.f.finish(leaf['operation'])
        # This is the interval before tick queues the already-selected return.
        with self.rt.host.tx() as db:
            db.execute("UPDATE rsi_choices SET state='finished' WHERE id=?",(leaf['id'],))
        self.assertEqual(self.rt.status()['state'],'needs_selection')
        result=recover_unselected(self.rt,self.policy)
        self.assertEqual(result['state'],'owner_already_selected')
        self.rt.tick()
        self.assertEqual(sum(c['id'].startswith('return.') for c in self.rt.choices()),1)
        self.assertFalse(any(c['id'].startswith('recover.') for c in self.rt.choices()))

    def test_disposition_appearing_during_inspection_wins(self):
        self.f.start();self.closed_owner()
        original=self.rt._work
        def concurrent(value,**kwargs):
            result=original(value,**kwargs)
            self.rt._queue('concurrent-disposition',{'role':'continuation','work':result})
            return result
        with patch.object(self.rt,'_work',side_effect=concurrent):
            self.assertEqual(recover_unselected(self.rt,self.policy)['state'],'responsibility_changed')
        self.assertFalse(any(c['id'].startswith('recover.') for c in self.rt.choices()))

    def test_live_producer_process_cannot_race_a_return(self):
        self.f.start();self.closed_owner()
        fixtures.ops.atomic(self.rt.host.operation_dir(self.f.operation)/'child.json',
                            fixtures.ops.process_identity(os.getpid()))
        self.assertEqual(recover_unselected(self.rt,self.policy)['state'],'responsible_process_pending')

    def test_pinned_companion_attaches_without_new_host_or_model(self):
        self.f.start();self.closed_owner()
        script=Path(__file__).with_name('procontract_responsibility_recovery.py').resolve()
        source=script.parent.parent
        policy=self.f.base/'policy.json';policy.write_text(json.dumps(self.policy))
        pins={'runtime':str(self.rt.root),'host_source':str(source),
              'store_binary':str(Path(fixtures.ops.kernel.store_bin()).resolve()),
              'policy':str(policy),'records':str(self.f.base/'records')}
        files=[script,policy,Path(pins['store_binary']),self.rt.root/'runtime.json',
               self.rt.root/'host/host.json',*(source/'scripts').glob('*.py')]
        pins['sources']={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        manifest=self.f.base/'pins.json';manifest.write_text(json.dumps(pins))
        command=[sys.executable,'-B',str(script),'--pins',str(manifest),
                 '--pins-sha256',hashlib.sha256(manifest.read_bytes()).hexdigest(),'--once']
        result=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(sum(c['id'].startswith('recover.') for c in self.rt.choices()),1)
        before=len(self.rt.choices())
        result=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(len(self.rt.choices()),before)
        policy.write_text('changed')
        result=subprocess.run(command,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('recovery source changed',result.stderr)
        self.assertEqual(len(self.rt.choices()),before)

if __name__=='__main__':unittest.main()
