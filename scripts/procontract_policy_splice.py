"""Pure byte-range replacement of a pinned selector strategy in retained JSON.

Packet identities/count and all bytes outside the strategy spans stay unchanged.
Earlier spans retain their original decoded-character capacities; the last
strategy span absorbs the length change. No native rechunking or whole-body dump.
"""
from __future__ import annotations

import hashlib
import json
import re

SCHEMA = "selector-policy-splice/v1"
sha = lambda raw: hashlib.sha256(raw).hexdigest()


def _json(raw):
    def pairs(items):
        found={}
        for key,value in items:
            if key in found:raise ValueError('duplicate JSON key')
            found[key]=value
        return found
    def invalid(_):raise ValueError('nonfinite JSON')
    value=json.loads(raw.decode('utf-8') if isinstance(raw,bytes) else raw,object_pairs_hook=pairs,parse_constant=invalid)
    # Validate finite values and Unicode scalars without using this rendering.
    json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
    return value


def _string_spans(raw):
    """Locate JSON string tokens by structural path; JSON validity is checked first."""
    found={}
    def space(i):
        while i<len(raw) and raw[i] in b' \r\n\t':i+=1
        return i
    def string(i):
        start=i;i+=1
        while raw[i]!=34:
            i+=2 if raw[i]==92 else 1
        return start,i+1
    def value(i,path):
        i=space(i);c=raw[i]
        if c==34:
            start,end=string(i);found[path]=(start,end);return end
        if c==123:
            i=space(i+1)
            if raw[i]==125:return i+1
            while True:
                start,end=string(i);key=json.loads(raw[start:end]);i=space(end)
                i=value(i+1,(*path,key));i=space(i)
                if raw[i]==125:return i+1
                i=space(i+1)
        if c==91:
            i=space(i+1);n=0
            if raw[i]==93:return i+1
            while True:
                i=value(i,(*path,n));n+=1;i=space(i)
                if raw[i]==93:return i+1
                i=space(i+1)
        while i<len(raw) and raw[i] not in b' \r\n\t,]}':i+=1
        return i
    end=space(value(0,()))
    if end!=len(raw):raise ValueError('trailing JSON bytes')
    return found


def _decoded_boundaries(raw,span):
    """Map Python Unicode-character boundaries to original escaped wire offsets."""
    start,end=span;i=start+1;boundaries=[i];characters=[]
    simple={34:'"',92:'\\',47:'/',98:'\b',102:'\f',110:'\n',114:'\r',116:'\t'}
    while i<end-1:
        if raw[i]==92:
            code=raw[i+1]
            if code!=117:
                characters.append(simple[code]);i+=2
            else:
                point=int(raw[i+2:i+6],16);i+=6
                if 0xD800<=point<=0xDBFF:
                    if raw[i:i+2]!=b'\\u':raise ValueError('unpaired surrogate')
                    low=int(raw[i+2:i+6],16)
                    if not 0xDC00<=low<=0xDFFF:raise ValueError('unpaired surrogate')
                    point=0x10000+((point-0xD800)<<10)+(low-0xDC00);i+=6
                elif 0xDC00<=point<=0xDFFF:raise ValueError('unpaired surrogate')
                characters.append(chr(point))
        else:
            first=raw[i]
            size=1 if first<128 else 2 if first<224 else 3 if first<240 else 4
            characters.append(raw[i:i+size].decode('utf-8'));i+=size
        boundaries.append(i)
    if ''.join(characters)!=json.loads(raw[start:end]):raise ValueError('string boundary decoding differs')
    return boundaries


def _packets(document):
    if not isinstance(document,dict) or not isinstance(document.get('input'),list):
        raise ValueError('expected a retained request input array')
    packets=[];styles=set()
    for i,item in enumerate(document['input']):
        if not isinstance(item,dict):continue
        name=item.get('name')
        match=re.fullmatch(r'pro_contract_packet_(\d+)',name) if isinstance(name,str) else None
        if match:
            if item.get('type')!='function_call_output' or item.get('call_id') is not None or not isinstance(item.get('output'),str):
                raise ValueError('packet is not a retained context fragment')
            packets.append((int(match[1]),('input',i,'output'),0,item['output'],item['name']))
            styles.add('named_context')
        elif item.get('type')=='message' and isinstance(item.get('content'),list):
            for j,part in enumerate(item.get('content',[])):
                if not isinstance(part,dict) or not isinstance(part.get('text'),str):continue
                match=re.fullmatch(r'(<(external_pro_contract_packet_(\d+))>)(.*)</\2>',part['text'],re.S)
                if match:
                    packets.append((int(match[3]),('input',i,'content',j,'text'),len(match[1]),match[4],match[2]))
                    styles.add('external_tag')
    if not packets or len(styles)!=1 or [p[0]for p in packets]!=list(range(len(packets))):
        raise ValueError('packet sequence is missing, ambiguous, mixed or out of order')
    return packets,styles.pop()


