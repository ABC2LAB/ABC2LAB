"""
kg 소유 스키마. 다른 모듈을 import 하지 않는다.

팀 간 계약은 파이썬 import 가 아니라 디스크의 JSON 이다.
  - 입력: CrawlResult (crawl_result.json, 명세 A.1) ← 엄격 검증
      · extra="forbid": 명세에 없는 칸이 오면 조용히 버리지 않고 거부
        (옛 공유 스키마의 extra="ignore" 때문에 response_shape 가 유실됐었다)
      · 모든 필수 칸은 present 강제, null 가능한 칸은 `| None` (막힌 요청의 status=null 등)
  - 출력: KGCandidates (kg 후보, Neo4j 적재 입력) ← 버전 관리

출력 규칙
  - 모든 노드/엣지에 evidence(근거) 최소 1개: 어느 role 이 어느 url 에서 나온 주장인지,
    크롤 레코드 id(evidence_id)로 입력과 대조할 수 있게.
  - label / 관계 type 은 Enum(화이트리스트)로 고정 -> Cypher Injection 차단
    (Cypher 에서 label/타입은 $파라미터로 못 넣는다).
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─────────────────────────── 입력: crawl_result.json (엄격) ───────────────────────────
_Strict = ConfigDict(extra="forbid")


class FormField(BaseModel):
    model_config = _Strict
    name: str
    type: str


class PageLink(BaseModel):
    model_config = _Strict
    action_id: str
    url: str | None
    endpoint: str | None
    text: str | None
    is_state_changing: bool
    outcome: str


class PageAction(BaseModel):
    model_config = _Strict
    action_id: str
    kind: str
    label: str | None
    method: str | None
    target_url: str | None
    fields: list[FormField]
    is_state_changing: bool
    outcome: str


class PageRecord(BaseModel):
    model_config = _Strict
    id: str
    role: str
    url: str
    endpoint: str
    title: str | None
    status: int | None
    depth: int
    source_page: str | None
    source_action: str | None
    source_page_id: str | None
    links: list[PageLink]
    actions: list[PageAction]


class RequestRecord(BaseModel):
    model_config = _Strict
    id: str
    role: str
    method: str
    resource_type: str
    url: str
    endpoint: str
    status: int | None
    query_params: dict[str, list[str]]
    body_params: dict[str, list[str]]
    resource_ids: list[str]
    request_headers: dict[str, str]
    response_headers: dict[str, str]
    response_shape: Any | None
    source_page: str | None
    source_action: str | None
    source_page_id: str | None
    captured_at: str


class RoleResult(BaseModel):
    model_config = _Strict
    id: str
    role: str
    error: str | None
    pages: list[PageRecord]
    requests: list[RequestRecord]


class CrawlResult(BaseModel):
    model_config = _Strict
    schema_version: str
    run_id: str
    target_base_url: str
    started_at: str
    finished_at: str | None
    roles: list[RoleResult]


# ─────────────────────────── 출력: KG 후보 ───────────────────────────
# 0.2.0: Evidence.evidence_id 추가, 속성값 null 허용(막힌 요청의 status)
SCHEMA_VERSION = "0.2.0"

PropValue = str | int | float | bool
# 없는 값도 키는 남기고 null 로 둔다 (팀 JSON 계약: 키 생략 금지)
Properties = dict[str, PropValue | list[PropValue] | None]
# 노드 id 는 Neo4j MERGE 에서 $파라미터 값으로만 쓰므로 문자셋은 넉넉히 두되,
# 제어문자와 백틱(라벨 탈출 위험 문자)은 방어적으로 막는다.
_ID = re.compile(r"[^\x00-\x1f`]{1,200}")


class NodeLabel(str, Enum):
    ROLE = "Role"          # 크롤링 신원(user, admin, guest ...)
    ENDPOINT = "Endpoint"  # method + 템플릿 경로 (/api/products/{id})
    RESOURCE = "Resource"  # 구체 자원 인스턴스 (endpoint + resource_id) -> IDOR 단위
    PAGE = "Page"          # 발견한 UI 페이지


class RelType(str, Enum):
    ACCESSED = "ACCESSED"        # Role -> Resource/Endpoint (요청 결과 status 포함)
    VISITED = "VISITED"          # Role -> Page
    INSTANCE_OF = "INSTANCE_OF"  # Resource -> Endpoint
    LINKS_TO = "LINKS_TO"        # Page -> Page (네비게이션)
    EXPOSES = "EXPOSES"          # Page -> Endpoint (그 페이지에서 발생한 요청)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_url: str
    locator: str                       # 어디서 나왔는지(captured_request, discovered_page, link ...)
    evidence_id: str | None = None     # 근거가 된 크롤 레코드 id (request:1, page:1, role:user). LLM 추론은 null
    role: str | None = None            # 어떤 role 의 관찰인지 (접근통제 분석에 중요)
    snippet: str = Field("", max_length=500)
    confidence: float = Field(0.6, ge=0.0, le=1.0)


def _check_id(v: str) -> str:
    if not _ID.fullmatch(v):
        raise ValueError(f"id 형식 오류: {v!r}")
    return v


class NodeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., min_length=1, max_length=200)
    label: NodeLabel
    properties: Properties = Field(default_factory=dict)
    evidence: list[Evidence] = Field(..., min_length=1)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        return _check_id(v)


class EdgeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(..., min_length=1, max_length=200)
    target: str = Field(..., min_length=1, max_length=200)
    type: RelType
    properties: Properties = Field(default_factory=dict)
    evidence: list[Evidence] = Field(..., min_length=1)


class KGCandidates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = Field(default=SCHEMA_VERSION)
    target_base_url: str = ""
    crawl_run_id: str | None = None
    nodes: list[NodeCandidate] = Field(default_factory=list)
    edges: list[EdgeCandidate] = Field(default_factory=list)
    meta: dict[str, PropValue] = Field(default_factory=dict)
