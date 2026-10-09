"""JSON Schema and contract semantic validation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

from modules.safety_policy.exceptions import ContractValidationError
from modules.safety_policy.utils.hashing import calculate_sha256

_BINDING_PLACEHOLDER_PATTERN = re.compile(r"\{([^{}]+)\}")
_ASSESSMENT_KEYS = (
    "target_scope",
    "test_accounts",
    "request_budget",
    "state_change",
    "data_impact",
    "service_impact",
)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractValidationError(f"중복 JSON 키: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ContractValidationError(f"유한하지 않은 JSON 숫자: {value}")


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as source:
            value = json.load(
                source,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
            )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractValidationError(f"JSON 읽기 실패: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ContractValidationError("산출물 최상위 값은 object여야 함")
    return value


def parse_json_bytes(content: bytes) -> dict[str, Any]:
    """Validate the same bytes that will be stored as a policy snapshot."""
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ContractValidationError("JSON 데이터 읽기 실패") from error
    if not isinstance(value, dict):
        raise ContractValidationError("설정 최상위 값은 object여야 함")
    return value


def validate_schema(value: dict[str, Any], schema_path: Path) -> None:
    schema = load_json(schema_path)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        raise ContractValidationError(f"Schema 위반 ({location}): {error.message}")


def load_and_validate_artifact(path: Path, schema_path: Path) -> dict[str, Any]:
    artifact = load_json(path)
    validate_schema(artifact, schema_path)
    return artifact


def validate_test_scenarios_semantics(artifact: dict[str, Any]) -> None:
    if artifact["status"] == "failed":
        return

    scenarios = artifact["data"]["scenarios"]
    _require_unique(
        (scenario["scenario_id"] for scenario in scenarios),
        "scenario_id",
    )
    for scenario in scenarios:
        _validate_scenario(scenario)


def _validate_scenario(scenario: dict[str, Any]) -> None:
    scenario_id = scenario["scenario_id"]
    steps = scenario["steps"]
    step_ids = _require_unique(
        (step["step_id"] for step in steps),
        f"step_id ({scenario_id})",
    )
    orders = [step["order"] for step in steps]
    if sorted(orders) != list(range(len(orders))):
        raise ContractValidationError("scenario step order는 0부터 연속이어야 함")

    all_checks = [*scenario["preconditions"], *scenario["assertions"]]
    _require_unique(
        (check["check_id"] for check in all_checks),
        f"check_id ({scenario_id})",
    )

    order_by_step_id = {step["step_id"]: step["order"] for step in steps}
    all_binding_ids = [
        binding["binding_id"]
        for step in steps
        for binding in step["bindings"]
    ]
    _require_unique(all_binding_ids, f"binding_id ({scenario_id})")
    for step in steps:
        _validate_step(step, step_ids, order_by_step_id)


def _validate_step(
    step: dict[str, Any],
    step_ids: set[str],
    order_by_step_id: dict[str, int],
) -> None:
    bindings = step["bindings"]
    binding_ids = {binding["binding_id"] for binding in bindings}
    for binding in bindings:
        source_step_id = binding["source_step_id"]
        if source_step_id not in step_ids:
            raise ContractValidationError("binding source_step_id가 scenario에 없음")
        if order_by_step_id[source_step_id] >= step["order"]:
            raise ContractValidationError("binding은 현재 단계보다 앞선 단계만 참조해야 함")
        _validate_binding_selector(binding)

    for parameter in step["request"]["parameters"]:
        binding_ref = parameter["binding_ref"]
        if binding_ref is None:
            continue
        if parameter["value"] is not None:
            raise ContractValidationError(
                "binding_ref를 사용한 parameter의 value는 null이어야 함"
            )
        if binding_ref not in binding_ids:
            raise ContractValidationError("parameter binding_ref가 현재 step bindings에 없음")

    _validate_url_template(step["request"]["url_template"], binding_ids)


def _validate_binding_selector(binding: dict[str, Any]) -> None:
    selector = binding["selector"]
    if binding["source_part"] == "response_body" and not selector.startswith("/"):
        raise ContractValidationError("response_body binding selector는 JSON Pointer여야 함")
    if binding["source_part"] == "response_header" and selector != selector.lower():
        raise ContractValidationError("response_header binding selector는 소문자여야 함")


def _validate_url_template(url_template: str, binding_ids: set[str]) -> None:
    placeholders = _BINDING_PLACEHOLDER_PATTERN.findall(url_template)
    without_placeholders = _BINDING_PLACEHOLDER_PATTERN.sub("", url_template)
    if "{" in without_placeholders or "}" in without_placeholders:
        raise ContractValidationError("url_template binding 표현이 올바르지 않음")
    if any(placeholder not in binding_ids for placeholder in placeholders):
        raise ContractValidationError("url_template이 없는 binding_id를 참조함")


def validate_safety_decisions_semantics(artifact: dict[str, Any]) -> None:
    if artifact["status"] == "failed":
        return

    decisions = artifact["data"]["decisions"]
    _require_unique(
        (decision["decision_id"] for decision in decisions),
        "decision_id",
    )
    _require_unique(
        (decision["scenario_id"] for decision in decisions),
        "decision scenario_id",
    )
    for decision in decisions:
        _validate_decision(decision)


def _validate_decision(decision: dict[str, Any]) -> None:
    _require_unique(decision["reason_codes"], "reason_code")
    _require_unique(decision["effective_origins"], "effective_origin")
    _require_unique(decision["effective_account_ids"], "effective_account_id")

    statuses = tuple(
        decision["assessment"][key]["status"] for key in _ASSESSMENT_KEYS
    )
    result = decision["decision"]
    if result == "allow" and any(status != "pass" for status in statuses):
        raise ContractValidationError("allow 판정은 6개 assessment가 모두 pass여야 함")
    if "block" in statuses and result != "block":
        raise ContractValidationError("block assessment가 있으면 최종 판정도 block이어야 함")
    if result == "require_approval" and "block" in statuses:
        raise ContractValidationError("block assessment를 승인 요청으로 바꿀 수 없음")
    if result != "allow" and decision["approval_ref"] is not None:
        raise ContractValidationError("미허용 판정의 approval_ref는 null이어야 함")


def validate_safety_decisions_against_scenarios(
    decisions_artifact: dict[str, Any],
    scenarios_artifact: dict[str, Any],
    scenarios_path: Path,
) -> None:
    if decisions_artifact["status"] == "failed":
        return
    if scenarios_artifact["status"] == "failed":
        raise ContractValidationError("failed test_scenarios로 정상 판정을 만들 수 없음")

    expected_hash = calculate_sha256(scenarios_path)
    data = decisions_artifact["data"]
    if data["scenarios_sha256"] != expected_hash:
        raise ContractValidationError("scenarios_sha256이 입력 파일의 실제 해시와 다름")

    source_refs = [
        item
        for item in decisions_artifact["input_refs"]
        if item["artifact_type"] == "test_scenarios"
        and item["artifact_id"] == scenarios_artifact["artifact_id"]
    ]
    if len(source_refs) != 1 or source_refs[0]["sha256"] != expected_hash:
        raise ContractValidationError("test_scenarios input_ref 대응이 올바르지 않음")

    scenario_ids = {
        scenario["scenario_id"] for scenario in scenarios_artifact["data"]["scenarios"]
    }
    decision_scenario_ids = {
        decision["scenario_id"] for decision in data["decisions"]
    }
    if decision_scenario_ids != scenario_ids:
        raise ContractValidationError("모든 scenario_id에 정확히 하나의 판정이 필요함")


def _require_unique(values: Iterable[str], label: str) -> set[str]:
    values_list = list(values)
    values_set = set(values_list)
    if len(values_list) != len(values_set):
        raise ContractValidationError(f"중복 {label}")
    return values_set
