"""Bounded deterministic Silver transformations and Gold materialization."""
import json
import math
from datetime import date
from .cleaning import clean_value, number
from .models import Column


def contract(spec):
    return json.dumps({'primary_key':spec.primary_key,'columns':[{'name':c.name,'dtype':c.dtype} for c in [*spec.columns,*spec.derived_columns]]},sort_keys=True)


def derive(record,definitions):
    edits=[]
    for d in definitions:
        values=[record[f] for f in d.fields]
        if d.operation=='coalesce':value=next((v for v in values if v is not None),None)
        elif d.operation=='concat':value=d.separator.join(str(v) for v in values if v is not None)
        elif any(v is None for v in values):value=None
        elif d.operation in ('year','month'):
            parsed=date.fromisoformat(str(values[0]));value=getattr(parsed,d.operation)
        else:
            nums=[number(v) for v in values]
            if d.operation=='add':value=math.fsum(nums)
            elif d.operation=='multiply':value=math.prod(nums)
            elif d.operation=='subtract':value=nums[0]-nums[1]
            else:
                if nums[1]==0:raise ValueError('Derived division by zero')
                value=nums[0]/nums[1]
        value,_=clean_value(value,Column(name=d.name,dtype=d.dtype))
        record[d.name]=value
        edits.append({'column':d.name,'rule':'derive_'+d.operation,'fields':d.fields,'after':value})
    return edits


def included(record,filters):
    for rule in filters:
        value=record[rule.column];other=rule.value;op=rule.operator
        if op=='not_null':passed=value is not None
        elif op=='eq':passed=value==other
        elif op=='ne':passed=value!=other
        elif value is None or other is None:passed=False
        else:
            if type(value) is not type(other) and not(isinstance(value,(int,float)) and isinstance(other,(int,float))):
                raise ValueError('Filter comparison types differ')
            if op=='gt':passed=value>other
            elif op=='gte':passed=value>=other
            elif op=='lt':passed=value<other
            else:passed=value<=other
        if not passed:return False
    return True


def aggregate(records,gold):
    if gold is None:return []
    groups={}
    for record in records:
        key=tuple(record.get(c) for c in gold.group_by)
        groups.setdefault(key,[]).append(record)
    output=[]
    for key,rows in sorted(groups.items(),key=lambda pair:json.dumps(pair[0])):
        result=dict(zip(gold.group_by,key))
        for m in gold.measures:
            values=[r.get(m.column) for r in rows if r.get(m.column) is not None] if m.column else rows
            if m.operation=='count':value=len(values)
            elif not values:value=None
            elif m.operation=='sum':value=math.fsum(number(v) for v in values)
            elif m.operation=='avg':value=math.fsum(number(v) for v in values)/len(values)
            elif m.operation=='min':value=min(values)
            else:value=max(values)
            if isinstance(value,float) and not math.isfinite(value):raise ValueError('Nonfinite Gold measure')
            result[m.name]=value
        output.append(result)
    return output


def publish_gold(conn,run_id,spec):
    # The full current detail state is read under the same publish transaction.
    if spec.gold is None:
        conn.execute('DELETE FROM gold_current WHERE target=?',(spec.target,))
        return []
    total=conn.execute('SELECT count(*) FROM warehouse WHERE target=?',(spec.target,)).fetchone()[0]
    if total>100000:raise ValueError('Local Gold detail limit is 100,000 rows; use a distributed engine for larger targets')
    detail=conn.execute('SELECT record_json,run_id FROM warehouse WHERE target=? ORDER BY record_key',(spec.target,)).fetchall()
    rows=aggregate([json.loads(r['record_json']) for r in detail],spec.gold)
    lineage=sorted({r['run_id'] for r in detail})
    conn.execute('INSERT INTO gold_snapshots VALUES(?,?,?,?,?)',(run_id,spec.target,spec.gold.model_dump_json(),json.dumps(rows,allow_nan=False),json.dumps(lineage)))
    conn.execute('INSERT INTO gold_current VALUES(?,?) ON CONFLICT(target) DO UPDATE SET run_id=excluded.run_id',(spec.target,run_id))
    return rows
