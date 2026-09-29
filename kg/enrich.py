"""
kg 모듈의 LLM 보강 (재상 담당의 '크롤 → LLM 추론 → 후보' 부분).

규칙 기반 빌더(build_kg)가 만든 그래프에, 규칙으로는 못 잡는 판단을 얹는다:
각 Endpoint 가 어느 접근 범위(access_scope)인지 LLM 이 분류한다.
  public         : 누구나
  authenticated  : 로그인만 하면
  user_owned     : 특정 사용자 소유 (IDOR 후보!)
  admin_only     : 관리자 전용

설계 원칙
  - 결과는 Endpoint 노드의 property 로만 붙이고 evidence(locator="llm_inference")를 남긴다.
    -> 스키마(라벨/관계)를 안 바꾸므로 B 쪽과의 계약이 그대로 유지된다.
  - API 키가 없으면 조용히 건너뛴다(원본 그대로 반환). 개발/테스트가 안 막히게.
  - LLM 호출은 LLMClient 프로토콜로 분리 -> 테스트는 가짜 클라이언트로 네트워크 없이.
"""
from __future__ import annotations

import os
from typing import Literal, Protocol

from pydantic import BaseModel

from kg.schemas import Evidence, KGCandidates, NodeLabel

AccessScope = Literal["public", "authenticated", "user_owned", "admin_only"]


class EndpointClass(BaseModel):
    endpoint_id: str
    access_scope: AccessScope
    rationale: str


class LLMClassification(BaseModel):
    endpoints: list[EndpointClass]


class LLMClient(Protocol):
    def classify_endpoints(self, endpoints: list[dict]) -> LLMClassification: ...


# ── 실제 OpenAI 클라이언트 ─────────────────────────────
class OpenAIClient:
    """OPENAI_API_KEY / OPENAI_MODEL 을 써서 엔드포인트 접근범위를 분류."""
    SYSTEM = (
        "너는 웹 접근통제 분류기다. 주어진 엔드포인트 목록만 근거로 각 엔드포인트의 "
        "access_scope 를 public/authenticated/user_owned/admin_only 중 하나로 분류하라. "
        "목록에 없는 내용은 지어내지 마라."
    )

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key or os.environ["OPENAI_API_KEY"])
        self._model = model or os.getenv("OPENAI_MODEL", "gpt-6-sol")

    def classify_endpoints(self, endpoints: list[dict]) -> LLMClassification:
        resp = self._client.responses.parse(
            model=self._model,
            input=[
                {"role": "system", "content": self.SYSTEM},
                {"role": "user", "content": str(endpoints)},
            ],
            text_format=LLMClassification,
        )
        return resp.output_parsed


def make_default_client() -> LLMClient | None:
    """키가 있으면 OpenAI 클라이언트, 없으면 None(보강 건너뜀)."""
    if not os.getenv("OPENAI_API_KEY"):
        return None
    return OpenAIClient()


def enrich_with_llm(kg: KGCandidates, client: LLMClient | None) -> KGCandidates:
    """Endpoint 노드에 access_scope 를 채워 넣는다. client 가 None 이면 원본 그대로."""
    if client is None:
        return kg

    endpoints = [
        {"id": n.id, "method": n.properties.get("method"),
         "template": n.properties.get("template")}
        for n in kg.nodes if n.label == NodeLabel.ENDPOINT
    ]
    if not endpoints:
        return kg

    result = client.classify_endpoints(endpoints)
    by_id = {n.id: n for n in kg.nodes}
    for ec in result.endpoints:
        n = by_id.get(ec.endpoint_id)
        if n is None or n.label != NodeLabel.ENDPOINT:
            continue
        n.properties["access_scope"] = ec.access_scope
        n.evidence.append(Evidence(
            source_url=str(n.properties.get("template", ec.endpoint_id)),
            locator="llm_inference",
            snippet=ec.rationale[:200],
            confidence=0.5,
        ))
    kg.meta["llm_enriched"] = True
    return kg
