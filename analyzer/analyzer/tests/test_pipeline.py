"""
analyzer 파이프라인 테스트.

레포 루트에서 실행:
    uv run pytest analyzer/
"""

import json
from pathlib import Path

import pytest

from analyzer.pipeline import analyze
from analyzer.evaluate import evaluate
from analyzer.schema import parse_crawl
from analyzer.rules import run_rules

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def crawl_data():
    return json.loads((FIX / "crawl_vulnerable.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ground_truth():
    return json.loads((FIX / "vulnerabilities.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report(crawl_data):
    return analyze(crawl_data)


# ── 스키마/파싱 ──────────────────────────────────────────────

def test_schema_ok(report):
    assert report.schema_ok is True
    assert report.schema_errors == []


def test_roles_parsed(report):
    assert set(report.roles) == {"guest", "user", "admin"}


def test_parse_is_lenient_on_unknown_fields():
    data = {
        "schema_version": "1.0", "run_id": "x", "target_base_url": "http://t",
        "roles": [{"id": "role:user", "role": "user", "UNKNOWN": 1,
                   "pages": [], "requests": [
                       {"id": "request:1", "method": "get", "endpoint": "/x",
                        "url": "http://t/x", "status": 200, "EXTRA": "ignore"}]}],
    }
    run, errors = parse_crawl(data)
    assert errors == []
    assert run.roles[0].requests[0].method == "GET"  # 대문자 정규화


def test_schema_reports_missing_fields():
    run, errors = parse_crawl({"schema_version": "1.0"})
    assert any("roles" in e for e in errors)


# ── 그래프 ──────────────────────────────────────────────────

def test_graph_built(report):
    assert report.graph is not None
    assert report.stats["graph_nodes"] > 0
    assert report.stats["graph_edges"] > 0
    types = {n["type"] for n in report.graph.to_dict()["nodes"]}
    assert {"Role", "Page", "Endpoint"} <= types


def test_observed_edges_have_status(report):
    edges = report.graph.to_dict()["edges"]
    observed = [e for e in edges if e["relation"] == "OBSERVED"]
    assert observed
    assert all("status" in e["props"] for e in observed)


# ── 룰 태스크 ────────────────────────────────────────────────

def _tasks_for(report, endpoint, rule_id=None):
    return [t for t in report.tasks
            if t.endpoint == endpoint and (rule_id is None or t.rule_id == rule_id)]


def test_v1_cross_access_task_exists(report):
    """V1: /orders/{id} 교차접근 검증 태스크가 R1으로 생성."""
    ts = _tasks_for(report, "/orders/{id}", "R1")
    assert ts and ts[0].category == "cross_access"
    assert ts[0].expected_vuln_hint == "V1"


def test_v2_hidden_and_cross_tasks(report):
    """V2: /api/users/{id} 가 교차접근(R1)과 숨은표면(R2) 둘 다로 잡힘."""
    assert _tasks_for(report, "/api/users/{id}", "R1")
    assert _tasks_for(report, "/api/users/{id}", "R2")


def test_v3_admin_api_high_priority(report):
    """V3: /admin/api/users 는 fetch API라 high 우선순위 + hint V3."""
    ts = _tasks_for(report, "/admin/api/users", "R3")
    assert ts
    assert ts[0].severity_hint == "high"
    assert ts[0].expected_vuln_hint == "V3"


def test_admin_pages_are_low_priority(report):
    """decoy D3: admin HTML 페이지들은 low 우선순위 (오탐 부담 축소)."""
    for ep in ["/admin", "/admin/orders", "/admin/products"]:
        ts = _tasks_for(report, ep, "R3")
        assert ts and ts[0].severity_hint == "low"


def test_cart_add_not_hidden_surface(report):
    """POST /cart/add 는 상태변경이므로 R2(숨은표면)로 오분류되면 안 됨."""
    assert not _tasks_for(report, "/cart/add", "R2")
    assert _tasks_for(report, "/cart/add", "R4")  # 대신 R4 상태변경 플래그


def test_v4_not_reached_by_rules(report):
    """V4(/admin/export/orders)는 크롤 미관측 → 룰로 안 잡히는 게 정상."""
    assert not _tasks_for(report, "/admin/export/orders")


# ── 평가 ────────────────────────────────────────────────────

def test_reach_rate(report, ground_truth):
    res = evaluate(report.to_dict(), ground_truth)
    assert res["reached"] == 4          # V1,V2,V3,V5
    assert res["total"] == 5
    reached_ids = {r["id"] for r in res["per_vuln"] if r["reached"]}
    assert reached_ids == {"V1", "V2", "V3", "V5"}
    assert "V4" not in reached_ids


# ── 결정론 ──────────────────────────────────────────────────

def test_deterministic(crawl_data):
    """같은 입력 → 같은 태스크 순서/내용 (LLM 없음)."""
    r1 = analyze(crawl_data)
    r2 = analyze(crawl_data)
    ids1 = [t.task_id for t in r1.tasks]
    ids2 = [t.task_id for t in r2.tasks]
    assert ids1 == ids2
