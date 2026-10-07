"""엄격한 JSON 읽기·쓰기. 명세: NaN·Infinity·중복 키를 허용하지 않는다."""

import json
from pathlib import Path
from typing import Any


class InvalidJsonError(ValueError):
    """UTF-8·JSON 문법·중복 키·NaN/Infinity 위반."""


def _reject_constant(name: str) -> Any:
    raise InvalidJsonError(f"허용하지 않는 JSON 상수: {name}")


def _build_object_rejecting_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    # json.loads 기본 동작은 중복 키를 조용히 마지막 값으로 덮어쓴다.
    built: dict[str, Any] = {}
    for key, value in pairs:
        if key in built:
            raise InvalidJsonError(f"중복 키: {key}")
        built[key] = value
    return built


def parse_json_strict(raw: bytes) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InvalidJsonError("UTF-8이 아닌 바이트가 있다") from error
    try:
        return json.loads(
            text,
            object_pairs_hook=_build_object_rejecting_duplicates,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as error:
        raise InvalidJsonError(f"JSON 문법 오류: {error.msg} (줄 {error.lineno}, 열 {error.colno})") from error


def read_json_strict(path: Path) -> Any:
    return parse_json_strict(path.read_bytes())


def dump_json_bytes(document: Any) -> bytes:
    """저장할 UTF-8 바이트를 만든다. 해시는 이 바이트 기준이다."""
    try:
        text = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)
    except ValueError as error:
        raise InvalidJsonError(f"JSON으로 직렬화할 수 없다: {error}") from error
    return (text + "\n").encode("utf-8")
