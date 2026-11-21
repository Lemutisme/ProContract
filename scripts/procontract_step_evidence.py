"""Retain and page public native step events, independently of final messages."""
import base64
import hashlib
import json


def retain_steps(host, run, thread, operation=None):
    path = run / 'rpc.jsonl'
    if thread is None or not path.exists():
        return None
    target = run / 'steps.jsonl'
    count = malformed = 0
    raw_hash = hashlib.sha256()
    with path.open('rb') as incoming, target.open('xb') as outgoing:
        for line_number, line in enumerate(incoming, 1):
            raw_hash.update(line)
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                malformed += 1
                continue
            if not isinstance(event,dict) or not isinstance(event.get('params',{}),dict):
                malformed += 1
                continue
            params = event.get('params', {})
            item = params.get('item', {})
            if not isinstance(item,dict):
                malformed += 1
                continue
            if (event.get('method') not in ('item/started', 'item/completed')
                    or params.get('threadId') != thread
                    or item.get('type') in ('reasoning', 'agentMessage', 'userMessage')
                    or not item.get('id')):
                continue
            record = {'index': count, 'step': item['id'], 'type': item.get('type'),
                      'event': event['method'], 'thread': thread, 'turn': params.get('turnId'),
                      'raw_line': line_number, 'raw_line_sha256': hashlib.sha256(line).hexdigest(),
                      'item': item}
            outgoing.write((json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n').encode())
            count += 1
    with target.open('rb') as stream:
        digest = hashlib.file_digest(stream,'sha256').hexdigest()
    arguments={'expected_sha256':digest,'label':'native public steps:'+run.name,
               'media_type':'application/x-ndjson','origin':{'raw_rpc_sha256':raw_hash.hexdigest(),'thread':thread}}
    source = (host.capture_return_source(operation,target,**arguments) if operation is not None
              else host.capture_source(target,**arguments))
    return {'source': source, 'sha256': digest, 'records': count, 'thread': thread,
            'raw_rpc_sha256': raw_hash.hexdigest(), 'malformed_lines': malformed,
            'scope': 'Public item start/completion events for this native thread. Completion is not success; partial output and exit status retain their recorded meaning.'}


def view_steps(host, descriptor, *, after=0, limit=20, record=None, field=None, offset=0, length=1024):
    if (type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100
            or type(offset) is not int or offset < 0 or type(length) is not int or not 1 <= length <= 65536):
        raise ValueError('invalid step page or byte range')
    if record is not None and (type(record) is not int or record < 0):
        raise ValueError('record must be a nonnegative index')
    if field is not None and (not isinstance(field, list) or not all(isinstance(key, str) for key in field)):
        raise ValueError('field must be a list of object keys')
    if record is None and field is not None:
        raise ValueError('field needs an exact record')
    if not descriptor:
        return {'available': False, 'reason': 'This outcome has no retained step trace; absence does not establish that no tool ran.'}
    source = host.get(descriptor['source'], 'source')
    if source['sha256'] != descriptor['sha256']:
        raise ValueError('step source identity differs')
    path = host.root / 'sources' / source['sha256']
    entries = []
    selected = None
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for line in stream:
            digest.update(line)
            item = json.loads(line)
            index = item['index']
            if record is not None and index == record:
                selected = item
            elif record is None and index >= after and len(entries) <= limit:
                entries.append({key: item.get(key) for key in ('index', 'step', 'type', 'event', 'turn')})
    if digest.hexdigest() != descriptor['sha256']:
        raise ValueError('retained step bytes changed')
    binding = {'available': True, 'trace': descriptor['source'], 'sha256': descriptor['sha256'],
               'records': descriptor['records'], 'scope': descriptor['scope']}
    if record is None:
        page = entries[:limit]
        return {**binding, 'entries': page, 'cursor': page[-1]['index'] + 1 if page else after,
                'more': len(entries) > limit}
    if selected is None:
        raise ValueError('step record is not in the retained trace')
    value = selected
    for key in field or []:
        if not isinstance(value, dict) or key not in value:
            raise ValueError('field is not in the selected step')
        value = value[key]
    raw = value.encode() if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
    if offset > len(raw):
        raise ValueError('offset exceeds step field')
    data = raw[offset:offset + length]
    result = {**binding, 'record': record, 'step': selected['step'], 'field': field or [],
              'bytes': len(raw), 'offset': offset, 'end': offset + len(data),
              'eof': offset + len(data) == len(raw), 'slice_sha256': hashlib.sha256(data).hexdigest()}
    try:
        result.update(encoding='utf-8', text=data.decode())
    except UnicodeDecodeError:
        result.update(encoding='base64', base64=base64.b64encode(data).decode())
    return result
