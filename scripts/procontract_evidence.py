"""Content custody and bounded evidence views; no interpretation or update policy."""
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid


def digest_file(stream):
    digest=hashlib.sha256()
    for block in iter(lambda:stream.read(1<<20),b''):digest.update(block)
    return digest.hexdigest()


class Evidence:
    def __init__(self, host):
        self.host=host
        self.directory=host.root/'sources'

    def capture(self, path, *, expected_sha256, label, media_type='text/plain', origin=None, finalizing=None):
        """Provisioner-only import. Actors receive IDs, never a host-path reader."""
        if finalizing is None:
            self.host.allowed()
        else:
            # Retaining an admitted operation's return is not a new execution.
            # This host-only path remains available after expiry/cancellation;
            # it is never offered on the actor capability socket.
            operation=self.host.inspect(finalizing)
            assignment=operation['request']
            origin={'operation':finalizing,'assignment':self.host_hash(assignment),
                    'version':assignment['request']['version'],'source_origin':origin}
        path=Path(path)
        self.directory.mkdir(exist_ok=True,mode=0o700)
        temporary=self.directory/(uuid.uuid4().hex+'.partial')
        try:
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
            with os.fdopen(fd,'rb') as source,temporary.open('xb') as target:
                before=os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode):raise ValueError('source must be a regular file')
                digest=hashlib.sha256();size=0
                for block in iter(lambda:source.read(1<<20),b''):
                    digest.update(block);target.write(block);size+=len(block)
                after=os.fstat(source.fileno())
                if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                    raise ValueError('source changed during capture')
                identity=digest.hexdigest()
                if identity!=expected_sha256:raise ValueError('source identity differs')
                target.flush();os.fsync(target.fileno())
            destination=self.directory/identity
            if destination.exists():
                with destination.open('rb') as source:
                    if digest_file(source)!=identity:raise ValueError('retained source identity differs')
                temporary.unlink()
            else:
                temporary.chmod(0o400);temporary.replace(destination)
                fd=os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(fd)
                finally:os.close(fd)
            return self.host.put('source',{'sha256':identity,'bytes':size,'media_type':media_type,
                'label':label,'origin':str(path.resolve()) if origin is None else origin})
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def host_hash(value):
        raw=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
        return hashlib.sha256(raw).hexdigest()

    def read(self, identity, offset=0, length=8192):
        if type(offset) is not int or offset<0 or type(length) is not int or not 1<=length<=65536:
            raise ValueError('source view needs a nonnegative offset and 1..65536 bytes')
        descriptor=self.host.get(identity,'source')
        digest=descriptor.get('sha256')
        if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('invalid retained source digest')
        path=self.directory/descriptor['sha256']
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:
            before=os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size!=descriptor['bytes']:
                raise ValueError('retained source size/type differs')
            stamp=(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)
            with self.host._source_lock:
                if self.host._source_verified.get(descriptor['sha256'])!=stamp:
                    if digest_file(stream)!=descriptor['sha256']:raise ValueError('retained source identity differs')
                    verified=os.fstat(stream.fileno())
                    if (verified.st_dev,verified.st_ino,verified.st_size,verified.st_mtime_ns,verified.st_ctime_ns)!=stamp:
                        raise ValueError('source changed during verification')
                    self.host._source_verified[descriptor['sha256']]=stamp
            if offset>before.st_size:raise ValueError('offset exceeds source')
            stream.seek(offset);data=stream.read(length);after=os.fstat(stream.fileno())
            if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                with self.host._source_lock:self.host._source_verified.pop(descriptor['sha256'],None)
                raise ValueError('source changed during view')
        result={'source':identity,'sha256':descriptor['sha256'],'offset':offset,'end':offset+len(data),
            'bytes':descriptor['bytes'],'eof':offset+len(data)==descriptor['bytes'],
            'slice_sha256':hashlib.sha256(data).hexdigest(),'base64':base64.b64encode(data).decode()}
        try:result['text']=data.decode('utf-8')
        except UnicodeDecodeError:result['text']=None
        return result

    def object_view(self, identity, field=None, offset=0, length=1024):
        """Bounded projection of an immutable object's selected field."""
        if field is not None and (not isinstance(field,list) or not all(isinstance(key,str) for key in field)):
            raise ValueError('field must be a list of object keys')
        if type(offset) is not int or offset<0 or type(length) is not int or not 1<=length<=65536:
            raise ValueError('invalid object view byte range')
        value=self.host.get(identity)
        for key in field or []:
            if not isinstance(value,dict) or key not in value:raise ValueError('object field missing')
            value=value[key]
        raw=value.encode() if isinstance(value,str) else json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
        if offset>len(raw):raise ValueError('offset exceeds object field')
        data=raw[offset:offset+length]
        result={'object':identity,'field':field or [],'bytes':len(raw),'offset':offset,'end':offset+len(data),
                'eof':offset+len(data)==len(raw),'slice_sha256':hashlib.sha256(data).hexdigest()}
        try:result.update(encoding='utf-8',text=data.decode())
        except UnicodeDecodeError:result.update(encoding='base64',base64=base64.b64encode(data).decode())
        return result

    def knowledge_view(self, identity, after=0, snapshot=None, limit=10, max_bytes=4096):
        """Page explicit correction relations within a serialized response budget."""
        self.host.get(identity, 'knowledge')
        if (type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=100
                or type(max_bytes) is not int or not 2048<=max_bytes<=65536):
            raise ValueError('invalid knowledge page or byte budget')
        def preview(value,chars):
            text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,separators=(',',':'))
            return {'text':text[:chars],'complete':len(text)<=chars}
        with self.host.tx() as db:
            maximum=db.execute('SELECT coalesce(max(rowid),0) FROM objects').fetchone()[0]
            snapshot=maximum if snapshot is None else snapshot
            if type(snapshot) is not int or not after<=snapshot<=maximum:raise ValueError('invalid knowledge snapshot')
            root=db.execute('SELECT rowid FROM objects WHERE id=?',(identity,)).fetchone()[0]
            if root>snapshot:raise ValueError('knowledge is newer than the selected snapshot')
            rows=db.execute("""WITH RECURSIVE lineage(rowid,id,body) AS (
                SELECT rowid,id,body FROM objects WHERE id=? UNION
                SELECT o.rowid,o.id,o.body FROM objects o JOIN lineage l
                ON json_extract(o.body,'$.supersedes')=l.id
                WHERE o.kind='knowledge' AND o.rowid<=?)
                SELECT * FROM lineage WHERE rowid>? ORDER BY rowid LIMIT ?""",
                (identity,snapshot,after,limit+1)).fetchall()
            result={'root':identity,'snapshot':snapshot,'entries':[],'cursor':after,'more':False,
                    'scope':'Explicit correction edges and scoped previews; recency is not truth. Follow cursor for all branches. object_view(id,field) reads complete fields without changing historical objects.'}
            for row in rows[:limit]:
                body=json.loads(row['body'])
                encoded=json.dumps({'schema':1,'kind':'knowledge','body':body},sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
                if hashlib.sha256(encoded).hexdigest()!=row['id']:raise ValueError('knowledge identity differs')
                count=db.execute("SELECT count(*) FROM objects WHERE kind='knowledge' AND rowid<=? AND json_extract(body,'$.supersedes')=?",(snapshot,row['id'])).fetchone()[0]
                entry={'id':row['id'],'supersedes':body.get('supersedes'),'correction_count':count,
                       'subject':preview(body.get('subject'),32),'applicability':preview(body.get('applicability'),64),
                       'interpretation':preview(body.get('interpretation',''),128)}
                candidate={**result,'entries':[*result['entries'],entry],'cursor':row['rowid'],'more':True}
                # Match the public Python client's ASCII/pretty serialization,
                # including non-ASCII scope text, rather than counting characters.
                if len(json.dumps(candidate,indent=2).encode())>max_bytes:break
                result=candidate
            result['more']=len(rows)>len(result['entries'])
            if rows and not result['entries']:raise ValueError('knowledge metadata exceeds requested byte budget')
        return result

    def search(self, text='', kinds=None, after=0, snapshot=None, limit=10):
        """A stable, literal-text page. Corrections are relations, not truth labels."""
        if not isinstance(text,str) or type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=100:
            raise ValueError('invalid evidence query')
        if kinds is not None and (not isinstance(kinds,list) or not all(isinstance(k,str) for k in kinds)):
            raise ValueError('kinds must be a list')
        with self.host.tx() as db:
            maximum=db.execute('SELECT coalesce(max(rowid),0) FROM objects').fetchone()[0]
            if snapshot is None:snapshot=maximum
            if type(snapshot) is not int or not after<=snapshot<=maximum:raise ValueError('invalid evidence snapshot')
            query='SELECT rowid,id,kind,body FROM objects WHERE rowid>? AND rowid<=? AND instr(lower(body),lower(?))>0'
            params=[after,snapshot,text]
            if kinds is not None:
                query+=' AND kind IN ('+','.join('?' for _ in kinds)+')';params.extend(kinds)
            rows=db.execute(query+' ORDER BY rowid LIMIT ?',(*params,limit+1)).fetchall()
            matches=[]
            for row in rows[:limit]:
                body=json.loads(row['body'])
                encoded=json.dumps({'schema':1,'kind':row['kind'],'body':body},sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
                if hashlib.sha256(encoded).hexdigest()!=row['id']:raise ValueError('object identity differs')
                corrections=[r[0] for r in db.execute("SELECT id FROM objects WHERE kind='knowledge' AND rowid<=? AND json_extract(body,'$.supersedes')=? ORDER BY rowid",(snapshot,row['id']))]
                matches.append({'id':row['id'],'kind':row['kind'],'preview':row['body'][:1024],
                    'preview_complete':len(row['body'])<=1024,'superseded_by':corrections,
                    **({'correction_view':{'operation':'knowledge_view','arguments':{'identity':row['id'],'snapshot':snapshot}}} if row['kind']=='knowledge' else {})})
        return {'snapshot':snapshot,'cursor':rows[min(len(rows),limit)-1]['rowid'] if rows else after,
                'more':len(rows)>limit,'matches':matches}
