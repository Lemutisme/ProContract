"""Counterexamples from missing tool receipts and stale corrected knowledge."""
import json
import dataclasses
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import test_procontract_research_runtime as fixtures
from procontract_operations_rpc import Broker
from procontract_research_runtime import COMMON, RuntimeController
from procontract_step_evidence import retain_steps
from procontract_review_profile import apply_review_profile, qualify_review_image


class FeedbackViewsTest(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.RuntimeTest();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.rt=self.f.runtime;self.f.start()

    def trace(self):
        run=self.rt.root/'native-actions'/self.f.operation;run.mkdir(parents=True)
        events=[]
        for thread,phase,item in [
            ('other','item/completed',{'id':'foreign','type':'commandExecution','command':'other work'}),
            ('actor','item/completed',{'id':'reason','type':'reasoning','text':'not a public tool receipt'}),
            ('actor','item/started',{'id':'call-1','type':'commandExecution','command':'check artifact'}),
            ('actor','item/completed',{'id':'call-1','type':'commandExecution','command':'check artifact',
                                       'exitCode':7,'aggregatedOutput':'真实失败\n'+'x'*9000}),
        ]:
            events.append(json.dumps({'method':phase,'params':{'threadId':thread,'turnId':'turn-1','item':item}}))
        (run/'rpc.jsonl').write_text('\n'.join(events)+'\nnot-json\n')
        descriptor=retain_steps(self.rt.host,run,'actor',operation=self.f.operation)
        self.f.native_result(self.f.operation,steps=descriptor)
        return descriptor,next(c['id'] for c in self.rt.choices() if c['operation']==self.f.operation)

    def test_returned_trace_preserves_steps_failures_and_bounded_output(self):
        descriptor,choice=self.trace()
        broker=Broker(self.f.base/'feedback.sock',self.rt.host,'research',operations=COMMON,
                      controller=RuntimeController(self.rt,self.f.operation))
        self.addCleanup(broker.server_close)
        page=broker.invoke('work_trace',{'identity':choice,'limit':1})
        self.assertEqual(page['producer_binding'],'confirmed')
        self.assertTrue(page['more']);self.assertEqual(page['entries'][0]['event'],'item/started')
        completed=broker.invoke('work_trace',{'identity':choice,'after':page['cursor']})
        self.assertEqual(completed['entries'][0]['step'],'call-1')
        self.assertEqual(descriptor['records'],2);self.assertEqual(descriptor['malformed_lines'],1)
        view=broker.invoke('work_trace',{'identity':choice,'record':1,'field':['item','exitCode']})
        self.assertEqual(view['text'],'7')
        view=broker.invoke('work_trace',{'identity':choice,'record':1,'field':['item','aggregatedOutput'],'length':32})
        self.assertFalse(view['eof']);self.assertLess(len(json.dumps(view)),1800)
        self.assertNotIn('base64',view)
        self.assertTrue(self.rt.work_output(choice)['steps']['available'])
        self.assertIn('work_trace',broker.invoke('describe',{})['operations'])

    def test_unretained_trace_is_unknown_and_no_arbitrary_path_read(self):
        self.f.native_result(self.f.operation)
        choice=next(c['id'] for c in self.rt.choices() if c['operation']==self.f.operation)
        self.assertFalse(self.rt.work_trace(choice)['available'])
        with self.assertRaisesRegex(ValueError,'unknown selected work'):self.rt.work_trace('/etc/passwd')

    def test_step_hash_change_is_rejected(self):
        descriptor,choice=self.trace()
        path=self.rt.host.root/'sources'/descriptor['sha256'];path.chmod(0o600)
        path.write_bytes(path.read_bytes().replace(b'check artifact',b'changed action'))
        with self.assertRaisesRegex(ValueError,'bytes changed'):self.rt.work_trace(choice)

    def test_trace_from_another_operation_is_not_attributed_to_the_consumer(self):
        descriptor,original_choice=self.trace()
        selected=self.rt.control('input','second-owner','Continue with the same responsibility.')
        self.rt.tick()
        later=next(c for c in self.rt.choices() if c['id']==selected['selected_choice'])
        self.f.native_result(later['operation'],steps=descriptor)
        with self.assertRaisesRegex(ValueError,'another operation, assignment or version'):
            self.rt.work_trace(later['id'])
        self.assertEqual(self.rt.work_trace(original_choice)['producer_binding'],'confirmed')

    def test_correction_branches_keep_scopes_and_late_correction_reaches_dispatch(self):
        host=self.rt.host
        old=host.knowledge('receipt visibility',[],'access unverified','from summary only',applicability='old consumer view')
        work=self.rt._work({'capability':'research','prompt':'Inspect the relevant observation.','knowledge':[old]})
        new=host.knowledge('receipt visibility',[],'access observed','prefix only',supersedes=old,applicability='actual 128-byte read')
        narrow=host.knowledge('receipt visibility',[],'another source remains unknown','different subject',supersedes=old,applicability='other source')
        later=host.knowledge('receipt visibility',[],'request delivery also observed','same prefix',supersedes=new,applicability='actual 128-byte read')
        context=self.rt.knowledge_context(work)[0]
        self.assertEqual({v['id'] for v in context['entries']},{old,new,narrow,later})
        original=next(v for v in context['entries'] if v['id']==old)
        self.assertEqual(original['correction_count'],2)
        self.assertCountEqual([v['id'] for v in context['entries'] if v['supersedes']==old],[new,narrow])
        self.assertEqual(host.get(old)['interpretation'],'access unverified')
        first=host.knowledge_view(old,limit=1)
        host.knowledge('late',[],'later correction','scope',supersedes=later)
        rest=host.knowledge_view(old,after=first['cursor'],snapshot=first['snapshot'])
        self.assertEqual(len(rest['entries']),3)

    def test_correction_pages_bound_large_scopes_and_preserve_every_branch(self):
        host=self.rt.host
        root=host.knowledge('subject'*1000,[],'old'*10000,'unknown',applicability={'long':'范围'*10000})
        expected={root}
        for i in range(20):
            expected.add(host.knowledge(str(i),[],'修正'*1000,'unknown',supersedes=root,applicability='条件'*10000))
        page=host.knowledge_view(root);seen=set()
        while True:
            self.assertLessEqual(len(json.dumps(page,indent=2).encode()),4096)
            seen.update(x['id'] for x in page['entries'])
            if not page['more']:break
            page=host.knowledge_view(root,after=page['cursor'],snapshot=page['snapshot'])
        self.assertEqual(seen,expected)
        data=host.object_view(root,field=['applicability'],length=32)
        self.assertFalse(data['eof']);self.assertEqual(data['end'],32)

    def test_native_exception_after_a_tool_retains_steps_after_host_expiry(self):
        from procontract_native_operation import native
        host=self.rt.host;original=host.inspect(self.f.operation)['request']
        run=self.rt.root/'native-actions'/self.f.operation
        class FaultingServer:
            def __init__(inner,argv,env,log,*args):inner.log=log
            def request(inner,method,arguments,handler):
                if method=='initialize':return {}
                if method=='thread/start':return {'thread':{'id':'actor'}}
                if method=='turn/start':
                    result=subprocess.run([sys.executable,'-c','print("retained before failure"); raise SystemExit(7)'],capture_output=True,text=True)
                    event={'method':'item/completed','params':{'threadId':'actor','turnId':'turn',
                        'item':{'id':'actual-tool','type':'commandExecution','command':'public local control',
                                'exitCode':result.returncode,'aggregatedOutput':result.stdout}}}
                    inner.log.write_text(json.dumps(event)+'\n')
                    host.authority=dataclasses.replace(host.authority,deadline=time.time()-1)
                    raise RuntimeError('injected caller failure after actual tool exit')
            def notify(inner,*args):pass
            def close(inner):return {'status':'exited','returncode':0}
        with patch('procontract_native_operation.verify_actor_view',return_value={'fixture':True}), \
             patch('procontract_native_operation.confirm_environment_shutdown',return_value={'confirmed':True}), \
             patch('procontract_native_operation.observe_build',return_value=host.get(original['request']['version'])['definition']['build']['snapshot']), \
             patch('procontract_native_operation.AppServer',FaultingServer):
            with self.assertRaisesRegex(RuntimeError,'caller failure'):
                native(run,self.f.package,'fixture',original['request']['version'],host,'fault control',
                       inputs={},seconds=10,operation_id=self.f.operation)
        retained=json.loads((run/'step-evidence.json').read_text())
        self.assertIsNone(retained['steps_error']);self.assertEqual(retained['steps']['records'],1)
        fixtures.ops.atomic(host.operation_dir(self.f.operation)/'receipt.json',{
            'id':self.f.operation,'assignment':fixtures.ops.sha(original),'execution':'failed',
            'shutdown_confirmed':True,'measurement':{'validity':'unavailable',**retained}})
        host.reconcile(self.f.operation)
        choice=next(c['id'] for c in self.rt.choices() if c['operation']==self.f.operation)
        self.assertEqual(self.rt.work_trace(choice,record=0,field=['item','exitCode'])['text'],'7')
        self.assertEqual(host.inspect(self.f.operation)['request'],original)
        with self.assertRaisesRegex(ValueError,'allocation exhausted'):
            host.capture_source(run/'rpc.jsonl',expected_sha256='unused',label='new work')
        with sqlite3.connect(run/'requests.sqlite') as db:
            self.assertEqual(db.execute('select count(*) from requests').fetchone()[0],0)

    def test_knowledge_handles_and_scope_do_not_accept_source_or_path(self):
        with self.assertRaises(ValueError):self.rt._work({'capability':'research','prompt':'x','knowledge':['/tmp/claim']})
        with self.assertRaises(ValueError):self.rt._work({'capability':'research','prompt':'x','knowledge':'not-list'})

    def test_external_review_profile_preserves_task_grants_and_explicit_absence(self):
        settings={'evaluation':{'check':{'image':'unused-no-checks'}},
                  'succession':{'total_secs':28800},'resource_snapshot':'original-budget'}
        self.assertNotIn('review_execution',apply_review_profile(settings,None,task_image='sha256:task'))
        new=apply_review_profile(settings,{'max_calls':2,'timeout_secs':10,'max_output_bytes':8192},task_image='sha256:task')
        self.assertEqual(new['review_execution']['image'],'sha256:task')
        self.assertEqual({k:v for k,v in new.items() if k!='review_execution'},settings)
        explicit=apply_review_profile(settings,{'max_calls':2,'timeout_secs':10,'max_output_bytes':8192,'image':'sha256:analysis'},task_image='sha256:task')
        self.assertEqual(explicit['review_execution']['image'],'sha256:analysis')
        with self.assertRaises(ValueError):apply_review_profile(settings,{'max_calls':2,'timeout_secs':10,'max_output_bytes':8192})

    def test_preflight_distinguishes_missing_container_from_unavailable_daemon(self):
        profile={'max_calls':2,'timeout_secs':10,'max_output_bytes':8192}
        for diagnostic,closed in [('error: no such object: owned',True),('Error: No such container: owned',True),('daemon unavailable',False)]:
            with self.subTest(diagnostic=diagnostic), \
                 patch('procontract_review_profile.subprocess.check_output',return_value='sha256:qualified\n'), \
                 patch('procontract_review_profile.subprocess.run',side_effect=[
                     subprocess.CompletedProcess([],0,'{"python":[3,12,3]}',''),
                     subprocess.CompletedProcess([],0,'',''),
                     subprocess.CompletedProcess([],1,'[]',diagnostic)]):
                result=qualify_review_image(profile,task_image='task')
                self.assertEqual(result['effects_closed'],closed)
                self.assertEqual(result['passed'],closed)
                self.assertEqual(result['grant']['image'],'sha256:qualified')

    def test_measurement_subject_tracks_code_conditions_and_missing_dependencies(self):
        cap=self.rt.config['capabilities']['measure']
        cap.update(project='target',subject_paths=['tool.py','optional-data.json'])
        initial=self.rt.project_snapshot('target','initial')
        subject=self.rt.measurement_subject('measure',initial)
        (self.f.project/'new-decision.md').write_text('a new research decision, same code')
        after=self.rt.project_snapshot('target','after decision')
        self.assertNotEqual(initial,after)
        self.assertEqual(subject,self.rt.measurement_subject('measure',after))
        (self.f.project/'tool.py').write_text('print("B")\n')
        code=self.rt.project_snapshot('target','code changed')
        changed=self.rt.measurement_subject('measure',code)
        self.assertNotEqual(subject,changed)
        cap['measurement']['environment']='different declared environment'
        self.assertNotEqual(changed,self.rt.measurement_subject('measure',code))
        before_data=self.rt.measurement_subject('measure',code)
        (self.f.project/'optional-data.json').write_text('{}')
        with_data=self.rt.project_snapshot('target','data appeared')
        self.assertNotEqual(before_data,self.rt.measurement_subject('measure',with_data))


if __name__=='__main__':unittest.main()
