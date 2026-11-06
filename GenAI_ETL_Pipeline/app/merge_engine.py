"""Disk-backed, bounded-memory source merges with protected spill records."""
import hashlib
import hmac
import itertools
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path
from . import store
from .mapping import flatten
from .privacy import decrypt_source,protect_source,sensitive_source_paths,flagged,PREFIX


def chunk_size():return min(5000,max(1,int(os.getenv('MERGE_CHUNK_ROWS','1000'))))


def disk_check():
    if shutil.disk_usage(store.data_dir()).free<int(os.getenv('MERGE_MIN_FREE_BYTES','134217728')):
        raise ValueError('Insufficient free disk space for the merge; free space or change DATA_DIR')


def key_value(row,path):
    flat=flatten(row)
    if path not in flat:raise ValueError('Join key does not exist in every source row')
    value=flat[path]
    if value is None:return None
    if isinstance(value,(bool,list,dict)):raise ValueError('Join keys must be scalar strings or numbers')
    return json.dumps([type(value).__name__,value],ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()


class MergeSession:
    def __init__(self,body):
        if len(set(body.dataset_ids))!=len(body.dataset_ids):raise ValueError('Select distinct source datasets')
        if body.mode!='append' and (len(body.dataset_ids)!=2 or not body.left_key or not body.right_key):raise ValueError('Join requires exactly two sources and both join keys')
        self.body=body;self.sources=[store.get_dataset_info(i) for i in body.dataset_ids]
        self.sensitive=[];self.columns=[];self.types=[];self.impact={'source_rows':[{'dataset_id':s['id'],'rows':s['row_count']} for s in self.sources]}
        self.secret=os.urandom(32);self.temp=None;self.db=None;self.output_columns=set();self.output_rows=0

    def __enter__(self):
        disk_check();self.temp=tempfile.TemporaryDirectory(prefix='merge-',dir=store.data_dir())
        try:
            self.db=sqlite3.connect(str(Path(self.temp.name)/'index.sqlite'))
            self.db.execute('PRAGMA journal_mode=MEMORY');self.db.execute('PRAGMA cache_size=-8192')
            self.db.execute('CREATE TABLE lookup (key_hash BLOB PRIMARY KEY, record_json TEXT NOT NULL, matched INTEGER NOT NULL DEFAULT 0)')
            self.db.execute('CREATE TABLE left_keys (key_hash BLOB PRIMARY KEY, occurrences INTEGER NOT NULL)')
            self._inventory()
            if self.body.mode!='append':self._index_right()
            return self
        except Exception:self.__exit__(None,None,None);raise

    def __exit__(self,*args):
        if self.db:self.db.close();self.db=None
        if self.temp:self.temp.cleanup();self.temp=None

    def _inventory(self):
        expected_headers=None
        for source in self.sources:
            from .profiling import typename
            paths=set();types={};pii=sensitive_source_paths(source['metadata']);seen=0;headers=None
            for _,rows in store.iter_dataset_rows(source['id'],batch_size=chunk_size()):
                for row in rows:
                    if headers is None:headers=set(row)
                    if self.body.mode=='append' and set(row)!=headers:raise ValueError('Append requires identical top-level columns. Use mapped-row append for different source schemas.')
                    for path,value in flatten(row).items():
                        paths.add(path)
                        types.setdefault(path,set()).add(typename(value))
                        if flagged(path,value) or isinstance(value,str) and value.startswith(PREFIX):pii.add(path)
                    if len(paths)>500:raise ValueError('Merge supports at most 500 flattened source fields; project a narrower source')
                seen+=len(rows);disk_check()
            if seen!=source['row_count']:raise ValueError('Source row count changed or extraction is incomplete; merge stopped')
            if self.body.mode=='append':
                if expected_headers is None:expected_headers=headers
                elif expected_headers!=headers:raise ValueError('Append requires identical top-level columns. Use mapped-row append for different source schemas.')
            self.sensitive.append(pii);self.columns.append(paths);self.types.append(types)
        self.output_columns={('' if self.body.mode=='append' else 'left.' if i==0 else 'right.')+p for i,paths in enumerate(self.columns) for p in paths}

    def _hash(self,value):return hmac.new(self.secret,value,hashlib.sha256).digest() if value is not None else None

    def _index_right(self):
        source=self.sources[1];reference=source['metadata'].get('privacy',{}).get('key_reference') or 'PII_ENCRYPTION_KEY';nulls=0
        for _,stored in store.iter_dataset_rows(source['id'],batch_size=chunk_size()):
            clear=decrypt_source(stored)
            present={p for row in stored for p in flatten(row)}
            protected,_=protect_source(stored,self.sensitive[1]&present,reference)
            entries=[]
            for raw,row in zip(protected,clear):
                key=self._hash(key_value(row,self.body.right_key))
                if key is None:nulls+=1;continue
                entries.append((key,json.dumps(raw,ensure_ascii=False,allow_nan=False)))
            try:self.db.executemany('INSERT INTO lookup(key_hash,record_json) VALUES(?,?)',entries)
            except sqlite3.IntegrityError:raise ValueError('Right join key must be unique; many-to-many joins are unsupported') from None
            self.db.commit();disk_check()
        self.impact['right_null_key_rows']=nulls

    def __iter__(self):
        if self.body.mode=='append':
            for source in self.sources:
                for _,rows in store.iter_dataset_rows(source['id'],batch_size=chunk_size()):
                    for row in decrypt_source(rows):yield self._output(row)
        else:
            matched=unmatched=nulls=0
            for _,rows in store.iter_dataset_rows(self.sources[0]['id'],batch_size=chunk_size()):
                for left in decrypt_source(rows):
                    key=self._hash(key_value(left,self.body.left_key));right=None
                    if key is None:nulls+=1
                    else:
                        self.db.execute('INSERT INTO left_keys VALUES(?,1) ON CONFLICT(key_hash) DO UPDATE SET occurrences=occurrences+1',(key,))
                        record=self.db.execute('SELECT record_json FROM lookup WHERE key_hash=?',(key,)).fetchone()
                        if record:
                            right=decrypt_source([json.loads(record[0])])[0]
                            self.db.execute('UPDATE lookup SET matched=1 WHERE key_hash=?',(key,))
                    if right is None:unmatched+=1
                    else:matched+=1
                    if right is not None or self.body.mode=='left_join':yield self._output({'left':left,'right':right or {}})
                self.db.commit();disk_check()
            duplicate_left=self.db.execute('SELECT coalesce(sum(occurrences-1),0) FROM left_keys').fetchone()[0]
            used_right=self.db.execute('SELECT count(*) FROM lookup WHERE matched=1').fetchone()[0]
            self.impact.update({'left_rows':self.sources[0]['row_count'],'right_rows':self.sources[1]['row_count'],'matched_left_rows':matched,'unmatched_left_rows':unmatched,'unused_right_rows':self.sources[1]['row_count']-used_right,'dropped_left_rows':unmatched if self.body.mode=='inner_join' else 0,'left_null_key_rows':nulls,'left_duplicate_key_rows':duplicate_left,'relationship_cardinality':'many_to_one' if duplicate_left else 'one_to_one'})
        if not self.output_rows:raise ValueError('Merge produced no records')

    def _output(self,row):
        self.output_rows+=1;self.output_columns.update(flatten(row));return row

    def metadata(self):
        paths=set()
        for index,fields in enumerate(self.sensitive):
            prefix='' if self.body.mode=='append' else 'left.' if index==0 else 'right.'
            paths.update(prefix+field for field in fields)
        reference=next((s['metadata'].get('privacy',{}).get('key_reference') for s in self.sources if s['metadata'].get('privacy',{}).get('key_reference')),None) or 'PII_ENCRYPTION_KEY'
        schema={}
        for index,types in enumerate(self.types):
            prefix='' if self.body.mode=='append' else 'left.' if index==0 else 'right.'
            for name,observed in types.items():schema[prefix+name]=sorted(set(schema.get(prefix+name,[]))|observed)
        return {'kind':'merge','mode':self.body.mode,'parents':[{'dataset_id':s['id'],'sha256':s['sha256']} for s in self.sources],'left_key':self.body.left_key,'right_key':self.body.right_key,'pii_columns':sorted(paths),'pii_key_ref':reference,'merge_engine':'sqlite_disk_backed','column_order':sorted(self.output_columns),'schema':schema}

    def report(self):return {'mode':self.body.mode,'output_rows':self.output_rows,'output_columns':sorted(self.output_columns),**self.impact,'merge_engine':'sqlite_disk_backed','side_effects':'No dataset, mapping, run or warehouse records were written. Preview contains counts and schema, not raw join-key values.'}


def preview_merge(body):
    with MergeSession(body) as session:
        for _ in session:pass
        return session.report()


def save_generated_rows(rows,name,metadata):
    """Atomically publish chunked generated records; never collect the whole output."""
    dataset_id=uuid.uuid4().hex;stamp=store.now();meta=dict(metadata);reference=meta.pop('pii_key_ref','PII_ENCRYPTION_KEY');declared=set(meta.pop('pii_columns',[]))
    digest=hashlib.sha256();count=byte_count=0;protected_paths=set();iterator=iter(rows)
    with store.db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        contract = conn.execute('SELECT contract_json FROM source_contracts WHERE source_name=?', (name,)).fetchone()
        policy = json.loads(contract[0]) if contract else {}
        contract_paths = set(policy.get('pii_columns', []))
        declared.update(contract_paths)
        reference = policy.get('pii_key_ref') or reference
        seen_contract_paths = set()
        conn.execute('INSERT INTO datasets VALUES(?,?,?,?,?,?)',(dataset_id,name[:200],'pending',stamp,'[]','{}'))
        while True:
            batch=list(itertools.islice(iterator,chunk_size()))
            if not batch:break
            present={p for row in batch for p in flatten(row)}
            seen_contract_paths.update(contract_paths & present)
            protected,paths=protect_source(batch,declared&present,reference);protected_paths.update(paths)
            encoded=[]
            for row in protected:
                text=json.dumps(row,ensure_ascii=False,allow_nan=False,separators=(',',':'))
                if len(text.encode())>1048576:raise ValueError('Merged record exceeds the 1MB per-record bound; project fewer columns')
                count+=1;encoded.append((dataset_id,count,text));raw=text.encode()+b'\n';digest.update(raw);byte_count+=len(raw)
            conn.executemany('INSERT INTO dataset_rows VALUES(?,?,?)',encoded);disk_check()
        if not count:raise ValueError('Merge produced no records')
        if seen_contract_paths != contract_paths:
            raise ValueError('Declared PII field is absent from the merged source')
        meta.update({'row_count':count,'chunked':True,'_ingested_at':stamp,'_source_system':name,'_batch_id':dataset_id,'_file_name':name,'load_date':stamp[:10],'privacy':{'protected_columns':sorted(protected_paths),'sensitive_columns':sorted(protected_paths),'key_reference':reference if protected_paths else None,'mode':'column_aes256_gcm' if protected_paths else 'no_pii_detected'},'bronze':{'representation':'protected_columns_json_chunks' if protected_paths else 'generated_json_chunks','bytes':byte_count,'original_bytes_retained':False,'stored_sha256':digest.hexdigest()}})
        conn.execute('UPDATE datasets SET sha256=?,metadata_json=? WHERE id=?',(digest.hexdigest(),json.dumps(meta),dataset_id))
        manifest=json.dumps({'dataset_id':dataset_id,'representation':meta['bronze']['representation'],'rows':count,'sha256':digest.hexdigest()}).encode()
        conn.execute('INSERT INTO bronze_objects VALUES(?,?,?,?)',(dataset_id,manifest,'chunk_manifest',len(manifest)))
        store.audit(conn,None,'dataset.created',{'id':dataset_id,'sha256':digest.hexdigest(),'row_count':count,'chunked':True})
    return {'id':dataset_id,'name':name,'sha256':digest.hexdigest(),'row_count':count,'metadata':meta}


def merge(body):
    with MergeSession(body) as session:
        result=save_generated_rows(session,body.name,session.metadata())
        result['merge_impact'] = session.report()
        result['merge_impact']['side_effects'] = 'Created a staged combined dataset for review.'
        return result
