import hmac
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from . import store
from .ai import provider, public_config
from .engine import ConflictError, execute
from .extraction import extract
from .generation import artifacts, generate
from .ingest import fetch_api, ingest_bytes, MAX_ROWS
from .mapping import infer_mapping
from .models import PipelineSpec, PlanRequest, RunRequest, StrictModel, TranslateRequest, demo_spec
from .remediation import LogRequest, apply, diagnose, propose
from .translation import translate
from .schema_discovery import DiscoveryRequest, discover
from .semantic_mapping import MappingRequest, MappingApproval, propose_mapping, approve_mapping, get_proposal

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@asynccontextmanager
async def lifespan(app):
    store.init()
    # Single-process deployment: an interrupted transaction never publishes a partial batch.
    with store.db() as conn:
        for row in conn.execute("SELECT id FROM runs WHERE status='running'").fetchall():
            conn.execute("UPDATE runs SET status='interrupted',finished_at=? WHERE id=?", (store.now(), row["id"]))
            store.audit(conn, row["id"], "run.interrupted", {"reason": "process restarted; replay with a new key after review"})
        conn.execute("UPDATE extraction_batches SET status='interrupted',finished_at=? WHERE status='extracting'",(store.now(),))
    from .notifications import NotificationWorker
    mail_worker=NotificationWorker(); mail_worker.start()
    try:
        yield
    finally:
        mail_worker.close()


app = FastAPI(title="FlowForge · GenAI ETL & Data Quality", version="1.5.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def request_bounds(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH"):
        length = request.headers.get("content-length")
        # Reject streaming requests in this bounded local service; production ingress must
        # enforce its own limit before multipart parsing or authentication.
        if length is None and request.headers.get("transfer-encoding"):
            return JSONResponse(status_code=411, content={"detail": "Content-Length is required"})
        try:
            size = int(length or 0)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
        if size < 0 or size > int(os.getenv("MAX_UPLOAD_BYTES", "31457280")) + 65536:
            return JSONResponse(status_code=413, content={"detail": "Request exceeds configured limit"})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def authorize(request: Request):
    token = os.getenv("APP_API_TOKEN", "")
    if token:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(401, "A valid bearer token is required")
    elif request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(403, "Remote access requires APP_API_TOKEN")
    # Protect localhost mutations against cross-origin requests, including multipart uploads.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Cross-origin writes are not allowed")


@app.exception_handler(ValueError)
async def bad_input(request, error):
    content={"detail": str(error)[:2000]}
    from .llm_router import ModelError
    status = 409 if isinstance(error, ConflictError) else 400
    if isinstance(error, ModelError):
        content['llm_error'] = {'category': error.category, 'attempts': error.attempts}
        if error.category == 'rate_limit':
            status = 429
    return JSONResponse(status_code=status, content=content)


@app.get("/health")
def health():
    with store.db() as conn:
        conn.execute("SELECT 1").fetchone()
    return {"status": "ok", "version": "1.5.0"}


@app.get("/api/config", dependencies=[Depends(authorize)])
def config():
    routing=public_config()
    return {"provider": provider(), "llm_routing":routing, "live_ai_configured": routing["configured"], "default_spec": demo_spec().model_dump(), "max_upload_bytes": int(os.getenv("MAX_UPLOAD_BYTES", "31457280")), "max_rows_per_source": MAX_ROWS}


@app.get("/api/schema", dependencies=[Depends(authorize)])
def schema():
    return app.openapi()


@app.get("/api/datasets", dependencies=[Depends(authorize)])
def datasets():
    with store.db() as conn:
        rows = conn.execute("SELECT id,name,sha256,created_at,rows_json,metadata_json FROM datasets ORDER BY created_at DESC LIMIT 100").fetchall()
    return [{"id": r["id"], "name": r["name"], "sha256": r["sha256"], "created_at": r["created_at"], "row_count": json.loads(r["metadata_json"]).get("row_count",len(json.loads(r["rows_json"]))), "metadata": json.loads(r["metadata_json"])} for r in rows]


async def read_upload(file):
    maximum = int(os.getenv("MAX_UPLOAD_BYTES", "31457280"))
    raw = await file.read(maximum + 1)
    if len(raw) > maximum:
        raise HTTPException(413, "Upload exceeds configured limit")
    return raw


@app.post("/api/datasets/upload", dependencies=[Depends(authorize)])
async def upload(file: UploadFile = File(...),delimiter:str|None=Form(default=None),upload_id:str|None=Header(default=None,alias='X-Upload-ID')):
    raw = await read_upload(file)
    # Parsing is bounded; no filenames from uploads are ever used as filesystem paths.
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(ingest_bytes, raw, Path(file.filename or "upload.csv").name,delimiter,upload_id)


