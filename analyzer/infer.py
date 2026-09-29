"""
추론(llm) 단계: ApiOperation 이 다루는 Resource 를 추론하고
ApiOperation -ACCESSES-> Resource 관계를 만든다. derived_by="llm".
Feature/Flow 추론은 다음 단계(스텁).
"""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from analyzer.llm_client import LLMClient
from analyzer.normalize import resource_id
from analyzer.schemas import DerivedBy, Node, NodeType, Relationship

_PROMPTS = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    return (_PROMPTS / f"{name}.md").read_text(encoding="utf-8")


class ResourceGuess(BaseModel):
    resource_name: str
    resource_type: str = "business_object"
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str = ""


def infer_resources(api_nodes: list[Node], client: LLMClient
                    ) -> tuple[list[Node], list[Relationship]]:
    tmpl = load_prompt("resource")
    resources: dict[str, Node] = {}
    rels: list[Relationship] = []
    for api in api_nodes:
        if api.type != NodeType.API_OPERATION:
            continue
        method = str(api.properties.get("method", ""))
        endpoint = str(api.properties.get("endpoint", ""))
        # 프롬프트 예시에 {id} 같은 literal 중괄호가 있어 .format 대신 replace 사용
        prompt = tmpl.replace("{method}", method).replace("{endpoint}", endpoint)
        guess = client.infer(prompt, ResourceGuess)
        rid = resource_id(guess.resource_name)
        if rid not in resources:
            resources[rid] = Node(
                id=rid, type=NodeType.RESOURCE, name=guess.resource_name.title(),
                derived_by=DerivedBy.LLM, confidence=guess.confidence,
                evidence_ids=list(api.evidence_ids),
                properties={"resource_type": guess.resource_type},
            )
        rels.append(Relationship(
            from_id=api.id, type="ACCESSES", to_id=rid, derived_by=DerivedBy.LLM,
            confidence=guess.confidence, evidence_ids=list(api.evidence_ids)))
    return list(resources.values()), rels


def infer_features(*args, **kwargs):
    """TODO(analyzer): Feature 추론 (USES_PAGE/USES_API)."""
    return [], []


def infer_flows(*args, **kwargs):
    """TODO(analyzer): BusinessFlow/FlowStep 추론 (HAS_STEP/NEXT/VISITS)."""
    return [], [], []


def demo_fake_responder(prompt: str, schema: type) -> BaseModel:
    """키 없이 테스트/데모용. 프롬프트의 endpoint 줄만 보고 Resource 를 흉내낸다."""
    if schema is ResourceGuess:
        m = re.search(r"경로:\s*(\S+)", prompt)
        low = (m.group(1) if m else prompt).lower()
        for kw, name in (("order", "order"), ("product", "product"),
                         ("user", "user"), ("cart", "cart")):
            if kw in low:
                return ResourceGuess(resource_name=name, confidence=0.9, rationale="fake")
        return ResourceGuess(resource_name="resource", confidence=0.4, rationale="fake-fallback")
    raise NotImplementedError(schema)
