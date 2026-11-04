"""Atomic load strategies, batch-level quality checks and SCD Type 2 history."""
import hashlib
import json
import math
from datetime import datetime,timezone
from . import store


def checks(conn,spec,accepted):
    records=[r['record'] for r in accepted];results=[]
    for check in spec.batch_checks:
        note='';value=None
        try:
            if check.kind=='row_count':value=len(records)
            elif check.kind=='sum':value=math.fsum(r[check.column] for r in records if r[check.column] is not None)
            elif check.kind=='volume_change':
                previous=conn.execute("SELECT report_json FROM runs WHERE status='succeeded' AND json_extract(request_json,'$.spec.target')=? ORDER BY finished_at DESC LIMIT 1",(spec.target,)).fetchone()
                count=json.loads(previous[0])['accepted'] if previous else 0
                if not count:
                    results.append({'name':check.name,'kind':check.kind,'severity':check.severity,'passed':True,'status':'baseline_unavailable','value':None});continue
                value=100*(len(records)-count)/count
            elif check.kind=='reference':
                keys={r[0] for r in conn.execute('SELECT record_key FROM warehouse WHERE target=?',(check.reference_target,))}
                value=sum(r[check.column] is None or str(r[check.column]) not in keys for r in records)
            else:
                stamps=[]
                for r in records:
                    stamp=datetime.fromisoformat(str(r[check.column]).replace('Z','+00:00'))
                    if stamp.tzinfo is None:raise ValueError('Freshness timestamps need an explicit timezone')
                    stamps.append(stamp)
                value=(datetime.now(timezone.utc)-max(stamps)).total_seconds()/60 if stamps else None
            passed=value is not None and (value==0 if check.kind=='reference' else (check.minimum is None or value>=check.minimum) and (check.maximum is None or value<=check.maximum))
        except (ValueError,TypeError,OverflowError,KeyError):passed=False;note='Check could not evaluate the supplied values'
        results.append({'name':check.name,'kind':check.kind,'severity':check.severity,'passed':passed,'value':value,'note':note})
    return results


def preflight(conn,spec,accepted):
    records=[r['record'] for r in accepted]
    if spec.load.mode=='append':
        existing={r[0] for r in conn.execute('SELECT record_key FROM warehouse WHERE target=?',(spec.target,))}
        if any(str(r[spec.primary_key]) in existing for r in records):return 'Append would overwrite an existing key'
    if spec.load.mode=='partition_overwrite':
        if any(str(r[spec.load.partition_column]) not in spec.load.partition_values for r in records):return 'Input contains values outside the explicit overwrite partitions'
        # Business keys cannot move across partitions during a partition replacement.
        existing={r['record_key']:json.loads(r['record_json']) for r in conn.execute('SELECT record_key,record_json FROM warehouse WHERE target=?',(spec.target,))}
        if any(str(r[spec.primary_key]) in existing and str(existing[str(r[spec.primary_key])].get(spec.load.partition_column)) not in spec.load.partition_values for r in records):return 'Key collision outside overwrite partitions'
    if spec.load.mode=='scd2':
        count=conn.execute('SELECT count(*) FROM warehouse WHERE target=?',(spec.target,)).fetchone()[0]
        history=conn.execute('SELECT count(*) FROM dimension_history WHERE target=? AND is_current=1',(spec.target,)).fetchone()[0]
        if count!=history:return 'SCD2 requires an empty target or consistent existing SCD2 history; migrate explicitly'
    elif conn.execute('SELECT 1 FROM dimension_history WHERE target=? LIMIT 1',(spec.target,)).fetchone():return 'SCD2 target cannot change load strategy without migration'
    return None


def prepare(conn,run_id,spec,accepted):
    if spec.load.mode=='full_refresh':conn.execute('DELETE FROM warehouse WHERE target=?',(spec.target,))
    if spec.load.mode=='partition_overwrite':
        rows=conn.execute('SELECT record_key,record_json FROM warehouse WHERE target=?',(spec.target,)).fetchall()
        conn.executemany('DELETE FROM warehouse WHERE target=? AND record_key=?',[(spec.target,r['record_key']) for r in rows if str(json.loads(r['record_json']).get(spec.load.partition_column)) in spec.load.partition_values])
    if spec.load.mode=='scd2':
        stamp=store.now()
        for row in accepted:
            record=row['record'];key=str(record[spec.primary_key]);tracked=spec.load.tracked_columns or list(record)
            digest=hashlib.sha256(json.dumps({k:record[k] for k in tracked},sort_keys=True).encode()).hexdigest()
            current=conn.execute('SELECT version,row_hash FROM dimension_history WHERE target=? AND business_key=? AND is_current=1',(spec.target,key)).fetchone()
            if current and current['row_hash']==digest:
                conn.execute('UPDATE dimension_history SET record_json=? WHERE target=? AND business_key=? AND is_current=1',(json.dumps(record),spec.target,key))
                continue
            version=current['version']+1 if current else 1
            if current:conn.execute('UPDATE dimension_history SET effective_to=?,is_current=0 WHERE target=? AND business_key=? AND is_current=1',(stamp,spec.target,key))
            surrogate=hashlib.sha256(json.dumps([spec.target,key,version]).encode()).hexdigest()
            conn.execute('INSERT INTO dimension_history VALUES(?,?,?,?,?,?,?,?,?,?)',(spec.target,key,version,surrogate,digest,json.dumps(record),stamp,None,1,run_id))
