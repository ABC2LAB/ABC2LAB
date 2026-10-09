"""출력 adapter: 내부 결과를 test_scenarios.json 계약(envelope + data)으로 바꾸고 Schema로 다시 검증한다."""

import functools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from modules.scenario_generator.utils.schema_errors import summarize_schema_errors

SCHEMA_VERSION = "0.2.0"
ARTIFACT_TYPE = "test_scenarios"
PRODUCER = "scenario_generator"
OUTPUT_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "output" / "test_scenarios.schema.json"

STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"


class OutputContractError(RuntimeError):
    """우리가 만든 결과가 자기 출력 계약을 어겼다. 이런 파일은 공개하지 않는다."""


@dataclass(frozen=True)
class RunIdentity:
    run_id: str
    iteration: int
    mode: str
    artifact_id: str


@dataclass(frozen=True)
class OutputParts:
    status: str
    errors: list[dict[str, Any]]
    input_refs: list[dict[str, Any]]
    scenarios: list[dict[str, Any]] | None  # None이면 사용할 결과가 없다(failed)
    model_info: dict[str, Any] | None
    runtime_metrics: dict[str, Any] | None


def decide_status(scenario_count: int, candidate_count: int, error_count: int) -> str:
    """completed: 오류 없음 / failed: 후보가 있었는데 쓸 시나리오가 하나도 없음 / 그 외 오류가 있으면 partial."""
    if error_count == 0:
        return STATUS_COMPLETED
    if scenario_count == 0 and candidate_count > 0:
        return STATUS_FAILED
    return STATUS_PARTIAL


@functools.cache
def _get_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(OUTPUT_SCHEMA_PATH.read_text(encoding="utf-8")))


def validate_output_document(document: dict[str, Any]) -> None:
    validator = _get_validator()
    if not validator.is_valid(document):
        raise OutputContractError(f"출력이 계약을 어겼다: {summarize_schema_errors(validator, document)}")


def build_output_document(identity: RunIdentity, created_at: str, parts: OutputParts) -> dict[str, Any]:
    data = None if parts.scenarios is None else {"scenarios": parts.scenarios, "model_info": parts.model_info}
    document = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "artifact_id": identity.artifact_id,
        "run_id": identity.run_id,
        "iteration": identity.iteration,
        "producer": PRODUCER,
        "mode": identity.mode,
        "created_at": created_at,
        "status": parts.status,
        "input_refs": parts.input_refs,
        "errors": parts.errors,
        "runtime_metrics": parts.runtime_metrics,
        "data": data,
    }
    validate_output_document(document)
    return document
