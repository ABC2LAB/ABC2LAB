"""입력 3종(test_scenarios·safety_decisions·crawl_result)과 자기 출력(verification_results) 검증.

문제를 예외로 끊지 않고 ValidationIssue 목록으로 모아 돌려준다. 빈 목록이면 통과다. 메시지에는 문서 값이 아니라
위치·규칙 이름만 넣는다(값 누출 방지). 다른 모듈 코드를 import하지 않고 자기 schemas/와 함께 둔다.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"
TEST_SCENARIOS_SCHEMA = "input/test_scenarios.schema.json"
SAFETY_DECISIONS_SCHEMA = "input/safety_decisions.schema.json"
CRAWL_RESULT_SCHEMA = "input/crawl_result.schema.json"
VERIFICATION_RESULTS_SCHEMA = "output/verification_results.schema.json"
VERIFICATION_RESULTS_FILE_NAME = "verification_results.json"
ROOT_LOCATION = "$"
UTC_TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|\+00:00)"
)
KEY_ONLY_SCHEMA_RULES = frozenset({"required", "additionalProperties"})

# 재현 결과와 실행 상태의 대응(명세 m7 표). result → 허용 execution_status.
EXECUTION_STATUS_BY_RESULT = {
    "success": {"completed"},
    "failure": {"completed"},
    "blocked": {"not_executed"},
    "indeterminate": {"error", "completed", "not_executed"},
}
# Policy 판정 → 허용 result.
RESULT_BY_POLICY_DECISION = {
    "allow": {"success", "failure", "indeterminate"},
    "block": {"blocked"},
    "require_approval": {"blocked"},
}


class IssueCode(StrEnum):
    JSON_INVALID = "JSON_INVALID"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    TIME_INVALID = "TIME_INVALID"
    DUPLICATE_ID = "DUPLICATE_ID"
    REFERENCE_MISSING = "REFERENCE_MISSING"
    RESULT_STATUS_INVALID = "RESULT_STATUS_INVALID"
    GRAPH_UPDATE_INVALID = "GRAPH_UPDATE_INVALID"


@dataclass(frozen=True)
class ValidationIssue:
    code: IssueCode
    location: str
    message: str


@dataclass
class _Report:
    issues: list[ValidationIssue] = field(default_factory=list)

    def add(self, code: IssueCode, location: str, message: str) -> None:
        self.issues.append(ValidationIssue(code, location, message))


class _StrictJsonError(ValueError):
    """표준 json이 조용히 받아 주지만 계약에서 금지한 입력(중복 키, NaN·Infinity)."""


# ── 입력 검증 ──

def validate_test_scenarios_file(path: Path) -> list[ValidationIssue]:
    return _validate_input_file(path, TEST_SCENARIOS_SCHEMA, _check_test_scenarios_semantics)


def validate_safety_decisions_file(path: Path) -> list[ValidationIssue]:
    return _validate_input_file(path, SAFETY_DECISIONS_SCHEMA, _check_safety_decisions_semantics)


def validate_crawl_result_file(path: Path) -> list[ValidationIssue]:
    return _validate_input_file(path, CRAWL_RESULT_SCHEMA, _check_crawl_result_semantics)


def _validate_input_file(path: Path, schema_name: str, semantic) -> list[ValidationIssue]:
    report = _Report()
    document = _parse_strict_json(report, ROOT_LOCATION, path.read_bytes())
    if document is None or not _check_schema(report, ROOT_LOCATION, document, schema_name):
        return report.issues
    _check_utc_timestamp(report, f"{ROOT_LOCATION}.created_at", document["created_at"])
    if document["data"] is not None:
        semantic(report, document["data"])
    return report.issues


def _check_test_scenarios_semantics(report: _Report, data: dict[str, Any]) -> None:
    scenario_ids: set[str] = set()
    for index, scenario in enumerate(data["scenarios"]):
        location = f"{ROOT_LOCATION}.data.scenarios[{index}]"
        _require_unique(report, f"{location}.scenario_id", scenario["scenario_id"], scenario_ids, "scenario_id")
        step_ids: set[str] = set()
        for step_index, step in enumerate(scenario["steps"]):
            step_location = f"{location}.steps[{step_index}]"
            _require_unique(report, f"{step_location}.step_id", step["step_id"], step_ids, "step_id")
        for step_index, step in enumerate(scenario["steps"]):
            for binding in step["bindings"]:
                # 바인딩은 같은 시나리오의 선행 단계를 가리켜야 한다.
                if binding["source_step_id"] not in step_ids:
                    report.add(
                        IssueCode.REFERENCE_MISSING,
                        f"{location}.steps[{step_index}].bindings",
                        f"binding source_step_id가 시나리오 단계에 없음: {binding['source_step_id']}",
                    )


def _check_safety_decisions_semantics(report: _Report, data: dict[str, Any]) -> None:
    scenario_ids: set[str] = set()
    decision_ids: set[str] = set()
    for index, decision in enumerate(data["decisions"]):
        location = f"{ROOT_LOCATION}.data.decisions[{index}]"
        _require_unique(report, f"{location}.decision_id", decision["decision_id"], decision_ids, "decision_id")
        _require_unique(report, f"{location}.scenario_id", decision["scenario_id"], scenario_ids, "scenario_id")


def _check_crawl_result_semantics(report: _Report, data: dict[str, Any]) -> None:
    account_ids: set[str] = set()
    for index, account in enumerate(data["accounts"]):
        _require_unique(
            report, f"{ROOT_LOCATION}.data.accounts[{index}].account_id", account["account_id"], account_ids, "account_id"
        )
    request_ids: set[str] = set()
    for index, request in enumerate(data["requests"]):
        _require_unique(
            report, f"{ROOT_LOCATION}.data.requests[{index}].request_id", request["request_id"], request_ids, "request_id"
        )


# ── 출력 검증 ──

def validate_verification_results_bytes(raw: bytes) -> list[ValidationIssue]:
    report = _Report()
    document = _parse_strict_json(report, ROOT_LOCATION, raw)
    if document is None or not _check_schema(report, ROOT_LOCATION, document, VERIFICATION_RESULTS_SCHEMA):
        return report.issues
    _check_utc_timestamp(report, f"{ROOT_LOCATION}.created_at", document["created_at"])
    if document["data"] is not None:
        _check_verification_results_semantics(report, document["data"])
    return report.issues


def _check_verification_results_semantics(report: _Report, data: dict[str, Any]) -> None:
    results = data["results"]
    verification_ids: set[str] = set()
    success_ids: set[str] = set()
    for index, item in enumerate(results):
        location = f"{ROOT_LOCATION}.data.results[{index}]"
        _require_unique(report, f"{location}.verification_id", item["verification_id"], verification_ids, "verification_id")
        _check_result_pairing(report, location, item)
        if item["result"] == "success" and item["execution_status"] == "completed":
            success_ids.add(item["verification_id"])
    _check_graph_updates(report, data["graph_updates"], success_ids)


def _check_result_pairing(report: _Report, location: str, item: dict[str, Any]) -> None:
    """명세 m7: policy_decision↔result, result↔execution_status, blocked는 steps=[]."""
    policy_decision = item["policy_decision"]
    result = item["result"]
    execution_status = item["execution_status"]
    allowed_results = RESULT_BY_POLICY_DECISION[policy_decision]
    if result not in allowed_results:
        report.add(
            IssueCode.RESULT_STATUS_INVALID,
            f"{location}.result",
            f"policy_decision={policy_decision}에 허용되지 않은 result={result}",
        )
    if execution_status not in EXECUTION_STATUS_BY_RESULT[result]:
        report.add(
            IssueCode.RESULT_STATUS_INVALID,
            f"{location}.execution_status",
            f"result={result}에 허용되지 않은 execution_status={execution_status}",
        )
    if result == "blocked" and item["steps"]:
        report.add(IssueCode.RESULT_STATUS_INVALID, f"{location}.steps", "blocked 결과는 steps가 비어 있어야 함")


def _check_graph_updates(report: _Report, graph_updates: dict[str, Any], success_ids: set[str]) -> None:
    """검증 근거가 있는 성공 결과만 graph_updates의 출처가 될 수 있다(명세 m7)."""
    source_ids = graph_updates["source_verification_ids"]
    has_updates = bool(graph_updates["nodes"] or graph_updates["relationships"])
    if has_updates and not source_ids:
        report.add(
            IssueCode.GRAPH_UPDATE_INVALID,
            f"{ROOT_LOCATION}.data.graph_updates.source_verification_ids",
            "graph update가 있으면 source_verification_ids가 있어야 함",
        )
    for source_id in source_ids:
        if source_id not in success_ids:
            report.add(
                IssueCode.GRAPH_UPDATE_INVALID,
                f"{ROOT_LOCATION}.data.graph_updates.source_verification_ids",
                "source_verification_id가 성공·완료 검증 결과가 아님",
            )


# ── 공용 도구 ──

def _require_unique(report: _Report, location: str, value: str, seen: set[str], label: str) -> None:
    if value in seen:
        report.add(IssueCode.DUPLICATE_ID, location, f"{label}가 중복됨: {value}")
    seen.add(value)


@cache
def _load_validator(schema_name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _check_schema(report: _Report, location: str, document: object, schema_name: str) -> bool:
    errors = sorted(_load_validator(schema_name).iter_errors(document), key=lambda error: error.json_path)
    for error in errors:
        report.add(IssueCode.SCHEMA_INVALID, f"{location}{error.json_path[1:]}", _describe_schema_error(error))
    return not errors


def _describe_schema_error(error: ValidationError) -> str:
    if error.validator in KEY_ONLY_SCHEMA_RULES:
        return error.message
    return f"{error.validator} 규칙 위반 (Schema 값: {error.validator_value!r})"


def _parse_strict_json(report: _Report, location: str, raw: bytes) -> Any | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        report.add(IssueCode.JSON_INVALID, location, "UTF-8이 아님")
        return None
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_non_finite)
    except json.JSONDecodeError as error:
        report.add(IssueCode.JSON_INVALID, location, f"JSON 형식 오류: {error.msg} ({error.lineno}행 {error.colno}열)")
    except _StrictJsonError as error:
        report.add(IssueCode.JSON_INVALID, location, str(error))
    return None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _StrictJsonError(f"중복 키: {key}")
        result[key] = value
    return result


def _reject_non_finite(constant: str) -> float:
    raise _StrictJsonError(f"허용하지 않는 숫자: {constant}")


def _check_utc_timestamp(report: _Report, location: str, text: str) -> None:
    if not _is_utc_timestamp(text):
        report.add(IssueCode.TIME_INVALID, location, "UTC RFC3339 시각이 아님 (예: 2026-10-06T03:00:00Z)")


def _is_utc_timestamp(text: str) -> bool:
    if not UTC_TIMESTAMP_PATTERN.fullmatch(text):
        return False
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return False
    return True