class ApiSource(StrictModel):
    url: str = Field(max_length=2000)


@app.post("/api/datasets/api", dependencies=[Depends(authorize)])
def api_source(body: ApiSource):
    return fetch_api(body.url)


@app.post("/api/extract", dependencies=[Depends(authorize)])
async def extraction(file: UploadFile = File(...)):
    raw = await read_upload(file)
    # Long AI/PDF work runs in the worker pool, preserving server responsiveness.
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(extract, raw, Path(file.filename or "document.txt").name)


@app.post("/api/generate", dependencies=[Depends(authorize)])
def generation(body: PlanRequest):
    return generate(body.prompt)


@app.post("/api/artifacts", dependencies=[Depends(authorize)])
def export(body: PipelineSpec):
    return artifacts(body)


class ProfileRequest(StrictModel):
    dataset_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    spec: PipelineSpec


@app.post("/api/mapping", dependencies=[Depends(authorize)])
def mapping(body: ProfileRequest):
    data = store.get_dataset_info(body.dataset_id)
    if data["row_count"] > 25_000:
        rows=store.get_dataset_sample(body.dataset_id)
    else:
        rows=store.get_dataset(body.dataset_id)["rows"]
    return {"mapping": infer_mapping(rows, body.spec), "sample": rows[:10]}


@app.post("/api/runs", dependencies=[Depends(authorize)])
def run(body: RunRequest):
    return execute(body,background=True)


@app.post("/api/mappings/discover", dependencies=[Depends(authorize)])
def discover_schema(body: DiscoveryRequest):
    return discover(body)


@app.post("/api/mappings/generate", dependencies=[Depends(authorize)])
def semantic_mapping(body: MappingRequest):
    return propose_mapping(body)


@app.get("/api/mappings/{proposal_id}", dependencies=[Depends(authorize)])
def mapping_proposal(proposal_id: str):
    return get_proposal(proposal_id)


@app.post("/api/mappings/{proposal_id}/approve", dependencies=[Depends(authorize)])
def mapping_approval(proposal_id: str, body: MappingApproval):
    return approve_mapping(proposal_id, body)


@app.get("/api/runs", dependencies=[Depends(authorize)])
def runs():
    with store.db() as conn:
        rows = conn.execute("SELECT id,status,created_at,finished_at,report_json FROM runs ORDER BY created_at DESC LIMIT 100").fetchall()
    return [{**{k: r[k] for k in ("id", "status", "created_at", "finished_at")}, "report": {k: v for k, v in json.loads(r["report_json"]).items() if k in ("total", "accepted", "rejected", "published", "quality_score","progress")}} for r in rows]


@app.get("/api/runs/{run_id}", dependencies=[Depends(authorize)])
def run_detail(run_id: str):
    return store.get_run(run_id)


@app.get("/api/runs/{run_id}/quarantine", dependencies=[Depends(authorize)])
def quarantine(run_id: str, offset: int = 0, limit: int = 100):
    if offset < 0 or not 1 <= limit <= 1000:
        raise ValueError("Use nonnegative offset and limit 1–1000")
    with store.db() as conn:
        rows = conn.execute("SELECT * FROM quarantine WHERE run_id=? ORDER BY row_number LIMIT ? OFFSET ?", (run_id, limit, offset)).fetchall()
    return [{"row_number": r["row_number"], "source": json.loads(r["raw_json"]), "issues": json.loads(r["issues_json"])} for r in rows]


@app.get("/api/warehouse", dependencies=[Depends(authorize)])
def warehouse(target: str = "products", offset: int = 0, limit: int = 100):
    if offset < 0 or not 1 <= limit <= 1000:
        raise ValueError("Use nonnegative offset and limit 1–1000")
    with store.db() as conn:
        total = conn.execute("SELECT count(*) FROM warehouse WHERE target=?", (target,)).fetchone()[0]
        rows = conn.execute("SELECT * FROM warehouse WHERE target=? ORDER BY record_key LIMIT ? OFFSET ?", (target, limit, offset)).fetchall()
    return {"total": total, "rows": [{"record": json.loads(r["record_json"]), "run_id": r["run_id"], "updated_at": r["updated_at"]} for r in rows]}


@app.get("/api/warehouse/targets", dependencies=[Depends(authorize)])
def warehouse_targets():
    with store.db() as conn:
        rows = conn.execute("SELECT target,count(*) AS total FROM warehouse GROUP BY target ORDER BY target").fetchall()
    return [{"target": row["target"], "total": row["total"]} for row in rows]


