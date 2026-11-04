import csv
import hashlib
import io
import ipaddress
import json
import os
import socket
import uuid
import time as timer
from datetime import date, datetime, time
from decimal import Decimal
from urllib.parse import urlparse
import httpx
from . import store

MAX_ROWS = 25000


def parquet_rows(raw):
    import pyarrow as pa
    import pyarrow.parquet as pq
    def scalar(value):
        if isinstance(value,(date,datetime,time)):return value.isoformat()
        if isinstance(value,Decimal):return str(value)  # preserve decimal precision at ingestion
        if isinstance(value,list):return [scalar(v) for v in value]
        if isinstance(value,dict):return {k:scalar(v) for k,v in value.items()}
        if isinstance(value,bytes):raise ValueError('Binary Parquet columns need an explicit decoding adapter')
        return value
    try:
        file=pq.ParquetFile(pa.BufferReader(raw),thrift_string_size_limit=1048576,thrift_container_size_limit=100000)
        if not 1<=file.metadata.num_rows<=MAX_ROWS or file.metadata.num_columns>200:
            raise ValueError(f'Parquet requires 1–{MAX_ROWS:,} rows and at most 200 leaf columns')
        expanded=sum(file.metadata.row_group(i).total_byte_size for i in range(file.metadata.num_row_groups))
        if expanded>int(os.getenv('MAX_PARQUET_EXPANDED_BYTES','52428800')):raise ValueError('Parquet expanded data exceeds configured limit')
        rows=[]
        for batch in file.iter_batches(batch_size=512):
            rows.extend(scalar(r) for r in batch.to_pylist())
            if len(rows)>MAX_ROWS:raise ValueError('Parquet row limit exceeded')
        return rows
    except (pa.ArrowException,OSError) as error:
        raise ValueError('Invalid or unsupported Parquet file') from None


def decode_rows(raw: bytes, name: str, delimiter=None):
    if len(raw) > int(os.getenv("MAX_UPLOAD_BYTES", "5242880")):
        raise ValueError("Upload exceeds configured byte limit")
    suffix = name.lower().rsplit(".", 1)[-1]
    if suffix in ('parquet','pq'):
        rows=parquet_rows(raw)
    else:
        try:text=raw.decode('utf-8-sig')
        except UnicodeDecodeError:raise ValueError('Text sources must use UTF-8 encoding') from None
    if delimiter is not None and delimiter not in (',','|','\t',';'):raise ValueError('Delimiter must be comma, pipe, tab or semicolon')
    if suffix in ("csv",'tsv') or suffix=='txt' and delimiter is not None:
        reader = csv.DictReader(io.StringIO(text),delimiter=delimiter or ('\t' if suffix=='tsv' else ','))
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("CSV requires unique column names")
        rows = list(reader)
        if any(None in row for row in rows):
            raise ValueError("CSV row has more cells than headers")
    elif suffix in ("json", "jsonl", "ndjson"):
        payload = [json.loads(line) for line in text.splitlines() if line.strip()] if suffix != "json" else json.loads(text)
        if isinstance(payload, dict):
            payload = payload.get("records", payload.get("data", [payload]))
        rows = payload
    elif suffix=='txt':
        rows=[{'record_id':str(i),'text':line} for i,line in enumerate(text.splitlines(),1) if line.strip()]
    elif suffix not in ('parquet','pq'):
        raise ValueError("Use CSV, TSV, JSON, JSONL, NDJSON, TXT or Parquet")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS or not all(isinstance(row, dict) and row for row in rows):
        raise ValueError(f"Source must contain 1–{MAX_ROWS:,} nonempty object records")
    if any(len(json.dumps(row)) > 100000 for row in rows):
        raise ValueError("Record exceeds 100KB")
    # Reject NaN/Infinity accepted by Python's permissive JSON parser.
    json.dumps(rows, allow_nan=False)
    return rows


