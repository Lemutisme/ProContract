"""Actual runtime measurement -> Operations child -> loopback wire -> return.

Only initial/return native actors and Docker provisioning are fixtures. The
runtime selection, measurement subprocess, child assignments, HTTP transport,
raw custody, closure and shared slot registry are real isolated components.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
import unittest

import procontract_decision_study as driver
import procontract_decision_study_admission as admission
import procontract_decision_study_spec as spec_module
import procontract_operations as ops
import procontract_research_runtime as runtime
from test_procontract_policy_splice import packet
from test_procontract_research_runtime import RuntimeTest


class StudyTest(unittest.TestCase):
    def setUp(self):
        provider_path = os.environ.get('REPLAY_PROVIDER_FIXTURE')
        if not provider_path:
            self.skipTest('set REPLAY_PROVIDER_FIXTURE to the loopback provider')
        self.fixture = RuntimeTest('test_create_is_zero_work_exactly_idempotent_and_incomplete_root_not_admitted')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.base = self.fixture.base
        module = importlib.util.spec_from_file_location('study_test_provider', provider_path)
        provider = importlib.util.module_from_spec(module)
        module.loader.exec_module(provider)
        self.server, self.thread = provider.start(self.base / 'provider')
        self.addCleanup(self.stop_provider)
        self.cap = self.base / 'capacity'
        self.cap.mkdir()
        self.capacity = {'external_job_roots': [], 'max_concurrent_attempts': 1,
                         'disk_admission_reserve_bytes': 0, 'admission_memory_reserve_bytes': 0}
        ops.atomic(self.cap / 'capacity.json', self.capacity)
        self.baseline = self.base / 'baseline.md'
        self.baseline.write_text('baseline strategy with enough room')
        self.candidate = self.base / 'candidate.md'
        self.candidate.write_text('new candidate strategy with a declared prediction')
        self.frame = []
        for n in range(2):
            body = json.loads(packet(self.baseline.read_bytes()))
            body.update(model='gpt-6-luna', reasoning={'effort': 'max'}, stream=True, tools=[],
                text={'format': {'type': 'json_schema', 'name': 'decision', 'strict': True, 'schema': {
                    'type': 'object', 'required': ['choice'], 'properties': {'choice': {
                        'type': 'object', 'required': ['action', 'reason'], 'properties': {
                            'action': {'const': 'rest'}, 'reason': {'type': 'string'}}}}}}})
            body['input'].append({'role': 'user', 'content': f'point {n}'})
            facts = None
            if getattr(self, 'zero_step', False):
                joined = ''.join(item['output'] for item in body['input'] if 'output' in item)
                facts = json.loads(joined[len(self.baseline.read_text()) + 2:])
                facts['available_actions'] = ['check', 'rest']
                facts['capabilities'] = {'handoff_observations': {'check': {
                    'executable_steps': 0, 'check_pipeline_executes_program_or_container': False}}}
                joined = self.baseline.read_text() + '\n\n' + json.dumps(facts)
                body['input'][0]['output'] = joined[:5]
                body['input'][1]['output'] = joined[5:]
            path = self.base / f'{n}.payload.json'
            ops.atomic(path, body)
            self.frame.append({'point_id': f'p{n}', 'run_id': f'r{n}', 'family': 'fixture',
                               'payload': str(path), 'payload_sha256': ops.sha(path.read_bytes()),
                               'strategy_sha256': ops.sha(self.baseline.read_bytes())})
            if facts is not None:
                self.frame[-1]['facts'] = facts
        self.frame_path = self.base / 'frame.json'
        ops.atomic(self.frame_path, self.frame)
        self.profile_path = self.base / 'study-host.json'
        self.profile = {'schema': driver.SCHEMA, 'runtime_root': str(self.base / 'study-runtime'),
            'capability': 'measure', 'sources': driver.implementation(),
            'frames': {'development': {'path': str(self.frame_path), 'sha256': ops.sha(self.frame_path.read_bytes()),
                'baseline': {'path': str(self.baseline), 'sha256': ops.sha(self.baseline.read_bytes())},
                'exposure': 'synthetic qualification only'}},
            'provider': {'endpoint': self.server.endpoint, 'model': 'gpt-6-luna', 'effort': 'max',
                         'environment': 'loopback qualification', 'auth_env': None},
            'resources': {'max_calls': 8, 'request_seconds': 5, 'max_source_bytes': 1 << 20,
                          'max_request_bytes': 1 << 20, 'max_bootstrap_replicates': 100},
            'admission': {'capacity_root': str(self.cap), 'disk_reservation_bytes': 4096}}
        ops.atomic(self.profile_path, self.profile)
        cfg = copy.deepcopy(self.fixture.config)
        cfg['capabilities']['measure']['measurement'] = {
            'argv': [sys.executable, '-B', str(Path(driver.__file__).resolve()), '--config', str(self.profile_path)],
            'identity': ops.sha(driver.implementation()), 'environment': 'local loopback fixture',
            'external_effects': True}
        cfg['capabilities']['measure']['reconcile_argv'] = [sys.executable, '-B',
            str(Path(driver.__file__).resolve()), '--config', str(self.profile_path), '--reconcile']
        self.r = runtime.ResearchRuntime.create(self.base / 'study-runtime', cfg)
        self.fixture.runtime, self.fixture.root = self.r, self.r.root
        self.candidate_id = self.r.host.capture_source(self.candidate,
            expected_sha256=ops.sha(self.candidate.read_bytes()), label='candidate')
        self.spec = {'schema': spec_module.SCHEMA,
            'candidate': {'source_id': self.candidate_id, 'sha256': ops.sha(self.candidate.read_bytes())},
            'frame': {'id': 'development', 'sha256': ops.sha(self.frame_path.read_bytes())},
            'baseline_policy_sha256': ops.sha(self.baseline.read_bytes()),
            'reading': {'id': 'action_delivery', 'version': 1,
                        'parameters': {'actions': ['rest'], 'closed_non_delivery': 'zero'}},
            'prediction': {'direction': 'increase', 'min_effect': 0,
                           'falsifier': {'kind': 'opposite_interval', 'min_effect': 0}},
            'applicable': {'all': []}, 'unknown_rule': 'retain', 'repeats': {'A': 2, 'B': 2},
            'ordering_seed': 18, 'analysis_seed': 19, 'cluster': 'run_id',
            'bootstrap_replicates': 50, 'aa': 'shared_A'}
        if getattr(self, 'zero_step', False):
            self.spec['reading'] = {'id': 'zero_step_check_avoidance', 'version': 1,
                'parameters': {'closed_non_delivery': 'unknown', 'population': 'known_applicable'}}
            self.spec['prediction']['falsifier']['kind'] = 'nonpositive_interval'

    def stop_provider(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def tearDown(self):
        retained = os.environ.get('STUDY_TEST_ARTIFACTS')
        if retained and hasattr(self, 'base'):
            target = Path(retained) / self._testMethodName
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(self.base, target)

    def select(self, dispatch=True):
        path = self.base / 'spec.json'
        ops.atomic(path, self.spec)
        identity = self.r.host.capture_source(path, expected_sha256=ops.sha(path.read_bytes()), label='study')
        view = self.fixture.start()
        selected = self.fixture.select(view, call={
            'work': {'capability': 'measure', 'sources': {'candidate': self.candidate_id, 'study': identity}},
            'return_to': {'capability': 'research', 'prompt': 'Interpret the returned observation.'}})
        self.fixture.finish()
        self.r.tick()
        self.leaf = next(x for x in self.r.choices() if x['id'] == selected['choice'])
        self.assignment = self.r.host.operation_dir(self.leaf['operation']) / 'assignment.json'
        self.directory = self.assignment.parent / 'decision-study'
        if dispatch:
            self.fixture.original_dispatch(self.r.host, self.leaf['operation'])
        return selected

    def finish(self):
        until = time.monotonic() + 25
        while time.monotonic() < until:
            item = self.r.host.reconcile(self.leaf['operation'])
            if item['state'] == 'finished':
                return item
            time.sleep(.025)
        logs = {str(p): p.read_text(errors='replace')[-5000:] for p in self.assignment.parent.rglob('*')
                if p.is_file() and p.name in ('stderr', 'worker.log')}
        self.fail(str(logs))

    def keeper_closed(self):
        lease = self.directory / 'lease.json'
        if lease.exists():
            keeper = driver.read(lease)['keeper']
            until = time.monotonic() + 5
            while ops.alive(keeper) and time.monotonic() < until:
                time.sleep(.05)
            self.assertFalse(ops.alive(keeper))
            self.assertEqual(admission.census(self.cap, driver.read(self.cap / 'capacity.json')), [])

    def test_actual_call_children_raw_return_and_duplicate_no_send(self):
        self.select()
        result = self.finish()
        self.assertTrue(result['outcome']['shutdown_confirmed'])
        self.assertEqual(len(self.server.records), 8)
        report = self.r.host.get(result['outcome']['measurement']['report'], 'decision_study_report')
        self.assertEqual(report['recorded_calls'], 8)
        self.assertEqual(report['primary']['mean_difference'], 0)
        self.assertEqual(report['unknown_calls'], 0)
        self.assertTrue(report['aa']['shared_with_primary'])
        self.assertFalse(report['admissible'])
        self.assertEqual(len({r['headers']['X-Allocation-ID'] for r in self.server.records}), 8)
        self.assertEqual(len({r['headers']['X-Execution-ID'] for r in self.server.records}), 8)
        plan = driver.read(self.directory / 'plan.json')
        expected = {ops.sha(Path(c['payload']).read_bytes()) for b in plan['blocks'] for c in b['calls']}
        self.assertEqual({r['body_sha256'] for r in self.server.records}, expected)
        self.r.tick()
        returns = [c for c in self.r.choices() if c['id'].startswith('return.')]
        self.assertEqual(len(returns), 1)
        self.assertEqual(returns[0]['body']['origin']['return_of']['outcome'], result['outcome']['object'])
        self.r.tick()
        self.r.host.dispatch(self.leaf['operation'])
        repeated = driver.run(self.profile_path, self.assignment, reconcile=True)
        self.assertEqual(repeated['report'], result['outcome']['measurement']['report'])
        self.assertEqual(len(self.server.records), 8)
        self.assertEqual(len([c for c in self.r.choices() if c['id'].startswith('return.')]), 1)
        self.keeper_closed()

    def test_unknown_disconnect_stops_without_refill_and_keeps_slot(self):
        self.server.modes = ('disconnect',)
        self.select()
        result = self.finish()
        self.assertFalse(result['outcome']['shutdown_confirmed'])
        self.assertEqual(len(self.server.records), 1)
        report = self.r.host.get(result['outcome']['measurement']['report'], 'decision_study_report')
        self.assertEqual(report['unknown_calls'], 8)
        self.assertIsNone(report['primary']['mean_difference'])
        self.assertEqual(len(admission.census(self.cap, driver.read(self.cap / 'capacity.json'))), 1)
        again = driver.run(self.profile_path, self.assignment, reconcile=True)
        self.assertFalse(again['effects_resolved'])
        self.assertEqual(len(self.server.records), 1)
        # Local fake provider has no delegated work. Its stopped server is a real
        # fixture-specific closure observation; production cannot infer this.
        self.stop_provider()
        children = ops.Operations(self.directory / 'children')
        child = report['unresolved_executions'][0]
        children.reconcile_effects(child, {'shutdown_confirmed': True,
            'observer': 'qualification owner', 'reason': 'loopback fake provider stopped; no remote executor'})
        closed = driver.run(self.profile_path, self.assignment, reconcile=True)
        self.assertTrue(closed['effects_resolved'])
        self.assertEqual(len(self.server.records), 1)
        self.keeper_closed()

    def test_waiting_slot_does_not_start_child_grants_and_cancel_returns(self):
        ops.atomic(self.cap / 'capacity.json', {**self.capacity, 'max_concurrent_attempts': 0})
        self.select()
        until = time.monotonic() + 10
        while not (self.directory / 'admission.json').exists() and time.monotonic() < until:
            time.sleep(.025)
        self.assertEqual(driver.read(self.directory / 'admission.json')['reason'], 'capacity')
        children = ops.Operations(self.directory / 'children')
        with children.tx() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM operations').fetchone()[0], 0)
        # A changed owner epoch is observed without issuing or renewing any child.
        with self.r.host.tx() as db:
            db.execute("UPDATE inbox SET epoch=epoch+1 WHERE id='research'")
        result = self.finish()
        self.assertTrue(result['outcome']['shutdown_confirmed'])
        self.assertEqual(self.server.records, [])
        report = self.r.host.get(result['outcome']['measurement']['report'], 'decision_study_report')
        self.assertEqual(report['unknown_calls'], 8)
        self.r.tick()
        returning = next(c for c in self.r.choices() if c['id'].startswith('return.'))
        self.assertEqual(returning['state'], 'stale')

    def test_unregistered_frame_rejected_before_wire_and_returns_failure(self):
        self.spec['frame']['id'] = 'sealed-unregistered'
        self.select()
        result = self.finish()
        self.assertTrue(result['outcome']['shutdown_confirmed'])
        self.assertEqual(result['outcome']['measurement']['reason'], 'preflight_rejected')
        self.assertEqual(self.server.records, [])
        self.assertFalse((self.directory / 'started.json').exists())
        self.r.tick()
        self.assertEqual(len([c for c in self.r.choices() if c['id'].startswith('return.')]), 1)

    def test_other_candidate_id_and_excess_calls_cannot_select_provider_or_refill(self):
        self.spec['candidate']['sha256'] = '0' * 64
        self.spec['repeats']['B'] = 99
        self.select()
        result = self.finish()
        self.assertEqual(result['outcome']['measurement']['reason'], 'preflight_rejected')
        self.assertEqual(self.server.records, [])

    def test_closure_only_measurement_is_not_a_measured_non_delivery(self):
        call = {'id': 'unmeasured', 'arm': 'B1', 'checkpoint': 'cp', 'payload': self.frame[0]['payload']}
        for measured in (None, {'effects_resolved': True}, {'schema': 'another/v1', 'effects_resolved': True}):
            item = {'request': {}, 'state': 'finished', 'outcome': {
                'shutdown_confirmed': True, 'measurement': measured}}
            result = driver.row(self.r.host, self.frame[0], call, item)
            self.assertIsNone(result['record'])
            self.assertFalse(result['valid_decision'])

    def test_reserved_child_is_cancelled_by_original_worker_without_a_post(self):
        self.select(dispatch=False)
        parent = self.r.host.inspect(self.leaf['operation'])
        self.directory.mkdir(parents=True)
        plan = driver.freeze(self.profile, self.r, parent, self.leaf, self.directory)
        children = ops.Operations(self.directory / 'children')
        call = plan['blocks'][0]['calls'][0]
        reserved = children.submit(call['id'], 'study', plan['version'], 'replay',
            {'checkpoint': call['checkpoint']}, 'interruption between submit and dispatch', seconds=5)
        # Restore only actual Operations dispatch for the cancellation worker;
        # initial/return native actions stay fixtures, no native model is run.
        from unittest.mock import patch
        with patch.object(ops.Operations, 'dispatch', self.fixture.original_dispatch):
            result = driver.closeout(self.profile, self.r, self.directory, plan, cancel=True)
        ended = children.inspect(call['id'])
        self.assertEqual(ended['request'], reserved['request'])
        self.assertEqual(ended['outcome']['execution'], 'cancelled')
        self.assertTrue(result['effects_resolved'])
        self.assertEqual(self.server.records, [])
        self.assertFalse((children.operation_dir(call['id']) / 'dispatch-intent.json').exists())
        report = self.r.host.get(result['report'], 'decision_study_report')
        self.assertEqual(report['unknown_calls'], 8)

    def test_replay_metadata_without_its_observation_is_unknown(self):
        point = self.frame[0]
        call = {'id': 'unmeasured', 'arm': 'B1', 'checkpoint': 'cp', 'payload': point['payload']}
        measured = {'schema': 'decision-replay-observation/v1', 'execution': call['id'],
                    'allocation': ops.sha({}), 'checkpoint': 'cp',
                    'payload_sha256': point['payload_sha256'], 'effects_resolved': True,
                    'counts': {'transport_requests_sent': 1}, 'category': 'no_output',
                    'parent_state_id': 'selector-input:' + point['payload_sha256']}
        item = {'request': {}, 'state': 'finished', 'outcome': {
            'shutdown_confirmed': True, 'measurement': measured}}
        self.assertIsNone(driver.row(self.r.host, point, call, item)['record'])
        measured['observation'] = self.r.host.put('decision_observation', {
            'schema': 'decision-observation/v1', 'parent_state_id': 'another-parent',
            'output': {'category': 'no_output'}})
        self.assertIsNone(driver.row(self.r.host, point, call, item)['record'])

    def test_configuration_parsed_bytes_are_the_bytes_pinned_by_runtime(self):
        self.spec['repeats'] = {'A': 4, 'B': 4}
        self.select(dispatch=False)
        retained = self.r.host.inspect(self.leaf['operation'])
        ops.atomic(self.assignment, {**retained['request'], 'version': self.r.host.get(
            retained['request']['request']['version'], 'version')})
        config, rt, item, choice, _ = driver.binding(self.profile_path, self.assignment, reconcile=True)
        self.directory.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'exceeds host allocation'):
            driver.freeze(config, rt, item, choice, self.directory)
        original = self.profile_path.read_bytes()
        changed = copy.deepcopy(config)
        changed['resources']['max_calls'] = 16
        real_read = Path.read_bytes
        swaps = []
        def interleave(path):
            if path == self.profile_path and not swaps:
                swaps.append(True)
                path.write_bytes(ops.encoded(changed).encode())
                try:
                    return real_read(path)
                finally:
                    path.write_bytes(original)
            return real_read(path)
        from unittest.mock import patch
        with patch.object(Path, 'read_bytes', interleave):
            with self.assertRaisesRegex(ValueError, 'not pinned'):
                driver.binding(self.profile_path, self.assignment, reconcile=True)
        self.assertEqual(self.profile_path.read_bytes(), original)
        self.assertEqual(self.server.records, [])
        self.assertFalse((self.directory / 'plan.json').exists())


class RegisteredStudyTest(unittest.TestCase):
    zero_step = True
    setUp = StudyTest.setUp
    tearDown = StudyTest.tearDown
    stop_provider = StudyTest.stop_provider
    select = StudyTest.select
    finish = StudyTest.finish
    keeper_closed = StudyTest.keeper_closed

    def test_registered_metric_runs_from_actual_bound_payload_and_returns(self):
        self.select()
        result = self.finish()
        report = self.r.host.get(result['outcome']['measurement']['report'], 'decision_study_report')
        self.assertEqual(len(self.server.records), 8)
        self.assertEqual(report['population']['true'], 2)
        self.assertEqual(report['eligible_calls'], 8)
        self.assertEqual(report['primary']['mean_difference'], 0)
        self.assertEqual(report['rest_guardrail']['contrast']['mean_difference'], 0)
        self.assertEqual(report['status'], 'refutation')
        self.assertFalse(report['admissible'])
        self.keeper_closed()

    def test_frame_cannot_relabel_baseline_facts_without_payload_change(self):
        self.select(dispatch=False)
        parent = self.r.host.inspect(self.leaf['operation'])
        # Exercise the trusted registry parser separately: even a host frame
        # with a newly correct file hash cannot substitute facts for another payload.
        altered = copy.deepcopy(self.frame)
        altered[0]['facts']['capabilities']['handoff_observations']['check']['executable_steps'] = 99
        ops.atomic(self.frame_path, altered)
        changed = copy.deepcopy(self.profile)
        changed['frames']['development']['sha256'] = ops.sha(self.frame_path.read_bytes())
        # The actor spec must bind this same revised frame; it still cannot
        # overcome the original-payload fact check during freeze.
        spec_value = copy.deepcopy(self.spec)
        spec_value['frame']['sha256'] = changed['frames']['development']['sha256']
        path = self.base / 'changed-spec.json'; ops.atomic(path, spec_value)
        identity = self.r.host.capture_source(path, expected_sha256=ops.sha(path.read_bytes()), label='changed spec')
        choice = copy.deepcopy(self.leaf); choice['body']['work']['sources']['study'] = identity
        self.directory.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'facts differ'):
            driver.freeze(changed, self.r, parent, choice, self.directory)
        self.assertEqual(self.server.records, [])
        self.assertFalse((self.directory / 'plan.json').exists())


if __name__ == '__main__':
    unittest.main()
