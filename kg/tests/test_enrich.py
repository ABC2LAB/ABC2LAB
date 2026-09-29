import json
import pathlib

from kg.builder import build_kg
from kg.enrich import (
    EndpointClass,
    LLMClassification,
    enrich_with_llm,
    make_default_client,
)
from kg.schemas import CrawlResult, KGCandidates, NodeLabel

SAMPLE = pathlib.Path(__file__).resolve().parent / "data" / "crawl_sample.json"


def _base_kg():
    s = CrawlResult.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))
    return build_kg(s)


class FakeLLM:
    """네트워크 없이 결정적으로 분류하는 테스트용 클라이언트."""
    def classify_endpoints(self, endpoints):
        out = []
        for e in endpoints:
            tmpl = e["template"] or ""
            if "admin" in tmpl:
                scope = "admin_only"
            elif "orders" in tmpl:
                scope = "user_owned"
            else:
                scope = "public"
            out.append(EndpointClass(endpoint_id=e["id"], access_scope=scope,
                                     rationale=f"rule for {tmpl}"))
        return LLMClassification(endpoints=out)


def test_enrich_sets_access_scope():
    kg = enrich_with_llm(_base_kg(), FakeLLM())
    scopes = {n.properties.get("access_scope")
              for n in kg.nodes if n.label == NodeLabel.ENDPOINT}
    assert {"admin_only", "user_owned", "public"} <= scopes


def test_enrich_adds_llm_evidence():
    kg = enrich_with_llm(_base_kg(), FakeLLM())
    ep = next(n for n in kg.nodes
              if n.label == NodeLabel.ENDPOINT and "orders" in n.id)
    assert ep.properties["access_scope"] == "user_owned"
    assert any(ev.locator == "llm_inference" for ev in ep.evidence)


def test_result_still_valid_schema():
    kg = enrich_with_llm(_base_kg(), FakeLLM())
    # 보강 후에도 KG 후보 계약을 그대로 만족해야 한다
    KGCandidates.model_validate(json.loads(kg.model_dump_json()))
    assert kg.meta.get("llm_enriched") is True


def test_no_client_returns_unchanged():
    base = _base_kg()
    same = enrich_with_llm(base, None)
    assert all("access_scope" not in n.properties for n in same.nodes)


def test_make_default_client_none_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert make_default_client() is None
