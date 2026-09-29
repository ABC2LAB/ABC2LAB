from analyzer.infer import demo_fake_responder, infer_resources
from analyzer.llm_client import FakeClient
from analyzer.observed import build_observed
from analyzer.schemas import NodeType
from analyzer.tests._helpers import load_crawl


def test_infer_resources():
    nodes, _ = build_observed(load_crawl())
    resources, rels = infer_resources(nodes, FakeClient(demo_fake_responder))
    rids = {r.id for r in resources}
    assert "resource:product" in rids and "resource:order" in rids
    assert all(n.type == NodeType.RESOURCE and n.derived_by.value == "llm" for n in resources)
    assert all(r.type == "ACCESSES" and r.confidence is not None for r in rels)
