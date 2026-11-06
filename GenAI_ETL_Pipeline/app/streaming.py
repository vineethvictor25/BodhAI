"""Bounded-memory CSV/Parquet pipeline execution with atomic publication."""
import itertools
import json
import math
from datetime import datetime, timezone

from . import store
from .mapping import flatten, infer_mapping
from .medallion import contract as target_contract, publish_gold
from .engine import transform_batch


def _progress(run_id,processed,total,phase='transforming'):
    percent=min(99,int(100*processed/total)) if total else 0
    with store.db() as conn:
        row=conn.execute('SELECT report_json FROM runs WHERE id=?',(run_id,)).fetchone()
        report=json.loads(row['report_json']) if row else {}
        report['progress']={'phase':phase,'processed_rows':processed,'total_rows':total,'percent':percent}
        conn.execute('UPDATE runs SET report_json=? WHERE id=?',(json.dumps(report),run_id))


def _quality_checks(conn,spec,run_id,accepted):
    results=[]
    for check in spec.batch_checks:
        value=None;note='';passed=False
        try:
            if check.kind=='row_count':value=accepted
            elif check.kind=='sum':
                total=0.0;count=0
                cursor=conn.execute('SELECT record_json FROM stream_run_rows WHERE run_id=? AND kind=?',(run_id,'accepted'))
                while True:
                    rows=cursor.fetchmany(1000)
                    if not rows:break
                    for row in rows:
                        item=json.loads(row['record_json']).get(check.column)
                        if item is not None:total=math.fsum((total,float(item)));count+=1
                value=total if count else 0
            elif check.kind=='volume_change':
                previous=conn.execute("SELECT report_json FROM runs WHERE status='succeeded' AND json_extract(request_json,'$.spec.target')=? ORDER BY finished_at DESC LIMIT 1",(spec.target,)).fetchone()
                count=json.loads(previous[0])['accepted'] if previous else 0
                if not count:
                    results.append({'name':check.name,'kind':check.kind,'severity':check.severity,'passed':True,'status':'baseline_unavailable','value':None});continue
                value=100*(accepted-count)/count
            elif check.kind=='reference':
                path='$.'+check.column.replace('"','\\"')
                value=conn.execute(f"SELECT count(*) FROM stream_run_rows s WHERE s.run_id=? AND s.kind='accepted' AND (json_type(s.record_json,?) IS NULL OR NOT EXISTS (SELECT 1 FROM warehouse w WHERE w.target=? AND w.record_key=CAST(json_extract(s.record_json,?) AS TEXT)))",(run_id,path,check.reference_target,path)).fetchone()[0]
            else:
                latest=None;cursor=conn.execute('SELECT record_json FROM stream_run_rows WHERE run_id=? AND kind=?',(run_id,'accepted'))
                while True:
                    rows=cursor.fetchmany(1000)
                    if not rows:break
                    for row in rows:
                        raw=json.loads(row['record_json']).get(check.column)
                        stamp=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
                        if stamp.tzinfo is None:raise ValueError('Freshness timestamps need an explicit timezone')
                        latest=stamp if latest is None or stamp>latest else latest
                value=(datetime.now(timezone.utc)-latest).total_seconds()/60 if latest else None
            passed=value is not None and (value==0 if check.kind=='reference' else (check.minimum is None or value>=check.minimum) and (check.maximum is None or value<=check.maximum))
        except (ValueError,TypeError,OverflowError,KeyError):note='Check could not evaluate the supplied values'
        results.append({'name':check.name,'kind':check.kind,'severity':check.severity,'passed':passed,'value':value,'note':note})
    return results


