"""Strict JSON, JSON Schema, identifier, and hash validation."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from modules.reporter.exceptions import (
    ContractValidationError,
    HashMismatchError,
)
from modules.reporter.utils.hashing import calculate_sha256

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractValidationError(f"중복 JSON 키: {key}")
        result[key] = value
    return result


def reject_non_finite(value: str) -> None:
    raise ContractValidationError(f"유한하지 않은 JSON 숫자: {value}")


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as source:
            value = json.load(
                source,
                object_pairs_hook=reject_duplicate_keys,
                parse_constant=reject_non_finite,
            )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractValidationError(f"JSON 읽기 실패: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ContractValidationError("JSON 최상위 값은 object여야 함")
    return value


def validate_schema(value: dict[str, Any], schema_path: Path) -> None:
    schema = load_json(schema_path)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(value),
        key=lambda item: [str(part) for part in item.absolute_path],
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        raise ContractValidationError(
            f"Schema 위반 ({location}): {error.message}"
        )


def require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise ContractValidationError(f"{label} SHA-256 형식이 올바르지 않음")
    return value


def verify_file_sha256(path: Path, expected_sha256: str) -> str:
    actual_sha256 = calculate_sha256(path)
    if actual_sha256 != expected_sha256:
        raise HashMismatchError(f"입력 파일 SHA-256 불일치: {path.name}")
    return actual_sha256


def require_unique(values: Iterable[str], label: str) -> set[str]:
    values_list = list(values)
    unique_values = set(values_list)
    if len(values_list) != len(unique_values):
        raise ContractValidationError(f"중복 {label}")
    return unique_values
