"""Host-produced, rederived fork receipts for the selector-exchange capability.

The built-in measure is operational reliability: did this closed exchange return
a schema-valid decision? It is not task quality, a noise qualification or adoption.
"""
import argparse
import base64
from pathlib import Path
import sys

import procontract_operations as ops
import procontract_credit as credit
import procontract_fork as fork
import procontract_selector_exchange as exchange
from procontract_frozen_evidence import FrozenEvidence

PROTOCOL='operations-fork/v1'
CASE='valid_structured_decision'


def implementation():
    return ops.sha({'source':ops.sha(Path(__file__).read_bytes()),'exchange':exchange.implementation()})


def registration(profile):
    environment={k:profile[k]for k in ('implementation','model','effort','code_mode_sha256','max_requests','max_reads')}
    # A new runtime/method must not silently mint a fresh pool of the same cases.
    return {'pool':ops.sha({'scope':'readable selector operational diagnostic states/v1'}),
            'evaluator':implementation(),'environment':ops.sha(environment),
            'cases':credit.case_set([CASE]),'total':1}


def _closed(host,identity):
    item=host.inspect(identity)
    if item['state']!='finished' or (item.get('outcome') or {}).get('shutdown_confirmed') is not True:
        raise ValueError('operation has not closed')
    outcome=host.get(item['outcome']['object'],'operation_outcome')
    if outcome['assignment']!=ops.sha(item['request']):raise ValueError('outcome belongs to another assignment')
    closure={'completed':True,'shutdown_confirmed':True,'execution_state':outcome['execution'],
             'exit_code':outcome.get('exit_code')}
    return item,outcome,closure


def _capture(host,identity):
    capture=host.get(identity,'selector_evidence_capture')
    if capture.get('schema')!='selector-evidence-capture/v1':raise ValueError('unknown selector capture')
    native = None
    if 'origin' in capture:
        import procontract_selector_native_source as native_source
        native = native_source.resolve_capture(host, capture)
        closure = native['closure']
    else:
        parent,outcome,closure=_closed(host,capture['parent'])
        if (capture['parent_assignment']!=outcome['assignment'] or capture['parent_outcome']!=parent['outcome']['object']
                or ops.encoded((outcome.get('measurement')or{}).get('selector_checkpoint'))!=ops.encoded(
                    {'payload_sha256':capture['payload_sha256'],'boundary':capture['boundary']})):
            raise ValueError('capture is not bound to its actual closed parent')
    raw=base64.b64decode(capture['payload_base64'],validate=True)
    evidence=host.get(capture['evidence'],'frozen_public_evidence');FrozenEvidence(evidence)
    if (ops.sha(raw)!=capture['payload_sha256'] or ops.encoded(evidence['boundary'])!=ops.encoded(capture['boundary'])
            or ops.sha((ops.encoded(evidence)+'\n').encode())!=capture['evidence_file_sha256']):
        raise ValueError('captured component differs')
    record=capture['boundary']['contract']
    record=record.get('id') if isinstance(record,dict) else record
    if not isinstance(record,str) or not record:raise ValueError('native responsibility identity is missing')
    result = {'capture': capture, 'closure': closure, 'record': record}
    if native is not None:
        if record != native['record']:raise ValueError('native responsibility differs from archive')
        result.update(execution=native['execution'], allocation=native['allocation'],
                      parent_measurement=native['parent_measurement'], source_origin=native['origin'])
    else:
        result.update(execution=capture['parent'], allocation=outcome['assignment'],
                      parent_measurement=parent['request']['request']['measurement'])
    return result


