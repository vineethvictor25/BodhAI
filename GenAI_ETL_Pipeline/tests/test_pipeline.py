import ast
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from app import store
from app.cleaning import currency_number, length_m
from app.engine import ConflictError, execute
from app.generation import artifacts, generate
from app.ingest import decode_rows, ingest_bytes, save_rows
from app.mapping import infer_mapping
from app.models import RunRequest, demo_spec
from app.remediation import apply, diagnose, propose

ROOT = Path(__file__).resolve().parent.parent


def sample_request(key="test-run"):
    data = ingest_bytes((ROOT / "samples/products.csv").read_bytes(), "products.csv")
    spec = demo_spec()
    spec.max_reject_ratio = .2
    return RunRequest(spec=spec, dataset_ids=[data["id"]], idempotency_key=key)


@pytest.mark.parametrize("value,expected", [("Length = 200cm", 2), ("2m", 2), ("450mm", .45), ("12in", .3048), ("1ft", .3048)])
def test_unit_equivalence(value, expected):
    assert length_m(value) == pytest.approx(expected)


@pytest.mark.parametrize("value", ["2", "200cms", "2m junk", 2, "NaNm"])
def test_ambiguous_measurements_rejected(value):
    with pytest.raises(ValueError):
        length_m(value)


@pytest.mark.parametrize("value", ["1.299,00", "$1,29", "NaN", "USD twelve"])
def test_ambiguous_currency_rejected(value):
    with pytest.raises(ValueError):
        currency_number(value)


def test_gate_atomicity_and_remediation():
    original = execute(sample_request())
    assert original["status"] == "quality_failed"
    assert original["report"]["accepted"] == 3
    assert original["report"]["rejected"] == 3
    with store.db() as conn:
        assert conn.execute("SELECT count(*) FROM warehouse").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM silver").fetchone()[0] == 3
    proposal = propose(original["id"])
    assert proposal["patch"][0]["after"] == ["currency_number"]
    healed = apply(proposal["id"])
    assert healed["status"] == "succeeded"
    assert healed["report"]["published"] == 5
    assert healed["report"]["rejected"] == 1
    assert healed["request"]["spec"]["max_reject_ratio"] == .2
    assert store.get_run(original["id"])["status"] == "quality_failed"
    with store.db() as conn:
        records = [json.loads(r[0]) for r in conn.execute("SELECT record_json FROM warehouse")]
        assert len(records) == 5
        assert all(r["product_id"] != "P-005" for r in records)
        assert next(r for r in records if r["product_id"] == "P-002")["price"] == 1299
        assert next(r for r in records if r["product_id"] == "P-004")["currency"] == "USD"
    assert apply(proposal["id"])["id"] == healed["id"]


def test_idempotency_and_mismatched_request():
    request = sample_request()
    first = execute(request)
    assert execute(request)["id"] == first["id"]
    request.spec.max_reject_ratio = 1
    with pytest.raises(ConflictError):
        execute(request)


def test_concurrent_idempotency_creates_one_run():
    request = sample_request()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: execute(request), range(4)))
    assert len({r["id"] for r in results}) == 1
    with store.db() as conn:
        assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 1


def test_nested_schema_mapping():
    dataset = ingest_bytes((ROOT / "samples/nested_products.json").read_bytes(), "nested.json")
    run = execute(RunRequest(spec=demo_spec(), dataset_ids=[dataset["id"]], idempotency_key="nested"))
    assert run["status"] == "succeeded"
    assert run["report"]["published"] == 2
    assert run["report"]["preview"][0]["record"]["length_m"] == 1.5


def test_ambiguity_requires_review_and_override():
    spec = demo_spec()
    rows = [{"sku": "A", "item_id": "B", "name": "Desk", "cost": 1}]
    mapping = infer_mapping(rows, spec)
    assert mapping[0]["source"] is None
    spec.mapping_overrides = {"product_id": "sku"}
    assert infer_mapping(rows, spec)[0]["source"] == "sku"


def test_schema_change_blocks_existing_destination():
    request = sample_request()
    request.spec.max_reject_ratio = 1
    first = execute(request)
    assert first["status"] == "succeeded"
    request.idempotency_key = "changed"
    request.spec.columns[-1].dtype = "integer"
    changed = execute(request)
    assert changed["status"] == "contract_failed"
    with store.db() as conn:
        assert conn.execute("SELECT count(*) FROM warehouse").fetchone()[0] == 3


def test_duplicate_keys_and_missing_required_values():
    data = save_rows([{"sku": "A", "name": "Desk", "price": 1}, {"sku": " a ", "name": "Chair", "price": 2}, {"sku": "B", "price": 3}], "duplicates")
    run = execute(RunRequest(spec=demo_spec(), dataset_ids=[data["id"]], idempotency_key="dupes"))
    assert run["report"]["accepted"] == 1
    assert run["report"]["rejected"] == 2
    assert any(i["code"] == "duplicate" for row in run["report"]["issues"] for i in row["issues"])


@pytest.mark.parametrize("raw,name", [(b'[{"x":NaN}]', 'a.json'), (b'a,a\n1,2', 'a.csv'), (b'[]','a.json'), (b'a,b\n1,2,3','a.csv')])
def test_bad_sources_rejected(raw, name):
    with pytest.raises(ValueError):
        decode_rows(raw,name)


def test_generated_artifacts_are_valid():
    result = generate("Load product catalog, normalize measurements and currency symbols, default currency to USD, quarantine at most 20% invalid rows")
    assert result["metadata"]["provider"] == "local"
    assert result["spec"]["max_reject_ratio"] == .2
    assert result["spec"]["columns"][3]["transforms"] == ["currency_number"]
    ast.parse(result["artifacts"]["generated_pipeline.py"])
    import yaml
    assert yaml.safe_load(result["artifacts"]["pipeline.yaml"]) == result["spec"]


def test_local_compiler_rejects_unsupported_domains():
    with pytest.raises(ValueError):
        generate("Join customer orders in Snowflake daily")
    with pytest.raises(ValueError):
        generate("Load products into Snowflake daily")


def test_log_diagnosis_does_not_apply_patch():
    result = diagnose("ConnectionResetError: socket closed")
    assert result["retryable"] is True
    assert result["applied"] is False
    assert diagnose("nondescript failure")["root_cause"] == "unknown"
