"""규칙 설정 로드. 매직 넘버 대신 configs/access_analyzer.toml에서 읽는다(표준 tomllib, 새 의존성 없음).

위치: ACCESS_ANALYZER_CONFIG_PATH > modules/access_analyzer/configs/access_analyzer.toml. 파일이 없으면 코드의
기본값으로 동작한다. 대상 앱별 조정(역할 순서 등)은 레포 밖 설정 파일을 환경변수로 가리킨다(코드에 앱 값을 두지 않는다).
"""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_PATH_ENV = "ACCESS_ANALYZER_CONFIG_PATH"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "access_analyzer.toml"
DEFAULT_MAX_CANDIDATES = 100
# 성공 응답 범위(RFC 9110 2xx). 역할의 접근 집합과 "이미 공유된 엔드포인트" 판단에 쓴다.
DEFAULT_SUCCESS_STATUS_RANGE = (200, 299)
# 거절을 관찰한 것으로 보는 상태. 그 밖(3xx·400·404·405·422 등)은 인가 판단 근거가 아니라 후보에서 빼지 않는다.
DEFAULT_DENIED_STATUS_CODES = (401, 403)
SAME_ROLE_SECTION = "same_role_other_owner"
VERTICAL_SECTION = "vertical_access"
HTTP_STATUS_MIN = 100
HTTP_STATUS_MAX = 599


class RuleConfigError(ValueError):
    """설정 파일 값이 규칙이 쓸 수 있는 모양이 아니다. 메시지에는 키 이름과 기대 모양만 넣는다."""


@dataclass(frozen=True)
class SameRoleRuleConfig:
    enabled: bool
    max_candidates: int


@dataclass(frozen=True)
class VerticalRuleConfig:
    enabled: bool
    max_candidates: int
    # 낮은 역할 → 높은 역할 순서의 role_id. 비어 있으면 관찰(2xx 엔드포인트 집합의 진부분집합)로 추정한다.
    role_order: tuple[str, ...]
    # (최소, 최대) 포함 범위. 이 범위의 응답을 "접근 성공"으로 본다.
    success_status_range: tuple[int, int]
    # 낮은 역할이 이 상태로 관찰한 엔드포인트는 거절을 이미 관찰한 것이라 후보에서 뺀다.
    denied_status_codes: frozenset[int]


@dataclass(frozen=True)
class RuleConfig:
    same_role_other_owner: SameRoleRuleConfig
    vertical_access: VerticalRuleConfig


DEFAULT_RULE_CONFIG = RuleConfig(
    same_role_other_owner=SameRoleRuleConfig(enabled=True, max_candidates=DEFAULT_MAX_CANDIDATES),
    vertical_access=VerticalRuleConfig(
        enabled=True,
        max_candidates=DEFAULT_MAX_CANDIDATES,
        role_order=(),
        success_status_range=DEFAULT_SUCCESS_STATUS_RANGE,
        denied_status_codes=frozenset(DEFAULT_DENIED_STATUS_CODES),
    ),
)


def resolve_config_path() -> Path:
    """환경변수가 있으면 그 경로, 없거나 비었으면 커밋된 기본 파일."""
    configured = os.environ.get(CONFIG_PATH_ENV, "").strip()
    return Path(configured) if configured else CONFIG_PATH


def load_rule_config(path: Path | None = None) -> RuleConfig:
    source = path or resolve_config_path()
    if not source.is_file():
        return DEFAULT_RULE_CONFIG
    with source.open("rb") as handle:
        raw = tomllib.load(handle)
    rules = raw.get("rules", {})
    same_role = rules.get(SAME_ROLE_SECTION, {})
    vertical = rules.get(VERTICAL_SECTION, {})
    defaults = DEFAULT_RULE_CONFIG.vertical_access
    return RuleConfig(
        same_role_other_owner=SameRoleRuleConfig(
            enabled=_parse_bool(same_role, "enabled", True, SAME_ROLE_SECTION),
            max_candidates=_parse_max_candidates(same_role, SAME_ROLE_SECTION),
        ),
        vertical_access=VerticalRuleConfig(
            enabled=_parse_bool(vertical, "enabled", True, VERTICAL_SECTION),
            max_candidates=_parse_max_candidates(vertical, VERTICAL_SECTION),
            role_order=_parse_role_order(vertical.get("role_order", [])),
            success_status_range=_parse_status_range(vertical.get("success_status_range", list(defaults.success_status_range))),
            denied_status_codes=_parse_status_codes(vertical.get("denied_status_codes", sorted(defaults.denied_status_codes))),
        ),
    )


def _parse_bool(section: dict[str, Any], key: str, default: bool, section_name: str) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise RuleConfigError(f"rules.{section_name}.{key}는 true/false여야 함")
    return value


def _parse_max_candidates(section: dict[str, Any], section_name: str) -> int:
    value = section.get("max_candidates", DEFAULT_MAX_CANDIDATES)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuleConfigError(f"rules.{section_name}.max_candidates는 1 이상의 정수여야 함")
    return value


def _parse_role_order(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(role_id, str) or not role_id for role_id in value):
        raise RuleConfigError(f"rules.{VERTICAL_SECTION}.role_order는 비어 있지 않은 role_id 문자열 배열이어야 함")
    if len(set(value)) != len(value):
        raise RuleConfigError(f"rules.{VERTICAL_SECTION}.role_order에 같은 role_id가 두 번 있음")
    return tuple(value)


def _parse_status_range(value: Any) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2 or any(not _is_http_status(code) for code in value):
        raise RuleConfigError(f"rules.{VERTICAL_SECTION}.success_status_range는 [최소, 최대] HTTP 상태 코드여야 함")
    low, high = value
    if low > high:
        raise RuleConfigError(f"rules.{VERTICAL_SECTION}.success_status_range의 최소가 최대보다 큼")
    return (low, high)


def _parse_status_codes(value: Any) -> frozenset[int]:
    if not isinstance(value, list) or any(not _is_http_status(code) for code in value):
        raise RuleConfigError(f"rules.{VERTICAL_SECTION}.denied_status_codes는 HTTP 상태 코드 배열이어야 함")
    return frozenset(value)


def _is_http_status(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and HTTP_STATUS_MIN <= value <= HTTP_STATUS_MAX
