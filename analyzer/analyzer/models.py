"""
데이터 모델
==========

크롤러 JSON(schema_version 1.0)을 파싱한 뒤 analyze 내부에서 다루는 구조와,
analyze가 최종 출력하는 그래프/검증태스크 구조를 정의한다.

설계 원칙:
- 입력 파싱은 관대하게(모르는 필드는 무시), 출력은 엄격하게(고정 스키마).
- LLM은 여기 관여하지 않는다. 전부 결정론적 dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, Any


# ---------------------------------------------------------------------------
# 입력 측 (크롤 JSON 파싱 결과)
# ---------------------------------------------------------------------------

@dataclass
class LinkObs:
    """페이지 안에서 발견된 링크/액션."""
    action_id: str
    endpoint: str
    url: str
    text: str = ""
    is_state_changing: bool = False
    outcome: str = ""


@dataclass
class PageObs:
    """한 역할이 방문한 한 페이지."""
    role: str
    id: str
    url: str
    endpoint: str
    title: str = ""
    status: Optional[int] = None
    depth: Optional[int] = None
    source_page_id: Optional[str] = None
    source_action: Optional[str] = None
    links: list[LinkObs] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)


@dataclass
class RequestObs:
    """한 역할이 발생시킨 한 HTTP 요청."""
    role: str
    id: str
    method: str
    endpoint: str
    url: str
    status: Optional[int] = None
    resource_type: str = ""          # document | fetch | xhr | ...
    query_params: dict = field(default_factory=dict)
    body_params: dict = field(default_factory=dict)
    resource_ids: list[str] = field(default_factory=list)
    response_shape: Optional[dict] = None
    source_page_id: Optional[str] = None
    source_action: Optional[str] = None


@dataclass
class RoleObs:
    role: str
    id: str
    error: Optional[str] = None
    pages: list[PageObs] = field(default_factory=list)
    requests: list[RequestObs] = field(default_factory=list)


@dataclass
class CrawlRun:
    schema_version: str
    run_id: str
    target_base_url: str
    roles: list[RoleObs] = field(default_factory=list)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    def all_requests(self) -> list[RequestObs]:
        out = []
        for r in self.roles:
            out.extend(r.requests)
        return out

    def all_pages(self) -> list[PageObs]:
        out = []
        for r in self.roles:
            out.extend(r.pages)
        return out

    def role_names(self) -> list[str]:
        return [r.role for r in self.roles]


# ---------------------------------------------------------------------------
# 출력 측 (그래프)
# ---------------------------------------------------------------------------

@dataclass
class GraphNode:
    id: str
    type: str          # Role | Page | Endpoint | Param
    props: dict = field(default_factory=dict)


@dataclass
class GraphEdge:
    src: str
    dst: str
    relation: str      # OBSERVED | LINKS_TO | TRIGGERS | HAS_PARAM
    props: dict = field(default_factory=dict)


@dataclass
class Graph:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "nodes": [asdict(n) for n in self.nodes],
            "edges": [asdict(e) for e in self.edges],
        }


# ---------------------------------------------------------------------------
# 출력 측 (검증 태스크)
# ---------------------------------------------------------------------------

@dataclass
class VerificationTask:
    """
    analyze가 결정론적으로 뽑은 '검증해야 할 가설'.
    이것은 탐지 결과(=취약 확정)가 아니다. 뒤 단계(공격 시나리오 실행 모듈)가
    실제로 요청을 보내 확인하거나, LLM agent가 우선순위를 매기는 입력이 된다.

    - rule_id     : 어떤 룰이 만들었나 (R1~R4)
    - category    : 검증 유형 (cross_access / hidden_surface / coverage_gap / state_change)
    - expected_vuln_hint: 이 태스크가 성립하면 어느 취약점일 가능성이 있나 (평가 매핑용, 힌트일 뿐 확정 아님)
    - verify      : 뒤 단계가 실제로 수행할 검증 지시 (결정론적으로 실행 가능한 형태)
    """
    task_id: str
    rule_id: str
    category: str
    endpoint: str
    method: str
    rationale: str
    verify: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    expected_vuln_hint: Optional[str] = None
    severity_hint: str = "unknown"


@dataclass
class AnalyzeReport:
    run_id: str
    target_base_url: str
    schema_ok: bool
    schema_errors: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    graph: Optional[Graph] = None
    tasks: list[VerificationTask] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "target_base_url": self.target_base_url,
            "schema_ok": self.schema_ok,
            "schema_errors": self.schema_errors,
            "roles": self.roles,
            "graph": self.graph.to_dict() if self.graph else None,
            "tasks": [asdict(t) for t in self.tasks],
            "stats": self.stats,
        }
