"""Zero-model custody and actual-process restoration controls for evidence only."""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import procontract_frozen_evidence as frozen
import procontract_operations as ops


def fixture():
    subject = "3" * 64
    document = (ops.encoded({"subject_hash": subject, "detail": "可读证据\n" * 700}) + "\n").encode()
    handle = ops.sha(document)
    denied = "4" * 64
    body = {"schema": frozen.SCHEMA,
        "boundary": {"contract": "ordinary-task", "journal_head": "5"*64, "selector": "invocation-one"},
        "pages": {"index": [{"handle": handle, "subject_hash": subject, "review_checkpoints": [denied]}],
                  "frontier": [{"step": "public:case", "evidence": handle, "generation": 1, "subject_hash": subject}],
                  "sources": [{"source_id": "assessment", "handle": handle,
                      "source_subject_hash": subject, "selected_subject_hash": subject,
                      "same_subject": True, "scope": "Frozen for this invocation"}]},
        "blobs": {handle: base64.b64encode(document).decode()},
        "denied": {denied: "evidence handle is not public evidence of this responsibility"}}
    return body, handle, document


class FrozenEvidenceTest(unittest.TestCase):
    def setUp(self):
        keep = os.environ.get("FROZEN_EVIDENCE_TEST_ARTIFACTS")
        if keep:
            self.root = Path(keep) / self._testMethodName
            self.root.mkdir(parents=True, exist_ok=False)
        else:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            self.root = Path(temporary.name)
        self.body, self.handle, self.document = fixture()
        self.view = frozen.FrozenEvidence(self.body)

    def test_closed_parent_capture_then_two_new_processes_restore_same_bytes(self):
        runner = self.root / "runner.py"
        runner.write_text('''import base64,json,sys
from pathlib import Path
sys.path.insert(0, %r)
import procontract_frozen_evidence as f
import procontract_operations as ops
p=Path(sys.argv[1]);a=json.loads(p.read_text());q=a['request']['inputs']
if q['mode']=='parent':
    result={'effects_resolved':True,'selector_checkpoint':q['checkpoint']}
else:
    view=f.FrozenEvidence.load(q['snapshot'],q['sha256'])
    results=[]
    for query in q['queries']:
        try:results.append({'query':query,'result':view.read(query)})
        except f.QueryError as error:results.append({'query':query,'error':str(error)})
    result={'effects_resolved':True,'admissible':False,'capture':q['capture'],
            'restored_evidence_sha256':q['sha256'],'queries':results}
ops.atomic(p.parent/'result.json',result)
''' % str(Path(frozen.__file__).parent))
        host = ops.Operations.create(self.root/'host', ops.Authority(
            "test-owner", "zero-model-only", "frozen-evidence-component", (), ("fixture",), False), {
            "fixture": ops.Measurement((sys.executable, "-B", str(runner)), ops.sha(runner.read_bytes()),
                                        "synthetic-archive", external_effects=True)})
        host.subscribe("research", "task", ["operation_outcome"])
        version = host.register({"kind":"harness", "files":{}, "build":{"fixture":True},
                                  "model":{"name":"none"}, "resolver":"test", "tools":{}})
        payload = b'{ "model":"synthetic", "input":[] }\n'
        binding = {"payload_sha256":ops.sha(payload), "boundary":self.body['boundary']}
        host.submit("parent", "research", version, "fixture", {"mode":"parent", "checkpoint":binding}, "test")
        target = self.root/'frozen.json'
        with self.assertRaisesRegex(ValueError, "has not closed"):
            frozen.retain(host, "parent", payload, self.view.read, lambda:self.body['boundary'], target)
        host.run_worker("parent")
        with self.assertRaisesRegex(ValueError, "did not bind"):
            frozen.retain(host, "parent", b'{}', self.view.read, lambda:self.body['boundary'], target)
        capture = frozen.retain(host, "parent", payload, self.view.read, lambda:self.body['boundary'], target)
        saved = target.read_bytes(); pin = ops.sha(saved)
        descriptor = host.get(capture, "selector_evidence_capture")
        self.assertEqual(base64.b64decode(descriptor['payload_base64']), payload)
        self.assertEqual(descriptor['parent_outcome'], host.inspect('parent')['outcome']['object'])
        queries = [{"op":op} for op in frozen.PAGES] + [
            {"op":"read","source_id":"assessment"}, {"op":"read","handle":"f"*64}]
        receipts = []
        # No producer/reader is available in either child; each opens its own retained copy.
        del self.view
        for side in ("left", "right"):
            path = self.root/f'{side}.json';path.write_bytes(saved);path.chmod(0o400)
            host.submit(side, "research", version, "fixture", {"mode":"restore","snapshot":str(path),
                "sha256":pin,"capture":capture,"queries":queries}, "new isolated restoration")
            host.run_worker(side)
            receipts.append(host.inspect(side))
            self.assertEqual(path.read_bytes(),saved)
        self.assertTrue(all(r['outcome']['shutdown_confirmed'] for r in receipts))
        self.assertEqual(receipts[0]['outcome']['measurement'], receipts[1]['outcome']['measurement'])
        self.assertEqual(len({r['outcome']['assignment'] for r in [host.inspect('parent'),*receipts]}),3)
        self.assertEqual(target.read_bytes(),saved)
        with self.assertRaises(FileExistsError):
            frozen.retain(host, "parent", payload, frozen.FrozenEvidence(self.body).read,
                          lambda:self.body['boundary'], target)

    def test_capture_does_not_follow_new_source_state(self):
        body = frozen.capture(self.view.read, lambda:self.body['boundary'])
        self.body['pages']['index'].clear()
        restored = frozen.FrozenEvidence(body)
        self.assertEqual(restored.read({'op':'index'})['total'],1)
        count=0
        def changing():
            nonlocal count
            count+=1
            return {'journal_head':str(count)}
        with self.assertRaisesRegex(ValueError,'changed during capture'):
            frozen.capture(self.view.read, changing)

    def test_returned_pages_cannot_change_frozen_bindings_or_nested_indexes(self):
        before = {op:self.view.read({'op':op}) for op in frozen.PAGES}
        original_named = self.view.read({'op':'read','source_id':'assessment'})
        self.view.read({'op':'sources'})['entries'][0]['selected_subject_hash'] = '9'*64
        self.view.read({'op':'index'})['entries'][0]['review_checkpoints'].append('a'*64)
        self.view.read({'op':'frontier'})['entries'][0]['step'] = 'modified'
        for op in frozen.PAGES:
            self.assertEqual(self.view.read({'op':op}),before[op])
        self.assertEqual(self.view.read({'op':'read','source_id':'assessment'}),original_named)

    def test_native_byte_offsets_page_limits_and_response_budget(self):
        offset=0;parts=[]
        while True:
            result=self.view.read({'op':'read','handle':self.handle,'offset':offset})
            self.assertLessEqual(result['next_offset']-offset,2048)
            self.assertLessEqual(len(ops.encoded(result).encode()),8192)
            parts.append(result['text'])
            if result['eof']:break
            self.assertGreater(result['next_offset'],offset);offset=result['next_offset']
        self.assertEqual(''.join(parts).encode(),self.document)
        position=self.document.index('可'.encode())
        with self.assertRaisesRegex(frozen.QueryError,'UTF-8'):
            self.view.read({'op':'read','handle':self.handle,'offset':position+1})
        with self.assertRaisesRegex(frozen.QueryError,'budget'):
            self.view.read({'op':'read','handle':self.handle},1)
        named=self.view.read({'op':'read','source_id':'assessment','offset':len(self.document)})
        self.assertTrue(named['eof']);self.assertEqual(named['text'],'')
        self.assertEqual(named['source_subject_hash'],'3'*64)
        entries=list(range(12))
        self.assertEqual(frozen._page(entries,0,8192)['entries'],list(range(8)))
        self.assertEqual(frozen._page(entries,8,8192)['entries'],list(range(8,12)))

    def test_denied_foreign_handles_and_invalid_queries_never_open_a_path(self):
        for query in ({'op':'read','handle':'4'*64},{'op':'read','handle':'f'*64},
                      {'op':'read','source_id':'another-invocation'},
                      {'op':'read','handle':self.handle,'source_id':'assessment'},
                      {'op':'read','handle':'../../private'}, {'op':'index','offset':True},
                      {'op':'index','offset':-1},{'op':'index','path':'/private'}, {'op':'write'}):
            with self.subTest(query=query), self.assertRaises(frozen.QueryError):
                self.view.read(query)
        with self.assertRaisesRegex(frozen.QueryError,'offset'):
            self.view.read({'op':'sources','offset':2})

    def test_tampering_missing_bytes_and_wrong_named_subject_are_rejected(self):
        body=frozen.capture(self.view.read,lambda:self.body['boundary'])
        path=self.root/'view.json';path.write_text(ops.encoded(body))
        pin=ops.sha(path.read_bytes());path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'pinned bytes'):frozen.FrozenEvidence.load(path,pin)
        altered=json.loads(ops.encoded(body));altered['blobs'][self.handle]=base64.b64encode(b'wrong').decode()
        with self.assertRaisesRegex(ValueError,'differ from handle'):frozen.FrozenEvidence(altered)
        altered=json.loads(ops.encoded(body));altered['blobs'].clear()
        with self.assertRaisesRegex(ValueError,'account'):frozen.FrozenEvidence(altered)
        altered=json.loads(ops.encoded(body));altered['pages']['sources'][0]['source_subject_hash']='8'*64
        with self.assertRaisesRegex(ValueError,'subject binding'):frozen.FrozenEvidence(altered)

    def test_capture_limits_nonprogress_and_io_errors_fail_closed(self):
        with self.assertRaisesRegex(ValueError,'capture limit'):
            frozen.capture(self.view.read,lambda:self.body['boundary'],max_bytes=10)
        def stopped(query,budget):
            return {'entries':[], 'offset':0,'next_offset':0,'eof':False,'total':1}
        with self.assertRaisesRegex(ValueError,'did not advance'):
            frozen.capture(stopped,lambda:self.body['boundary'])
        def failed(query,budget):
            if query['op']=='read':raise OSError('reader unavailable')
            return self.view.read(query,budget)
        with self.assertRaises(OSError):frozen.capture(failed,lambda:self.body['boundary'])


if __name__=='__main__':
    unittest.main()
