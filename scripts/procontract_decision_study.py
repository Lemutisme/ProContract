#!/usr/bin/env python3
"""ResearchRuntime measurement adapter for a fixed-frame decision study.

Actor inputs are two immutable source IDs (candidate and study). The provisioner
owns frame paths, provider, source pins, resources and admission. Children use
the unchanged qualified single-request replay runner and real Operations grants.
This produces observations, never Support, adoption or a runtime policy gate.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import json
import math
from pathlib import Path
import random
import sys
import time

from jsonschema import Draft202012Validator

import procontract_decision_replay as replay
import procontract_decision_study_admission as admission
import procontract_decision_study_spec as study
import procontract_operations as ops
import procontract_policy_splice as splice
from procontract_research_runtime import ResearchRuntime

SCHEMA = 'decision-study-host/v1'
SOURCES = ('procontract_decision_study.py', 'procontract_decision_study_spec.py',
           'procontract_zero_step_check.py',
           'procontract_decision_study_admission.py', 'procontract_policy_splice.py',
           'procontract_decision_replay.py', 'procontract_replay_transport.py',
           'procontract_operations.py', 'procontract_operations_rpc.py',
           'procontract_evidence.py', 'procontract_store.py', 'procontract_research_runtime.py')


def implementation():
    return {name: ops.sha(Path(__file__).with_name(name).read_bytes()) for name in SOURCES}


def read(path):
    return replay.strict_json(Path(path).read_bytes())


def pinned(descriptor, maximum):
    path = Path(descriptor['path'])
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum or ops.sha(raw) != descriptor['sha256']:
        raise ValueError('protected source bytes differ or exceed allowance')
    return raw


def source(host, identity, maximum):
    descriptor = host.get(identity, 'source')
    if descriptor['bytes'] > maximum:
        raise ValueError('retained source exceeds allowance')
    parts = []
    offset = 0
    while True:
        page = host.source_read(identity, offset, min(maximum + 1, 65536))
        parts.append(base64.b64decode(page['base64'], validate=True))
        if page['eof']:
            break
        if page['end'] <= offset:
            raise ValueError('source cursor did not advance')
        offset = page['end']
    raw = b''.join(parts)
    if len(raw) != descriptor['bytes'] or ops.sha(raw) != descriptor['sha256']:
        raise ValueError('source differs from retained identity')
    return raw


def binding(config_path, assignment_path, *, reconcile=False):
    config_path, assignment_path = Path(config_path).resolve(), Path(assignment_path).resolve()
    config_raw = config_path.read_bytes()
    config = replay.strict_json(config_raw)
    if config.get('schema') != SCHEMA or config.get('sources') != implementation():
        raise ValueError('study implementation differs from protected configuration')
    resources = config['resources']
    for key in ('max_calls', 'max_source_bytes', 'max_request_bytes', 'max_bootstrap_replicates'):
        if type(resources[key]) is not int or resources[key] < 1:
            raise ValueError('invalid study resource allocation')
    if (type(resources['request_seconds']) not in (int, float)
            or not math.isfinite(resources['request_seconds']) or resources['request_seconds'] <= 0):
        raise ValueError('invalid child time allocation')
    if (type(config['admission']['disk_reservation_bytes']) is not int
            or config['admission']['disk_reservation_bytes'] <= 0):
        raise ValueError('invalid disk reservation')
    runtime = ResearchRuntime(config['runtime_root'])
    cap = runtime.config['capabilities'][config['capability']]
    if (cap['kind'] != 'measurement' or cap.get('project') is not None
            or cap['runner_sources'].get(str(config_path)) != ops.sha(config_raw)):
        raise ValueError('study configuration is not pinned by the runtime capability')
    for path, digest in cap['runner_sources'].items():
        if ops.sha(Path(path).read_bytes()) != digest:
            raise ValueError('runtime runner sources changed')
    supplied = read(assignment_path)
    item = runtime.host.inspect(supplied['request']['id'])
    directory = runtime.host.operation_dir(item['id'])
    original = {**item['request'], 'version': runtime.host.get(item['request']['request']['version'], 'version')}
    choice = next(row for row in runtime.choices() if row['id'] == original['request']['inputs']['choice'])
    work = choice['body']['work']
    if choice['operation'] != item['id'] or work['capability'] != config['capability']:
        raise ValueError('study is not the selected runtime work')
    if reconcile:
        expected = original
        expected_path = directory / 'assignment.json'
    else:
        if item['state'] != 'running':
            raise ValueError('study parent is not running')
        expected = {**original, 'runtime_project': None, 'runtime_project_snapshot': None,
                    'runtime_inputs': cap.get('inputs', {}), 'selected_work': work}
        expected_path = directory / 'capability-assignment.json'
    if assignment_path != expected_path or ops.encoded(supplied) != ops.encoded(expected):
        raise ValueError('not the retained runtime capability assignment')
    return config, runtime, item, choice, directory


def active(runtime, item, choice):
    inbox = runtime.host.inbox('research')
    deadline = item['request']['deadline']
    return (not inbox['cancelled'] and inbox['epoch'] == choice['body']['epoch']
            and runtime.state()['generation'] == choice['body']['generation']
            and (deadline is None or time.time() < deadline)
            and not (runtime.host.operation_dir(item['id']) / 'cancel.json').exists())


def freeze(config, runtime, item, choice, directory):
    work = choice['body']['work']
    refs = work.get('sources', {})
    if set(refs) != {'candidate', 'study'}:
        raise ValueError('study work requires candidate and study retained sources only')
    maximum = config['resources']['max_source_bytes']
    spec = study.validate(replay.strict_json(source(runtime.host, refs['study'], maximum)),
                          frame_registry={key: value['sha256'] for key, value in config['frames'].items()})
    candidate = source(runtime.host, refs['candidate'], maximum)
    if spec['candidate'] != {'source_id': refs['candidate'], 'sha256': ops.sha(candidate)}:
        raise ValueError('study names another candidate')
    descriptor = config['frames'][spec['frame']['id']]
    frame = replay.strict_json(pinned(descriptor, maximum))
    if not isinstance(frame, list) or not frame:
        raise ValueError('frame must be a nonempty array of registered points')
    baseline = pinned(descriptor['baseline'], maximum)
    if ops.sha(baseline) != spec['baseline_policy_sha256']:
        raise ValueError('baseline differs from registered frame')
    count = len(frame) * sum(spec['repeats'].values())
    if count > config['resources']['max_calls'] or spec['bootstrap_replicates'] > config['resources']['max_bootstrap_replicates']:
        raise ValueError('study exceeds host allocation')
    # Validate all fixed-frame identities and applicability before any output.
    study.analyze(spec, frame, [])
    directory.mkdir(parents=True, exist_ok=True)
    checkpoints, blocks = [], []
    rng = random.Random(spec['ordering_seed'])
    order = list(frame)
    rng.shuffle(order)
    for index, point in enumerate(order):
        original = pinned({'path': point['payload'], 'sha256': point['payload_sha256']},
                          config['resources']['max_request_bytes'])
        changed, proof = splice.splice(original, point['payload_sha256'], baseline, candidate)
        if spec['reading']['id'] == 'zero_step_check_avoidance':
            parts, _ = splice._packets(replay.strict_json(original))
            joined = ''.join(part[3] for part in parts)
            facts = replay.strict_json(joined[len(baseline.decode('utf-8')) + 2:])
            if study.canonical(point.get('facts')) != study.canonical(facts):
                raise ValueError('registered frame facts differ from original baseline payload')
        proof_path = directory / 'inputs' / f'{index}.proof.json'
        ops.atomic(proof_path, proof)
        arms = {}
        for arm, raw in (('A', original), ('B', changed)):
            path = directory / 'inputs' / f'{index}.{arm}.json'
            with path.open('xb') as stream:
                stream.write(raw)
            payload = replay.strict_json(raw)
            schema = payload['text']['format']['schema']
            Draft202012Validator.check_schema(schema)
            cp = replay.checkpoint(path, 'selector-input:' + point['payload_sha256'],
                {'id': point['run_id'], 'family': point.get('family', 'development')},
                'effective_payload' if arm == 'A' else 'reconstructed_payload',
                [refs['study'], str(proof_path)],
                {'files': 'No solver restored', 'contract': 'Original kernel not resumed',
                 'evidence': 'Historical prefix fixed; returned tools never executed',
                 'exposure': descriptor['exposure'], 'environment': 'Current registered provider',
                 'pending': 'No old effects resumed', 'resources': 'New child grant at dispatch'})
            checkpoints.append(cp)
            arms[arm] = cp
        calls = []
        for arm in ('A', 'B'):
            for repeat in range(1, spec['repeats'][arm] + 1):
                label = arm + str(repeat)
                calls.append({'arm': label, 'checkpoint': arms[arm]['id'], 'payload': arms[arm]['path'],
                              'id': 'study-' + ops.sha(item['id'])[:24] + f'-{index}-{label}'})
        rng.shuffle(calls)
        blocks.append({'point': point, 'calls': calls, 'proof': str(proof_path)})
    profile_path = directory / 'replay-profile.json'
    profile = replay.profile(directory / 'children', config['provider']['endpoint'], checkpoints,
                             auth_env=config['provider'].get('auth_env'), model=config['provider']['model'],
                             effort=config['provider']['effort'], max_request_bytes=config['resources']['max_request_bytes'])
    for cp in checkpoints:
        replay._payload(profile, {'request': {'inputs': {'checkpoint': cp['id']}}})
    ops.atomic(profile_path, profile)
    host = ops.Operations.create(directory / 'children', ops.Authority(
        runtime.host.authority.principal, 'runtime-operation:' + item['id'], 'decision study ' + study.identity(spec),
        (), ('replay',), allow_adoption=False, operation_limit=count, deadline=item['request']['deadline']), {
        'replay': ops.Measurement((sys.executable, '-B', str(Path(replay.__file__).resolve()),
                                   '--profile', str(profile_path)), replay.implementation(),
                                  config['provider']['environment'], max_seconds=config['resources']['request_seconds'],
                                  external_effects=True)})
    host.subscribe('study', 'task', ['operation_outcome'])
    version = host.register({'kind': 'harness', 'files': {}, 'build': {'replay': replay.implementation()},
        'model': {key: config['provider'][key] for key in ('model', 'effort')},
        'resolver': 'selector-policy-splice/v1', 'tools': {}})
    plan = {'schema': SCHEMA, 'parent': item['id'], 'parent_assignment': ops.sha(item['request']),
            'spec': spec, 'frame': frame, 'blocks': blocks, 'version': version, 'calls': count,
            'config_sha256': ops.sha(config), 'sources': implementation(),
            'pins': {str(path): ops.sha(path.read_bytes()) for path in (directory / 'inputs').iterdir()}}
    plan['pins'][str(profile_path)] = ops.sha(profile_path.read_bytes())
    ops.atomic(directory / 'plan.json', plan)
    return plan


def verify(plan, config):
    if plan['sources'] != implementation() or plan['config_sha256'] != ops.sha(config):
        raise ValueError('frozen study implementation or config changed')
    for path, digest in plan['pins'].items():
        if ops.sha(Path(path).read_bytes()) != digest:
            raise ValueError('frozen study bytes changed')


def row(host, point, call, item):
    outcome = item.get('outcome') or {}
    result = outcome.get('measurement') or {}
    bound = (result.get('schema') == 'decision-replay-observation/v1'
             and result.get('execution') == call['id'] and result.get('allocation') == ops.sha(item['request'])
             and result.get('checkpoint') == call['checkpoint']
             and result.get('payload_sha256') == ops.sha(Path(call['payload']).read_bytes()))
    observed = host.get(result['observation'], 'decision_observation') if bound and result.get('observation') else {}
    observed_bound = (observed.get('schema') == 'decision-observation/v1'
                      and observed.get('parent_state_id') == 'selector-input:' + point['payload_sha256']
                      and result.get('parent_state_id') == observed.get('parent_state_id')
                      and observed.get('output', {}).get('category') == result.get('category'))
    decision = observed.get('output', {}).get('decision')
    schema = read(call['payload'])['text']['format']['schema']
    errors = list(Draft202012Validator(schema).iter_errors(decision))
    closed = outcome.get('shutdown_confirmed') is True
    measured = bool(bound and observed_bound and result.get('effects_resolved') is True
                    and result.get('counts', {}).get('transport_requests_sent') == 1
                    and result.get('category') in ('structured_json', 'unstructured_output', 'tool_request',
                                                  'refusal', 'no_response', 'no_output'))
    valid = bool(measured and closed and result.get('terminal') == 'response.completed'
                 and result.get('category') == 'structured_json' and not errors)
    usage = result.get('usage') if bound else None
    cost = usage.get('cost') if isinstance(usage, dict) else None
    if type(cost) not in (float, int) or not math.isfinite(cost) or cost < 0:
        cost = None
    return {'task': point['point_id'], 'arm': call['arm'], 'operation': call['id'],
            'record': result if measured else None, 'shutdown_confirmed': closed, 'valid_decision': valid,
            'decision': decision, 'action': decision['choice']['action'] if valid else None,
            'category': result.get('category'), 'terminal': result.get('terminal'),
            'http_status': (result.get('transport', {}).get('http') or {}).get('status'),
            'schema_errors': [error.message for error in errors], 'usage': usage, 'known_cost_usd': cost,
            'outcome': outcome.get('object'), 'execution_state': item['state']}


def closeout(config, runtime, directory, plan, *, cancel=False, reason=None):
    host = ops.Operations(directory / 'children')
    rows, unresolved = [], []
    for block in plan['blocks']:
        for call in block['calls']:
            try:
                item = host.inspect(call['id'])
            except ValueError as error:
                if str(error) == 'operation missing':
                    continue
                raise
            if cancel and item['state'] in ('reserved', 'running'):
                host.cancel(call['id'])
                if item['state'] == 'reserved':
                    # The original reservation is already durably cancelled.
                    # Its CAS-protected worker writes the ordinary cancelled
                    # receipt before it could spawn the replay subprocess.
                    # No new grant or network attempt is created here.
                    host.dispatch(call['id'])
                    until = time.monotonic() + 1
                    while time.monotonic() < until:
                        item = host.reconcile(call['id'])
                        if item.get('outcome') and not ops.alive(item.get('owner')):
                            break
                        time.sleep(.025)
            item = host.reconcile(call['id'])
            rows.append(row(host, block['point'], call, item))
            if not (item.get('outcome') or {}).get('shutdown_confirmed') or ops.alive(item.get('owner')):
                unresolved.append(call['id'])
    report = study.analyze(plan['spec'], plan['frame'], rows)
    report.update({'rows': rows, 'parent': plan['parent'], 'plan_sha256': ops.sha(plan),
                   'children_host': str(host.root), 'reason': reason,
                   'effects_resolved': not unresolved, 'unresolved_executions': unresolved})
    ops.atomic(directory / 'report.json', report)
    report_id = runtime.host.put('decision_study_report', report, source='decision-study', notify=False)
    result = {'validity': 'observation_only', 'admissible': False, 'beyond_reach': False,
              'effects_resolved': not unresolved, 'report': report_id, 'study': study.identity(plan['spec']),
              'scope': 'Fixed development frame; one request per continuation; no tools/solver/quality/adoption',
              'unresolved_executions': unresolved}
    ops.atomic(directory / 'closure.json', {'effects_resolved': not unresolved, 'report': report_id})
    ops.atomic(directory.parent / 'result.json', result)
    return result


def run(config_path, assignment_path, *, reconcile=False):
    config, runtime, item, choice, parent = binding(config_path, assignment_path, reconcile=reconcile)
    directory = parent / 'decision-study'
    directory.mkdir(exist_ok=True)
    with (directory / 'study.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        saved = parent / 'result.json'
        if saved.exists() and read(saved).get('effects_resolved') is True:
            return read(saved)
        if not (directory / 'plan.json').exists():
            if reconcile or (directory / 'preparing.json').exists():
                # Preparation is before child dispatch. Retain partial inputs; no automatic refill.
                if (directory / 'started.json').exists():
                    raise ValueError('started study has no immutable plan')
                result = {'validity': 'unavailable', 'admissible': False, 'beyond_reach': False,
                          'effects_resolved': True, 'reason': 'interrupted before plan/dispatch'}
                ops.atomic(saved, result)
                return result
            ops.atomic(directory / 'preparing.json', {'parent': item['id'], 'at': time.time()})
            try:
                plan = freeze(config, runtime, item, choice, directory)
            except (ValueError, KeyError, TypeError, OSError) as error:
                # No child is submitted before plan publication and started.json.
                # An unsupported reading is a returned measurement observation,
                # not an unexplained external-effect fence.
                result = {'validity': 'unavailable', 'admissible': False, 'beyond_reach': False,
                          'effects_resolved': True, 'reason': 'preflight_rejected',
                          'error': str(error), 'error_category': type(error).__name__}
                ops.atomic(saved, result)
                return result
        else:
            plan = read(directory / 'plan.json')
        verify(plan, config)
        if plan['parent_assignment'] != ops.sha(item['request']):
            raise ValueError('plan belongs to another allocation')
        if reconcile or (directory / 'started.json').exists():
            return closeout(config, runtime, directory, plan, cancel=True, reason='reconciliation_only_no_refill')
        ops.atomic(directory / 'started.json', {'at': time.time(), 'parent': item['id']})
        while active(runtime, item, choice):
            if admission.acquire(config['admission'], directory):
                break
            time.sleep(.25)
        else:
            return closeout(config, runtime, directory, plan, reason='cancelled_or_expired_while_queued')
        host = ops.Operations(directory / 'children')
        reason = None
        for block in plan['blocks']:
            for call in block['calls']:
                verify(plan, config)
                if not active(runtime, item, choice):
                    reason = 'parent_cancelled_or_expired'
                    break
                host.submit(call['id'], 'study', plan['version'], 'replay', {'checkpoint': call['checkpoint']},
                            'Registered decision study', seconds=config['resources']['request_seconds'])
                host.dispatch(call['id'])
                while True:
                    child = host.reconcile(call['id'])
                    if not active(runtime, item, choice):
                        host.cancel(call['id'])
                        reason = 'parent_cancelled_or_expired'
                    if child.get('outcome') and not ops.alive(child.get('owner')):
                        break
                    time.sleep(.1)
                observation = row(host, block['point'], call, child)
                if not observation['shutdown_confirmed']:
                    reason = 'unknown_external_effects'
                elif observation['http_status'] in (401, 403) or observation['category'] == 'preflight_rejected':
                    reason = 'authentication_or_configuration_failure'
                if reason:
                    break
            if reason:
                break
        return closeout(config, runtime, directory, plan, reason=reason)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--reconcile', action='store_true')
    parser.add_argument('assignment')
    args = parser.parse_args()
    result = run(args.config, args.assignment, reconcile=args.reconcile)
    if args.reconcile:
        print(ops.encoded({'shutdown_confirmed': result['effects_resolved'], 'adapter': SCHEMA,
                           'report': result.get('report')}))