def process_streamed(run_id,request,datasets,approved=None):
    spec=request.spec
    if spec.deduplication=='latest':
        raise ValueError('Chunked runs currently require reject duplicate handling; latest-wins needs a separate reviewed sort strategy')
    if spec.load.mode not in ('upsert','append','full_refresh'):
        raise ValueError('Chunked runs support upsert, append, and full refresh load strategies')
    if spec.gold:
        with store.db() as conn:existing=conn.execute('SELECT count(*) FROM warehouse WHERE target=?',(spec.target,)).fetchone()[0]
        if existing+sum(item['row_count'] for item in datasets)>100000:
            raise ValueError('Local Gold materialization is limited to 100,000 detail rows; disable Gold or use a distributed engine')

    mappings=[];accepted_count=rejected_count=filtered_count=total=0;changes=[]
    with store.db() as conn:
        conn.execute('DELETE FROM stream_run_rows WHERE run_id=?',(run_id,))
        conn.execute('DELETE FROM stream_run_keys WHERE run_id=?',(run_id,))
        conn.execute('UPDATE runs SET report_json=? WHERE id=?',(json.dumps({'progress':{'phase':'preparing','processed_rows':0,'total_rows':sum(item['row_count'] for item in datasets),'percent':0}}),run_id))
    expected=sum(item['row_count'] for item in datasets)
    for dataset in datasets:
        iterator=store.iter_dataset_rows(dataset['id'])
        first=next(iterator,None)
        if first is None:raise ValueError('Chunked source has no records')
        start,rows=first
        mapping=(approved or {}).get(dataset['id']) or infer_mapping(rows,spec)
        mappings.append({'dataset_id':dataset['id'],'name':dataset['name'],'columns':mapping})
        for start,rows in itertools.chain((first,),iterator):
            chunk=transform_batch(spec,[{'id':dataset['id'],'name':dataset['name'],'rows':rows,'metadata':dataset['metadata']}],{dataset['id']:mapping})
            accepted_rows=[];rejected_rows=[]
            with store.db() as conn:
                for record in chunk['accepted']:
                    local_row=record['row_number'];source_row=record['source_row']+start-1
                    key=str(record['record'][spec.primary_key])
                    inserted=conn.execute('INSERT OR IGNORE INTO stream_run_keys VALUES(?,?)',(run_id,key)).rowcount
                    if inserted:
                        accepted_rows.append((run_id,total+local_row,dataset['id'],source_row,json.dumps(record['record'],allow_nan=False),None,None,'accepted'))
                    else:
                        rejected_rows.append((run_id,total+local_row,dataset['id'],source_row,None,json.dumps(rows[record['source_row']-1],ensure_ascii=False,allow_nan=False),json.dumps([{'column':spec.primary_key,'code':'duplicate','message':'Duplicate primary key within this batch'}]),'rejected'))
                for record in chunk['rejected']:
                    rejected_rows.append((run_id,total+record['row_number'],dataset['id'],record['source_row']+start-1,None,json.dumps(record['raw'],ensure_ascii=False,allow_nan=False),json.dumps(record['issues'],ensure_ascii=False),'rejected'))
                filtered_rows=[(run_id,total+record['row_number'],dataset['id'],record['source_row']+start-1,json.dumps(record['record'],allow_nan=False),None,None,'filtered') for record in chunk['filtered']]
                conn.executemany('INSERT INTO stream_run_rows VALUES(?,?,?,?,?,?,?,?)',accepted_rows)
                conn.executemany('INSERT INTO stream_run_rows VALUES(?,?,?,?,?,?,?,?)',rejected_rows)
                conn.executemany('INSERT INTO stream_run_rows VALUES(?,?,?,?,?,?,?,?)',filtered_rows)
            accepted_count+=len(accepted_rows);rejected_count+=len(rejected_rows);filtered_count+=len(filtered_rows)
            changes.extend({**change,'row_number':change['row_number']+total,'source_row':change['source_row']+start-1} for change in chunk['changes'][:max(0,200-len(changes))])
            total+=len(rows)
            _progress(run_id,total,expected)
    evaluated=total-filtered_count
    ratio=rejected_count/evaluated if evaluated else 0
    status='succeeded' if accepted_count and ratio<=spec.max_reject_ratio else 'quality_failed'
    contract=target_contract(spec);skipped=published=0;gold=[]
    with store.db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing=conn.execute('SELECT contract_json FROM target_contracts WHERE target=?',(spec.target,)).fetchone()
        contract_error=existing is not None and existing[0]!=contract
        if contract_error:status='contract_failed'
        history=conn.execute('SELECT 1 FROM dimension_history WHERE target=? LIMIT 1',(spec.target,)).fetchone()
        load_error='SCD2 targets cannot use chunked execution' if history and spec.load.mode!='scd2' else None
        if status=='succeeded' and load_error:status='load_blocked'
        if status=='succeeded' and spec.load.mode=='append':
            collision=conn.execute("SELECT 1 FROM stream_run_rows s JOIN warehouse w ON w.target=? AND w.record_key=CAST(json_extract(s.record_json,?) AS TEXT) WHERE s.run_id=? AND s.kind='accepted' LIMIT 1",(spec.target,'$.'+spec.primary_key,run_id)).fetchone()
            if collision:status='load_blocked'
        quality_checks=_quality_checks(conn,spec,run_id,accepted_count)
        if status=='succeeded' and any(not item['passed'] and item['severity']=='critical' for item in quality_checks):status='quality_failed'
        if status=='succeeded':
            if spec.load.mode=='full_refresh':conn.execute('DELETE FROM warehouse WHERE target=?',(spec.target,))
            conn.execute('INSERT OR IGNORE INTO target_contracts VALUES(?,?)',(spec.target,contract))
            key_path='$.'+spec.primary_key
            if spec.load.mode=='upsert' and spec.load.duplicate_policy=='skip':
                skipped=conn.execute("SELECT count(*) FROM stream_run_rows s JOIN warehouse w ON w.target=? AND w.record_key=CAST(json_extract(s.record_json,?) AS TEXT) WHERE s.run_id=? AND s.kind='accepted'",(spec.target,key_path,run_id)).fetchone()[0]
                conn.execute("INSERT OR IGNORE INTO warehouse SELECT ?,CAST(json_extract(record_json,?) AS TEXT),record_json,?,? FROM stream_run_rows s WHERE run_id=? AND kind='accepted'",(spec.target,key_path,run_id,store.now(),run_id))
            else:
                conn.execute("INSERT INTO warehouse SELECT ?,CAST(json_extract(record_json,?) AS TEXT),record_json,?,? FROM stream_run_rows s WHERE run_id=? AND kind='accepted' ON CONFLICT(target,record_key) DO UPDATE SET record_json=excluded.record_json,run_id=excluded.run_id,updated_at=excluded.updated_at",(spec.target,key_path,run_id,store.now(),run_id))
            published=conn.execute('SELECT changes()').fetchone()[0]
            expected_published=accepted_count-skipped
            actual_published=conn.execute('SELECT count(*) FROM warehouse WHERE target=? AND run_id=?',(spec.target,run_id)).fetchone()[0]
            mismatches=conn.execute("SELECT count(*) FROM stream_run_rows s JOIN warehouse w ON w.target=? AND w.record_key=CAST(json_extract(s.record_json,?) AS TEXT) WHERE s.run_id=? AND s.kind='accepted' AND w.run_id=? AND w.record_json!=s.record_json",(spec.target,key_path,run_id,run_id)).fetchone()[0]
            if actual_published!=expected_published or mismatches:
                raise ValueError('Post-load reconciliation failed; the target update was rolled back')
        for kind in ('accepted','rejected','filtered'):
            cursor=conn.execute('SELECT * FROM stream_run_rows WHERE run_id=? AND kind=? ORDER BY row_number',(run_id,kind))
            while True:
                batch=cursor.fetchmany(1000)
                if not batch:break
                if kind=='accepted':
                    conn.executemany('INSERT INTO silver VALUES(?,?,?)',[(run_id,row['row_number'],json.dumps({'row_number':row['row_number'],'record':json.loads(row['record_json']),'dataset_id':row['dataset_id'],'source_row':row['source_row']})) for row in batch])
                elif kind=='rejected':
                    conn.executemany('INSERT INTO quarantine VALUES(?,?,?,?)',[(run_id,row['row_number'],json.dumps({'record':json.loads(row['raw_json']),'dataset_id':row['dataset_id'],'source_row':row['source_row']}),row['issues_json']) for row in batch])
                else:
                    conn.executemany('INSERT INTO filtered_rows VALUES(?,?,?)',[(run_id,row['row_number'],json.dumps({'row_number':row['row_number'],'record':json.loads(row['record_json']),'dataset_id':row['dataset_id'],'source_row':row['source_row']})) for row in batch])
        preview=[json.loads(row[0]) for row in conn.execute("SELECT json_object('row_number',row_number,'record',json(record_json),'dataset_id',dataset_id,'source_row',source_row) FROM stream_run_rows WHERE run_id=? AND kind='accepted' ORDER BY row_number LIMIT 20",(run_id,))]
        issue_preview=[{'row_number':row['row_number'],'issues':json.loads(row['issues_json'])} for row in conn.execute("SELECT row_number,issues_json FROM stream_run_rows WHERE run_id=? AND kind='rejected' ORDER BY row_number LIMIT 100",(run_id,))]
        if status=='succeeded':
            gold_records=[json.loads(row[0]) for row in conn.execute("SELECT record_json FROM warehouse WHERE target=? ORDER BY record_key",(spec.target,))] if spec.gold else []
            if spec.gold:gold=publish_gold(conn,run_id,spec)
        report={'total':total,'accepted':accepted_count,'rejected':rejected_count,'filtered':filtered_count,'reject_ratio':ratio,'quality_score':round(100*(1-ratio),2),'published':published if status=='succeeded' else 0,'skipped_existing':skipped if status=='succeeded' else 0,'mappings':mappings,'changes':changes,'changes_truncated':len(changes)>=200,'gate':{'max_reject_ratio':spec.max_reject_ratio,'passed':status=='succeeded'},'contract_error':contract_error,'load_error':load_error,'preview':preview,'issues':issue_preview,'quality_checks':quality_checks,'load_mode':spec.load.mode,'post_load_reconciled':status=='succeeded','progress':{'phase':'complete','processed_rows':total,'total_rows':expected,'percent':100},'layers':{'bronze':{'dataset_ids':[item['id'] for item in datasets],'rows':total},'silver':{'valid':accepted_count,'quarantined':rejected_count,'filtered':filtered_count,'published':status=='succeeded'},'gold':{'configured':spec.gold is not None,'published':status=='succeeded' and spec.gold is not None,'rows':len(gold),'scope':'full current detail target'}}}
        report.update({'gold_rows':len(gold),'gold_preview':gold[:20]})
        conn.execute('UPDATE runs SET status=?,finished_at=?,report_json=? WHERE id=?',(status,store.now(),json.dumps(report),run_id))
        store.audit(conn,run_id,'run.finished',{'status':status,'accepted':accepted_count,'rejected':rejected_count,'published':report['published'],'skipped_existing':report['skipped_existing']})
        conn.execute('DELETE FROM stream_run_rows WHERE run_id=?',(run_id,))
        conn.execute('DELETE FROM stream_run_keys WHERE run_id=?',(run_id,))
