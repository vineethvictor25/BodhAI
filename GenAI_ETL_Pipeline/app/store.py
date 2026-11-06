"""SQLite persistence; WAL, atomic publish, durable lineage, and idempotency."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def data_dir():
    path = Path(os.getenv("DATA_DIR", "data")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def db():
    conn = sqlite3.connect(data_dir() / "warehouse.db", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init():
    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS datasets (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL,
          created_at TEXT NOT NULL, rows_json TEXT NOT NULL, metadata_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS dataset_rows (
          dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
          row_number INTEGER NOT NULL, record_json TEXT NOT NULL,
          PRIMARY KEY(dataset_id,row_number));
        CREATE TABLE IF NOT EXISTS stream_run_rows (
          run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
          row_number INTEGER NOT NULL, dataset_id TEXT NOT NULL,
          source_row INTEGER NOT NULL, record_json TEXT, raw_json TEXT, issues_json TEXT,
          kind TEXT NOT NULL CHECK(kind IN ('accepted','rejected','filtered')),
          PRIMARY KEY(run_id,row_number));
        CREATE TABLE IF NOT EXISTS stream_run_keys (
          run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
          record_key TEXT NOT NULL, PRIMARY KEY(run_id,record_key));
        CREATE INDEX IF NOT EXISTS stream_rows_by_kind ON stream_run_rows(run_id,kind,row_number);
        CREATE TABLE IF NOT EXISTS bronze_objects (
          dataset_id TEXT PRIMARY KEY REFERENCES datasets(id), content BLOB NOT NULL,
          representation TEXT NOT NULL, byte_count INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS gold_snapshots (
          run_id TEXT PRIMARY KEY REFERENCES runs(id), target TEXT NOT NULL,
          definition_json TEXT NOT NULL, rows_json TEXT NOT NULL, lineage_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS gold_current (
          target TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES gold_snapshots(run_id));
        CREATE TABLE IF NOT EXISTS dimension_history (
          target TEXT NOT NULL, business_key TEXT NOT NULL, version INTEGER NOT NULL,
          surrogate_key TEXT NOT NULL UNIQUE, row_hash TEXT NOT NULL, record_json TEXT NOT NULL,
          effective_from TEXT NOT NULL, effective_to TEXT, is_current INTEGER NOT NULL,
          run_id TEXT NOT NULL REFERENCES runs(id), PRIMARY KEY(target,business_key,version));
        CREATE UNIQUE INDEX IF NOT EXISTS dimension_one_current ON dimension_history(target,business_key) WHERE is_current=1;
        CREATE TABLE IF NOT EXISTS filtered_rows (
          run_id TEXT NOT NULL REFERENCES runs(id),row_number INTEGER NOT NULL,record_json TEXT NOT NULL,
          PRIMARY KEY(run_id,row_number));
        CREATE TABLE IF NOT EXISTS extraction_batches (
          id TEXT PRIMARY KEY,source_name TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,
          status TEXT NOT NULL,sha256 TEXT NOT NULL,content BLOB NOT NULL,dataset_id TEXT,
          details_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS source_contracts (
          source_name TEXT PRIMARY KEY,revision INTEGER NOT NULL,contract_json TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS source_contract_versions (
          source_name TEXT NOT NULL,revision INTEGER NOT NULL,contract_json TEXT NOT NULL,created_at TEXT NOT NULL,
          PRIMARY KEY(source_name,revision));
        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
          status TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT,
          request_json TEXT NOT NULL, report_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS lineage (
          run_id TEXT NOT NULL REFERENCES runs(id), dataset_id TEXT NOT NULL REFERENCES datasets(id),
          PRIMARY KEY(run_id,dataset_id));
        CREATE TABLE IF NOT EXISTS silver (
          run_id TEXT NOT NULL REFERENCES runs(id), row_number INTEGER NOT NULL,
          record_json TEXT NOT NULL, PRIMARY KEY(run_id,row_number));
        CREATE TABLE IF NOT EXISTS quarantine (
          run_id TEXT NOT NULL REFERENCES runs(id), row_number INTEGER NOT NULL,
          raw_json TEXT NOT NULL, issues_json TEXT NOT NULL, PRIMARY KEY(run_id,row_number));
        CREATE TABLE IF NOT EXISTS warehouse (
          target TEXT NOT NULL, record_key TEXT NOT NULL, record_json TEXT NOT NULL,
          run_id TEXT NOT NULL REFERENCES runs(id), updated_at TEXT NOT NULL,
          PRIMARY KEY(target,record_key));
        CREATE TABLE IF NOT EXISTS target_contracts (
          target TEXT PRIMARY KEY, contract_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
          created_at TEXT NOT NULL, kind TEXT NOT NULL, details_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS remediations (
          id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), created_at TEXT NOT NULL,
          proposal_json TEXT NOT NULL, applied_run_id TEXT);
        CREATE TABLE IF NOT EXISTS mapping_proposals (
          id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
          proposal_json TEXT NOT NULL, approved_json TEXT);
        CREATE TABLE IF NOT EXISTS kb_documents (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, sha256 TEXT NOT NULL,
          trusted INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS kb_chunks (
          id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES kb_documents(id),
          ordinal INTEGER NOT NULL, start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL,
          text TEXT NOT NULL);
        CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(chunk_id UNINDEXED, text);
        CREATE TABLE IF NOT EXISTS kb_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        INSERT OR IGNORE INTO kb_meta VALUES('revision','0');
        CREATE TABLE IF NOT EXISTS evaluation_runs (
          id TEXT PRIMARY KEY, created_at TEXT NOT NULL, result_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS managed_pipelines (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
          active_revision INTEGER NOT NULL, source_run_id TEXT NOT NULL REFERENCES runs(id));
        CREATE TABLE IF NOT EXISTS pipeline_versions (
          pipeline_id TEXT NOT NULL REFERENCES managed_pipelines(id), revision INTEGER NOT NULL,
          created_at TEXT NOT NULL, parent_revision INTEGER, kind TEXT NOT NULL,
          definition_json TEXT NOT NULL, definition_hash TEXT NOT NULL, metadata_json TEXT NOT NULL,
          PRIMARY KEY(pipeline_id,revision));
        CREATE TABLE IF NOT EXISTS pipeline_changes (
          id TEXT PRIMARY KEY, pipeline_id TEXT NOT NULL REFERENCES managed_pipelines(id),
          base_revision INTEGER NOT NULL, parent_change_id TEXT, created_at TEXT NOT NULL,
          request_key TEXT NOT NULL, request_hash TEXT NOT NULL, status TEXT NOT NULL,
          proposal_json TEXT NOT NULL, preview_json TEXT, activated_revision INTEGER,
          UNIQUE(pipeline_id,request_key));
        CREATE TABLE IF NOT EXISTS notification_outbox (
          id TEXT PRIMARY KEY, event_id INTEGER UNIQUE NOT NULL REFERENCES events(id),
          created_at TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
          available_at REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
          lease_owner TEXT, payload_json TEXT NOT NULL, last_error TEXT, sent_at TEXT);
        ''')


def audit(conn, run_id, kind, details):
    event=conn.execute("INSERT INTO events(run_id,created_at,kind,details_json) VALUES(?,?,?,?)",
                 (run_id, now(), kind, json.dumps(details)))
    from .notifications import queue_event
    queue_event(conn,event.lastrowid,run_id,kind,details)


def get_dataset(dataset_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        metadata=json.loads(row["metadata_json"]) if row else {}
        row_count=metadata["row_count"] if "row_count" in metadata else len(json.loads(row["rows_json"])) if row else 0
        if row and row_count>25_000:
            raise ValueError("Dataset exceeds the 25,000-row in-memory limit; use iter_dataset_rows for bounded processing")
        chunked = bool(row and metadata.get("chunked"))
        chunk_rows = conn.execute("SELECT record_json FROM dataset_rows WHERE dataset_id=? ORDER BY row_number",(dataset_id,)).fetchall() if chunked else []
    if row is None:
        raise ValueError("Dataset not found: " + dataset_id)
    result = dict(row)
    result["rows"] = [json.loads(r["record_json"]) for r in chunk_rows] if chunked else json.loads(result.pop("rows_json"))
    if chunked:result.pop("rows_json")
    result["metadata"] = json.loads(result.pop("metadata_json"))
    return result


def get_dataset_info(dataset_id):
    with db() as conn:
        row=conn.execute("SELECT id,name,sha256,created_at,metadata_json,rows_json FROM datasets WHERE id=?",(dataset_id,)).fetchone()
    if row is None:raise ValueError("Dataset not found: "+dataset_id)
    metadata=json.loads(row["metadata_json"])
    return {k:row[k] for k in ("id","name","sha256","created_at")} | {"metadata":metadata,"row_count":metadata.get("row_count",len(json.loads(row["rows_json"]))),"chunked":bool(metadata.get("chunked"))}


def iter_dataset_rows(dataset_id,batch_size=25000):
    with db() as conn:
        metadata=conn.execute("SELECT metadata_json,rows_json FROM datasets WHERE id=?",(dataset_id,)).fetchone()
        if metadata is None:raise ValueError("Dataset not found: "+dataset_id)
        if not json.loads(metadata["metadata_json"]).get("chunked"):
            rows=json.loads(metadata["rows_json"])
            for start in range(0,len(rows),batch_size):yield start+1,rows[start:start+batch_size]
            return
        cursor=conn.execute("SELECT row_number,record_json FROM dataset_rows WHERE dataset_id=? ORDER BY row_number",(dataset_id,))
        while True:
            batch=cursor.fetchmany(batch_size)
            if not batch:break
            yield batch[0]["row_number"],[json.loads(row["record_json"]) for row in batch]


def get_dataset_sample(dataset_id,limit=25000):
    if not 1<=limit<=25_000:raise ValueError("Dataset sample limit must be between 1 and 25,000 rows")
    return next(iter_dataset_rows(dataset_id,batch_size=limit),(1,[]))[1]


def get_run(run_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        events = conn.execute("SELECT created_at,kind,details_json FROM events WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
    if row is None:
        raise ValueError("Run not found")
    result = dict(row)
    result["request"] = json.loads(result.pop("request_json"))
    result["report"] = json.loads(result.pop("report_json"))
    result["events"] = [{"at": e["created_at"], "kind": e["kind"], "details": json.loads(e["details_json"])} for e in events]
    return result
