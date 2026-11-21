import base64
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest

from procontract_operations import Authority, Operations
from procontract_operations_rpc import Broker, call, operation_descriptions
from procontract_benchmark_gateway import prepare_payload


def fragment(index, text):
    return {'type': 'function_call_output', 'namespace': 'agent',
            'name': f'pro_contract_packet_{index:04}', 'output': text}


def inline(text):
    return {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text',
        'text': '<codex_internal_context source="pro_contract_worker">\n'+text+'\n</codex_internal_context>'}]}


class ContextObservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.host = Operations.create(self.root/'host', Authority('owner', 'test', 'observation', (), ()), {})
        self.host.subscribe('research', 'test', ['observation'])

    def capture(self, value, compressed=False):
        data = json.dumps(value, ensure_ascii=False).encode()
        if compressed:data = gzip.compress(data)
        path = self.root/'input'
        path.write_bytes(data)
        identity = self.host.capture_source(path, expected_sha256=hashlib.sha256(data).hexdigest(), label='actual request fixture')
        path.unlink()
        return identity

    def test_search_crosses_fragment_boundaries_and_preserves_input_coordinates(self):
        request = {'input': [fragment(1, 'costly observation\n'), fragment(0, 'choose the least ')]}
        source = self.capture(request, compressed=True)
        page = self.host.source_context(source, query='least costly observation', length=5, record_limit=2)
        self.assertEqual(page['query'], {'literal': 'least costly observation', 'state': 'observed', 'offset': 11})
        self.assertEqual(page['text'], 'choos')
        self.assertFalse(page['eof'])
        self.assertEqual([r['item_index'] for r in page['records']], [1, 0])
        self.assertEqual(page['coverage'], 'contiguous_prefix_without_end_marker')
        self.assertEqual(page['source_encoding'], 'gzip')
        self.assertEqual(page['context_sha256'], hashlib.sha256(b'choose the least costly observation\n').hexdigest())

    def test_inline_context_removes_only_the_wire_envelope_and_pages_utf8(self):
        text = '\n中文\n\n'
        source = self.capture({'input': [inline(text)]})
        first = self.host.source_context(source, length=2)
        self.assertNotIn('text', first)
        data = bytearray(base64.b64decode(first['base64']))
        while not first['eof']:
            first = self.host.source_context(source, offset=first['end'], length=2)
            data.extend(first['text'].encode() if first['view_encoding'] == 'utf8' else base64.b64decode(first['base64']))
        self.assertEqual(data.decode(), text)
        self.assertEqual(first['coverage'], 'closed_inline_envelope')

    def test_incomplete_and_duplicate_fragments_are_unknown_not_nondelivery(self):
        for parts, state in (([fragment(1, 'policy')], 'incomplete'),
                             ([fragment(0, 'policy'), fragment(0, 'policy')], 'ambiguous')):
            source = self.capture({'input': [inline('Transport guidance'), *parts]})
            result = self.host.source_context(source, query='policy')
            self.assertEqual(result['state'], state)
            self.assertIsNone(result['query'])
            self.assertNotIn('text', result)
            self.assertEqual(self.host.source_context(source, context_index=1, length=64)['text'], 'Transport guidance')

    def test_ordinary_results_assistant_quotes_and_other_sources_are_not_host_context(self):
        genuine = inline('wanted')
        source = self.capture({'input': [{**fragment(0, 'wanted'), 'call_id': 'real-tool'},
            {**fragment(0, 'wanted'), 'namespace': 'functions'},
            {**genuine, 'role': 'assistant'},
            {'type': 'custom_tool_call_output', 'output': genuine['content']},
            inline('wanted')['content'][0],
            {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text',
                'text': genuine['content'][0]['text'].replace('pro_contract_worker', 'other_extension')}]}]})
        result = self.host.source_context(source, query='wanted')
        self.assertEqual(result['state'], 'not_observed')
        self.assertIsNone(result['query'])

    def test_request_and_context_selection_do_not_silently_merge_history(self):
        source = self.capture([{'input': [inline('first')]}, {'input': [inline('second'), inline('third')]}])
        with self.assertRaisesRegex(ValueError, 'explicit'):self.host.source_context(source)
        result = self.host.source_context(source, request_index=1, context_index=1, query='first', length=64)
        self.assertEqual(result['text'], 'third')
        self.assertEqual(result['context_count'], 2)
        self.assertEqual(result['query']['state'], 'not_observed_in_this_context')
        with self.assertRaisesRegex(ValueError, 'request_index'):self.host.source_context(source, request_index=True)
        with self.assertRaisesRegex(ValueError, 'context_index'):self.host.source_context(source, request_index=0, context_index=1)

    def test_bounds_unknown_format_and_source_corruption_are_explicit_errors(self):
        source = self.capture({'input': [inline('x'*10000)]}, compressed=True)
        with self.assertRaisesRegex(ValueError, 'expanded source'):self.host.source_context(source, max_bytes=1000)
        with self.assertRaisesRegex(ValueError, 'retained source'):self.host.source_context(source, max_bytes=1)
        unsupported = self.capture({'payload': {'input': [inline('not a request')]}})
        with self.assertRaisesRegex(ValueError, 'native API request'):self.host.source_context(unsupported)
        path = self.host.root/'sources'/self.host.get(source, 'source')['sha256']
        path.chmod(0o600);path.write_bytes(b'altered')
        with self.assertRaisesRegex(ValueError, 'differs'):self.host.source_context(source)
        with self.assertRaisesRegex(ValueError, 'missing object'):self.host.source_context('/etc/passwd')

    def test_actual_socket_exposes_the_query_only_when_offered(self):
        source = self.capture({'input': [fragment(0, 'left'), fragment(1, 'right')]})
        broker = Broker(self.root/'socket', self.host, 'research', operations=['source_context'])
        thread = threading.Thread(target=broker.serve_forever, daemon=True);thread.start()
        self.addCleanup(broker.server_close);self.addCleanup(broker.shutdown)
        schema = operation_descriptions(self.host, None, ['source_context'])['source_context']
        self.assertEqual(schema['required'], ['identity'])
        result = call(broker.path, 'source_context', {'identity': source, 'query': 'leftright'})
        self.assertEqual(result['query']['state'], 'observed')
        with self.assertRaisesRegex(RuntimeError, 'not offered'):call(broker.path, 'capture_source', {'path': '/etc/passwd'})
        self.assertFalse((self.host.store/'ledger_1.sqlite').exists())

    def test_query_default_survives_the_actual_gateway_without_dumping_context(self):
        text = 'a'*8190+'needle'+'b'*9000
        source = self.capture({'input': [fragment(0, text[:8192]), fragment(1, text[8192:])]})
        result = self.host.source_context(source, query='needle')
        self.assertEqual(result['query']['offset'], 8190)
        self.assertFalse(result['content_included'])
        self.assertNotIn('text', result);self.assertNotIn('base64', result);self.assertNotIn('records', result)
        encoded = json.dumps(result, indent=2)+'\n'
        raw = json.dumps({'model':'gpt-6-luna','reasoning':{'effort':'max'},'stream':True,
                          'input':[{'type':'custom_tool_call_output','call_id':'query','output':[{'type':'input_text','text':encoded}]}]}).encode()
        forwarded, changes = prepare_payload(raw, 'gpt-6-luna', 'max')
        self.assertEqual(changes, [])
        self.assertEqual(json.loads(json.loads(forwarded)['input'][0]['output'][0]['text']), result)
        page = self.host.source_context(source, offset=result['query']['offset'], length=6)
        self.assertEqual(page['text'], 'needle');self.assertNotIn('base64', page)
        binary = self.host.source_context(source, offset=result['query']['offset'], length=6, view_encoding='base64')
        self.assertEqual(base64.b64decode(binary['base64']), b'needle');self.assertNotIn('text', binary)

    def test_record_pages_and_content_pages_preserve_independent_coordinates(self):
        source = self.capture({'input':[fragment(i, str(i)) for i in range(70)]})
        summary = self.host.source_context(source)
        self.assertEqual(summary['record_count'],70);self.assertNotIn('records',summary)
        first = self.host.source_context(source, record_limit=64)
        last = self.host.source_context(source, record_offset=first['record_end'], record_limit=64)
        self.assertFalse(first['records_eof']);self.assertTrue(last['records_eof'])
        self.assertEqual([r['item_index'] for r in first['records']+last['records']],list(range(70)))
        self.assertEqual(first['context_sha256'],last['context_sha256'])
        self.assertFalse(first['content_included']);self.assertNotIn('text',last)


if __name__ == '__main__':unittest.main()
