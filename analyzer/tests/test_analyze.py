import json

import pytest

from analyzer.analyze import analyze
from analyzer.infer import demo_fake_responder
from analyzer.llm_client import FakeClient
from analyzer.tests._helpers import SCHEMAS, load_crawl


def _run():
    return analyze(load_crawl(), FakeClient(demo_fake_responder),
                   analyzed_at="2026-09-28T05:20:00Z")


def test_pipeline_shape():
    r = _run()
    assert r.schema_version == "1.0"
    assert r.analysis.crawl_run_id == "20260928-051203-a1b2"
    types = {n.type.value for n in r.nodes}
    assert {"ApiOperation", "Parameter", "Resource"} <= types
    assert any(rel.type == "ACCESSES" for rel in r.relationships)


def test_output_conforms_to_spec_json_schema():
    """명세 부록 A.2 JSON Schema로 출력이 실제로 유효한지 자동 검증."""
    jsonschema = pytest.importorskip("jsonschema")  # 없으면 skip
    r = _run()
    schema = json.loads((SCHEMAS / "analysis-result-1.0.json").read_text(encoding="utf-8"))
    payload = json.loads(r.model_dump_json(exclude_none=True))
    jsonschema.Draft202012Validator(schema).validate(payload)  # 위반 시 예외
