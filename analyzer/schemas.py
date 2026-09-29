"""
Analyzer 소유 스키마 (인터페이스 명세 1.0).

이 모듈은 analyzer 안에 있으며 다른 모듈의 스키마를 import 하지 않는다.
팀 간 '계약'은 파이썬 import 가 아니라 디스크의 JSON(crawl_result.json /
analysis_result.json)이고, 그 계약은 loader 의 엄격 검증이 지킨다.

- 입력: CrawlResult (crawl_result.json)  ← 명세 A.1대로 '엄격' 검증
    · extra="forbid": 명세에 없는 필드가 있으면 거부
    · 모든 필수 필드는 present 강제(값이 null 가능한 필드는 `| None`, 기본값 없음)
- 출력: AnalysisResult (analysis_result.json) ← 명세 A.2
    · derived_by = crawler|rule|llm, nodes/relationships 평평한 배열
    · Relationship.type 은 ^[A-Z][A-Z0-9_]*$
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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


# ─────────────────────────── 출력: analysis_result.json (A.2) ───────────────────────────
SCHEMA_VERSION = "1.0"
_Out = ConfigDict(extra="forbid")


class NodeType(str, Enum):
    WEBSITE = "Website"
    PAGE = "Page"
    API_OPERATION = "ApiOperation"
    PARAMETER = "Parameter"
    RESOURCE = "Resource"
    FEATURE = "Feature"
    BUSINESS_FLOW = "BusinessFlow"
    FLOW_STEP = "FlowStep"


class DerivedBy(str, Enum):
    CRAWLER = "crawler"
    RULE = "rule"
    LLM = "llm"


class Node(BaseModel):
    model_config = _Out
    id: str = Field(..., min_length=1)
    type: NodeType
    name: str = Field(..., min_length=1)
    derived_by: DerivedBy
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)


class Relationship(BaseModel):
    model_config = _Out
    from_id: str = Field(..., min_length=1)
    type: str = Field(..., pattern=r"^[A-Z][A-Z0-9_]*$")
    to_id: str = Field(..., min_length=1)
    derived_by: DerivedBy
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)


class AnalysisRunInfo(BaseModel):
    model_config = _Out
    crawl_run_id: str
    target_base_url: str
    analyzed_at: str
    source: str
    analyzer_version: str | None = None
    inference_policy_version: str | None = None


class AnalysisResult(BaseModel):
    model_config = _Out
    schema_version: str = SCHEMA_VERSION
    analysis: AnalysisRunInfo
    nodes: list[Node] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
