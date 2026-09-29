from analyzer.llm_client import FakeClient
from analyzer.infer import demo_fake_responder, infer_resources
from analyzer.observed import build_observed
from analyzer.tests._helpers import load_crawl


def test_infer_resources_with_fake_client():
    nodes, _, _ = build_observed(load_crawl())
    client = FakeClient(demo_fake_responder)
    resources, rels = infer_resources(nodes.api_operations, client)
    names = {r.id for r in resources}
    assert "resource:product" in names and "resource:order" in names
    assert all(0.0 <= r.confidence <= 1.0 for r in resources)
    assert all(rel.type.value == "ACCESSES" and rel.source.value == "llm_inferred" for rel in rels)
