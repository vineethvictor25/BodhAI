import ast
import pytest
from app.models import Column, PipelineSpec, demo_spec
from app.mapping import infer_mapping
from app.cleaning import clean_value
from app.generation import artifacts


def test_reserved_override_is_not_consumed_by_earlier_field():
    spec = PipelineSpec(columns=[Column(name="id",required=True,aliases=["name"]),Column(name="display",required=True)],primary_key="id",mapping_overrides={"display":"name"})
    mapped = infer_mapping([{"id":"A","name":"Desk"}],spec)
    assert mapped[0]["source"] == "id"
    assert mapped[1]["source"] == "name"


def test_integer_never_rounds_silently():
    with pytest.raises(ValueError):
        clean_value("2.3",Column(name="quantity",dtype="integer"))


def test_nonfinite_values_fail_quality():
    with pytest.raises(ValueError):
        clean_value("inf",Column(name="price",dtype="number"))


def test_arbitrary_transform_is_not_executable():
    with pytest.raises(ValueError):
        Column(name="price",transforms=["__import__('os').system('echo bad')"])


def test_generated_runner_executes_same_engine(tmp_path,monkeypatch):
    # Compile-only is insufficient: execute the generated main with persisted fixture inputs.
    import json
    import sys
    from app import store
    from app.ingest import save_rows
    dataset = save_rows([{"sku":"A","name":"Desk","price":2,"length":"200cm"}],"runner-input")
    code = artifacts(demo_spec())["generated_pipeline.py"]
    monkeypatch.setattr(sys,"argv",["generated_pipeline.py","--dataset",dataset["id"],"--key","generated-test"])
    exec(compile(code,"generated_pipeline.py","exec"),{"__name__":"__main__"})
    with store.db() as conn:
        row=conn.execute("SELECT record_json FROM warehouse").fetchone()
    assert json.loads(row[0])["length_m"] == 2