def save_rows(rows, name, raw=None, metadata=None):
    dataset_id = uuid.uuid4().hex
    from .privacy import protect_source
    meta=dict(metadata or {'kind':'file'})
    with store.db() as conn:contract=conn.execute('SELECT contract_json FROM source_contracts WHERE source_name=?',(name,)).fetchone()
    policy=json.loads(contract[0]) if contract else {}
    reference=policy.get('pii_key_ref') or meta.pop('pii_key_ref',None) or 'PII_ENCRYPTION_KEY'
    protected,paths=protect_source(rows,[*policy.get('pii_columns',[]),*meta.pop('pii_columns',[])],reference)
    original=raw if raw is not None else json.dumps(rows,sort_keys=True,allow_nan=False).encode()
    sha=hashlib.sha256(original).hexdigest()
    content=json.dumps(protected,sort_keys=True,allow_nan=False).encode() if paths else original
    rows=protected
    meta['privacy']={'protected_columns':paths,'sensitive_columns':paths,'key_reference':reference if paths else None,'mode':'column_aes256_gcm' if paths else 'no_pii_detected','detection':'declared fields plus conservative name/value heuristics; not exhaustive'}
    if paths and meta.get('extraction',{}).get('profile'):
        for column in meta['extraction']['profile']['columns']:
            if column['column'] in paths:column['minimum']=column['maximum']=None
    meta['bronze']={'representation':'protected_columns_json' if paths else 'original_bytes' if raw is not None else 'generated_json','bytes':len(content),'original_bytes_retained':raw is not None and not paths,'stored_sha256':hashlib.sha256(content).hexdigest()}
    meta.setdefault('_ingested_at',store.now());meta.setdefault('_source_system',name);meta.setdefault('_batch_id',uuid.uuid4().hex);meta.setdefault('_file_name',name);meta.setdefault('load_date',meta['_ingested_at'][:10])
    with store.db() as conn:
        conn.execute("INSERT INTO datasets VALUES(?,?,?,?,?,?)", (dataset_id, name[:200], sha, store.now(), json.dumps(rows, allow_nan=False), json.dumps(meta)))
        conn.execute('INSERT INTO bronze_objects VALUES(?,?,?,?)',(dataset_id,content,meta['bronze']['representation'],len(content)))
        store.audit(conn, None, "dataset.created", {"id": dataset_id, "sha256": sha, "row_count": len(rows)})
    return {"id": dataset_id, "name": name, "sha256": sha, "row_count": len(rows), "metadata": meta}


def ingest_bytes(raw, name, delimiter=None):
    if len(raw)>int(os.getenv('MAX_UPLOAD_BYTES','5242880')):raise ValueError('Upload exceeds configured byte limit')
    from .profiling import validate_source
    batch_id=uuid.uuid4().hex;started=store.now();clock=timer.monotonic()
    with store.db() as conn:
        conn.execute('INSERT INTO extraction_batches VALUES(?,?,?,?,?,?,?,?,?)',(batch_id,name,started,None,'extracting',hashlib.sha256(raw).hexdigest(),b'',None,'{}'))
    try:
        rows=decode_rows(raw,name,delimiter);validation=validate_source(name,rows)
        metadata={'kind':'file','format':name.lower().rsplit('.',1)[-1],'delimiter':delimiter,'_ingested_at':started,'_source_system':name,'_batch_id':batch_id,'_file_name':name,'load_date':started[:10],'extraction':validation}
        result=save_rows(rows,name,raw,metadata)
    except Exception as error:
        # Never persist unparsed source bytes on failure: PII classification may be incomplete.
        with store.db() as conn:
            conn.execute("UPDATE extraction_batches SET status='rejected',finished_at=?,details_json=? WHERE id=?",(store.now(),json.dumps({'error_type':type(error).__name__,'message':'Parsing, source contract or privacy policy failed; original bytes were not retained','duration_seconds':timer.monotonic()-clock,'raw_retained':False}),batch_id))
            store.audit(conn,None,'extraction.rejected',{'batch_id':batch_id,'source_name':name,'error_type':type(error).__name__})
        raise
    with store.db() as conn:
        safe_content=conn.execute('SELECT content FROM bronze_objects WHERE dataset_id=?',(result['id'],)).fetchone()[0]
        conn.execute("UPDATE extraction_batches SET status='succeeded',finished_at=?,dataset_id=?,details_json=?,content=? WHERE id=?",(store.now(),result['id'],json.dumps({'rows_extracted':len(rows),'duration_seconds':timer.monotonic()-clock,'schema_drift':validation['drift'],'min_watermark':None,'max_watermark':None,'representation':result['metadata']['bronze']['representation']}),safe_content,batch_id))
    return result


def fetch_api(url):
    parsed = urlparse(url)
    hosts = {h.strip().lower() for h in os.getenv("API_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("API requires HTTPS on port 443 and a hostname in API_ALLOWED_HOSTS")
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Private, loopback, and reserved API addresses are blocked")
    # No ambient proxies or redirects; DNS-rebinding protection still belongs at egress firewall.
    with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
        with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
            if response.status_code != 200:
                raise ValueError(f"API returned HTTP {response.status_code}")
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > int(os.getenv("MAX_UPLOAD_BYTES", "5242880")):
                    raise ValueError("API payload exceeds configured limit")
    rows = decode_rows(bytes(raw), "api.json")
    return save_rows(rows, f"API:{parsed.hostname}", bytes(raw), {"kind": "api", "host": parsed.hostname})
