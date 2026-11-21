import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

from procontract_benchmark_gateway import Budget, Gateway, prepare_payload
from procontract_selector_capture import bind_request, encoded, sha


def fixture(root):
    native, retained = root / "native", root / "wire"
    native.mkdir(); retained.mkdir()
    def put(body):
        raw = encoded(body); identity = sha(raw)
        (native / (identity + '.json')).write_bytes(raw)
        return identity
    boundary = {'selected_subject': 'a'*64, 'kernel_head': {'seq': 4}, 'journal_head': {'seq': 8}}
    archive = put({'schema': 'frozen-public-evidence/v1', 'boundary': boundary,
                   'pages': {'index': [], 'sources': [], 'frontier': []}, 'blobs': {}, 'denied': {}})
    prompt = 'strategy\n\n{"contract_id":"c","subject_hash":"s","available_actions":["rest"]}'
    schema = {'type': 'object', 'additionalProperties': True, 'properties': {'choice': {'type': 'object'}}}
    identity = put({'schema': 'native-selector-capture/v1', 'entry': 'initial_native_selector_exchange',
                    'native_thread_id': 'native-thread', 'boundary': boundary, 'archive_sha256': archive,
                    'prompt': prompt, 'prompt_sha256': sha(prompt.encode()), 'response_schema': schema,
                    'instructions': 'Host worker instructions', 'model': 'gpt-5.6-luna', 'reasoning_effort': 'max'})
    body = {'model': 'gpt-5.6-luna', 'reasoning': {'effort': 'max'}, 'stream': True,
            'instructions': 'Host worker instructions', 'text': {'format': {'type': 'json_schema', 'schema': schema}},
            'tools': [{'type': 'function', 'name': 'exec', 'parameters': {'type': 'object'}}],
            'input': [{'type': 'message', 'role': 'user', 'content': [{'type': 'input_text',
                      'text': '<codex_internal_context source="pro_contract_worker">\n'+prompt+'\n</codex_internal_context>'}]}],
            'client_metadata': {'procontract_capture': identity, 'procontract_capture_thread': 'native-thread',
                                'procontract_capture_request': '0'}}
    return native, retained, body


class CaptureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.native, self.retained, self.body = fixture(self.root)
        self.budget = Budget(self.root / 'budget.sqlite', seconds=60, requests=30)
        self.budget.arm()

    def tearDown(self):
        self.budget.close(); self.temp.cleanup()

    def forwarded(self, body=None):
        raw = encoded(body or self.body)
        forwarded, audit = prepare_payload(raw, 'gpt-5.6-luna', 'max')
        identity, _, forwarded = self.budget.reserve_with_context(raw, forwarded, audit)
        return raw, forwarded, str(identity)

    def test_exact_bytes_and_first_binding_are_retained_once(self):
        raw, effective, identity = self.forwarded()
        bound = bind_request(self.native, self.retained, raw, effective, gateway_request_id=identity)
        self.assertTrue(bound['initial']); self.assertFalse(bound['admissible'])
        self.assertEqual(bound['closure'], 'not_observed_here')
        self.assertEqual((self.retained / (sha(effective)+'.payload.json')).read_bytes(), effective)
        self.assertEqual(bind_request(self.native, self.retained, raw, effective, gateway_request_id=identity), bound)
        with self.assertRaisesRegex(ValueError, 'another request'):
            bind_request(self.native, self.retained, raw, effective, gateway_request_id='another')

    def test_retry_and_later_requests_cannot_become_new_initial_checkpoints(self):
        self.body['client_metadata']['procontract_capture_request'] = '1'
        self.body['input'].append({'type': 'function_call', 'name': 'exec', 'arguments': '{}', 'call_id': 'old'})
        raw, effective, identity = self.forwarded()
        bound = bind_request(self.native, self.retained, raw, effective, gateway_request_id=identity)
        self.assertFalse(bound['initial']); self.assertEqual(bound['request_ordinal'], 1)

    def test_initial_rejects_history_and_changed_prompt_before_any_send(self):
        for mutate in (lambda b: b['input'].append({'type': 'reasoning', 'summary': []}),
                       lambda b: b['input'][0]['content'][0].update(text='changed')):
            body = copy.deepcopy(self.body); mutate(body)
            raw, effective, identity = self.forwarded(body)
            with self.assertRaises(ValueError):
                bind_request(self.native, self.retained, raw, effective, gateway_request_id=identity)
        self.assertEqual(list(self.retained.iterdir()), [])

    def test_schema_tools_instructions_and_resource_must_stay_bound(self):
        raw, effective, identity = self.forwarded()
        for mutate in (lambda b: b['text']['format'].update(schema={}),
                       lambda b: b.update(tools=[]),
                       lambda b: b.update(instructions='changed'),
                       lambda b: b['input'].pop(),
                       lambda b: b['client_metadata'].update(procontract_capture_thread='another')):
            changed = json.loads(effective); mutate(changed)
            with self.assertRaises(ValueError):
                bind_request(self.native, self.retained, raw, encoded(changed), gateway_request_id=identity)

    def test_native_objects_and_worker_identity_are_checked(self):
        identity = self.body['client_metadata']['procontract_capture']
        path = self.native / (identity+'.json')
        original = path.read_bytes()
        path.write_bytes(original+b' ')
        raw, effective, request = self.forwarded()
        with self.assertRaisesRegex(ValueError, 'bytes changed'):
            bind_request(self.native, self.retained, raw, effective, gateway_request_id=request)
        path.write_bytes(original)
        self.body['client_metadata']['procontract_capture_thread'] = 'other-thread'
        raw, effective, request = self.forwarded()
        with self.assertRaisesRegex(ValueError, 'another worker'):
            bind_request(self.native, self.retained, raw, effective, gateway_request_id=request)

    def test_json_types_do_not_alias_in_schema_tools_or_input(self):
        self.body['tools'][0]['parameters']['additionalProperties'] = True
        self.body['input'][0]['metadata'] = {'flag': True}
        raw, effective, identity = self.forwarded()
        for mutate in (lambda b: b['text']['format']['schema'].update(additionalProperties=1),
                       lambda b: b['tools'][0]['parameters'].update(additionalProperties=1),
                       lambda b: b['input'][0]['metadata'].update(flag=1)):
            changed = json.loads(effective); mutate(changed)
            with self.assertRaises(ValueError):
                bind_request(self.native, self.retained, raw, encoded(changed), gateway_request_id=identity)

    def test_unmarked_requests_are_not_claimed_as_capture(self):
        self.body.pop('client_metadata')
        raw, effective, request = self.forwarded()
        self.assertIsNone(bind_request(self.native, self.retained, raw, effective, gateway_request_id=request))

    def test_current_core_base_instruction_item_is_bound_without_search(self):
        instructions = self.body.pop('instructions')
        self.body['input'].insert(0, {'type': 'message', 'role': 'developer',
                                     'content': [{'type': 'input_text', 'text': instructions}]})
        raw, effective, request = self.forwarded()
        result = bind_request(self.native, self.retained, raw, effective, gateway_request_id=request)
        self.assertEqual(result['base_instructions_location'], {'input_index': 0, 'content_index': 0})
        # A matching string later in history is not a substitute for the native prefix.
        self.body['input'].insert(0, {'type': 'message', 'role': 'developer',
                                     'content': [{'type': 'input_text', 'text': 'another base'}]})
        raw, effective, request = self.forwarded()
        with self.assertRaisesRegex(ValueError, 'base instructions differ'):
            bind_request(self.native, self.retained, raw, effective, gateway_request_id=request)


def native_gateway(native, retained, upstream, model, effort):
    """Fresh local fixture used by the actual native run_turn Rust integration test."""
    native, retained = Path(native), Path(retained)
    class CapturingBudget(Budget):
        def reserve_with_context(self, original, forwarded, truncations):
            request, deadline, effective = super().reserve_with_context(original, forwarded, truncations)
            (retained / f'unbound.{request}.original.json').write_bytes(original)
            (retained / f'unbound.{request}.effective.json').write_bytes(effective)
            bind_request(native, retained, original, effective, gateway_request_id=str(request))
            return request, deadline, effective
    budget = CapturingBudget(retained / 'budget.sqlite', seconds=90, requests=8, model=model, effort=effort)
    budget.arm()
    gateway = Gateway(budget, upstream)
    thread = threading.Thread(target=gateway.serve_forever, daemon=True); thread.start()
    print(gateway.base_url, flush=True)
    try:
        sys.stdin.readline()
    finally:
        gateway.shutdown(); gateway.server_close(); budget.close(); thread.join(5)


if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--native-gateway':
        native_gateway(*sys.argv[2:])
    else:
        unittest.main()
