import base64

from fastapi.testclient import TestClient

from app.main import app
from app.ingest import save_rows
from app.models import PipelineSpec
from app.spec_inference import suggest_spec


def test_suggest_spec_uses_source_columns_and_infers_safe_types():
    dataset = save_rows([
        {"Student_ID": "S-001", "Age": "21", "GPA": "3.75", "Platform": "Video"},
        {"Student_ID": "S-002", "Age": "22", "GPA": "3.50", "Platform": "Photo"},
    ], "Social_media_impact_on_life.csv")

    result = suggest_spec(dataset["id"])
    spec = PipelineSpec.model_validate(result["spec"])

    assert result["source_name"] == "Social_media_impact_on_life.csv"
    assert [column.aliases[0] for column in spec.columns] == [
        "Student_ID", "Age", "GPA", "Platform",
    ]
    assert [column.name for column in spec.columns] == [
        "student_id", "age", "gpa", "platform",
    ]
    assert [column.dtype for column in spec.columns] == [
        "string", "integer", "number", "string",
    ]
    assert spec.primary_key == "student_id"
    assert spec.columns[0].required is True
    assert result["warnings"] == []


def test_suggest_spec_reports_provisional_key_and_disambiguates_columns():
    dataset = save_rows([
        {"A-B": "same", "A B": "same"},
        {"A-B": "same", "A B": "same"},
    ], "unkeyed.csv")

    result = suggest_spec(dataset["id"])
    spec = PipelineSpec.model_validate(result["spec"])

    assert [column.name for column in spec.columns] == ["a_b", "a_b_2"]
    assert spec.primary_key == "a_b"
    assert result["warnings"]


def test_suggest_spec_excludes_protected_fields_from_provisional_key_selection(monkeypatch):
    monkeypatch.setenv("PII_ENCRYPTION_KEY", base64.b64encode(b"k" * 32).decode())
    dataset = save_rows(
        [
            {"Transaction_ID": "T-1", "Customer_Name": "Alex Smith", "Channel": "online"},
            {"Transaction_ID": "T-1", "Customer_Name": "Jamie Jones", "Channel": "online"},
        ],
        "transactions.csv",
        metadata={"kind": "file", "pii_columns": ["Customer_Name"]},
    )

    result = suggest_spec(dataset["id"])
    spec = PipelineSpec.model_validate(result["spec"])

    assert spec.primary_key == "transaction_id"
    assert result["warnings"]
    assert "No unique nonempty key" in result["warnings"][0]


def test_suggested_spec_api_returns_valid_draft():
    with TestClient(app) as client:
        response = client.post(
            "/api/datasets/upload",
            files={"file": ("survey.csv", b"Student_ID,Age\nS-1,20\nS-2,21\n")},
        )
        assert response.status_code == 200
        dataset_id = response.json()["id"]

        draft = client.get(f"/api/datasets/{dataset_id}/suggested-spec")

        assert draft.status_code == 200
        assert draft.json()["source_name"] == "survey.csv"
        assert draft.json()["spec"]["target"] == "survey"
        assert [column["name"] for column in draft.json()["spec"]["columns"]] == [
            "student_id", "age",
        ]
        assert client.post("/api/artifacts", json=draft.json()["spec"]).status_code == 200


def test_suggest_spec_rejects_too_many_columns():
    dataset = save_rows(
        [{f"column_{index}": str(index) for index in range(51)}],
        "wide.csv",
    )

    try:
        suggest_spec(dataset["id"])
    except ValueError as error:
        assert "at most 50" in str(error)
    else:
        raise AssertionError("Expected wide source to be rejected")
