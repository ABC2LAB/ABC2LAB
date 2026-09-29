import json, pathlib
from common.crawl_schema import CrawlSession
from common.kg_schema import NodeLabel, RelType
from kg.builder import build_kg

SAMPLE = pathlib.Path(__file__).resolve().parent / "data" / "crawl_sample.json"


def _kg():
    s = CrawlSession.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))
    return build_kg(s)


def test_every_node_and_edge_has_evidence():
    kg = _kg()
    assert kg.nodes and kg.edges
    assert all(n.evidence for n in kg.nodes)
    assert all(e.evidence for e in kg.edges)


def test_roles_become_nodes():
    roles = {n.properties["role"] for n in _kg().nodes if n.label == NodeLabel.ROLE}
    assert roles == {"user", "admin"}


def test_resource_nodes_for_resource_ids():
    kg = _kg()
    res = {n.id for n in kg.nodes if n.label == NodeLabel.RESOURCE}
    assert "res:/api/orders/{id}#7" in res
    assert "res:/api/products/{id}#3" in res


def test_accessed_edges_carry_status():
    accessed = [e for e in _kg().edges if e.type == RelType.ACCESSED]
    assert accessed and all("status" in e.properties for e in accessed)


def test_no_dangling_edges():
    kg = _kg()
    ids = {n.id for n in kg.nodes}
    for e in kg.edges:
        assert e.source in ids, e.source
        assert e.target in ids, e.target


def test_evidence_records_role():
    # 접근통제 분석의 핵심: 각 근거가 어떤 role 의 관찰인지 남아야 한다
    kg = _kg()
    accessed = [e for e in kg.edges if e.type == RelType.ACCESSED]
    assert all(e.evidence[0].role in {"user", "admin"} for e in accessed)
