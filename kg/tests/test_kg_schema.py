import json
import pathlib

import pytest
from pydantic import ValidationError

from kg.kg_schema import CrawlResult

SAMPLE = pathlib.Path(__file__).resolve().parent / "data" / "crawl_sample.json"


def _raw() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def test_parses_spec_format():
    s = CrawlResult.model_validate(_raw())
    assert s.target_base_url == "http://localhost:8001"
    assert {rc.role for rc in s.roles} == {"user", "admin"}


def test_keeps_response_shape():
    # 옛 공유 스키마는 모르는 칸을 무시해서 response_shape 가 조용히 사라졌다
    s = CrawlResult.model_validate(_raw())
    assert s.roles[0].requests[0].response_shape == {"id": "int"}


def test_accepts_null_status():
    s = CrawlResult.model_validate(_raw())
    assert any(r.status is None for rc in s.roles for r in rc.requests)


def test_rejects_unknown_fields():
    raw = _raw()
    raw["extra_new_field"] = 123
    with pytest.raises(ValidationError):
        CrawlResult.model_validate(raw)


def test_rejects_missing_required_field():
    raw = _raw()
    del raw["roles"][0]["requests"][0]["response_shape"]
    with pytest.raises(ValidationError):
        CrawlResult.model_validate(raw)
