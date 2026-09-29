"""
KG 후보 = kg 모듈의 출력, analyzer/Neo4j 의 입력. 공유 계약(버전 관리).

규칙
  - schema_version 으로 버전 관리(형식 바뀌면 올린다).
  - 모든 노드/엣지에 evidence(근거) 최소 1개: 어느 role 이 어느 url 에서 나온
    주장인지. analyzer/검증기가 입력과 대조할 수 있게.
  - label / 관계 type 은 Enum(화이트리스트)로 고정 -> Cypher Injection 차단
    (Cypher 에서 label/타입은 $파라미터로 못 넣는다).
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "0.1.0"

PropValue = Union[str, int, float, bool]
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
    properties: dict[str, PropValue | list[PropValue]] = Field(default_factory=dict)
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
    properties: dict[str, PropValue | list[PropValue]] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(..., min_length=1)


class KGCandidates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = Field(default=SCHEMA_VERSION)
    target_base_url: str = ""
    nodes: list[NodeCandidate] = Field(default_factory=list)
    edges: list[EdgeCandidate] = Field(default_factory=list)
    meta: dict[str, PropValue] = Field(default_factory=dict)
