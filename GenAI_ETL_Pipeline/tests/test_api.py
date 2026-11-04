import io
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app import store
from app.main import app
from app.models import TranslateRequest
from app.translation import translate


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


def test_http_demo_repair_flow(client):
    assert client.get("/health").json()["status"] == "ok"
    original = client.post("/api/demo", json={}).json()["run"]
    assert original["status"] == "quality_failed"
    proposal = client.post(f'/api/runs/{original["id"]}/remediation').json()
    assert client.post(f'/api/remediations/{proposal["id"]}/apply',json={"approved":False}).status_code == 400
    replay = client.post(f'/api/remediations/{proposal["id"]}/apply',json={"approved":True})
    assert replay.status_code == 200
    assert replay.json()["status"] == "succeeded"
    assert client.get("/api/warehouse").json()["total"] == 5
    targets = client.get("/api/warehouse/targets").json()
    assert targets == [{"target": "products", "total": 5}]
    validation = client.get("/api/warehouse/validate", params={"target": "products"}).json()
    assert validation["passed"] is True
    assert validation["rows_checked"] == 5
    assert len(client.get(f'/api/runs/{original["id"]}/quarantine').json()) == 3
    with store.db() as conn:
        row = conn.execute("SELECT record_key,record_json FROM warehouse WHERE target='products' LIMIT 1").fetchone()
        record = json.loads(row["record_json"])
        record["price"] = "not-a-number"
        record.pop("category")
        conn.execute("UPDATE warehouse SET record_json=? WHERE target='products' AND record_key=?", (json.dumps(record), row["record_key"]))
    validation = client.get("/api/warehouse/validate", params={"target": "products"}).json()
    assert validation["passed"] is False
    assert validation["invalid_records"] == 1
    assert {check["column"] for check in validation["checks"] if check["invalid_records"]} == {"category", "price"}


def test_warehouse_validation_requires_published_target(client):
    response = client.get("/api/warehouse/validate", params={"target": "missing"})
    assert response.status_code == 404


def test_auth_and_cross_origin_blocked(client, monkeypatch):
    monkeypatch.setenv("APP_API_TOKEN", "test-secret")
    assert client.get("/api/datasets").status_code == 401
    assert client.get("/api/datasets",headers={"Authorization":"Bearer test-secret"}).status_code == 200
    assert client.post("/api/demo",json={},headers={"Authorization":"Bearer test-secret","Origin":"https://evil.example"}).status_code == 403


def test_upload_limits(client, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "10")
    assert client.post("/api/datasets/upload",files={"file":("a.csv",b"a\n"+b"x"*20)}).status_code == 413


def test_upload_accepts_more_than_10000_records_and_reports_limit(client):
    raw = ("id\n" + "".join(f"{row}\n" for row in range(10001))).encode()
    response = client.post("/api/datasets/upload", files={"file": ("large.csv", raw)})
    assert response.status_code == 200, response.text
    assert response.json()["row_count"] == 10001
    assert client.get("/api/config").json()["max_rows_per_source"] == 25000


def test_path_names_are_data_only(client):
    result = client.post("/api/datasets/upload",files={"file":("../../hello.csv",b"sku,name,price\nA,Desk,2")})
    assert result.status_code == 200
    assert result.json()["name"] == "hello.csv"


def test_local_document_extraction(client):
    raw = Path("samples/supplier_note.txt").read_bytes()
    result = client.post("/api/extract",files={"file":("note.txt",raw)})
    assert result.status_code == 200
    payload = result.json()
    assert payload["requires_review"] is True
    assert payload["document"]["product_id"] == "P-009"
    assert payload["document"]["sentiment"] == "positive"
    assert payload["dataset"]["row_count"] == 1
    assert client.get("/api/warehouse").json()["total"] == 0


def test_image_requires_live_provider(client):
    result = client.post("/api/extract",files={"file":("image.png",b"\x89PNG\r\n\x1a\n")})
    assert result.status_code == 400
    assert "live" in result.json()["detail"]


def test_api_ingestion_blocked_by_default(client):
    assert client.post("/api/datasets/api",json={"url":"https://127.0.0.1/private"}).status_code == 400
    assert client.post("/api/datasets/api",json={"url":"http://example.com/data"}).status_code == 400


def test_sql_dialects_and_procedural_limit():
    result = translate(TranslateRequest(sql="SELECT TOP 10 ISNULL(price,0) FROM products",source="tsql",target="snowflake"))
    assert result["status"] == "translated"
    assert "LIMIT 10" in result["sql"]
    assert "COALESCE" in result["sql"]
    assert translate(TranslateRequest(sql="CREATE PROCEDURE p AS BEGIN SELECT 1; END"))["status"] == "manual_review"
    assert translate(TranslateRequest(sql="SELECT ("))["status"] == "manual_review"


def test_schema_and_static_page(client):
    assert "FlowForge" in client.get("/").text
    assert "/api/runs" in client.get("/api/schema").json()["paths"]
