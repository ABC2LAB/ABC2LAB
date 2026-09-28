from common.schemas import RelType
from analyzer.observed import build_observed
from analyzer.tests._helpers import load_crawl


def test_observed_nodes_and_no_dangling():
    nodes, rels, evid = build_observed(load_crawl())
    assert [r.name for r in nodes.roles] == ["user"]
    assert any(p.id == "page:/products/{id}" for p in nodes.pages)
    api_ids = {a.id for a in nodes.api_operations}
    assert {"api:GET:/api/products/{id}", "api:GET:/api/orders/{id}"} <= api_ids

    node_ids = {x.id for g in (nodes.roles, nodes.pages, nodes.api_operations, nodes.parameters) for x in g}
    for r in rels:
        assert r.from_id in node_ids and r.to_id in node_ids

    types = {r.type for r in rels}
    assert {RelType.CAN_ACCESS, RelType.CAN_CALL, RelType.CALLS, RelType.ACCEPTS} <= types
    # 모든 관찰 관계 evidence_ids는 실제 evidence를 가리킴
    ev_ids = {e.id for e in evid}
    assert all(all(e in ev_ids for e in r.evidence_ids) for r in rels)