def locate(payload,expected_sha256,policy):
    """Return exact decoded/wire policy spans after checking the source and prefix."""
    if not isinstance(payload,bytes) or sha(payload)!=expected_sha256:
        raise ValueError('retained payload digest differs')
    if not isinstance(policy,bytes):raise ValueError('policy must be pinned UTF-8 bytes')
    policy_text=policy.decode('utf-8')
    document=_json(payload);packets,style=_packets(document)
    joined=''.join(p[3]for p in packets)
    if not joined.startswith(policy_text+'\n\n'):
        raise ValueError('pinned policy is not the complete packet prefix')
    facts_text=joined[len(policy_text)+2:];facts=_json(facts_text)
    if not isinstance(facts,dict) or not {'contract_id','subject_hash','available_actions'}<=facts.keys():
        raise ValueError('strategy suffix is not the complete selector facts object')
    spans=_string_spans(payload);offset=0;ranges=[]
    for _,path,start,content,name in packets:
        take=min(len(content),max(0,len(policy_text)-offset))
        if take:
            boundaries=_decoded_boundaries(payload,spans[path])
            ranges.append({'path':list(path),'name':name,'chars':[start,start+take],
                           'wire':[boundaries[start],boundaries[start+take]],'characters':take})
        offset+=len(content)
    if policy_text and not ranges:raise ValueError('policy has no located spans')
    return {'style':style,'ranges':ranges,'policy_sha256':sha(policy),'policy_bytes':len(policy),
            'facts_sha256':sha(facts_text.encode()),'contract_id':facts['contract_id'],
            'subject_hash':facts['subject_hash'],'packet_count':len(packets)}


def splice(payload,expected_sha256,old_policy,new_policy):
    """Return (new bytes, receipt); no files, models, tools or state are touched."""
    if not isinstance(new_policy,bytes):raise ValueError('replacement policy must be UTF-8 bytes')
    new_text=new_policy.decode('utf-8');old_text=old_policy.decode('utf-8')
    before=locate(payload,expected_sha256,old_policy)
    if not before['ranges']:raise ValueError('an empty source policy has no owned replacement span')
    pieces=[];edits=[];cursor=0;used=0;old_used=0;after_offset=0
    for n,part in enumerate(before['ranges']):
        start,end=part['wire'];count=part['characters']
        replacement=new_text[used:] if n==len(before['ranges'])-1 else new_text[used:used+count]
        old_piece=old_text[old_used:old_used+count]
        # Identity replacements preserve the original escape spelling exactly.
        inserted=payload[start:end] if replacement==old_piece else json.dumps(replacement,ensure_ascii=False)[1:-1].encode()
        unchanged=payload[cursor:start];pieces.extend((unchanged,inserted));after_offset+=len(unchanged)
        edits.append({**part,'new_wire':[after_offset,after_offset+len(inserted)],
            'before_sha256':sha(payload[start:end]),'after_sha256':sha(inserted),
            'unchanged_prefix_sha256':sha(unchanged),'new_characters':len(replacement)})
        after_offset+=len(inserted);cursor=end;used+=len(replacement);old_used+=count
    if used!=len(new_text):raise ValueError('replacement did not fit declared span allocation')
    tail=payload[cursor:];pieces.append(tail);result=b''.join(pieces)
    after=locate(result,sha(result),new_policy)
    if (before['facts_sha256']!=after['facts_sha256'] or before['packet_count']!=after['packet_count']
            or before['style']!=after['style']):raise ValueError('splice changed frozen facts or packet structure')
    # All non-policy raw bytes were copied, including keys, IDs, schema, history,
    # resource snapshots, JSON number spellings and the original trailing bytes.
    return result,{'schema':SCHEMA,'source_sha256':expected_sha256,'result_sha256':sha(result),
        'old_policy_sha256':sha(old_policy),'new_policy_sha256':sha(new_policy),
        'old_policy_bytes':len(old_policy),'new_policy_bytes':len(new_policy),
        'wire_byte_delta':len(result)-len(payload),'identity':result==payload,
        'mode':'keep packets; original earlier span capacities; final strategy span absorbs delta',
        'style':before['style'],'facts_sha256':before['facts_sha256'],
        'contract_id':before['contract_id'],'subject_hash':before['subject_hash'],
        'edits':edits,'unchanged_tail_sha256':sha(tail)}
