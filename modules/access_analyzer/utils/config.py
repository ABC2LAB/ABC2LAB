"""규칙 설정 로드. 매직 넘버 대신 configs/access_analyzer.toml에서 읽는다(표준 tomllib, 새 의존성 없음).

파일이 없으면 코드의 기본값으로 동작한다. 대상 앱별 조정은 이 파일만 바꾼다.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "access_analyzer.toml"
DEFAULT_MAX_CANDIDATES = 100


@dataclass(frozen=True)
class SameRoleRuleConfig:
    enabled: bool
    max_candidates: int


@dataclass(frozen=True)
class RuleConfig:
    same_role_other_owner: SameRoleRuleConfig


DEFAULT_RULE_CONFIG = RuleConfig(
    same_role_other_owner=SameRoleRuleConfig(enabled=True, max_candidates=DEFAULT_MAX_CANDIDATES)
)


def load_rule_config(path: Path | None = None) -> RuleConfig:
    source = path or CONFIG_PATH
    if not source.is_file():
        return DEFAULT_RULE_CONFIG
    with source.open("rb") as handle:
        raw = tomllib.load(handle)
    section = raw.get("rules", {}).get("same_role_other_owner", {})
    return RuleConfig(
        same_role_other_owner=SameRoleRuleConfig(
            enabled=bool(section.get("enabled", True)),
            max_candidates=int(section.get("max_candidates", DEFAULT_MAX_CANDIDATES)),
        )
    )
