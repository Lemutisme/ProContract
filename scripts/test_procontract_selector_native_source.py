"""Synthetic source files, real SQLite custody and real Operations import processes."""
import base64
import copy
import gzip
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import procontract_operations as ops
import procontract_selector_native_source as native
from procontract_benchmark_gateway import Budget
from procontract_selector_capture import bind_request
from test_procontract_frozen_evidence import fixture as archive_fixture


def source_fixture(root, calls=1, *, wal=False):
    state = root / 'source'; state.mkdir()
    captures = state / 'native-selector-captures'; captures.mkdir()
    wires = state / 'selector-request-bindings'; wires.mkdir()
    audits = state / 'requests'; audits.mkdir()
    sessions = state / 'codex-home/sessions'; sessions.mkdir(parents=True)
    def put_native(body):
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2).encode()
        identity = ops.sha(raw); (captures / (identity + '.json')).write_bytes(raw)
        return identity
    archive, _, _ = archive_fixture()
    archive['boundary'] = {'contract': {'id': 'native-task'}, 'selected_subject': '3'*64,
                           'kernel_head': {'seq': 2, 'hash': '5'*64}, 'journal_head': {'seq': 5, 'hash': '6'*64}}
    archive_id = put_native(archive)
    prompt = 'Choose next work.\n\n{"contract_id":"native-task","subject_hash":"'+ '3'*64 +'","available_actions":["rest"]}'
    schema = {'type': 'object', 'properties': {'choice': {'type': 'object'}}, 'additionalProperties': False}
    manifest = {'schema': 'native-selector-capture/v1', 'entry': 'initial_native_selector_exchange',
                'archive_sha256': archive_id, 'boundary': archive['boundary'], 'native_thread_id': 'worker-thread',
                'prompt': prompt, 'prompt_sha256': ops.sha(prompt.encode()), 'instructions': 'Native fixture instructions',
                'response_schema': schema, 'model': 'gpt-6-luna', 'reasoning_effort': 'max'}
    capture = put_native(manifest)
    budget = Budget(state / 'budget.sqlite', seconds=60, requests=None, model='gpt-6-luna', effort='max')
    allocation = budget.arm(); ops.atomic(state / 'allocation.json', allocation)
    if wal:
        # Keep a producer connection open so the fixture genuinely has uncheckpointed WAL.
        with budget.lock:
            budget.db.execute('PRAGMA wal_autocheckpoint=0')
    for ordinal in range(calls):
        payload = {'model': 'gpt-6-luna', 'reasoning': {'effort': 'max'}, 'stream': True,
                   'instructions': manifest['instructions'], 'text': {'format': {'type': 'json_schema', 'schema': schema}},
                   'tools': [], 'input': [{'type': 'message', 'role': 'user', 'content': [{'type': 'input_text',
                        'text': '<codex_internal_context source="pro_contract_worker">\n'+prompt+'\n</codex_internal_context>'}]}],
                   'client_metadata': {'procontract_capture': capture, 'procontract_capture_thread': 'worker-thread',
                                       'procontract_capture_request': str(ordinal)}}
        original = ops.encoded(payload).encode()
        request, _, effective = budget.reserve_with_context(original, original, [])
        bind_request(captures, wires, original, effective, gateway_request_id=str(request))
        (audits / f'{request}.original.json.gz').write_bytes(gzip.compress(original, mtime=0))
        (audits / f'{request}.forwarded.json.gz').write_bytes(gzip.compress(effective, mtime=0))
        ops.atomic(audits / f'{request}.identity.json', {'thread-id': 'worker-thread'})
        budget.observe_identity(request, {'model': 'gpt-6-luna', 'reasoning': {'effort': 'max'}})
        budget.checkpoint(request, status=200, terminal='response.completed', usage={'cost': 0.01}, finished=True)
    ops.atomic(state / 'run.json', {'thread_id': 'root-thread', 'status': {'thread_id': 'root-thread', 'contract_id': 'native-task'},
                                  'effects_resolved': True, 'native_exit': {'status': 'exited', 'returncode': 0}, 'budget': budget.status()})
    if not wal:
        budget.close()
    put_native({'schema': 'native-selector-closure/v1', 'capture': capture, 'native_thread_id': 'worker-thread',
                'request_metadata_issued': calls, 'shutdown_confirmed': True, 'outcome': 'worker_returned'})
    session = [{'type': 'session_meta', 'payload': {'id': 'worker-thread', 'source': {'internal': 'extension_worker'}}},
               {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'native-turn'}},
               {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 'native-turn',
                                              'last_agent_message': '{"choice":{"action":"rest"}}'}}]
    (sessions / 'rollout-worker-thread.jsonl').write_text(''.join(ops.encoded(row)+'\n' for row in session))
    ops.atomic(state / 'FINAL_CLOSURE.json', {'effects_resolved': True, 'history': [{'proven_closed': True, 'observation': {
        'native': {'model_dispatch_stopped': True, 'process_exited': True, 'group_members': [], 'residuals': [],
                   'gateway': {'created': True, 'closing': True, 'socket_fileno': -1, 'thread_alive': False, 'handlers': 0, 'connections': 0}},
        'container_closure': {'effects_resolved': True, 'remaining': [], 'errors': []}}}]})
    owned = root / 'OWNED_EFFECTS.json'
    ops.atomic(owned, {'effects_resolved': True, 'main_closed': True, 'owned_running_containers': [], 'owned_native_processes': []})
    return state, capture, owned, budget if wal else None


