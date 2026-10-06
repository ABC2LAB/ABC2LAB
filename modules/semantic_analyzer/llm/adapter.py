"""LLM 한 겹. 의미 추론 호출을 여기 한 곳에 모아 모델만 바꿀 수 있게 한다.

명세: LLM은 의미 추론에만 쓴다. 정규화·구조 그래프는 service의 규칙이 만든다.
이번 PR은 네트워크 0인 FakeClient만 제공한다. 실제 provider(Ollama 등)는 같은 LlmClient
프로토콜로 다음 PR에서 붙인다(관리자 lock 갱신 필요).

주의: FakeClient의 결과는 추론(inferred)이다. service가 basis=inferred로 기록하고,
프로그램이 타입·참조를 다시 검증한다(LLM 출력은 데이터다).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from modules.semantic_analyzer.utils.normalize import ID_PLACEHOLDER

PROMPT_VERSION = "fake-v1"
# 의미 없는 경로 접두사(리소스 이름으로 보지 않음). 특정 앱 값이 아니라 흔한 관례만 둔다.
_NON_RESOURCE_SEGMENTS = frozenset({"api", "v1", "v2", "v3"})
_ACTION_BY_METHOD = {
    "GET": "read",
    "HEAD": "read",
    "POST": "create",
    "PUT": "update",
    "PATCH": "update",
    "DELETE": "delete",
}
_WORD = re.compile(r"[a-z][a-z0-9_]*")


@dataclass(frozen=True)
class RequestFeatures:
    """LLM에 넘기는 요청 특징(비밀값 없음)."""
    method: str
    path_template: str
    parameter_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequestMeaning:
    """LLM이 돌려주는 의미. 프로그램이 다시 검증한다."""
    action_meaning: str
    resource_keys: tuple[str, ...] = field(default=())


class LlmClient(Protocol):
    def infer_request_meaning(self, features: RequestFeatures) -> RequestMeaning: ...

    def model_info(self) -> dict | None: ...


class FakeClient:
    """네트워크 0 결정적 추론. method와 경로의 명사 세그먼트로 의미를 흉내낸다."""

    def infer_request_meaning(self, features: RequestFeatures) -> RequestMeaning:
        resource_keys = _resource_keys_from_path(features.path_template)
        verb = _ACTION_BY_METHOD.get(features.method.upper(), "access")
        primary = resource_keys[0] if resource_keys else "resource"
        return RequestMeaning(action_meaning=f"{verb}_{primary}", resource_keys=resource_keys)

    def model_info(self) -> dict | None:
        # 실제 모델이 아니므로 null. (명세: 미사용/미상이면 model_info=null)
        return None


def _resource_keys_from_path(path_template: str) -> tuple[str, ...]:
    """경로에서 리소스 후보(단수형)를 뽑는다. {id}와 흔한 접두사는 제외한다."""
    keys: list[str] = []
    for segment in path_template.split("/"):
        if not segment or segment == ID_PLACEHOLDER:
            continue
        if not _WORD.fullmatch(segment) or segment in _NON_RESOURCE_SEGMENTS:
            continue
        keys.append(_singularize(segment))
    return tuple(keys)


def _singularize(word: str) -> str:
    # 아주 단순한 복수→단수(영문 관례). 앱별 사전을 두지 않는다.
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith("ses") and len(word) > 3:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 1:
        return word[:-1]
    return word
