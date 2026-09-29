"""
LLM 추론 레이어. prompts + llm_client로 관찰 그래프에 의미를 얹는다.
V1은 Resource 추론을 구현하고, Feature/Flow는 스텁(다음 단계).
"""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from common.schemas import (
    ApiOperationNode, FeatureNode, FlowStepNode, BusinessFlowNode,
    Relationship, RelType, ResourceNode, Source,
)
from analyzer.llm_client import LLMClient
from analyzer.normalize import rel_id

_PROMPTS = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    return (_PROMPTS / f"{name}.md").read_text(encoding="utf-8")


# LLM이 돌려줄 형식 (계약 노드가 아니라 내부 응답 스키마)
class ResourceGuess(BaseModel):
    resource_name: str
    resource_type: str = "business_object"
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str = ""


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.strip().lower()).strip("-") or "resource"


def infer_resources(api_ops: list[ApiOperationNode], client: LLMClient
                    ) -> tuple[list[ResourceNode], list[Relationship]]:
    """각 ApiOperation → Resource 추론 + ApiOperation-ACCESSES->Resource."""
    tmpl = load_prompt("resource")
    resources: dict[str, ResourceNode] = {}
    rels: list[Relationship] = []
    for api in api_ops:
        prompt = tmpl.format(method=api.method, endpoint=api.endpoint)
        guess = client.infer(prompt, ResourceGuess)
        rid = f"resource:{_slug(guess.resource_name)}"
        resources.setdefault(rid, ResourceNode(
            id=rid, name=guess.resource_name.title(), resource_type=guess.resource_type,
            source=Source.LLM, confidence=guess.confidence, evidence_ids=list(api.evidence_ids)))
        rels.append(Relationship(
            id=rel_id(api.id, "ACCESSES", rid), type=RelType.ACCESSES,
            from_id=api.id, to_id=rid, source=Source.LLM,
            confidence=guess.confidence, evidence_ids=list(api.evidence_ids)))
    return list(resources.values()), rels


def infer_features(*args, **kwargs) -> tuple[list[FeatureNode], list[Relationship]]:
    """TODO(analyzer): Feature 추론 (USES_PAGE/USES_API)."""
    return [], []


def infer_flows(*args, **kwargs) -> tuple[list[BusinessFlowNode], list[FlowStepNode], list[Relationship]]:
    """TODO(analyzer): BusinessFlow/FlowStep 추론 (HAS_STEP/NEXT/VISITS)."""
    return [], [], []


def demo_fake_responder(prompt: str, schema: type) -> BaseModel:
    """키 없이 테스트/데모용. 프롬프트의 endpoint 키워드로 Resource를 흉내낸다."""
    if schema is ResourceGuess:
        # 프롬프트 예시 문구가 아니라 'endpoint:' 줄만 보고 판단
        m = re.search(r"엔드포인트:\s*\S+\s+(\S+)", prompt)
        low = (m.group(1) if m else prompt).lower()
        for kw, name in (("order", "order"), ("product", "product"),
                         ("user", "user"), ("cart", "cart")):
            if kw in low:
                return ResourceGuess(resource_name=name, confidence=0.9, rationale="fake")
        return ResourceGuess(resource_name="resource", confidence=0.4, rationale="fake-fallback")
    raise NotImplementedError(schema)
