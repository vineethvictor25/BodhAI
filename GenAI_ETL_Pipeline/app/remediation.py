"""Versioned, bounded remediation of transformation recipes; no arbitrary code patching."""
import json
import re
import uuid
from pydantic import Field
from .models import StrictModel, RunRequest
from . import store
from .engine import ConflictError, execute
from .generation import artifacts


class LogRequest(StrictModel):
    log: str = Field(min_length=1, max_length=20000)


def diagnose(log):
    rules = [(r"429|rate.?limit", "provider_rate_limit", "Retry with exponential backoff and a retry budget; reduce concurrency.", True), (r"Timeout|ConnectionReset|503|502", "transient_io", "Retry the read-only operation with bounded backoff; preserve the same idempotency key.", True), (r"KeyError|column.*not.*found|schema", "schema_drift", "Re-profile source columns and review an explicit mapping override.", False), (r"float|numeric|number|currency", "numeric_format", "Review a currency_number transformation and replay quarantined rows.", False), (r"permission|401|403|denied", "authorization", "Correct access or credentials outside pipeline code. Do not retry blindly.", False)]
    for pattern, cause, proposal, retryable in rules:
        if re.search(pattern, log, re.I):
            return {"root_cause": cause, "proposal": proposal, "retryable": retryable, "confidence": "heuristic", "applied": False}
    return {"root_cause": "unknown", "proposal": "Human investigation required; no automatic patch is available.", "retryable": False, "confidence": "unknown", "applied": False}


def propose(run_id):
    run = store.get_run(run_id)
    if run["status"] not in ("quality_failed", "succeeded") or not run["report"].get("rejected"):
        raise ValueError("A completed run with rejected records is required")
    request = RunRequest.model_validate(run["request"])
    from .cleaning import currency_number
    patch = []
    # Only an existing numeric parse failure with a provably parseable currency value qualifies.
    for column in request.spec.columns:
        if column.dtype not in ("number", "integer") or column.transforms != ["number"]:
            continue
        candidates = []
        for rejected in run["report"].get("issues", []):
            for issue in rejected["issues"]:
                if issue["column"] == column.name and issue.get("source") and "float" in issue["message"]:
                    from .mapping import flatten
                    value = flatten(rejected["raw"]).get(issue["source"])
                    try:
                        currency_number(value)
                        candidates.append(value)
                    except (ValueError, TypeError):
                        pass
        if candidates:
            patch.append({"column": column.name, "before": ["number"], "after": ["currency_number"], "evidence_count": len(candidates)})
            column.transforms = ["currency_number"]
    if not patch:
        raise ValueError("No supported automatic recipe patch. Review mapping, units, bounds or source data manually.")
    proposal_id = uuid.uuid4().hex
    proposal = {"id": proposal_id, "run_id": run_id, "root_cause": "Currency symbols/group separators rejected by numeric parser", "patch": patch, "spec": request.spec.model_dump(), "requires_approval": True, "risk": "Supports US-style grouping only; no values are invented. Original gate and all other rules remain in force.", "artifacts": artifacts(request.spec)}
    with store.db() as conn:
        conn.execute("INSERT INTO remediations VALUES(?,?,?,?,NULL)", (proposal_id, run_id, store.now(), json.dumps(proposal)))
        store.audit(conn, run_id, "remediation.proposed", {"proposal_id": proposal_id, "patch": patch})
    return proposal


def apply(proposal_id):
    with store.db() as conn:
        row = conn.execute("SELECT * FROM remediations WHERE id=?", (proposal_id,)).fetchone()
    if not row:
        raise ValueError("Proposal not found")
    if row["applied_run_id"]:
        return store.get_run(row["applied_run_id"])
    proposal = json.loads(row["proposal_json"])
    original = store.get_run(row["run_id"])
    request = RunRequest.model_validate({**original["request"], "spec": proposal["spec"], "idempotency_key": "remediation:" + proposal_id})
    result = execute(request)
    with store.db() as conn:
        conn.execute("UPDATE remediations SET applied_run_id=? WHERE id=?", (result["id"], proposal_id))
        store.audit(conn, result["id"], "remediation.applied", {"proposal_id": proposal_id, "parent_run_id": row["run_id"]})
    return result
