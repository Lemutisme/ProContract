"""Real retained native source -> verified import -> two local model fixtures.

Only the HTTP replies are scripted. Import, source custody, Operations grants,
native code-mode processes, immutable evidence, receipts and pair checks are real.
SELECTOR_NATIVE_DESCRIPTOR is a host-created pinned source descriptor; this test
does not discover or open original historical databases with SQLite.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import procontract_fork as fork
import procontract_operations as ops
import procontract_selector_exchange as exchange
import procontract_selector_native_source as native_source
import procontract_selector_receipts as receipts
from test_procontract_selector_exchange import ExchangeTest


class NativeRecoveryTest(unittest.TestCase):
    def setUp(self):
        descriptor = os.environ.get('SELECTOR_NATIVE_DESCRIPTOR')
        if not descriptor:
            self.skipTest('set a pinned SELECTOR_NATIVE_DESCRIPTOR')
        self.descriptor_path = Path(descriptor).resolve()
        self.descriptor_sha = os.environ.get('SELECTOR_NATIVE_DESCRIPTOR_SHA256')
        if ops.sha(self.descriptor_path.read_bytes()) != self.descriptor_sha:
            raise ValueError('native source descriptor pin differs')
        # Reuse only the actual HTTP/code-mode fixture setup, not its echo parent.
        self.fixture = ExchangeTest('test_two_real_process_exchanges_read_frozen_evidence_and_finish_decisions')
        self.fixture._testMethodName = self._testMethodName
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root

    def prepare(self):
        parent_path = self.root / 'native-import-profile.json'
        ops.atomic(parent_path, native_source.profile(self.root / 'host', {
            'first-initial': {'path': str(self.descriptor_path), 'sha256': self.descriptor_sha}}))
        exchange_path = self.root / 'exchange-profile.json'
        profile = exchange.profile(self.root / 'host', f'http://127.0.0.1:{self.fixture.server.server_port}/v1/responses',
            self.fixture.binary, self.fixture.bin_sha, parent_measurement='import_native',
            max_requests=8, max_reads=32)
        profile['reliability_measure'] = receipts.registration(profile)
        ops.atomic(exchange_path, profile)
        host = ops.Operations.create(self.root / 'host', ops.Authority(
            'qualification-owner', 'Human-selected native selector restoration qualification',
            'retained context/public evidence only; no action execution or adoption', (),
            ('import_native', 'exchange', 'measure'), allow_adoption=False, operation_limit=5), {
            'import_native': ops.Measurement((sys.executable, '-B', str(Path(native_source.__file__).resolve()),
                '--profile', str(parent_path)), native_source.implementation(), 'local source proof validation'),
            'exchange': ops.Measurement((sys.executable, '-B', str(Path(exchange.__file__).resolve()),
                '--profile', str(exchange_path)), exchange.implementation(), 'loopback/native-frozen-reader',
                external_effects=True, max_seconds=30),
            'measure': ops.Measurement((sys.executable, '-B', str(Path(receipts.__file__).resolve()),
                '--profile', str(exchange_path)), receipts.implementation(), 'registered operational reliability')})
        host.subscribe('research', 'task', ['operation_outcome'])
        version = host.register(exchange.version_definition(profile))
        host.submit('import-native', 'research', version, 'import_native', {'source': 'first-initial'},
                    'Verify and retain original native source; not the original execution')
        host.run_worker('import-native')
        imported = host.inspect('import-native')
        self.assertEqual(imported['outcome']['execution'], 'completed', imported)
        source_id = imported['outcome']['measurement']['source']
        source = native_source.resolve(host, source_id)
        capture_id = native_source.capture(host, source_id, self.root / 'capture' / 'evidence.json')
        capture = host.get(capture_id, 'selector_evidence_capture')
        self.assertEqual(capture['origin'], {'kind': 'native_selector_source', 'object': source_id})
        self.assertNotIn('parent', capture)
        return host, version, capture_id, source

    def test_original_native_identity_survives_two_fresh_restorations(self):
        host, version, capture_id, source = self.prepare()
        reader = receipts.ReceiptReader(host)
        checkpoint_id = reader.checkpoint(capture_id)
        checkpoint = reader.read(checkpoint_id, 'checkpoint_receipt')
        self.assertEqual(checkpoint['execution'], source['execution'])
        self.assertEqual(checkpoint['allocation'], source['allocation'])
        self.assertNotEqual(checkpoint['execution'], 'import-native')
        self.assertEqual(checkpoint['source_origin']['object'], source['source_id'])
        continuations, measurements = [], []
        for name in ('left', 'right'):
            host.submit(name, 'research', version, 'exchange', {'capture': capture_id},
                        'New continuation from the same native initial state', seconds=30)
            host.run_worker(name)
            item = host.inspect(name); result = item['outcome']['measurement']
            self.assertTrue(item['outcome']['shutdown_confirmed'], result)
            self.assertTrue(result['valid_decision'], result)
            self.assertEqual(result['tool_reads'], 1)
            self.assertEqual(result['payload_sha256'], ops.sha(source['payload']))
            self.assertEqual(result['restored_evidence_sha256'], source['evidence_file_sha256'])
            self.assertEqual(result['source_origin'], {'kind': 'native_selector_source', 'object': source['source_id']})
            continuations.append(reader.read(reader.continuation(checkpoint_id, name), 'continuation_receipt'))
            host.submit('measure-' + name, 'research', version, 'measure', {'source': name},
                        'Operational validity only; not task quality')
            host.run_worker('measure-' + name)
            measurements.append(reader.read(reader.measurement('measure-' + name), 'measurement_receipt'))
        pair = fork.measured(checkpoint, *continuations, measurements,
                             receipts.registration(exchange.replay.strict_json((self.root / 'exchange-profile.json').read_bytes())), [])
        self.assertEqual(len(self.fixture.server.requests), 4)
        for number in (0, 2):
            self.assertEqual(self.fixture.server.requests[number]['raw_sha256'], ops.sha(source['payload']))
        self.assertNotEqual(continuations[0]['execution'], continuations[1]['execution'])
        self.assertNotEqual(continuations[0]['allocation'], continuations[1]['allocation'])
        self.assertEqual(set(pair['not_shared']), {'files', 'contract', 'exposure', 'environment', 'pending', 'resources'})
        host.dispatch('left'); host.dispatch('right')
        self.assertEqual(len(self.fixture.server.requests), 4)
        ops.atomic(self.root / 'NATIVE_PAIR_READBACK.json', {
            'checkpoint': checkpoint, 'continuations': continuations, 'measurements': measurements,
            'pair': pair, 'source_origin': source['origin'], 'imported_by': source['imported_by'],
            'posts': 4, 'real_model_calls': 0, 'returned_actions_executed': False,
            'scope': 'Actual native source and frozen reader processes; scripted response qualification only'})

    def test_legacy_parent_cannot_override_native_origin(self):
        host, version, capture_id, _ = self.prepare()
        capture = host.get(capture_id, 'selector_evidence_capture')
        capture.update(parent='import-native', parent_assignment=ops.sha(host.inspect('import-native')['request']),
                       parent_outcome=host.inspect('import-native')['outcome']['object'])
        forged = host.put('selector_evidence_capture', capture, notify=False)
        with self.assertRaisesRegex(ValueError, 'mutually exclusive'):
            receipts.ReceiptReader(host).checkpoint(forged)
        host.submit('wrong-origin', 'research', version, 'exchange', {'capture': forged}, 'invalid origin', seconds=30)
        host.run_worker('wrong-origin')
        result = host.inspect('wrong-origin')['outcome']['measurement']
        self.assertEqual(result['category'], 'preflight_rejected')
        self.assertEqual(self.fixture.server.requests, [])


if __name__ == '__main__': unittest.main()
