"""재현 실행 설정 로드. 매직 넘버 대신 configs/verifier.toml에서 읽는다(표준 tomllib, 새 의존성 없음).

파일이 없으면 코드의 기본값으로 동작한다. 대상 앱별 조정은 이 파일만 바꾼다.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "verifier.toml"
# 기본 0: 리다이렉트를 따라가지 않는다(로그인 리다이렉트 → 200 오탐 방지).
DEFAULT_MAX_REDIRECTS = 0


@dataclass(frozen=True)
class ReplayConfig:
    max_redirects: int


DEFAULT_REPLAY_CONFIG = ReplayConfig(max_redirects=DEFAULT_MAX_REDIRECTS)


def load_replay_config(path: Path | None = None) -> ReplayConfig:
    source = path or CONFIG_PATH
    if not source.is_file():
        return DEFAULT_REPLAY_CONFIG
    with source.open("rb") as handle:
        raw = tomllib.load(handle)
    section = raw.get("replay", {})
    max_redirects = section.get("max_redirects", DEFAULT_MAX_REDIRECTS)
    if not isinstance(max_redirects, int) or isinstance(max_redirects, bool) or max_redirects < 0:
        return DEFAULT_REPLAY_CONFIG
    return ReplayConfig(max_redirects=max_redirects)
