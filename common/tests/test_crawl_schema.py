import json, pathlib
from common.crawl_schema import CrawlSession

SAMPLE = pathlib.Path(__file__).resolve().parents[2] / "kg" / "tests" / "data" / "crawl_sample.json"


def test_parses_minjun_format():
    s = CrawlSession.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))
    assert s.target_base_url == "http://localhost:8001"
    assert {rc.role for rc in s.roles} == {"user", "admin"}


def test_ignores_unknown_fields():
    s = CrawlSession.model_validate(
        {"target_base_url": "http://x", "roles": [], "extra_new_field": 123})
    assert s.target_base_url == "http://x"
