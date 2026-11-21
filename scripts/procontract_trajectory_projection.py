"""Source-addressed observable native history for later research.

This projection assigns no credit. Original archives, actual delivery and causal
interpretations remain distinct. Opaque reasoning and raw request histories are
not copied into this readable view.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import tempfile


def file_sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def project(run):
    run=Path(run).resolve()
    target=run/'trajectory-observations.jsonl'
    index_path=run/'trajectory-index.json'
    sources=[];total=0;invalid=0
    paths=sorted((run/'codex-home/sessions').rglob('rollout-*.jsonl*'))
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=run,prefix='.trajectory-',delete=False) as output:
        temporary=Path(output.name)
        try:
            for path in paths:
                if path.suffix not in ('.jsonl','.gz') or not path.is_file() or path.is_symlink():continue
                before=path.stat();digest=file_sha(path);thread=None;offset=0;count=0;bad=[]
                opener=gzip.open if path.suffix=='.gz' else open
                with opener(path,'rb') as stream:
                    for line,raw in enumerate(stream,1):
                        start=offset;offset+=len(raw)
                        try:record=json.loads(raw)
                        except (ValueError,UnicodeError):
                            bad.append({'line':line,'uncompressed_offset':start,'sha256':hashlib.sha256(raw).hexdigest()});continue
                        if not isinstance(record,dict) or not isinstance(record.get('payload',{}),dict):
                            bad.append({'line':line,'uncompressed_offset':start,'sha256':hashlib.sha256(raw).hexdigest()});continue
                        kind=record.get('type');payload=record.get('payload',{})
                        if kind=='session_meta':
                            thread=payload.get('id')
                            payload={k:payload[k] for k in ('id','session_id','source','originator','timestamp') if k in payload}
                        elif kind=='response_item':
                            if payload.get('type')=='reasoning':continue
                        elif kind=='event_msg':
                            if payload.get('type') not in ('item_started','item_completed','task_complete','turn_aborted','turn_complete','error'):continue
                        elif kind not in ('compacted','context_window_reset'):continue
                        row={'source_file':str(path.relative_to(run)),'source_sha256':digest,'line':line,
                             'uncompressed_offset':start,'line_sha256':hashlib.sha256(raw).hexdigest(),
                             'thread':thread,'timestamp':record.get('timestamp'),'record_type':kind,'payload':payload}
                        output.write(json.dumps(row,ensure_ascii=False)+'\n');count+=1
                after=path.stat()
                if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns) or file_sha(path)!=digest:
                    raise ValueError('native source changed during projection: '+str(path))
                sources.append({'path':str(path.relative_to(run)),'sha256':digest,'stored_bytes':before.st_size,
                                'thread':thread,'observable_records':count,'invalid_records':bad})
                total+=count;invalid+=len(bad)
            output.flush()
        except BaseException:
            temporary.unlink(missing_ok=True);raise
    temporary.replace(target)
    index={'schema':'procontract-observable-trajectory-v1','run':str(run),'sources':sources,
           'projection':target.name,'projection_sha256':file_sha(target),'observable_records':total,
           'invalid_records':invalid,'available':bool(sources),
           'scope':'Observable native records with exact source/line identities; no inferred roles, causal edges, confidence or credit. Offsets refer to decompressed source bytes. Sources may later move into the run retention archive.'}
    temporary=index_path.with_suffix('.tmp');temporary.write_text(json.dumps(index,indent=2)+'\n');temporary.replace(index_path)
    return index


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('run',type=Path)
    result=project(parser.parse_args().run)
    print(json.dumps({k:result[k] for k in ('available','observable_records','invalid_records','projection_sha256')}))