def _source(host,identity,*,require_measure=False):
    item,outcome,closure=_closed(host,identity);result=outcome.get('measurement') or {}
    if result.get('schema')!='selector-exchange-observation/v1':raise ValueError('not a selector exchange outcome')
    if item['request']['measurement']['identity']!=exchange.implementation():raise ValueError('exchange implementation changed')
    if result.get('effects_resolved') is not True or type(result.get('valid_decision')) is not bool:
        raise ValueError('unresolved exchange does not have a reliability outcome')
    if result.get('allocation')!=outcome['assignment'] or result.get('execution')!=identity:
        raise ValueError('exchange result belongs to another assignment')
    profile=host.get(result['profile'],'selector_exchange_profile')
    source=profile['source']
    if (item['request']['measurement']['sources'].get(source['path'])!=source['sha256']
            or profile['implementation']!=exchange.implementation()):
        raise ValueError('profile was not pinned by the admitted exchange')
    if require_measure and ops.encoded(profile.get('reliability_measure'))!=ops.encoded(registration(profile)):
        raise ValueError('this reliability measure was not registered before the exchange')
    origin=_capture(host,result['capture']);capture=origin['capture']
    if (origin['parent_measurement']!=profile['parent_measurement']
            or result['payload_sha256']!=capture['payload_sha256']
            or result['restored_evidence_sha256']!=capture['evidence_file_sha256']):
        raise ValueError('exchange did not restore its captured components')
    if 'origin' in capture and ops.encoded(result.get('source_origin')) != ops.encoded(capture['origin']):
        raise ValueError('exchange lost or changed its native source origin')
    subject=host.get(result['observation'],'selector_exchange_result')
    if ops.encoded(subject)!=ops.encoded({k:result[k]for k in ('capture','category','decision','valid_decision')}):
        raise ValueError('result subject differs from the exchange outcome')
    if require_measure and result['decision'] is not None:
        payload=exchange.replay.strict_json(base64.b64decode(capture['payload_base64'],validate=True))
        try:valid=exchange.Draft202012Validator(payload['text']['format']['schema']).is_valid(result['decision'])
        except Exception as error:raise ValueError('decision validity is unknown; no Boolean outcome') from error
        if valid is not result['valid_decision']:raise ValueError('validity differs from the registered projection')
    return item,outcome,closure,result,profile


