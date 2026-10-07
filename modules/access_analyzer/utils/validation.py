"""자기 출력(graph_query.json)의 Schema·의미 검증.

문제를 예외로 하나씩 끊지 않고 ValidationIssue 목록으로 모아 돌려준다. 빈 목록이면 통과다.
메시지에는 문서 값이 아니라 위치·규칙 이름만 넣는다(값 누출 방지). 다른 모듈 코드를 import하지 않고
자기 schemas/와 함께 둔다. graph_query_result 입력 검증과 vulnerability_candidates 검증은 analyze(다음 PR)에서 더한다.
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
GRAPH_QUERY_SCHEMA_NAME = "output/graph_query.schema.json"
GRAPH_QUERY_FILE_NAME = "graph_query.json"
ROOT_LOCATION = "$"
UTC_TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|\+00:00)"
)
# jsonschema 오류 중 메시지에 문서의 키 이름만 들어가는 것. 나머지는 값이 섞일 수 있어 규칙 이름만 쓴다.
KEY_ONLY_SCHEMA_RULES = frozenset({"required", "additionalProperties"})


class IssueCode(StrEnum):
    JSON_INVALID = "JSON_INVALID"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    TIME_INVALID = "TIME_INVALID"
    DUPLICATE_ID = "DUPLICATE_ID"
    QUERIES_EMPTY = "QUERIES_EMPTY"


@dataclass(frozen=True)
class ValidationIssue:
    code: IssueCode
    # "$.data.queries[2].query_id"처럼 문서 안 위치
    location: str
    message: str


@dataclass
class _Report:
    issues: list[ValidationIssue] = field(default_factory=list)

    def add(self, code: IssueCode, location: str, message: str) -> None:
        self.issues.append(ValidationIssue(code, location, message))


class _StrictJsonError(ValueError):
    """표준 json이 조용히 받아 주지만 계약에서 금지한 입력(중복 키, NaN·Infinity)."""


def validate_graph_query_bytes(raw: bytes) -> list[ValidationIssue]:
    """저장 전 직렬화 바이트를 검사한다. 빈 목록이면 통과."""
    report = _Report()
    document = _parse_strict_json(report, ROOT_LOCATION, raw)
    if document is None or not _check_schema(report, ROOT_LOCATION, document, GRAPH_QUERY_SCHEMA_NAME):
        return report.issues
    _check_utc_timestamp(report, f"{ROOT_LOCATION}.created_at", document["created_at"])
    if document["data"] is not None:
        _check_query_semantics(report, document["data"])
    return report.issues


def validate_graph_query_file(artifact_path: Path) -> list[ValidationIssue]:
    """공개된 graph_query.json 하나를 검사한다."""
    return validate_graph_query_bytes(artifact_path.read_bytes())


def _check_query_semantics(report: _Report, data: dict[str, Any]) -> None:
    """Schema를 통과한 data의 query_id 유일성과 비어 있지 않은 queries를 확인한다."""
    queries = data["queries"]
    if not queries:
        report.add(IssueCode.QUERIES_EMPTY, f"{ROOT_LOCATION}.data.queries", "질의가 하나도 없음")
    seen: set[str] = set()
    for index, query in enumerate(queries):
        query_id = query["query_id"]
        if query_id in seen:
            report.add(
                IssueCode.DUPLICATE_ID,
                f"{ROOT_LOCATION}.data.queries[{index}].query_id",
                f"query_id가 중복됨: {query_id}",
            )
        seen.add(query_id)


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
