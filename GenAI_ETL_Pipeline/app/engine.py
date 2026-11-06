"""Validated plan interpreter. AI-authored Python/SQL is never evaluated here."""
import hashlib
import json
import threading
import uuid
from . import store
from .ingest import MAX_IN_MEMORY_ROWS
from .cleaning import clean_value
from .mapping import flatten, infer_mapping
from .models import RunRequest
from .medallion import contract as target_contract, derive, included, aggregate, publish_gold
from .loading import checks as batch_checks, preflight, prepare


class ConflictError(ValueError):
    pass


def execute(request: RunRequest, background=False):
    if request.spec.destination.cloud!='local':
        raise ValueError('This cloud destination supports deployment-plan/DDL export and local preview only. A configured, integration-tested warehouse connector is required before remote publication. Select Local workspace to execute here.')
    dataset_info = [store.get_dataset_info(dataset_id) for dataset_id in request.dataset_ids]
    streamed = sum(dataset["row_count"] for dataset in dataset_info) > MAX_IN_MEMORY_ROWS
    datasets = dataset_info if streamed else [store.get_dataset(d) for d in request.dataset_ids]
    approved = None
    if request.approved_mapping_id:
        from .semantic_mapping import resolve_approved
        approved = resolve_approved(request.approved_mapping_id, request.spec, datasets)
    if request.pipeline_id:
        from .pipeline_changes import execution_mapping
        approved = execution_mapping(request,datasets)
    body = request.model_dump()
    identity={"spec": body["spec"], "sources": [(d["id"], d["sha256"]) for d in datasets], "approved_mapping_id": request.approved_mapping_id}
    if request.pipeline_id: identity["version_ref"]=[request.pipeline_id,request.pipeline_revision]
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    run_id = uuid.uuid4().hex
    with store.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute("SELECT id,fingerprint FROM runs WHERE idempotency_key=?", (request.idempotency_key,)).fetchone()
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise ConflictError("Idempotency key already used for a different request")
            run_id = previous["id"]
        else:
            conn.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)", (run_id, request.idempotency_key, fingerprint, "running", store.now(), None, json.dumps(body), "{}"))
            conn.executemany("INSERT INTO lineage VALUES(?,?)", [(run_id, d["id"]) for d in datasets])
            store.audit(conn, run_id, "run.started", {"fingerprint": fingerprint, "approved_mapping_id": request.approved_mapping_id})
    if previous:
        return store.get_run(run_id)
    if streamed and background:
        worker=threading.Thread(target=_process_run,args=(run_id,request,datasets,approved,True,False),daemon=True)
        worker.start()
        return store.get_run(run_id)
    _process_run(run_id,request,datasets,approved,streamed,True)
    return store.get_run(run_id)


def _process_run(run_id,request,datasets,approved,streamed,raise_errors=True):
    try:
        if streamed:
            from .streaming import process_streamed
            process_streamed(run_id,request,datasets,approved)
        else:
            _process(run_id, request, datasets, approved)
    except Exception as error:
        # Do not persist stack traces containing payloads or credentials.
        with store.db() as conn:
            message=str(error)[:1000] if isinstance(error,ValueError) else "Execution failed; inspect server logs with sensitive-data filtering"
            report = {"error": type(error).__name__, "message": message, "accepted": 0, "rejected": 0}
            conn.execute("DELETE FROM stream_run_rows WHERE run_id=?",(run_id,))
            conn.execute("DELETE FROM stream_run_keys WHERE run_id=?",(run_id,))
            conn.execute("UPDATE runs SET status='failed',finished_at=?,report_json=? WHERE id=?", (store.now(), json.dumps(report), run_id))
            store.audit(conn, run_id, "run.failed", {"error_type": type(error).__name__})
        if raise_errors:raise


