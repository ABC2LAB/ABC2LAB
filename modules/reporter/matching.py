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

RESOURCE_KEY_FIELD = "resource_key"
RESOURCE_SCOPE_FIELD = "resource_scope"
RESOURCE_MATCH_KEY_FIELD = "match_key"
RESOURCE_IDENTIFIERS_FIELD = "identifiers"

# KG 0.2의 resource_key를 기존 Ground Truth 표현과 비교할 때 사용하는
# 호환 alias다. Ground Truth 자체를 KG 내부 표현에 종속시키지 않는다.
RESOURCE_TYPE_ALIAS = "resource_type"
RESOURCE_NAME_ALIAS = "name"


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
    """Ground Truth match_key가 관찰된 속성에 포함되는지 비교한다.

    일반 엔티티는 기존 deterministic normalization 규칙을 그대로 사용한다.

    KG 0.2 Resource 속성은 비교 시에만 호환 view로 정규화한다.
    이를 통해 기존 Ground Truth의 다음 표현을 유지하면서도 최신 KG Resource와
    비교할 수 있다.

    예:
        Ground Truth
        {
            "resource_type": "order",
            "external_id": "order-b"
        }

        KG 0.2
        {
            "resource_key": "order",
            "resource_scope": "instance",
            "match_key": {
                "resource_key": "order",
                "identifiers": [
                    {
                        "key": "external_id",
                        "value": "order-b"
                    }
                ]
            }
        }

    Ground Truth는 KG의 내부 저장 표현을 알 필요가 없다.
    """
    if not expected:
        raise ContractValidationError(
            "비어 있는 정규화 match_key는 평가할 수 없음"
        )

    normalized_expected = normalized_mapping(expected)
    normalized_observed = normalized_mapping(
        _comparison_view(observed)
    )

    return all(
        key in normalized_observed
        and normalized_observed[key] == value
        for key, value in normalized_expected.items()
    )


def _comparison_view(
    observed: Mapping[str, Any],
) -> Mapping[str, Any]:
    """평가용 관찰 속성 view를 만든다.

    일반 엔티티는 그대로 반환한다.

    KG 0.2 Resource이면 다음 규칙으로 기존 Ground Truth와 호환되는
    비교 view를 만든다.

    - resource_key를 그대로 유지
    - resource_type = resource_key
    - name = resource_key
    - instance match_key.identifiers를 최상위 비교 key로 펼침

    실제 입력 artifact나 KG 데이터를 수정하지 않는다.
    """
    if not _is_resource_v0_2(observed):
        return observed

    resource_key = observed[RESOURCE_KEY_FIELD]

    comparison: dict[str, Any] = dict(observed)

    # resource_key가 KG 0.2 Resource 종류를 나타내는 canonical 값이다.
    # 기존 Ground Truth에서 사용해 온 resource_type/name과 비교할 수 있도록
    # 평가 view에만 alias를 제공한다.
    comparison[RESOURCE_TYPE_ALIAS] = resource_key
    comparison[RESOURCE_NAME_ALIAS] = resource_key

    match_key = observed.get(RESOURCE_MATCH_KEY_FIELD)
    if isinstance(match_key, Mapping):
        identifiers = match_key.get(RESOURCE_IDENTIFIERS_FIELD)

        if isinstance(identifiers, list):
            for identifier in identifiers:
                if not isinstance(identifier, Mapping):
                    continue

                key = identifier.get("key")
                value = identifier.get("value")

                if not isinstance(key, str) or not key.strip():
                    continue

                # Resource instance identity의 canonical identifier가
                # 일반 properties보다 우선한다.
                comparison[key] = value

    return comparison


def _is_resource_v0_2(
    value: Mapping[str, Any],
) -> bool:
    """KG 0.2 Resource properties 모양인지 판별한다.

    단순히 name 필드가 있다는 이유로 Resource로 간주하지 않는다.
    Role 등 다른 엔티티의 기존 matching 동작에 영향을 주지 않기 위해
    KG 0.2에서 새로 정의한 세 필드를 기준으로 판별한다.
    """
    return (
        RESOURCE_KEY_FIELD in value
        and RESOURCE_SCOPE_FIELD in value
        and RESOURCE_MATCH_KEY_FIELD in value
        and isinstance(value.get(RESOURCE_KEY_FIELD), str)
        and value.get(RESOURCE_SCOPE_FIELD) in {"type", "instance"}
    )


def normalized_mapping(
    value: Mapping[str, Any],
) -> dict[str, Hashable]:
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
            tuple(
                normalize_value(normalized_key, item)
                for item in value
            ),
        )

    raise ContractValidationError(
        f"match_key에 지원하지 않는 값 형식: "
        f"{type(value).__name__}"
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
        steps = sorted(
            value["steps"],
            key=lambda item: item["order"],
        )
        actions = [
            item["action"]
            for item in steps
        ]

    return (
        normalize_text(value["name"]),
        tuple(
            normalize_text(item)
            for item in actions
        ),
    )


def case_signature(
    value: Mapping[str, Any],
) -> tuple[object, ...]:
    return (
        normalize_text(value["category"]),
        normalize_text(value["vulnerability_type"]),
        normalize_text(value["actor_alias"]),
        mapping_signature(value["resource_match_key"]),
    )