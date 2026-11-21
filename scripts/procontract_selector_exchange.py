"""Operations runner for a retained selector input plus a frozen evidence view.

Every provider request is single-shot. Returned tools can only read the frozen
view through a pinned code-mode host. The result is an observation, not adoption.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
from importlib.metadata import PackageNotFoundError, version as package_version
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

import procontract_operations as ops
import procontract_decision_replay as replay
import procontract_replay_transport as transport
from procontract_frozen_evidence import FrozenEvidence
from procontract_selector_tools import FrozenToolSession

SCHEMA = "selector-exchange-profile/v1"


def _validator_versions():
    found={}
    for name in ('jsonschema','referencing','attrs','rpds-py','pyrsistent','jsonschema-specifications'):
        try:found[name]=package_version(name)
        except PackageNotFoundError:found[name]=None
    return found


def implementation():
    import procontract_selector_native_source as native_source
    return ops.sha({"operations": ops.IMPLEMENTATION, "native_source": native_source.implementation(),
        "validator_packages":_validator_versions(), "sources": {
        name: ops.sha(Path(__file__).with_name(name).read_bytes()) for name in (
            "procontract_selector_exchange.py", "procontract_selector_tools.py",
            "procontract_selector_native_source.py", "procontract_selector_capture.py",
            "procontract_frozen_evidence.py", "procontract_decision_replay.py", "procontract_replay_transport.py")}})


def profile(host_root, endpoint, code_mode_host, code_mode_sha256, *, parent_measurement,
            max_requests, auth_env=None, model="gpt-6-luna", effort="max", max_reads=None):
    return {"schema": SCHEMA, "implementation": implementation(), "host_root": str(Path(host_root).resolve()),
            "endpoint": endpoint, "code_mode_host": str(Path(code_mode_host).resolve()),
            "code_mode_sha256": code_mode_sha256, "parent_measurement": parent_measurement,
            "max_requests": max_requests, "max_reads": max_reads, "auth_env": auth_env,
            "model": model, "effort": effort}


def version_definition(config):
    return {"kind":"harness", "files":{}, "build":{"implementation":implementation()},
            "model":{"name":config['model'],"effort":config['effort']},
            "resolver":"selector-exchange/v1", "tools":{"code_mode_host":config['code_mode_sha256']}}


def _load(profile_path, assignment_path, *, measurement_identity=None):
    profile_path, assignment_path = Path(profile_path).resolve(), Path(assignment_path).resolve()
    profile_raw = profile_path.read_bytes()
    profile_sha256 = ops.sha(profile_raw)
    config = replay.strict_json(profile_raw)
    if config.get("schema") != SCHEMA or config.get("implementation") != implementation():
        raise ValueError("unqualified selector exchange implementation")
    assignment = replay.strict_json(assignment_path.read_bytes())
    request = assignment["request"]
    host = ops.Operations(config["host_root"])
    current = host.inspect(request["id"])
    expected = {**current["request"], "version": host.get(request["version"], "version")}
    if (ops.encoded(assignment) != ops.encoded(expected) or current["state"] != "running"
            or assignment_path != host.operation_dir(request["id"])/"assignment.json"):
        raise ValueError("not a running retained assignment")
    measurement = assignment["measurement"]
    sources = measurement.get("sources", {})
    if (measurement["identity"] != (measurement_identity or implementation()) or sources.get(str(profile_path)) != profile_sha256
            or any(ops.sha(Path(path).read_bytes()) != digest for path,digest in sources.items())):
        raise ValueError("registered runner/profile changed")
    if ops.encoded(assignment['version']['definition']) != ops.encoded(version_definition(config)):
        raise ValueError("this runner only executes its registered unchanged-input exchange version")
    config['_profile_ref']={'path':str(profile_path),'sha256':profile_sha256}
    return config, assignment, host


def _inputs(config, assignment, host):
    request = assignment["request"]
    if not isinstance(request['id'],str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',request['id']):
        raise ValueError('operation identity is not transport-safe')
    if set(request["inputs"]) != {"capture"}:
        raise ValueError("an exchange names one retained capture")
    captured = host.get(request["inputs"]["capture"], "selector_evidence_capture")
    if captured["schema"] != "selector-evidence-capture/v1":
        raise ValueError("unknown capture protocol")
    if 'origin' in captured:
        # The original native run was not an Operations invocation. Its typed
        # source keeps that distinction; an import operation cannot impersonate
        # the native producer or manufacture its old allocation/closure.
        import procontract_selector_native_source as native_source
        origin = native_source.resolve_capture(host, captured)
        if origin['parent_measurement'] != config['parent_measurement']:
            raise ValueError('native source validator differs from the registered capability')
    else:
        parent = host.inspect(captured["parent"])
        if (parent["state"] != "finished" or parent["outcome"]["shutdown_confirmed"] is not True
                or parent["request"]["request"]["measurement"] != config["parent_measurement"]
                or parent["outcome"]["object"] != captured["parent_outcome"]
                or ops.sha(parent["request"]) != captured["parent_assignment"]):
            raise ValueError("capture lacks its registered closed parent")
        bound = (parent["outcome"].get("measurement") or {}).get("selector_checkpoint")
        if ops.encoded(bound) != ops.encoded({"payload_sha256":captured["payload_sha256"],"boundary":captured["boundary"]}):
            raise ValueError("closed parent did not bind this capture")
    evidence = host.get(captured["evidence"], "frozen_public_evidence")
    if ops.encoded(evidence["boundary"]) != ops.encoded(captured["boundary"]):
        raise ValueError("evidence boundary differs from capture")
    view = FrozenEvidence(evidence)
    raw = base64.b64decode(captured["payload_base64"], validate=True)
    if ops.sha(raw) != captured["payload_sha256"]:
        raise ValueError("captured payload changed")
    payload = replay.strict_json(raw)
    if payload.get("model") != config["model"] or (payload.get("reasoning") or {}).get("effort") != config["effort"]:
        raise ValueError("captured model settings differ")
    if payload.get("previous_response_id") or payload.get("conversation") or payload.get("background"):
        raise ValueError("unretained remote state is unavailable")
    if not isinstance(payload.get("input"), list) or any(x.get("type")=="item_reference" for x in payload["input"]):
        raise ValueError("exchange needs a retained input array")
    if any(x.get('type') in ('reasoning','function_call','custom_tool_call','custom_tool_call_output')
           or (x.get('type')=='function_call_output' and x.get('call_id')) for x in payload['input']):
        raise ValueError('full exchange requires the initial request; historical code-mode state is not restored')
    def local_tools(definitions):
        for tool in definitions:
            if tool.get("type") == "namespace": local_tools(tool.get("tools", []))
            elif tool.get("type") not in ("function", "custom"):
                raise ValueError("provider-hosted tools are outside this exchange")
    local_tools(payload.get("tools", []))
    for item in payload["input"]:
        if item.get("type") == "additional_tools":local_tools(item.get("tools", []))
    maximum = config["max_requests"]
    if maximum is not None and (type(maximum) is not int or maximum < 1):
        raise ValueError("invalid new request allowance")
    url = urlsplit(config["endpoint"])
    if (url.scheme not in ("http","https") or not url.hostname or url.username or url.password or url.fragment
            or (url.scheme=="http" and url.hostname not in ("127.0.0.1","localhost","::1"))):
        raise ValueError("invalid provider endpoint")
    schema = payload["text"]["format"]["schema"]
    def local_schema(value):
        if isinstance(value,dict):
            for key,part in value.items():
                if key in ('$ref','$dynamicRef') and (not isinstance(part,str) or not part.startswith('#')):
                    raise ValueError('schema references must stay in the captured document')
                if key=='$id' and part:
                    raise ValueError('schema rebasing is outside this captured-document validator')
                local_schema(part)
        elif isinstance(value,list):
            for part in value:local_schema(part)
    local_schema(schema)
    Draft202012Validator.check_schema(schema)
    return captured, view, raw, payload, Draft202012Validator(schema)


def _runtime_content(response, *, max_tokens=10000, server_truncated=False, notifications=()):
    """Explicit bounded client representation of native code-mode observations.

    This formatter has its own implementation identity. It is not a claim of
    byte-identical rendering by an old Codex frontend.
    """
    if set(response) not in ({"Result"},{"Yielded"},{"Terminated"}):
        raise ValueError("unknown code-mode response")
    kind, value = next(iter(response.items()))
    items = value["content_items"]
    if any(item.get("type") != "input_text" for item in items):
        raise ValueError("nontext tool observations are outside this selector profile")
    text = "\n".join(item["text"] for item in items)
    if kind == "Yielded":header = "Script running with cell ID " + value["cell_id"]
    elif kind == "Terminated":header = "Script terminated"
    else:header = "Script failed" if value.get("error_text") is not None else "Script completed"
    if kind == "Result" and value.get("error_text") and not server_truncated:
        text += "\n" + value["error_text"]
    if notifications:
        text += '\n'+'\n'.join(f"Notification from cell {n['cellId']} (call {n['callId']}): {n['text']}" for n in notifications)
    if len(text.encode()) > 65536:
        raise ValueError("tool output exceeds the declared 64 KiB observation limit")
    # Legacy code-mode hosts leave budgeting to the client. Match the native
    # approximate 4-byte token convention; retain explicit control/omission text.
    if not server_truncated or notifications:
        budget=min(65536,max_tokens*4);raw=text.encode()
        if len(raw)>budget:
            text=raw[:budget].decode('utf-8','ignore')
            header+='\nOutput truncated by requested budget; remaining evidence was not delivered.'
    return [{"type":"input_text","text":header+"\nOutput:\n"+text}]


def _limits(args, token_key):
    limits={'yield_time_ms':args.get('yield_time_ms',10000),'max_output_tokens':args.get(token_key,10000)}
    if any(type(x) is not int or not 0<=x<2**64 for x in limits.values()):
        raise ValueError('tool observation limits must be unsigned integers')
    return limits


def _exec_source(source):
    if not isinstance(source,str) or not source.strip():raise ValueError('exec requires JavaScript source')
    first,separator,rest=source.partition('\n');prefix='// @exec:'
    if first.lstrip().startswith(prefix):
        args=replay.strict_json(first.lstrip()[len(prefix):].strip())
        if not isinstance(args,dict) or set(args)-{'yield_time_ms','max_output_tokens'} or not separator or not rest.strip():
            raise ValueError('invalid exec pragma')
        return rest,_limits(args,'max_output_tokens')
    return source,_limits({},'max_output_tokens')


def run(profile_path, assignment_path):
    config, assignment, host = _load(profile_path, assignment_path)
    directory = Path(assignment_path).resolve().parent
    with (directory/"exchange.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if (directory/"result.json").exists():return replay.strict_json((directory/"result.json").read_bytes())
        return _run(config,assignment,host,directory)


def _run(config, assignment, host, directory):
    execution = assignment["request"]["id"]
    allocation = ops.sha({k:v for k,v in assignment.items() if k!="version"})
    result = {"schema":"selector-exchange-observation/v1","admissible":False,"validity":"observation_only",
              "execution":execution,"allocation":allocation,"capture":assignment["request"]["inputs"].get("capture"),
              "calls":[],"decision":None,"valid_decision":False,"known_cost_usd":0,"calls_without_price":0}
    intent = directory/"exchange-intent.json"
    if intent.exists():
        result.update(category="interrupted_unreplayed",effects_resolved=False)
        ops.atomic(directory/"result.json",result);return result
    session = None
    remote_closed, local_closed = True, True
    try:
        captured, view, raw, payload, validator = _inputs(config,assignment,host)
        result['profile']=host.put('selector_exchange_profile',{
            **{key:config.get(key) for key in ('implementation','parent_measurement','model','effort',
                'code_mode_sha256','max_requests','max_reads','reliability_measure')},
            'source':config['_profile_ref']},source='selector-exchange',notify=False)
        headers = {"Content-Type":"application/json","Accept":"text/event-stream","X-Allocation-ID":allocation}
        auth_env = config.get("auth_env")
        if auth_env is not None:
            if not isinstance(auth_env,str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*",auth_env):raise ValueError("invalid credential source")
            key = os.environ.get(auth_env)
            if not key or '\r' in key or '\n' in key:raise ValueError("credential unavailable")
            headers["Authorization"]="Bearer "+key
        snapshot = directory/"frozen-evidence.json"
        ops.atomic(snapshot,view.body);snapshot.chmod(0o400)
        snapshot_sha=ops.sha(snapshot.read_bytes())
        result.update(payload_sha256=captured["payload_sha256"],evidence=captured["evidence"],
                      restored_evidence_sha256=snapshot_sha)
        if 'origin' in captured:
            result['source_origin'] = captured['origin']
        else:
            result.update(parent=captured['parent'], parent_assignment=captured['parent_assignment'])
        ops.atomic(intent,{"execution":execution,"allocation":allocation,"capture":result["capture"]})
        index = 0
        tool_ids={item['call_id'] for item in payload['input']
                  if item.get('type') in ('custom_tool_call','function_call') and item.get('call_id')}
        while config["max_requests"] is None or index < config["max_requests"]:
            if assignment["deadline"] is not None and time.time() >= assignment["deadline"]:
                result["category"]="allocation_exhausted";break
            call_dir=directory/f'call-{index:04d}';call_dir.mkdir()
            body=raw if index==0 else ops.encoded(payload).encode()
            if len(body) > 16 << 20:
                result['category']='request_bytes_exhausted';break
            ops.atomic(call_dir/'dispatch-intent.json',{"payload_sha256":ops.sha(body),"attempt":index})
            remote_closed=False
            received=transport.send(body,ops.sha(body),config['endpoint'],
                headers={**headers,"X-Execution-ID":f'{execution}:request:{index}'},
                execution_id=f'{execution}:request:{index}',allocation_id=allocation,
                deadline=assignment['deadline'],output_dir=call_dir/'transport')
            remote=received['remote'];local=received['local']
            remote_closed=(remote['terminal_observed'] is True or remote['state']=='not_reached')
            local_closed=local['socket_closed'] is True and local['watchdog_stopped'] is True
            trace=replay.retain_trace(host,call_dir,{k:v for k,v in assignment.items() if k!='version'},received)
            usage=(received.get('said') or {}).get('usage') or {}
            cost=usage.get('cost')
            if type(cost) in (int,float) and math.isfinite(cost) and cost>=0:result['known_cost_usd']+=cost
            else:result['calls_without_price']+=1
            result['calls'].append({'index':index,'trace':trace,'transport':received,
                                   'payload':replay.retain_bytes(host,'decision_request_bytes',body)})
            index+=1
            response=replay.provider_response(call_dir,received)
            if not remote_closed or not local_closed:
                result['category']='unresolved_request';break
            if not isinstance(response,dict) or response.get('status')!='completed':
                result['category']='provider_not_completed';break
            observed=replay.observation(response)
            if observed['category']!='tool_request':
                result['category']=observed['category'];result['decision']=observed.get('decision')
                result['valid_decision']=observed['category']=='structured_json' and validator.is_valid(result['decision'])
                break
            if session is None:
                session=FrozenToolSession(snapshot,snapshot_sha,config['code_mode_host'],config['code_mode_sha256'],
                    execution=execution,allocation=allocation,output_dir=directory/'tools',
                    deadline=assignment['deadline'],max_reads=config.get('max_reads'))
            outputs=[]
            for call in observed['calls']:
                name=call.get('name');call_id=call.get('call_id')
                if not isinstance(call_id,str) or not call_id:raise ValueError('tool call has no identity')
                if call_id in tool_ids:raise ValueError('duplicate tool call identity')
                tool_ids.add(call_id)
                if call['type']=='custom_tool_call' and name in ('exec','functions.exec'):
                    code,limits=_exec_source(call['input'])
                    runtime=session.execute(code,call_id,**limits)
                    content=_runtime_content(runtime,max_tokens=limits['max_output_tokens'],server_truncated=session.server_truncation,
                                             notifications=session.take_notifications());kind='custom_tool_call_output'
                elif call['type']=='function_call' and name in ('wait','functions.wait'):
                    args=replay.strict_json(call['arguments']);cell=args['cell_id']
                    if (set(args)-{'cell_id','terminate','yield_time_ms','max_tokens'}
                            or type(args.get('terminate',False)) is not bool):
                        raise ValueError('invalid wait arguments')
                    limits=_limits(args,'max_tokens')
                    runtime=session.wait(cell,terminate=args.get('terminate',False),**limits)
                    content=_runtime_content(next(iter(runtime.values())),max_tokens=limits['max_output_tokens'],
                        server_truncated=session.server_truncation,notifications=session.take_notifications());kind='function_call_output'
                else:
                    raise ValueError('unoffered selector tool requested')
                outputs.append({'type':kind,'call_id':call_id,'output':content})
            payload['input'].extend(response['output']);payload['input'].extend(outputs)
        else:
            result['category']='request_allowance_exhausted'
    except Exception as error:
        result.update(category='exchange_error' if intent.exists() else 'preflight_rejected',error_category=type(error).__name__)
    finally:
        if session is not None:
            try:session.close()
            except Exception:local_closed=False
        elif (directory/'tools').exists():
            # Constructor failure must have its own retained closure; absence
            # cannot attest that a started native host has stopped.
            try:
                tail=(directory/'tools/tools.jsonl').read_text().splitlines()[-1]
                local_closed &= replay.strict_json(tail).get('local_closed') is True
            except (OSError,IndexError,ValueError):local_closed=False
        result['effects_resolved']=remote_closed and local_closed
        result['http_attempts_reserved']=len(list(directory.glob('call-*/dispatch-intent.json')))
        result['tool_reads']=session.reads if session is not None else 0
        result['observation']=host.put('selector_exchange_result',{
            'capture':result['capture'],'category':result.get('category'),'decision':result['decision'],
            'valid_decision':result['valid_decision']},source='selector-exchange',notify=False)
        if (directory/'tools').exists():
            result['tools_trace']=host.put('selector_exchange_tools',{
                'assignment':allocation,'files':{p.name:replay.retain_bytes(host,'selector_tool_raw',p.read_bytes())
                    for p in (directory/'tools').iterdir() if p.is_file()}},source='selector-exchange',notify=False)
        ops.atomic(directory/'result.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--profile',required=True);parser.add_argument('assignment')
    args=parser.parse_args()
    try:
        result=run(args.profile,args.assignment)
        print(ops.encoded({k:result[k]for k in ('execution','category','http_attempts_reserved','valid_decision','effects_resolved')}))
    except Exception as error:
        print(ops.encoded({'error_category':type(error).__name__}));raise SystemExit(1) from None


if __name__=='__main__':main()