def transform_batch(spec, datasets, approved=None):
    """Pure transformation and DQ evaluation, shared by previews and publication."""
    mappings, accepted, rejected, changes, filtered = [], [], [], [], []
    seen = set()
    row_number = 0
    for dataset in datasets:
        from .privacy import decrypt_source, sensitive_source_paths
        clear_rows=decrypt_source(dataset['rows'])
        sensitive_paths=sensitive_source_paths(dataset.get('metadata',{}))
        mapping = approved[dataset["id"]] if approved is not None else infer_mapping(dataset["rows"], spec)
        mappings.append({"dataset_id": dataset["id"], "name": dataset["name"], "columns": mapping})
        lookup = {m["target"]: m["source"] for m in mapping}
        for source_row_number, raw in enumerate(dataset["rows"], 1):
            row_number += 1
            flat = flatten(clear_rows[source_row_number-1])
            record, issues, row_changes = {}, [], []
            for column in spec.columns:
                path = lookup[column.name]
                try:
                    if path is None and column.required:
                        raise ValueError("Required column mapping unresolved")
                    if path in sensitive_paths and column.protection=='none':raise ValueError('Sensitive source field requires an explicit target protection policy')
                    value, edits = clean_value(flat.get(path) if path is not None else None, column)
                    record[column.name] = value
                    row_changes.extend({"column": column.name, **edit} for edit in edits)
                except (ValueError, TypeError, OverflowError) as error:
                    sensitive=column.protection!='none' or path in sensitive_paths
                    issues.append({"column": column.name, "code": "mapping" if path is None else "quality", "message": 'Protected value violates the column contract' if sensitive else str(error), "source": path})
            if not issues:
                try:
                    row_changes.extend(derive(record,spec.derived_columns))
                    if not included(record,spec.row_filters):
                        filtered.append({'row_number':row_number,'dataset_id':dataset['id'],'source_row':source_row_number,'record':record})
                        continue
                except (ValueError,TypeError,OverflowError) as error:
                    issues.append({'column':'_row','code':'transformation','message':str(error)})
            key = record.get(spec.primary_key)
            if not issues and spec.deduplication=='reject' and str(key) in seen:
                issues.append({"column": spec.primary_key, "code": "duplicate", "message": "Duplicate primary key within this batch"})
            provenance = {"dataset_id": dataset["id"], "source_row": source_row_number}
            if issues:
                rejected.append({"row_number": row_number, "raw": raw, "issues": issues, **provenance})
            else:
                seen.add(str(key))
                accepted.append({"row_number": row_number, "record": record, **provenance})
            if row_changes:
                changes.append({"row_number": row_number, "changes": row_changes, **provenance})
    if spec.deduplication=='latest':
        winners={}
        for row in accepted:
            key=str(row['record'][spec.primary_key]);value=row['record'][spec.dedup_order_by]
            if value is None:raise ValueError('Deduplication order values cannot be null')
            previous=winners.get(key)
            if previous is None:winners[key]=row
            else:
                try:newer=value>previous['record'][spec.dedup_order_by]
                except TypeError:raise ValueError('Deduplication order types must be consistent') from None
                loser=previous if newer else row
                filtered.append({**loser,'reason':'duplicate_superseded'})
                if newer:winners[key]=row
        accepted=sorted(winners.values(),key=lambda r:r['row_number'])
    evaluated=row_number-len(filtered)
    ratio = len(rejected) / evaluated if evaluated else 0
    # Schema changes never silently reinterpret an existing destination.
    contract = target_contract(spec)
    status = "succeeded" if accepted and ratio <= spec.max_reject_ratio else "quality_failed"
    return {"accepted":accepted,"rejected":rejected,"filtered":filtered,"changes":changes,"mappings":mappings,"total":row_number,"ratio":ratio,"contract":contract,"status":status,'gold_preview':aggregate([r['record'] for r in accepted],spec.gold)}


