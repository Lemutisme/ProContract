import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from procontract_trajectory_projection import project


class ProjectionTests(unittest.TestCase):
    def test_observations_keep_exact_unicode_source_and_call_identity_without_reasoning(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp);sessions=run/'codex-home/sessions';sessions.mkdir(parents=True)
            records=[{'type':'session_meta','payload':{'id':'thread1','source':'cli','unselected_private_field':'private'}},
                     {'type':'response_item','payload':{'type':'reasoning','encrypted_content':'opaque'}},
                     {'type':'response_item','payload':{'type':'function_call','call_id':'a','name':'tool','arguments':'检查'}},
                     {'type':'response_item','payload':{'type':'function_call_output','call_id':'a','output':'failed'}},
                     {'type':'context_window_reset','payload':{'window':2}}]
            raw=[(json.dumps(x,ensure_ascii=False)+'\n').encode() for x in records]
            path=sessions/'rollout-one.jsonl';path.write_bytes(b''.join(raw))
            result=project(run);rows=[json.loads(l) for l in (run/result['projection']).read_text().splitlines()]
            self.assertEqual([r['line'] for r in rows],[1,3,4,5])
            self.assertNotIn('unselected_private_field',rows[0]['payload'])
            self.assertEqual(rows[1]['uncompressed_offset'],len(raw[0])+len(raw[1]))
            self.assertEqual(rows[1]['line_sha256'],hashlib.sha256(raw[2]).hexdigest())
            self.assertEqual(rows[1]['payload']['call_id'],rows[2]['payload']['call_id'])
            self.assertEqual(rows[2]['payload']['output'],'failed')
            self.assertEqual(rows[1]['source_sha256'],hashlib.sha256(path.read_bytes()).hexdigest())

    def test_gzip_and_invalid_tail_are_observed_without_inventing_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp);sessions=run/'codex-home/sessions';sessions.mkdir(parents=True)
            raw=b'{"type":"session_meta","payload":{"id":"t"}}\n{partial'
            path=sessions/'rollout-one.jsonl.gz';path.write_bytes(gzip.compress(raw,mtime=0))
            result=project(run)
            self.assertEqual(result['observable_records'],1);self.assertEqual(result['invalid_records'],1)
            self.assertEqual(result['sources'][0]['sha256'],hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(project(run)['projection_sha256'],result['projection_sha256'])

    def test_missing_source_is_unavailable_not_empty_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=project(Path(tmp));self.assertFalse(result['available']);self.assertEqual(result['observable_records'],0)


if __name__=='__main__':unittest.main()