class NativeImportTest(unittest.TestCase):
    def setUp(self):
        keep = os.environ.get('NATIVE_SOURCE_TEST_ARTIFACTS')
        if keep:
            self.root = Path(keep) / self._testMethodName; self.root.mkdir(parents=True, exist_ok=False)
        else:
            temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)

    def source(self, calls=1, *, wal=False):
        self.state, self.capture_hash, self.owned, budget = source_fixture(self.root, calls, wal=wal)
        if budget: self.addCleanup(budget.close)
        return self.descriptor()

    def descriptor(self):
        return native.describe(self.state, self.capture_hash, collection='fixture-collection', owned_effects=self.owned)

    def setup_host(self, descriptor):
        desc = self.root / 'descriptor.json'; ops.atomic(desc, descriptor)
        self.profile = self.root / 'profile.json'
        ops.atomic(self.profile, native.profile(self.root / 'host', {'selected': native.reference(desc)}))
        host = ops.Operations.create(self.root / 'host', ops.Authority('owner', 'zero-model fixture', 'source import only',
            (), ('native_import',), False, operation_limit=8), {'native_import': ops.Measurement(
                (sys.executable, '-B', str(Path(native.__file__).resolve()), '--profile', str(self.profile)),
                native.implementation(), 'read-only source import', max_seconds=30)})
        host.subscribe('research', 'task', ['operation_outcome'])
        version = host.register({'kind': 'harness', 'files': {}, 'build': {'importer': native.implementation()},
                                 'model': {'name': 'none'}, 'resolver': 'native-source-import/v1', 'tools': {}})
        return host, version

    def imported(self, descriptor=None):
        host, version = self.setup_host(descriptor or self.source())
        host.submit('import', 'research', version, 'native_import', {'source': 'selected'}, 'verify native source')
        host.run_worker('import'); item = host.inspect('import')
        return host, item

    def assert_failed(self, descriptor, reason):
        host, item = self.imported(descriptor)
        self.assertTrue(item['outcome']['shutdown_confirmed'])
        self.assertEqual(item['outcome']['execution'], 'failed')
        self.assertIsNone(item['outcome']['measurement'])
        self.assertIn(reason, (host.operation_dir('import') / 'stderr').read_text())

    def test_actual_import_keeps_original_execution_and_rederives_without_source_paths(self):
        descriptor = self.source(); originals = {p: Path(p).read_bytes() for p in native._references(descriptor) if Path(p).is_file()}
        host, item = self.imported(descriptor); result = item['outcome']['measurement']
        self.assertEqual(result['schema'], native.RESULT); self.assertFalse(result['admissible'])
        self.assertTrue(item['outcome']['shutdown_confirmed']); source = result['source']
        resolved = native.resolve(host, source)
        self.assertEqual(resolved['record'], 'native-task')
        self.assertTrue(resolved['execution'].startswith('native-run:'))
        self.assertNotEqual(resolved['execution'], 'import')
        self.assertNotEqual(resolved['allocation'], item['outcome']['assignment'])
        self.assertEqual(resolved['origin']['native_worker_thread'], 'worker-thread')
        self.assertEqual(resolved['parent_measurement'], 'native_import')
        self.assertNotEqual(resolved['native_archive_sha256'], resolved['evidence_file_sha256'])
        for path, raw in originals.items(): self.assertEqual(Path(path).read_bytes(), raw)
        self.state.rename(self.root / 'original-source-unavailable')
        again = native.resolve(host, source)
        self.assertEqual(again['payload'], resolved['payload'])
        captured = native.capture(host, source, self.root / 'canonical-evidence.json')
        body = host.get(captured, 'selector_evidence_capture')
        self.assertNotIn('parent', body)
        self.assertEqual(native.resolve_capture(host, body)['execution'], resolved['execution'])
        before = item['outcome']['object']; host.dispatch('import')
        self.assertEqual(host.inspect('import')['outcome']['object'], before)

    def test_uncheckpointed_wal_is_included_without_opening_original(self):
        descriptor = self.source(wal=True)
        self.assertGreater(Path(descriptor['files']['budget_wal']['path']).stat().st_size, 0)
        host, item = self.imported(descriptor)
        source = item['outcome']['measurement']['source']
        real = sqlite3.connect
        def guarded(path, *args, **kwargs):
            self.assertNotEqual(Path(path).resolve(), (self.state / 'budget.sqlite').resolve())
            return real(path, *args, **kwargs)
        with patch.object(native.sqlite3, 'connect', guarded):
            self.assertEqual(native.resolve(host, source)['origin']['gateway_requests'][0]['gateway_request_id'], '1')

    def test_completed_host_terminal_is_read_from_database_not_a_declared_row(self):
        self.source()
        with sqlite3.connect(self.state / 'budget.sqlite') as db:
            db.execute("UPDATE requests SET terminal='response.incomplete'")
        self.assert_failed(self.descriptor(), 'without a completed host terminal')

    def test_unrelated_unclosed_request_prevents_original_root_closure(self):
        self.source()
        with sqlite3.connect(self.state / 'budget.sqlite') as db:
            db.execute("INSERT INTO requests(id,started,original_sha256,forwarded_sha256,truncations) VALUES(99,1,'unknown','unknown','[]')")
        p = self.state / 'run.json'; body = json.loads(p.read_text()); body['budget']['used'] += 1; ops.atomic(p, body)
        self.assert_failed(self.descriptor(), 'source root has a request without')

    def test_missing_native_request_or_wrong_header_is_rejected_with_recomputed_pins(self):
        descriptor = self.source(calls=2); descriptor['requests'].pop()
        self.assert_failed(descriptor, 'cover every issued native ordinal')

    def test_other_worker_header_is_rejected(self):
        self.source(); ops.atomic(self.state / 'requests/1.identity.json', {'thread-id': 'other-worker'})
        self.assert_failed(self.descriptor(), 'another native worker')

    def test_source_closure_and_original_allocation_cannot_be_redeclared(self):
        self.source(); p = self.state / 'allocation.json'; value = json.loads(p.read_text()); value['started'] += 1; ops.atomic(p, value)
        self.assert_failed(self.descriptor(), 'allocation times changed')

    def test_unknown_source_name_and_mutated_source_bytes_are_refused(self):
        descriptor = self.source(); host, version = self.setup_host(descriptor)
        host.submit('unknown', 'research', version, 'native_import', {'source': 'unregistered'}, 'denial')
        host.run_worker('unknown')
        self.assertIsNone(host.inspect('unknown')['outcome']['measurement'])
        p = Path(descriptor['files']['archive']['path']); p.write_bytes(p.read_bytes()+b' ')
        host.submit('changed', 'research', version, 'native_import', {'source': 'selected'}, 'denial')
        host.run_worker('changed')
        self.assertIn('source bytes differ', (host.operation_dir('changed') / 'stderr').read_text())

    def test_resolve_rejects_an_unclosed_import_and_capture_cannot_mix_origins(self):
        host, item = self.imported(); source = item['outcome']['measurement']['source']
        captured = native.capture(host, source, self.root / 'evidence.json'); body = host.get(captured, 'selector_evidence_capture')
        body['parent'] = 'import'
        with self.assertRaisesRegex(ValueError, 'mutually exclusive'):
            native.resolve_capture(host, body)
        with host.tx() as db: db.execute("UPDATE operations SET state='unknown' WHERE id='import'")
        with self.assertRaisesRegex(ValueError, 'has not closed'):
            native.resolve(host, source)

    def test_readback_uses_immutable_db_not_forged_derived_claims(self):
        host, item = self.imported(); source = item['outcome']['measurement']['source']
        original = host.get(source, 'native_selector_source'); bad = copy.deepcopy(original)
        bad['derived']['allocation'] = 'new-import-allocation'
        forged = host.put('native_selector_source', bad, notify=False)
        with self.assertRaisesRegex(ValueError, 'actual completed import'):
            native.resolve(host, forged)


if __name__ == '__main__': unittest.main()
