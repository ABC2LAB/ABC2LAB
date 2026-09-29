"""
공유 계약 스키마 (crawler ↔ analyzer ↔ KG Builder 공통).

- 입력: CrawlResult (crawl_result.json)
- 출력: AnalysisResult (analysis_result.json)
관찰(observed)과 추론(llm_inferred)은 source로 구분하고, 근거는 evidence_ids로 참조한다.
"""
from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field

# ───────────────────────── 입력: crawl_result.json ─────────────────────────
_In = ConfigDict(extra="ignore")  # 크롤러가 필드 더 붙여도 안 깨지게


class FormField(BaseModel):
    model_config = _In
    name: str
    type: str = "text"


class PageLink(BaseModel):
    model_config = _In
    action_id: str
    url: str = ""
    endpoint: str = ""
    text: str | None = None
    is_state_changing: bool = False
    outcome: str = ""


class PageAction(BaseModel):
    model_config = _In
    action_id: str
    kind: str = ""
    label: str | None = None
    method: str | None = None
    target_url: str | None = None
    fields: list[FormField] = Field(default_factory=list)
    is_state_changing: bool = False
    outcome: str = ""


class PageRecord(BaseModel):
    model_config = _In
    id: str
    role: str = ""
    url: str = ""
    endpoint: str = ""
    title: str | None = None
    status: int | None = None
    depth: int = 0
    source_page: str | None = None
    source_action: str | None = None
    source_page_id: str | None = None
    links: list[PageLink] = Field(default_factory=list)
    actions: list[PageAction] = Field(default_factory=list)


class RequestRecord(BaseModel):
    model_config = _In
    id: str
    role: str = ""
    method: str = "GET"
    resource_type: str = ""
    url: str = ""
    endpoint: str = ""
    status: int | None = None
    query_params: dict[str, list[str]] = Field(default_factory=dict)
    body_params: dict = Field(default_factory=dict)
    resource_ids: list[str] = Field(default_factory=list)
    request_headers: dict[str, str] = Field(default_factory=dict)
    response_headers: dict[str, str] = Field(default_factory=dict)
    source_page: str | None = None
    source_action: str | None = None
    source_page_id: str | None = None
    captured_at: str = ""


class RoleResult(BaseModel):
    model_config = _In
    id: str
    role: str
    error: str | None = None
    pages: list[PageRecord] = Field(default_factory=list)
    requests: list[RequestRecord] = Field(default_factory=list)


class CrawlResult(BaseModel):
    model_config = _In
    schema_version: str = "1.0"
    run_id: str = ""
    target_base_url: str
    started_at: str = ""
    finished_at: str = ""
    roles: list[RoleResult] = Field(default_factory=list)


# ──────────────────────── 출력: analysis_result.json ────────────────────────
SCHEMA_VERSION = "1.0"
_Out = ConfigDict(extra="forbid")


class Source(str, Enum):
    OBSERVED = "observed"
    LLM = "llm_inferred"


class RelType(str, Enum):
    HAS_PAGE = "HAS_PAGE"
    CAN_ACCESS = "CAN_ACCESS"
    CAN_CALL = "CAN_CALL"
    CALLS = "CALLS"
    ACCEPTS = "ACCEPTS"
    ACCESSES = "ACCESSES"
    USES_PAGE = "USES_PAGE"
    USES_API = "USES_API"
    HAS_STEP = "HAS_STEP"
    NEXT = "NEXT"
    VISITS = "VISITS"


class Evidence(BaseModel):
    model_config = _Out
    id: str
    source_type: str = ""
    source_ref: str = ""
    role: str | None = None


class RoleNode(BaseModel):
    model_config = _Out
    id: str
    name: str
    source: Source = Source.OBSERVED
    evidence_ids: list[str] = Field(default_factory=list)


class PageNode(BaseModel):
    model_config = _Out
    id: str
    name: str = ""
    path_pattern: str = ""
    observed_urls: list[str] = Field(default_factory=list)
    source: Source = Source.OBSERVED
    evidence_ids: list[str] = Field(default_factory=list)


class ApiOperationNode(BaseModel):
    model_config = _Out
    id: str
    method: str
    endpoint: str
    summary: str | None = None
    summary_confidence: float | None = None
    summary_evidence_ids: list[str] = Field(default_factory=list)
    source: Source = Source.OBSERVED
    evidence_ids: list[str] = Field(default_factory=list)


class ParameterNode(BaseModel):
    model_config = _Out
    id: str
    name: str
    location: str
    required: bool = False
    data_type: str | None = None
    source: Source = Source.OBSERVED
    evidence_ids: list[str] = Field(default_factory=list)


class ResourceNode(BaseModel):
    model_config = _Out
    id: str
    name: str
    resource_type: str = "business_object"
    source: Source = Source.LLM
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)


class FeatureNode(BaseModel):
    model_config = _Out
    id: str
    name: str
    description: str = ""
    source: Source = Source.LLM
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)


class BusinessFlowNode(BaseModel):
    model_config = _Out
    id: str
    name: str
    description: str = ""
    actor_role_ids: list[str] = Field(default_factory=list)
    source: Source = Source.LLM
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)


class FlowStepNode(BaseModel):
    model_config = _Out
    id: str
    flow_id: str
    order: int
    name: str
    source: Source = Source.LLM
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)


class Nodes(BaseModel):
    model_config = _Out
    roles: list[RoleNode] = Field(default_factory=list)
    pages: list[PageNode] = Field(default_factory=list)
    api_operations: list[ApiOperationNode] = Field(default_factory=list)
    parameters: list[ParameterNode] = Field(default_factory=list)
    resources: list[ResourceNode] = Field(default_factory=list)
    features: list[FeatureNode] = Field(default_factory=list)
    business_flows: list[BusinessFlowNode] = Field(default_factory=list)
    flow_steps: list[FlowStepNode] = Field(default_factory=list)


class Relationship(BaseModel):
    model_config = _Out
    id: str
    type: RelType
    from_id: str
    to_id: str
    source: Source = Source.OBSERVED
    confidence: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class AnalysisMeta(BaseModel):
    model_config = _Out
    analysis_id: str
    crawl_run_id: str = ""
    target_base_url: str = ""
    analyzer_version: str = "analyzer-v1"


class AnalysisResult(BaseModel):
    model_config = _Out
    schema_version: str = SCHEMA_VERSION
    analysis: AnalysisMeta
    nodes: Nodes = Field(default_factory=Nodes)
    relationships: list[Relationship] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
