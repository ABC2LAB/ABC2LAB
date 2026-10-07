"""Deterministic normalization rules for the reporter default profile."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Hashable
from urllib.parse import urlsplit

from modules.reporter.exceptions import ContractValidationError

DEFAULT_MATCHING_PROFILE = "default-v1"
PATH_KEYS = frozenset(
    {"path", "url", "path_template", "endpoint_path_template"}
)


@dataclass(frozen=True)
class ObservedEntity:
    entity_id: str
    entity_type: str
    properties: Mapping[str, Any]


def require_supported_profile(profile: str | None) -> str:
    if profile != DEFAULT_MATCHING_PROFILE:
        raise ContractValidationError(
            f"지원하지 않는 matching_profile: {profile}"
        )
    return profile


def matches_key(
    expected: Mapping[str, Any],
    observed: Mapping[str, Any],
) -> bool:
    if not expected:
        raise ContractValidationError("비어 있는 정규화 match_key는 평가할 수 없음")
    normalized_expected = normalized_mapping(expected)
    normalized_observed = normalized_mapping(observed)
    return all(
        key in normalized_observed and normalized_observed[key] == value
        for key, value in normalized_expected.items()
    )


def normalized_mapping(value: Mapping[str, Any]) -> dict[str, Hashable]:
    return {
        str(key).strip().casefold(): normalize_value(str(key), item)
        for key, item in value.items()
    }


def mapping_signature(
    value: Mapping[str, Any],
) -> tuple[tuple[str, Hashable], ...]:
    return tuple(sorted(normalized_mapping(value).items()))


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def normalize_value(key: str, value: Any) -> Hashable:
    normalized_key = key.strip().casefold()
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        return ("number", value)
    if isinstance(value, str):
        if normalized_key == "method":
            normalized = value.strip().upper()
        elif normalized_key in PATH_KEYS:
            normalized = normalize_path(value)
        else:
            normalized = normalize_text(value)
        return ("string", normalized)
    if isinstance(value, Mapping):
        return (
            "object",
            tuple(sorted(normalized_mapping(value).items())),
        )
    if isinstance(value, list):
        return (
            "array",
            tuple(normalize_value(normalized_key, item) for item in value),
        )
    raise ContractValidationError(
        f"match_key에 지원하지 않는 값 형식: {type(value).__name__}"
    )


def normalize_path(value: str) -> str:
    stripped = value.strip()
    parsed = urlsplit(stripped)
    path = parsed.path
    if not path.startswith("/"):
        path = f"/{path}"
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def workflow_signature(
    value: Mapping[str, Any],
    is_ground_truth: bool,
) -> tuple[object, ...]:
    if is_ground_truth:
        actions = value["ordered_actions"]
    else:
        steps = sorted(value["steps"], key=lambda item: item["order"])
        actions = [item["action"] for item in steps]
    return (
        normalize_text(value["name"]),
        tuple(normalize_text(item) for item in actions),
    )


def case_signature(value: Mapping[str, Any]) -> tuple[object, ...]:
    return (
        normalize_text(value["category"]),
        normalize_text(value["vulnerability_type"]),
        normalize_text(value["actor_alias"]),
        mapping_signature(value["resource_match_key"]),
    )
