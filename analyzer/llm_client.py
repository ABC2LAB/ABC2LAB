"""
LLM 한 겹. 위층(infer)이 어느 모델인지 몰라도 되게 같은 인터페이스로 감싼다.
  - OpenAIClient : GPT (openai)
  - OllamaClient : 로컬 모델 (ollama)   ← 무료·오프라인
  - FakeClient   : 테스트용 (네트워크 0)
셋 다 infer(prompt, schema) → 검증된 pydantic 모델을 돌려준다.
"""
from __future__ import annotations

import os
from typing import Callable, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    def infer(self, prompt: str, schema: type[T]) -> T: ...


class OpenAIClient:
    def __init__(self, model: str | None = None) -> None:
        from openai import OpenAI
        self._c = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self._model = model or os.getenv("OPENAI_MODEL", "gpt-6-sol")

    def infer(self, prompt: str, schema: type[T]) -> T:
        r = self._c.responses.parse(model=self._model, input=prompt, text_format=schema)
        return r.output_parsed


class OllamaClient:
    def __init__(self, model: str | None = None) -> None:
        import ollama  # 지연 import
        self._ollama = ollama
        self._model = model or os.getenv("OLLAMA_MODEL", "llama3.1")

    def infer(self, prompt: str, schema: type[T]) -> T:
        r = self._ollama.chat(model=self._model,
                              messages=[{"role": "user", "content": prompt}],
                              format=schema.model_json_schema())
        return schema.model_validate_json(r["message"]["content"])


class FakeClient:
    """responder(prompt, schema) → schema 인스턴스. 테스트·오프라인 데모용."""
    def __init__(self, responder: Callable[[str, type], BaseModel]) -> None:
        self._r = responder

    def infer(self, prompt: str, schema: type[T]) -> T:
        return self._r(prompt, schema)


def make_client() -> LLMClient:
    """.env의 LLM_PROVIDER로 실제 클라이언트 선택 (openai | ollama)."""
    if os.getenv("LLM_PROVIDER") == "ollama":
        return OllamaClient()
    return OpenAIClient()