def _process(run_id, request, datasets, approved=None):
    spec=request.spec
    batch=transform_batch(spec,datasets,approved)
    accepted,rejected,changes,mappings=[batch[key] for key in ("accepted","rejected","changes","mappings")]
    row_number,ratio,contract,status=[batch[key] for key in ("total","ratio","contract","status")]
    with store.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT contract_json FROM target_contracts WHERE target=?", (spec.target,)).fetchone()
        contract_error = existing is not None and existing[0] != contract
        if contract_error:
            status = "contract_failed"
        quality_checks=batch_checks(conn,spec,accepted)
        load_error=preflight(conn,spec,accepted)
        if status=='succeeded' and any(not c['passed'] and c['severity']=='critical' for c in quality_checks):status='quality_failed'
        if status=='succeeded' and load_error:status='load_blocked'
        load_rows=accepted
        skipped_existing=0
        if status=='succeeded' and spec.load.mode=='upsert' and spec.load.duplicate_policy=='skip':
            conn.execute('CREATE TEMP TABLE IF NOT EXISTS incoming_keys(record_key TEXT PRIMARY KEY)')
            conn.execute('DELETE FROM incoming_keys')
            conn.executemany('INSERT OR IGNORE INTO incoming_keys VALUES(?)',[(str(r['record'][spec.primary_key]),) for r in accepted])
            existing_keys={r[0] for r in conn.execute('SELECT i.record_key FROM incoming_keys i JOIN warehouse w ON w.target=? AND w.record_key=i.record_key',(spec.target,))}
            load_rows=[r for r in accepted if str(r['record'][spec.primary_key]) not in existing_keys]
            skipped_existing=len(accepted)-len(load_rows)
        conn.executemany("INSERT INTO silver VALUES(?,?,?)", [(run_id, r["row_number"], json.dumps(r)) for r in accepted])
        conn.executemany('INSERT INTO filtered_rows VALUES(?,?,?)',[(run_id,r['row_number'],json.dumps(r)) for r in batch['filtered']])
        conn.executemany("INSERT INTO quarantine VALUES(?,?,?,?)", [(run_id, r["row_number"], json.dumps({"record": r["raw"], "dataset_id": r["dataset_id"], "source_row": r["source_row"]}), json.dumps(r["issues"])) for r in rejected])
        gold=[]
        if status == "succeeded":
            conn.execute("INSERT OR IGNORE INTO target_contracts VALUES(?,?)", (spec.target, contract))
            prepare(conn,run_id,spec,load_rows)
            if spec.load.mode=='upsert' and spec.load.duplicate_policy=='skip':
                conn.executemany("INSERT OR IGNORE INTO warehouse VALUES(?,?,?,?,?)", [(spec.target, str(r["record"][spec.primary_key]), json.dumps(r["record"]), run_id, store.now()) for r in load_rows])
            else:
                conn.executemany("INSERT INTO warehouse VALUES(?,?,?,?,?) ON CONFLICT(target,record_key) DO UPDATE SET record_json=excluded.record_json,run_id=excluded.run_id,updated_at=excluded.updated_at", [(spec.target, str(r["record"][spec.primary_key]), json.dumps(r["record"]), run_id, store.now()) for r in load_rows])
            gold=publish_gold(conn,run_id,spec)
            published={r['record_key']:json.loads(r['record_json']) for r in conn.execute('SELECT record_key,record_json FROM warehouse WHERE target=?',(spec.target,))}
            if any(published.get(str(r['record'][spec.primary_key]))!=r['record'] for r in load_rows):raise ValueError('Post-load reconciliation failed; transaction rolled back')
        report = {"total": row_number, "accepted": len(accepted), "rejected": len(rejected), "reject_ratio": ratio, "quality_score": round(100 * (1 - ratio), 2), "published": len(load_rows) if status == "succeeded" else 0, "skipped_existing": skipped_existing if status == "succeeded" else 0, "mappings": mappings, "changes": changes[:200], "changes_truncated": len(changes) > 200, "gate": {"max_reject_ratio": spec.max_reject_ratio, "passed": status == "succeeded"}, "contract_error": contract_error, "preview": accepted[:20], "issues": rejected[:100]}
        report.update({'filtered':len(batch['filtered']),'filtered_preview':batch['filtered'][:100],'gold_rows':len(gold),'gold_preview':gold[:20], 'layers':{'bronze':{'dataset_ids':[d['id'] for d in datasets],'rows':row_number},'silver':{'valid':len(accepted),'quarantined':len(rejected),'filtered':len(batch['filtered']),'published':status=='succeeded'},'gold':{'configured':spec.gold is not None,'published':status=='succeeded' and spec.gold is not None,'rows':len(gold),'scope':'full current upserted detail target'}}})
        report.update({'quality_checks':quality_checks,'load_mode':spec.load.mode,'load_error':load_error,'post_load_reconciled':status=='succeeded'})
        if request.pipeline_id: report["pipeline_version"]={"id":request.pipeline_id,"revision":request.pipeline_revision}
        conn.execute("UPDATE runs SET status=?,finished_at=?,report_json=? WHERE id=?", (status, store.now(), json.dumps(report), run_id))
        store.audit(conn, run_id, "run.finished", {"status": status, "accepted": len(accepted), "rejected": len(rejected), "published": report["published"], "skipped_existing": report["skipped_existing"]})
