from analyzer.observed import build_observed
from analyzer.schemas import DerivedBy
from analyzer.tests._helpers import load_crawl


def test_observed_builds_api_and_param():
    nodes, rels = build_observed(load_crawl())
    ids = {n.id for n in nodes}
    assert "api:GET:/api/products/{id}" in ids
    assert "api:GET:/api/orders/{id}" in ids
    assert "param:api:GET:/api/products/{id}:query:sort" in ids
    # 모든 관찰 노드는 rule
    assert all(n.derived_by == DerivedBy.RULE for n in nodes)
    # HAS_PARAMETER 관계, 양 끝 존재
    hp = [r for r in rels if r.type == "HAS_PARAMETER"]
    assert hp and all(r.from_id in ids and r.to_id in ids for r in hp)
    # evidence 는 request:N 그대로
    api = next(n for n in nodes if n.id == "api:GET:/api/products/{id}")
    assert api.evidence_ids == ["request:1"]
    assert api.properties == {"method": "GET", "endpoint": "/api/products/{id}"}
