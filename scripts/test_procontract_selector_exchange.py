"""Actual Operations + native code-mode + scripted HTTP provider, zero inference."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import procontract_operations as ops
import procontract_frozen_evidence as frozen
import procontract_selector_exchange as exchange
import procontract_selector_receipts as receipts
import procontract_fork as fork
import procontract_store as kernel
from test_procontract_frozen_evidence import fixture


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*_):pass
    def do_POST(self):
        raw=self.rfile.read(int(self.headers['Content-Length']));body=json.loads(raw)
        identity=self.headers['X-Execution-ID'];index=int(identity.rsplit(':',1)[1])
        self.server.requests.append({'execution':identity,'allocation':self.headers['X-Allocation-ID'],
                                     'body':body,'raw_sha256':ops.sha(raw)})
        if self.server.mode=='disconnect' and index==1:
            self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
        if self.server.mode=='wait' and index==0:
            output=[{'type':'custom_tool_call','name':'exec','call_id':'yield-1',
                'input':'text("before"); await yield_control(); text(await tools.contract_read_evidence({op:"sources"}));'}]
        elif self.server.mode=='wait' and index==1:
            cell=re.search(r'Script running with cell ID ([^\n]+)',body['input'][-1]['output'][0]['text']).group(1)
            output=[{'type':'function_call','name':'wait','call_id':'wait-1',
                     'arguments':json.dumps({'cell_id':cell,'yield_time_ms':0,'max_tokens':1})}]
        elif index==0 or self.server.mode=='always_tool':
            output=[{'type':'custom_tool_call','name':'exec','call_id':f'read-{index}',
                     'input':'text(await tools.contract_read_evidence({op:"sources"}));'}]
        else:
            output=[{'type':'message','role':'assistant','content':[{'type':'output_text',
                    'text':'{"choice":{"action":"rest","reason":"read frozen evidence"}}'}]}]
        response={'id':'fake-'+identity,'status':'completed','model':'gpt-6-luna','output':output,
                  'usage':{'input_tokens':1,'output_tokens':1,'cost':0.001}}
        raw=('event: response.completed\ndata: '+json.dumps({'type':'response.completed','response':response})+'\n\n').encode()
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(raw)))
        self.send_header('Connection','close');self.end_headers();self.wfile.write(raw);self.wfile.flush();self.close_connection=True


class ExchangeTest(unittest.TestCase):
    def setUp(self):
        self.binary=os.environ.get('SELECTOR_CODE_MODE_HOST');self.bin_sha=os.environ.get('SELECTOR_CODE_MODE_HOST_SHA256')
        if not self.binary or not self.bin_sha:self.skipTest('set pinned code-mode host')
        keep=os.environ.get('SELECTOR_EXCHANGE_ARTIFACTS')
        if keep:
            self.root=Path(keep)/self._testMethodName;self.root.mkdir(parents=True,exist_ok=False)
        else:
            temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.server.mode='normal';self.server.requests=[]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)
        native=os.environ.get('NATIVE_ARCHIVE_FIXTURE')
        if native:
            self.view=frozen.FrozenEvidence.load(native,os.environ['NATIVE_ARCHIVE_SHA256'])
            self.body=self.view.body
        else:
            self.body,_,_=fixture();self.view=frozen.FrozenEvidence(self.body)
        self.payload=ops.encoded({'model':'gpt-6-luna','reasoning':{'effort':'max'},'stream':True,'input':[],
            'text':{'format':{'type':'json_schema','schema':{'type':'object','properties':{'choice':{'type':'object',
                'properties':{'action':{'const':'rest'},'reason':{'type':'string'}},'required':['action','reason'],
                'additionalProperties':False}},'required':['choice'],'additionalProperties':False}}}}).encode()

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())
        ops.atomic(self.root/'PROVIDER_REQUESTS.json',self.server.requests)

    def host(self,max_requests=2,measured=False):
        profile_path=self.root/'profile.json'
        config=exchange.profile(self.root/'host',f'http://127.0.0.1:{self.server.server_port}/v1/responses',
            self.binary,self.bin_sha,parent_measurement='parent',max_requests=max_requests,max_reads=8)
        if measured:config['reliability_measure']=receipts.registration(config)
        ops.atomic(profile_path,config)
        parent_runner=self.root/'parent.py'
        parent_runner.write_text('import json,sys\nfrom pathlib import Path\np=Path(sys.argv[1]);a=json.loads(p.read_text())\n'
            '(p.parent/"result.json").write_text(json.dumps({"effects_resolved":True,"selector_checkpoint":a["request"]["inputs"]}))\n')
        measurements={
            'parent':ops.Measurement((sys.executable,str(parent_runner)),ops.sha(parent_runner.read_bytes()),'fixture'),
            'exchange':ops.Measurement((sys.executable,'-B',str(Path(exchange.__file__).resolve()),'--profile',str(profile_path)),
                exchange.implementation(),'scripted-provider/native-code-mode',external_effects=True)}
        if measured:measurements['measure']=ops.Measurement((sys.executable,'-B',str(Path(receipts.__file__).resolve()),
            '--profile',str(profile_path)),receipts.implementation(),'registered-operational-reliability')
        host=ops.Operations.create(self.root/'host',ops.Authority('test-owner','zero-model','selector-exchange',(),
            tuple(measurements),False,operation_limit=5),measurements)
        host.subscribe('research','task',['operation_outcome'])
        version=host.register(exchange.version_definition(config))
        host.submit('parent','research',version,'parent',{'payload_sha256':ops.sha(self.payload),
            'boundary':self.body['boundary']},'closed synthetic parent');host.run_worker('parent')
        capture=frozen.retain(host,'parent',self.payload,self.view.read,lambda:self.body['boundary'],self.root/'capture.json')
        return host,version,capture

    def run_exchange(self,host,version,capture,name):
        host.submit(name,'research',version,'exchange',{'capture':capture},'new selector exchange',seconds=20)
        host.run_worker(name);return host.inspect(name)

    def test_two_real_process_exchanges_read_frozen_evidence_and_finish_decisions(self):
        host,version,capture=self.host()
        left=self.run_exchange(host,version,capture,'left');right=self.run_exchange(host,version,capture,'right')
        a,b=[x['outcome']['measurement']for x in (left,right)]
        for result in (a,b):
            self.assertTrue(result['valid_decision']);self.assertTrue(result['effects_resolved'])
            self.assertFalse(result['admissible']);self.assertEqual(result['http_attempts_reserved'],2)
            self.assertEqual(result['tool_reads'],1);self.assertEqual(result['calls_without_price'],0)
        self.assertEqual(a['observation'],b['observation']);self.assertNotEqual(a['allocation'],b['allocation'])
        self.assertTrue(left['outcome']['shutdown_confirmed']);self.assertTrue(right['outcome']['shutdown_confirmed'])
        self.assertEqual(len(self.server.requests),4)
        for index in (0,2):self.assertEqual(self.server.requests[index]['raw_sha256'],ops.sha(self.payload))
        for index in (1,3):
            inp=self.server.requests[index]['body']['input']
            self.assertEqual(inp[-1]['type'],'custom_tool_call_output')
            self.assertIn('assessment',inp[-1]['output'][0]['text'])
        host.dispatch('left');host.dispatch('right')
        self.assertEqual(len(self.server.requests),4)

    def test_request_allowance_is_not_retried_or_refilled(self):
        self.server.mode='always_tool';host,version,capture=self.host(max_requests=1)
        result=self.run_exchange(host,version,capture,'limited')['outcome']['measurement']
        self.assertEqual(result['category'],'request_allowance_exhausted')
        self.assertFalse(result['valid_decision']);self.assertTrue(result['effects_resolved'])
        self.assertEqual(len(self.server.requests),1)

    def test_interrupted_second_request_retains_first_tool_and_unknown_remote(self):
        self.server.mode='disconnect';host,version,capture=self.host()
        done=self.run_exchange(host,version,capture,'lost');result=done['outcome']['measurement']
        self.assertEqual(result['category'],'unresolved_request');self.assertFalse(result['effects_resolved'])
        self.assertFalse(done['outcome']['shutdown_confirmed']);self.assertEqual(result['tool_reads'],1)
        self.assertEqual(len(self.server.requests),2);self.assertEqual(result['calls_without_price'],1)
        host.dispatch('lost');self.assertEqual(len(self.server.requests),2)

    def test_spent_intent_is_never_replayed(self):
        host,version,capture=self.host();directory=host.operation_dir('uncertain');directory.mkdir(parents=True)
        (directory/'exchange-intent.json').write_text('{partial')
        done=self.run_exchange(host,version,capture,'uncertain')
        self.assertEqual(done['outcome']['measurement']['category'],'interrupted_unreplayed')
        self.assertFalse(done['outcome']['shutdown_confirmed']);self.assertEqual(self.server.requests,[])

    def test_schema_cannot_add_an_external_lookup(self):
        payload=json.loads(self.payload);payload['text']['format']['schema']={'$ref':'http://127.0.0.1/private'}
        self.payload=ops.encoded(payload).encode();host,version,capture=self.host()
        result=self.run_exchange(host,version,capture,'bad-schema')['outcome']['measurement']
        self.assertEqual(result['category'],'preflight_rejected');self.assertTrue(result['effects_resolved'])
        self.assertEqual(self.server.requests,[])

    def test_profile_parse_and_hash_use_the_same_bytes(self):
        host,version,capture=self.host(max_requests=2)
        host.submit('race','research',version,'exchange',{'capture':capture},'read-window control',seconds=20)
        item=host.inspect('race');assignment={**item['request'],'version':host.get(version,'version')}
        directory=host.operation_dir('race');directory.mkdir(parents=True)
        path=directory/'assignment.json';ops.atomic(path,assignment)
        # Method-level running-state precondition, no exchange/model dispatched.
        with host.tx() as db:db.execute("UPDATE operations SET state='running' WHERE id='race'")
        profile=(self.root/'profile.json').resolve();original=profile.read_bytes()
        changed=json.loads(original);changed['max_requests']=100;profile.write_text(ops.encoded(changed))
        original_read=Path.read_bytes;seen=0
        def read(p):
            nonlocal seen
            raw=original_read(p)
            if p.resolve()==profile:
                seen+=1
                if seen==1:profile.write_bytes(original)
            return raw
        with patch.object(Path,'read_bytes',read),self.assertRaisesRegex(ValueError,'registered runner/profile'):
            exchange._load(profile,path)
        self.assertEqual(profile.read_bytes(),original);self.assertEqual(self.server.requests,[])

    def test_wait_optional_limits_are_honored(self):
        self.server.mode='wait';host,version,capture=self.host(max_requests=3)
        result=self.run_exchange(host,version,capture,'waited')['outcome']['measurement']
        self.assertTrue(result['valid_decision']);self.assertEqual(len(self.server.requests),3)
        returned=self.server.requests[2]['body']['input'][-1]['output'][0]['text']
        self.assertLessEqual(len(returned.split('\nOutput:\n',1)[1].encode()),4)
        trace=[json.loads(x) for x in (host.operation_dir('waited')/'tools/tools.jsonl').read_text().splitlines()]
        wait=next(x for x in trace if x['type']=='wait')
        self.assertEqual(wait['request']['request']['yield_time_ms'],0)
        self.assertEqual(wait['max_output_tokens'],1)

    def test_pragma_and_client_budget_are_explicit(self):
        code,limits=exchange._exec_source('// @exec: {"yield_time_ms":0,"max_output_tokens":1}\ntext("hello");')
        self.assertEqual(code,'text("hello");');self.assertEqual(limits,{'yield_time_ms':0,'max_output_tokens':1})
        body={'Result':{'cell_id':'1','content_items':[{'type':'input_text','text':'abcdef'*100}],
                        'error_text':None}}
        result=exchange._runtime_content(body,max_tokens=1)[0]['text']
        self.assertIn('truncated',result);self.assertEqual(result.split('\nOutput:\n',1)[1],'abcd')
        with self.assertRaises(ValueError):exchange._limits({'max_tokens':True},'max_tokens')

    def test_later_historical_request_is_not_a_fresh_tool_session(self):
        payload=json.loads(self.payload);payload['input']=[{'type':'reasoning','encrypted_content':'old'}]
        self.payload=ops.encoded(payload).encode();host,version,capture=self.host()
        result=self.run_exchange(host,version,capture,'old-prefix')['outcome']['measurement']
        self.assertEqual(result['category'],'preflight_rejected');self.assertEqual(self.server.requests,[])

    def test_continuation_receipt_does_not_require_a_measure(self):
        if not Path(kernel.store_bin()).exists():self.skipTest('set qualified native digest binary')
        host,version,capture=self.host();reader=receipts.ReceiptReader(host)
        cp=reader.checkpoint(capture);self.run_exchange(host,version,capture,'unmeasured')
        child=reader.continuation(cp,'unmeasured')
        self.assertEqual(reader.read(child,'continuation_receipt')['execution'],'unmeasured')

    def test_unknown_validity_is_not_projected_to_false(self):
        if not Path(kernel.store_bin()).exists():self.skipTest('set qualified native digest binary')
        payload=json.loads(self.payload);payload['text']['format']['schema']={'$ref':'#/$defs/missing'}
        self.payload=ops.encoded(payload).encode();host,version,capture=self.host(measured=True)
        result=self.run_exchange(host,version,capture,'unresolved-schema')['outcome']['measurement']
        self.assertEqual(result['category'],'exchange_error');self.assertIsNotNone(result['decision'])
        host.submit('measure-unknown','research',version,'measure',{'source':'unresolved-schema'},'unknown is not false')
        host.run_worker('measure-unknown')
        self.assertIsNone(host.inspect('measure-unknown')['outcome']['measurement'])
        with self.assertRaisesRegex(ValueError,'not an admitted reliability projection'):
            receipts.ReceiptReader(host).measurement('measure-unknown')

    def test_environment_change_does_not_reset_the_diagnostic_pool(self):
        if not Path(kernel.store_bin()).exists():self.skipTest('set qualified native digest binary')
        config=exchange.profile(self.root/'h','http://localhost/',self.binary,self.bin_sha,
            parent_measurement='p',max_requests=2)
        first=receipts.registration(config);config['max_requests']=3;second=receipts.registration(config)
        self.assertEqual(first['pool'],second['pool']);self.assertNotEqual(first['environment'],second['environment'])

    def test_real_host_receipts_bind_actual_parent_children_and_measurements(self):
        if not Path(kernel.store_bin()).exists():self.skipTest('set qualified native digest binary')
        host,version,capture=self.host(measured=True);reader=receipts.ReceiptReader(host)
        checkpoint=reader.checkpoint(capture);continued=[];measured=[]
        for side in ('left','right'):
            self.run_exchange(host,version,capture,side)
            continued.append(reader.continuation(checkpoint,side))
            host.submit('measure-'+side,'research',version,'measure',{'source':side},'registered reliability projection')
            host.run_worker('measure-'+side)
            measured.append(reader.measurement('measure-'+side))
        cp=reader.read(checkpoint,'checkpoint_receipt')
        cs=[reader.read(i,'continuation_receipt')for i in continued]
        ms=[reader.read(i,'measurement_receipt')for i in measured]
        config=json.loads((self.root/'profile.json').read_text());registration=config['reliability_measure']
        paired=fork.measured(cp,*cs,ms,registration,())
        self.assertEqual(paired['differs'],{})
        self.assertEqual(paired['outcomes'],[{'cases':{receipts.CASE:True}}]*2)
        record=self.body['boundary']['contract']
        record=record['id'] if isinstance(record,dict) else record
        self.assertEqual(cp['analysis_unit'],{'id':record,'family':'native-responsibility'})
        self.assertEqual(len({cp['allocation'],cs[0]['allocation'],cs[1]['allocation']}),3)
        self.assertEqual({m['execution']for m in ms},{'measure-left','measure-right'})
        self.assertEqual(set(paired['not_shared']),set(fork.COMPONENTS)-{'context','evidence'})
        # Immutable objects alone are not enough: the reader rederives host facts.
        for kind,original,edit in (
            ('checkpoint_receipt',cp,lambda b:b['analysis_unit'].update(id='alias')),
            ('continuation_receipt',cs[0],lambda b:b['costs'].update(http_attempts=999)),
            ('measurement_receipt',ms[0],lambda b:b['outcome']['cases'].update({receipts.CASE:False}))):
            changed=json.loads(ops.encoded(original));edit(changed)
            forged=host.put(kind,changed,notify=False)
            with self.assertRaisesRegex(ValueError,'differs from actual host facts'):reader.read(forged,kind)
        ops.atomic(self.root/'RECEIPT_READBACK.json',{'checkpoint':checkpoint,'continuations':continued,
            'measurements':measured,'pair':paired,'scope':'synthetic model; real Operations/code-mode/projection executions'})


if __name__=='__main__':unittest.main()
