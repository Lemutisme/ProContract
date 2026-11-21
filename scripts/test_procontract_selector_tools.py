"""Actual existing code-mode host; only frozen evidence is registered. Zero models."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

import procontract_operations as ops
from procontract_selector_tools import FrozenToolSession
from test_procontract_frozen_evidence import fixture


class SelectorToolsTest(unittest.TestCase):
    def setUp(self):
        self.binary = os.environ.get("SELECTOR_CODE_MODE_HOST")
        self.binary_hash = os.environ.get("SELECTOR_CODE_MODE_HOST_SHA256")
        if not self.binary or not self.binary_hash:
            self.skipTest("set pinned SELECTOR_CODE_MODE_HOST and SHA256")
        keep = os.environ.get("SELECTOR_TOOLS_ARTIFACTS")
        if keep:
            self.root=Path(keep)/self._testMethodName;self.root.mkdir(parents=True,exist_ok=False)
        else:
            temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
            self.root=Path(temporary.name)
        body, self.handle, _ = fixture()
        self.snapshot=self.root/'snapshot.json';self.snapshot.write_text(ops.encoded(body))
        self.pin=ops.sha(self.snapshot.read_bytes())

    def session(self, name='one', **kw):
        return FrozenToolSession(self.snapshot,self.pin,self.binary,self.binary_hash,
            execution=name,allocation='new-allocation-'+name,output_dir=self.root/name,
            deadline=time.time()+20,**kw)

    @staticmethod
    def result(value):
        assert 'Result' in value,value
        return value['Result']

    def test_only_registered_reader_executes_and_cannot_reach_process_or_network(self):
        with self.session() as session:
            result=self.result(session.execute('text(await tools.contract_read_evidence({op:"sources"}));','sources'))
            self.assertIsNone(result['error_text'])
            text=''.join(p['text'] for p in result['content_items'] if p['type']=='input_text')
            self.assertEqual(json.loads(text)['entries'][0]['handle'],self.handle)
            output=self.result(session.execute('text({process:typeof process,require:typeof require,fetch:typeof fetch,Deno:typeof Deno});','no-host-globals'))
            text=''.join(p['text'] for p in output['content_items'] if p['type']=='input_text')
            self.assertEqual(json.loads(text),dict.fromkeys(['process','require','fetch','Deno'],'undefined'))
            failed=self.result(session.execute('await tools.exec_command({cmd:"touch /tmp/not-authorized"});','foreign-tool'))
            self.assertIsNotNone(failed['error_text'])
            self.assertEqual(session.reads,1)
        self.assertIsNotNone(session.process.returncode)
        records=[json.loads(x) for x in (self.root/'one/tools.jsonl').read_text().splitlines()]
        self.assertTrue(records[-1]['local_closed'])
        self.assertTrue(all(x['execution']=='one' for x in records))
        self.assertEqual(ops.sha(self.snapshot.read_bytes()),self.pin)

    def test_private_handles_and_read_allowance_fail_without_another_capability(self):
        with self.session(max_reads=1) as session:
            result=self.result(session.execute('text(await tools.contract_read_evidence({op:"read",handle:"'+'f'*64+'"}));','private'))
            self.assertIsNotNone(result['error_text'])
            result=self.result(session.execute('text(await tools.contract_read_evidence({op:"index"}));','exhausted'))
            self.assertIsNotNone(result['error_text'])
            self.assertEqual(session.reads,1)

    def test_yielded_cell_is_owned_and_terminates_through_native_wait(self):
        with self.session('left') as left, self.session('right') as right:
            pending=left.execute('await new Promise(() => {});','pending',yield_time_ms=0)
            self.assertIn('Yielded',pending)
            cell=pending['Yielded']['cell_id']
            with self.assertRaisesRegex(ValueError,'belong'):right.wait(cell)
            closed=left.wait(cell,terminate=True)
            self.assertIn('Terminated',closed['LiveCell'])
        self.assertIsNotNone(left.process.returncode);self.assertIsNotNone(right.process.returncode)

    def test_wrong_pins_reject_before_start(self):
        for field in ('host','snapshot'):
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'differ'):
                FrozenToolSession(self.snapshot,'0'*64 if field=='snapshot' else self.pin,
                    self.binary,'0'*64 if field=='host' else self.binary_hash,
                    execution='new',allocation='new',output_dir=self.root/field)
            self.assertFalse((self.root/field).exists())

    def test_trace_allowance_closes_a_script_that_keeps_requesting_reads(self):
        with self.session(max_trace_bytes=8192) as session:
            with self.assertRaisesRegex(ValueError,'trace allocation'):
                session.execute('for(let i=0;i<100;i++) await tools.contract_read_evidence({op:"sources"});','many-reads')
        self.assertIsNotNone(session.process.returncode)
        self.assertLessEqual((self.root/'one/tools.jsonl').stat().st_size,8192)


if __name__=='__main__':unittest.main()
