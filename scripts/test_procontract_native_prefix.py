"""Read-only guards on a host-selected actual native custody fixture; no model/container."""
import json
import os
from pathlib import Path
import tempfile
import unittest

import procontract_native_prefix as export


class NativePrefixGuardTest(unittest.TestCase):
    def setUp(self):
        path = os.environ.get('NATIVE_PREFIX_TEST_INPUTS')
        if not path:
            self.skipTest('provide host-selected custody fixture NATIVE_PREFIX_TEST_INPUTS')
        self.fixture = json.loads(Path(path).read_text())
        self.inputs = self.fixture['inputs']
        self.original = json.loads(Path(self.inputs['capture_path']).read_text())
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)

    def altered(self, mutate):
        body = json.loads(json.dumps(self.original)); mutate(body)
        raw = export.encoded(body); path = Path(self.temp.name) / (export.sha(raw)+'.json'); path.write_bytes(raw)
        return {**self.inputs, 'capture_path': str(path)}

    def test_current_export_preserves_the_qualified_d660_metadata(self):
        prepared = export.prepare(**self.inputs)
        expected = json.loads(Path(self.fixture['expected_pair']).read_text())['metadata']
        observed = prepared['metadata']
        # Guard-only source changes intentionally change the exporter identity.
        self.assertEqual({k:v for k,v in observed.items()if k!='implementation'},
                         {k:v for k,v in expected.items()if k!='implementation'})

    def test_same_contract_with_another_selected_subject_is_rejected(self):
        base = json.loads(Path(self.fixture['expected_pair']).read_text())['metadata']['base_subject']
        inputs = self.altered(lambda body: body['boundary'].update(selected_subject=base))
        with self.assertRaisesRegex(ValueError, 'selection differs from the institution candidate'):
            export.prepare(**inputs)

    def test_json_boolean_generation_does_not_equal_integer_generation(self):
        inputs = self.altered(lambda body: body['boundary']['contract'].update(generation=True))
        with self.assertRaisesRegex(ValueError, 'institution differs from captured boundary'):
            export.prepare(**inputs)

    def test_missing_policy_facts_separator_is_explicitly_rejected(self):
        inputs = self.altered(lambda body: body.update(prompt=body['prompt'].replace('\n\n', '')))
        with self.assertRaisesRegex(ValueError, 'policy/facts separator'):
            export.prepare(**inputs)


if __name__ == '__main__': unittest.main()
