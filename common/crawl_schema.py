"""
크롤러(민준) 출력 = kg 모듈 입력. 크롤러와 kg 사이의 공유 계약.

역할(role)별로 pages/requests 를 담는다. role 이 갈리는 것이 접근통제(IDOR)
분석의 핵심 재료다: 같은 endpoint/resource 를 어떤 role 이 어떤 status 로
접근했는지 비교할 수 있다.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# 크롤러가 필드를 더 붙여도 깨지지 않게 입력은 extra="ignore"
_In = ConfigDict(extra="ignore")


class Link(BaseModel):
    model_config = _In
    action_id: str
    url: str
    endpoint: str
    text: str = ""
    is_state_changing: bool = False
    outcome: str = ""


class PageAction(BaseModel):
    model_config = _In
    action_id: str
    kind: str = ""
    label: str = ""
    method: str | None = None
    target_url: str | None = None
    field_names: list[str] = Field(default_factory=list)
    is_state_changing: bool = False
    outcome: str = ""


class DiscoveredPage(BaseModel):
    model_config = _In
    role: str
    url: str
    endpoint: str
    title: str = ""
    status: int = 0
    depth: int = 0
    source_page: str | None = None
    source_action: str | None = None
    links: list[Link] = Field(default_factory=list)
    actions: list[PageAction] = Field(default_factory=list)


class CapturedRequest(BaseModel):
    model_config = _In
    role: str
    method: str
    resource_type: str = ""
    url: str
    endpoint: str
    status: int = 0
    query_params: dict[str, list[str]] = Field(default_factory=dict)
    body_params: dict = Field(default_factory=dict)
    resource_ids: list[str] = Field(default_factory=list)
    request_headers: dict[str, str] = Field(default_factory=dict)
    response_headers: dict[str, str] = Field(default_factory=dict)
    source_page: str | None = None
    source_action: str | None = None
    captured_at: str = ""


class RoleCapture(BaseModel):
    model_config = _In
    role: str
    error: str | None = None
    pages: list[DiscoveredPage] = Field(default_factory=list)
    requests: list[CapturedRequest] = Field(default_factory=list)


class CrawlSession(BaseModel):
    model_config = _In
    target_base_url: str
    started_at: str = ""
    roles: list[RoleCapture] = Field(default_factory=list)
