import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from native_history import INDEX, capture_history
from capture_history import compose


def event(i, status="completed", command="printf test", kind="event_msg"):
    return (json.dumps(dict(type=kind, payload=dict(type="item_completed", thread_id="thread", turn_id="turn",
        completed_at_ms=i, item=dict(type="CommandExecution", id=f"command-{i}", command=command,
        status=status, exit_code=0 if status=="completed" else None))))+'\n').encode()


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.sessions=self.root/'sessions';self.sessions.mkdir()
        self.log=self.sessions/'rollout-one.jsonl'
        self.limits=dict(max_bytes_per_log=1<<20,max_file_bytes=1<<16,max_total_bytes=1<<20)

    def capture(self, name="projection", **limits):
        p=self.root/name;p.mkdir()
        result=capture_history(self.sessions,p,**{**self.limits,**limits})
        return p,result,json.loads((p/INDEX).read_text())

    def test_all_records_and_original_bytes_survive_tail(self):
        raw=b''.join(event(i,status="failed" if i==0 else "pending" if i==1 else "completed") for i in range(35))
        self.log.write_bytes(raw+event(99,kind="response_item"))
        p,result,index=self.capture()
        self.assertEqual(len(index['events']),35)
        self.assertEqual([r['status'] for r in index['events'][:2]],['failed','pending'])
        self.assertEqual(index['events'][0]['item_id'],'command-0')
        for row in index['events']:
            chunk=(p/row['chunk']).read_bytes();data=chunk[row['offset']:row['offset']+row['bytes']]
            self.assertEqual(hashlib.sha256(data).hexdigest(),row['sha256'])
            self.assertEqual(data,raw[row['origin']['offset']:row['origin']['offset']+row['origin']['bytes']])
        self.assertEqual(result['observations'][0]['kind'],'capture_metadata')
        self.assertTrue(all(x['kind']=='execution_observation' for x in result['observations'][1:]))

    def test_noncommand_telemetry_does_not_change_projection(self):
        self.log.write_bytes(event(0));a,_,_=self.capture('a')
        with self.log.open('ab') as f:f.write(b'{"type":"event_msg","payload":{"type":"reasoning","text":"more"}}\n')
        b,_,_=self.capture('b')
        self.assertEqual((a/INDEX).read_bytes(),(b/INDEX).read_bytes())
        with self.log.open('ab') as f:f.write(event(1))
        c,_,_=self.capture('c')
        self.assertNotEqual((b/INDEX).read_bytes(),(c/INDEX).read_bytes())

    def test_window_and_invalid_lines_keep_explicit_omissions(self):
        self.log.write_bytes(b''.join(event(i) for i in range(35))+b'invalid\n'+b'{"unfinished":')
        _,result,index=self.capture(max_bytes_per_log=2000)
        w=index['windows'][0]
        self.assertTrue(w['prefix_omitted']);self.assertGreater(w['start'],0)
        self.assertGreaterEqual(w['partial_or_invalid_lines'],1)
        self.assertEqual(result['diagnostics']['scans'][0]['after_event_cutoff_partial_or_invalid_lines'],2)
        self.assertLess(len(index['events']),35)
        self.assertIn('no claim',index['cutoff'])

    def test_large_records_are_omitted_without_truncating_their_identity(self):
        large=event(1,command='x'*20000)
        self.log.write_bytes(event(0)+large+event(2))
        _,_,index=self.capture(max_file_bytes=4096)
        self.assertEqual(len(index['events']),2)
        omitted=index['windows'][0]['omitted_events']
        self.assertEqual(omitted[0]['reason'],'record_exceeds_file_bound')
        self.assertEqual(omitted[0]['origin']['sha256'],hashlib.sha256(large).hexdigest())

    def test_source_links_and_namespace_collisions_are_explicit(self):
        outside=self.root/'outside.jsonl';outside.write_bytes(event(0))
        self.log.symlink_to(outside)
        p,_,index=self.capture()
        self.assertEqual(len(index['events']),0)
        self.assertEqual(index['source_errors'][0]['reason'],'source link or escape')
        before=(p/INDEX).read_bytes()
        with self.assertRaises(FileExistsError):capture_history(self.sessions,p,**self.limits)
        self.assertEqual((p/INDEX).read_bytes(),before)

    def test_composition_preserves_base_and_actual_cli_declares_hashes(self):
        self.log.write_bytes(event(0))
        p=self.root/'projection';p.mkdir();(p/'artifact.txt').write_text('fixed artifact')
        provider=dict(schema='procontract_capture_provider/v1',projection_root=str(p),container_id='owned',
            scope='base scope',observation_sha256='base-digest',projection=dict(task_files=dict(prefix='files',task_root='/'),observations=[]))
        cmd=[sys.executable,str(HERE/'capture_history.py'),'--sessions',str(self.sessions),'--diagnostics',str(self.root/'diagnostics.json')]
        for key,value in self.limits.items():cmd += ['--'+key.replace('_','-'),str(value)]
        cmd += ['--',sys.executable,'-c','print('+repr(json.dumps(provider))+')']
        run=subprocess.run(cmd,capture_output=True,text=True,check=True)
        actual=json.loads(run.stdout)
        self.assertEqual((p/'artifact.txt').read_text(),'fixed artifact')
        self.assertEqual(actual['container_id'],'owned')
        self.assertEqual(actual['observation_sha256'],'base-digest')
        for row in actual['projection']['observations']:
            self.assertEqual(hashlib.sha256((p/row['path']).read_bytes()).hexdigest(),row['sha256'])
        self.assertTrue((self.root/'diagnostics.json').exists())

    def test_index_is_subject_to_the_same_declared_bounds(self):
        self.log.write_bytes(event(0))
        with self.assertRaisesRegex(ValueError,'need at least'):
            self.capture(max_file_bytes=32,max_total_bytes=100)

    def test_partial_telemetry_after_event_cutoff_is_diagnostic_only(self):
        self.log.write_bytes(event(0))
        before,_,_=self.capture('before')
        with self.log.open('ab') as f:f.write(b'{"type":"response_item","payload":')
        after,result,index=self.capture('after')
        self.assertEqual((before/INDEX).read_bytes(),(after/INDEX).read_bytes())
        self.assertEqual(result['diagnostics']['scans'][0]['after_event_cutoff_partial_or_invalid_lines'],1)
        # An invalid line between observed command events affects that interval.
        with self.log.open('ab') as f:f.write(b'\n'+event(1))
        _,_,later=self.capture('later')
        self.assertEqual(later['windows'][0]['partial_or_invalid_lines'],1)

    def test_index_storage_pressure_preserves_base_and_reports_unavailable(self):
        raw=event(0,command='x'*2048);self.log.write_bytes(raw)
        p=self.root/'projection';p.mkdir();(p/'artifact.txt').write_text('keep the base')
        provider=dict(schema='procontract_capture_provider/v1',projection_root=str(p),container_id='owned',
            projection=dict(task_files=dict(prefix='files',task_root='/'),observations=[]))
        result,_=compose(provider,self.sessions,max_bytes_per_log=1<<20,max_file_bytes=4096,max_total_bytes=len(raw)+16)
        index=json.loads((p/INDEX).read_text())
        self.assertEqual(index['state'],'unavailable')
        self.assertEqual(index['observed_events'],1)
        self.assertEqual(index['omitted_events'],1)
        self.assertEqual(index['events'],[])
        self.assertEqual(list((p/'EXECUTION_HISTORY').iterdir()),[p/INDEX])
        self.assertEqual((p/'artifact.txt').read_text(),'keep the base')
        self.assertEqual(len(result['projection']['observations']),1)


if __name__=='__main__':unittest.main()
