"""JSON Schema 위반을 위치와 어긴 규칙 이름만으로 요약한다.

jsonschema 기본 메시지는 입력 값을 그대로 담는데, 입력에 비밀값이 섞일 수 있어서 값은 쓰지 않는다.
"""

from collections.abc import Iterable
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

# 오류 메시지가 길어지지 않게 앞쪽 몇 건만 보여 준다.
MAX_REPORTED_SCHEMA_ERRORS = 5


def format_json_path(parts: Iterable[Any]) -> str:
    formatted = "$"
    for part in parts:
        formatted += f"[{part}]" if isinstance(part, int) else f".{part}"
    return formatted


def summarize_schema_errors(validator: Draft202012Validator, document: Any) -> str:
    errors: list[ValidationError] = sorted(
        validator.iter_errors(document), key=lambda error: [str(part) for part in error.absolute_path]
    )
    reported = [
        f"{format_json_path(error.absolute_path)} ({error.validator})"
        for error in errors[:MAX_REPORTED_SCHEMA_ERRORS]
    ]
    remaining = len(errors) - len(reported)
    suffix = f" 외 {remaining}건" if remaining > 0 else ""
    return ", ".join(reported) + suffix