@app.get("/api/warehouse/validate", dependencies=[Depends(authorize)])
def validate_warehouse(target: str):
    import math

    def matches_type(value, dtype):
        if dtype == "string":
            return isinstance(value, str)
        if dtype == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            return False

    with store.db() as conn:
        target_exists = conn.execute("SELECT 1 FROM warehouse WHERE target=? LIMIT 1", (target,)).fetchone()
        if target_exists is None:
            raise HTTPException(404, "No published warehouse data exists for this target")
        contract_row = conn.execute("SELECT contract_json FROM target_contracts WHERE target=?", (target,)).fetchone()
        if contract_row is None:
            raise HTTPException(409, "No published schema contract is available for this target")
        contract = json.loads(contract_row["contract_json"])
        columns = contract["columns"]
        primary_key = contract["primary_key"]
        column_names = {column["name"] for column in columns}
        missing = {column["name"]: 0 for column in columns}
        invalid_types = {column["name"]: 0 for column in columns}
        unexpected = {}
        invalid_json = 0
        invalid_primary_key = 0
        invalid_records = 0
        checked = 0
        rows = conn.execute("SELECT record_key,record_json FROM warehouse WHERE target=? ORDER BY record_key", (target,))
        while True:
            batch = rows.fetchmany(500)
            if not batch:
                break
            for row in batch:
                checked += 1
                row_invalid = False
                try:
                    record = json.loads(row["record_json"])
                except (json.JSONDecodeError, TypeError):
                    invalid_json += 1
                    invalid_records += 1
                    continue
                if not isinstance(record, dict):
                    invalid_json += 1
                    invalid_records += 1
                    continue
                for name in column_names - record.keys():
                    missing[name] += 1
                    row_invalid = True
                for name in record.keys() - column_names:
                    unexpected[name] = unexpected.get(name, 0) + 1
                    row_invalid = True
                for column in columns:
                    name, dtype = column["name"], column["dtype"]
                    if name not in record or record[name] is None:
                        continue
                    if not matches_type(record[name], dtype):
                        invalid_types[name] += 1
                        row_invalid = True
                key_value = record.get(primary_key)
                if key_value is None or str(key_value) != row["record_key"]:
                    invalid_primary_key += 1
                    row_invalid = True
                if row_invalid:
                    invalid_records += 1

    checks = [
        {"check": "valid_json_record", "column": None, "invalid_records": invalid_json},
        {"check": "primary_key_matches_warehouse_key", "column": primary_key, "invalid_records": invalid_primary_key},
    ]
    checks.extend({"check": "column_present", "column": name, "invalid_records": count} for name, count in missing.items())
    checks.extend({"check": "data_type", "column": name, "invalid_records": count} for name, count in invalid_types.items())
    checks.extend({"check": "unexpected_column", "column": name, "invalid_records": count} for name, count in sorted(unexpected.items()))
    return {
        "target": target,
        "rows_checked": checked,
        "invalid_records": invalid_records,
        "passed": invalid_records == 0,
        "checks": checks,
    }


@app.post("/api/translate", dependencies=[Depends(authorize)])
def translation(body: TranslateRequest):
    return translate(body)


@app.post("/api/diagnose", dependencies=[Depends(authorize)])
def diagnosis(body: LogRequest):
    return diagnose(body.log)


@app.post("/api/runs/{run_id}/remediation", dependencies=[Depends(authorize)])
def remediation(run_id: str):
    return propose(run_id)


class Approval(StrictModel):
    approved: bool


@app.post("/api/remediations/{proposal_id}/apply", dependencies=[Depends(authorize)])
def apply_remediation(proposal_id: str, body: Approval):
    if not body.approved:
        raise ValueError("Review and approve the proposal before applying")
    return apply(proposal_id)


@app.post("/api/demo", dependencies=[Depends(authorize)])
def demo():
    # Deliberately strict numeric parser triggers a real quality failure for remediation.
    dataset = ingest_bytes((ROOT / "samples" / "products.csv").read_bytes(), "demo_products.csv")
    spec = demo_spec()
    spec.max_reject_ratio = 0.2
    result = execute(RunRequest(spec=spec, dataset_ids=[dataset["id"]], idempotency_key="demo:" + uuid.uuid4().hex))
    return {"dataset": dataset, "run": result}


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


from .control_api import router as control_router
from .changes_api import router as changes_router
from .medallion_api import router as medallion_router
app.include_router(control_router(authorize))
app.include_router(changes_router(authorize))
app.include_router(medallion_router(authorize))
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
