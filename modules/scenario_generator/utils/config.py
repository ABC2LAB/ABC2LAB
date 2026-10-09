"""초안 생성기(drafter) 설정 읽기·검증. 표준 tomllib만 쓴다(새 의존성 없음).

위치: SCENARIO_GENERATOR_CONFIG_PATH > modules/scenario_generator/configs/scenario_generator.toml (현재 폴더 = 레포 루트 기준).
CLI 옵션도 같은 검증(parse_drafter_settings)을 거친다. 실패는 전부 ValueError이고 메시지에는 출처·키 이름만 넣는다(값은 안 넣음).
ollama 선택 값(base_url·temperature·timeout_seconds 등)의 기본값은 OllamaDrafterConfig 한 곳에만 있다.
여기서는 사용자가 준 키만 넘긴다.
"""

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_PATH_ENV = "SCENARIO_GENERATOR_CONFIG_PATH"
DEFAULT_CONFIG_PATH = Path("modules") / "scenario_generator" / "configs" / "scenario_generator.toml"
LLM_TABLE = "llm"

PROVIDER_NONE = "none"
PROVIDER_OLLAMA = "ollama"
PROVIDER_REPLAY = "replay"
PROVIDERS = (PROVIDER_NONE, PROVIDER_OLLAMA, PROVIDER_REPLAY)

PROVIDER_KEY = "provider"
DRAFTS_PATH_KEY = "drafts_path"
MODEL_ID_KEY = "model_id"
# OllamaDrafterConfig 필드 이름과 같다. 사용자가 준 것만 그대로 넘긴다.
OLLAMA_STRING_KEYS = (MODEL_ID_KEY, "base_url", "model_version")
OLLAMA_NUMBER_KEYS = ("temperature", "timeout_seconds")
OLLAMA_INTEGER_KEYS = ("seed",)
OLLAMA_KEYS = (*OLLAMA_STRING_KEYS, *OLLAMA_NUMBER_KEYS, *OLLAMA_INTEGER_KEYS)
LLM_KEYS = frozenset({PROVIDER_KEY, DRAFTS_PATH_KEY, *OLLAMA_KEYS})


@dataclass(frozen=True)
class DrafterSettings:
    provider: str
    # OllamaDrafterConfig에 그대로 넘길 키워드 인자(사용자가 준 키만). ollama가 아니면 비어 있다.
    ollama_options: dict[str, Any] = field(default_factory=dict)
    drafts_path: Path | None = None


def resolve_config_path() -> Path:
    """환경변수가 있으면 그 경로, 없거나 비었으면 기본값(현재 폴더 기준)."""
    configured = os.environ.get(CONFIG_PATH_ENV, "").strip()
    return Path(configured) if configured else DEFAULT_CONFIG_PATH


def load_drafter_settings(path: Path) -> DrafterSettings:
    """설정 파일을 읽어 검증한다. 파일 없음·읽기 실패·형식 오류·미정의 키·타입·범위·provider 규칙 위반은 ValueError."""
    try:
        if not path.is_file():
            raise ValueError(f"scenario_generator 설정 파일이 없음: {path}")
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        # 권한 등으로 읽지 못함. 설정 문제는 모두 ValueError로 알리고 원인은 예외 체인에 남긴다.
        raise ValueError(f"scenario_generator 설정 파일을 읽지 못함: {path}") from error
    except UnicodeDecodeError as error:
        raise ValueError(f"설정 파일 형식 오류 ({path.name}): {error}") from None
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"설정 파일 형식 오류 ({path.name}): {error}") from None
    unknown_tables = sorted(key for key in document if key != LLM_TABLE)
    if unknown_tables:
        raise ValueError(f"{path.name}: 정의되지 않은 설정 키: {', '.join(unknown_tables)}")
    llm_table = document.get(LLM_TABLE)
    if not isinstance(llm_table, dict):
        raise ValueError(f"{path.name}: [{LLM_TABLE}] 표가 없음")
    return parse_drafter_settings(llm_table, f"{path.name} [{LLM_TABLE}]")


def parse_drafter_settings(values: Mapping[str, Any], source: str) -> DrafterSettings:
    """[llm] 표 또는 CLI 옵션 묶음을 검증한다. source는 오류 메시지의 출처 이름(파일 이름·'CLI')."""
    unknown_keys = sorted(key for key in values if key not in LLM_KEYS)
    if unknown_keys:
        raise ValueError(f"{source}: 정의되지 않은 설정 키: {', '.join(unknown_keys)}")
    _check_types(values, source)
    provider = values.get(PROVIDER_KEY)
    if provider is None:
        raise ValueError(f"{source}: {PROVIDER_KEY}가 없음")
    if provider not in PROVIDERS:
        raise ValueError(f"{source}: {PROVIDER_KEY}는 {', '.join(PROVIDERS)} 중 하나여야 함")
    if provider == PROVIDER_OLLAMA:
        return DrafterSettings(provider, ollama_options=_ollama_options(values, source))
    if provider == PROVIDER_REPLAY:
        return DrafterSettings(provider, drafts_path=_existing_drafts_path(values, source))
    return DrafterSettings(provider)


def _check_types(values: Mapping[str, Any], source: str) -> None:
    for key in (PROVIDER_KEY, DRAFTS_PATH_KEY, *OLLAMA_STRING_KEYS):
        if key in values and not isinstance(values[key], str):
            raise ValueError(f"{source}: {key}는 문자열이어야 함")
    # bool은 int의 하위 클래스라 따로 막는다(True가 1로 통과하지 않게).
    for key in OLLAMA_NUMBER_KEYS:
        if key in values and (isinstance(values[key], bool) or not isinstance(values[key], (int, float))):
            raise ValueError(f"{source}: {key}는 수여야 함")
    for key in OLLAMA_INTEGER_KEYS:
        if key in values and (isinstance(values[key], bool) or not isinstance(values[key], int)):
            raise ValueError(f"{source}: {key}는 정수여야 함")
    if "temperature" in values and values["temperature"] < 0:
        raise ValueError(f"{source}: temperature는 0 이상이어야 함")
    if "timeout_seconds" in values and values["timeout_seconds"] <= 0:
        raise ValueError(f"{source}: timeout_seconds는 0보다 커야 함")


def _ollama_options(values: Mapping[str, Any], source: str) -> dict[str, Any]:
    if not values.get(MODEL_ID_KEY, "").strip():
        raise ValueError(f"{source}: {PROVIDER_KEY}={PROVIDER_OLLAMA}에는 {MODEL_ID_KEY}가 필요함")
    return {key: values[key] for key in OLLAMA_KEYS if key in values}


def _existing_drafts_path(values: Mapping[str, Any], source: str) -> Path:
    raw_path = values.get(DRAFTS_PATH_KEY, "").strip()
    if not raw_path:
        raise ValueError(f"{source}: {PROVIDER_KEY}={PROVIDER_REPLAY}에는 {DRAFTS_PATH_KEY}가 필요함")
    drafts_path = Path(raw_path)
    try:
        is_file = drafts_path.is_file()
    except OSError as error:
        # 부모 폴더 권한 등으로 확인하지 못함. 경로 값은 메시지에 넣지 않는다.
        raise ValueError(f"{source}: {DRAFTS_PATH_KEY}가 가리키는 파일을 확인하지 못함") from error
    if not is_file:
        raise ValueError(f"{source}: {DRAFTS_PATH_KEY}가 가리키는 파일이 없음")
    return drafts_path