class ReceiptReader:
    """Protected host capability. Caller supplies object IDs, never receipt bodies."""
    def __init__(self,host):self.host=host

    def _checkpoint(self,capture_id):
        origin=_capture(self.host,capture_id);capture=origin['capture'];record=origin['record']
        components={'context':capture['payload_sha256'],'evidence':capture['evidence_file_sha256']}
        state=ops.sha({'record':record,'captured':components})
        body = {'schema':PROTOCOL,'record':record,'subject':state,'state':state,
                'analysis_unit':{'id':record,'family':'native-responsibility'},
                'execution':origin['execution'],'allocation':origin['allocation'],
                'captured':components,'omitted':{name:'Not restored by the selector-only capability'
                    for name in fork.COMPONENTS if name not in components},'closure':origin['closure'],
                '_binding':{'capture':capture_id},'scope':'selector context and public evidence only'}
        if 'source_origin' in origin:body['source_origin']=origin['source_origin']
        return body

    def checkpoint(self,capture_id):
        body=self._checkpoint(capture_id);fork.checkpoint(body)
        return self.host.put('checkpoint_receipt',body,source='selector-host',notify=False)

    def _continuation(self,checkpoint_id,operation):
        parent=self.read(checkpoint_id,'checkpoint_receipt')
        item,outcome,closure,result,profile=_source(self.host,operation)
        if result['capture']!=parent['_binding']['capture']:raise ValueError('continuation belongs to another checkpoint')
        costs={'http_attempts':result['http_attempts_reserved'],'wall_seconds':outcome['elapsed_seconds']}
        if result['calls_without_price']==0:costs['provider_reported_usd']=result['known_cost_usd']
        inputs={'version':item['request']['request']['version'],**parent['captured'],
                'exchange_implementation':profile['implementation'],'model':profile['model'],'effort':profile['effort'],
                'code_mode_host':profile['code_mode_sha256'],'requests':profile['max_requests'],
                'reads':profile['max_reads'],'requested_seconds':item['request']['request']['seconds']}
        return {'schema':PROTOCOL,'parent':fork.checkpoint(parent),'execution':operation,'allocation':outcome['assignment'],
                'resolved_inputs':inputs,'restored':parent['captured'],'omitted':parent['omitted'],
                'closure':closure,'costs':costs,'result':result['observation'],
                '_binding':{'checkpoint':checkpoint_id,'operation':operation},'source_outcome':item['outcome']['object']}

    def continuation(self,checkpoint_id,operation):
        body=self._continuation(checkpoint_id,operation);fork.continuation(body)
        return self.host.put('continuation_receipt',body,source='selector-host',notify=False)

    def _measurement(self,operation):
        item,outcome,closure=_closed(self.host,operation);result=outcome.get('measurement')or{}
        if (item['request']['measurement']['identity']!=implementation()
                or result.get('schema')!='selector-reliability-measurement/v1' or result.get('admissible') is not True):
            raise ValueError('not an admitted reliability projection')
        source,_,_,observed,profile=_source(self.host,result['source'],require_measure=True)
        measure=registration(profile);expected={'cases':{CASE:observed['valid_decision']}}
        if (item['request']['request']['inputs']!={'source':result['source']}
                or item['request']['request']['version']!=source['request']['request']['version']
                or result['subject']!=observed['observation'] or ops.encoded(result['registration'])!=ops.encoded(measure)
                or ops.encoded(result['outcome'])!=ops.encoded(expected)):
            raise ValueError('measurement does not match its actual source and registration')
        return {'schema':PROTOCOL,'subject':observed['observation'],'execution':operation,
                'measure':credit.measure_id(measure),'evaluator':measure['evaluator'],'environment':measure['environment'],
                'cases':measure['cases'],'closure':closure,'admissible':True,'outcome':expected,
                '_binding':{'operation':operation},'source_outcome':item['outcome']['object'],
                'scope':'operational reliability only; not task quality'}

    def measurement(self,operation):
        body=self._measurement(operation)
        return self.host.put('measurement_receipt',body,source='selector-host',notify=False)

    def read(self,object_id,kind):
        body=self.host.get(object_id,kind);binding=body.get('_binding')or{}
        if kind=='checkpoint_receipt':expected=self._checkpoint(binding['capture'])
        elif kind=='continuation_receipt':expected=self._continuation(binding['checkpoint'],binding['operation'])
        elif kind=='measurement_receipt':expected=self._measurement(binding['operation'])
        else:raise ValueError('unsupported receipt kind')
        if ops.encoded(body)!=ops.encoded(expected):raise ValueError('receipt differs from actual host facts')
        return body


def measure(profile_path,assignment_path):
    config,assignment,host=exchange._load(profile_path,assignment_path,measurement_identity=implementation())
    inputs=assignment['request']['inputs']
    if set(inputs)!={'source'}:raise ValueError('measurement names one source exchange')
    source,_,_,observed,profile=_source(host,inputs['source'],require_measure=True)
    if (profile['source']!=config['_profile_ref']
            or source['request']['request']['version']!=assignment['request']['version']):
        raise ValueError('measurement profile/version differs from source')
    result={'schema':'selector-reliability-measurement/v1','source':inputs['source'],
            'subject':observed['observation'],'registration':registration(profile),
            'outcome':{'cases':{CASE:observed['valid_decision']}},'effects_resolved':True,
            'validity':'valid','admissible':True,'scope':'operational reliability, not task quality'}
    ops.atomic(Path(assignment_path).parent/'result.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--profile',required=True);parser.add_argument('assignment')
    args=parser.parse_args()
    try:
        result=measure(args.profile,args.assignment);print(ops.encoded({'subject':result['subject'],'outcome':result['outcome']}))
    except Exception as error:
        print(ops.encoded({'error_category':type(error).__name__}));sys.exit(1)


if __name__=='__main__':main()
