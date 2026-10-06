import json
from pathlib import Path
from shutil import copyfile
from typing import Any

import pytest

from modules.safety_policy.evaluate_adapter import parse_evaluate_request
from modules.safety_policy.exceptions import (
    ContractValidationError,
    SourceArtifactFailedError,
)
from modules.safety_policy.models import EvaluationRequest
from modules.safety_policy.service import prepare_evaluation
from modules.safety_policy.utils.hashing import calculate_sha256
from modules.safety_policy.utils.validation import load_json


def test_prepare_evaluation_returns_valid_internal_input(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
) -> None:
    request = parse_evaluate_request(*evaluate_arguments)

    prepared = prepare_evaluation(request)

    assert prepared.request == request
    assert prepared.source.artifact_id == "test_scenarios_demo_001"
    assert prepared.source.status == "completed"
    assert prepared.source.sha256 == request.expected_sha256
    assert prepared.source_errors == ()
    assert len(prepared.scenarios.scenarios) == 2


def test_prepare_evaluation_preserves_partial_input_errors(
    fixture_root: Path,
    evaluate_run_root: Path,
) -> None:
    input_path = _input_path(evaluate_run_root)
    copyfile(fixture_root / "status/test_scenarios.partial.json", input_path)
    request = _request_for(evaluate_run_root, input_path)

    prepared = prepare_evaluation(request)

    assert prepared.source.status == "partial"
    assert len(prepared.source_errors) == 1
    assert prepared.source_errors[0].code == "SCENARIO_GENERATION_PARTIAL"
    assert prepared.scenarios.scenarios == ()


def test_prepare_evaluation_rejects_failed_input(
    fixture_root: Path,
    evaluate_run_root: Path,
) -> None:
    input_path = _input_path(evaluate_run_root)
    copyfile(fixture_root / "status/test_scenarios.failed.json", input_path)
    request = _request_for(evaluate_run_root, input_path)

    with pytest.raises(SourceArtifactFailedError, match="failed test_scenarios"):
        prepare_evaluation(request)


def test_prepare_evaluation_rejects_hash_mismatch(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    input_paths["test_scenarios"]["sha256"] = "f" * 64
    request = parse_evaluate_request(input_paths, output_dir, context)

    with pytest.raises(ContractValidationError, match="실제 파일과 다름"):
        prepare_evaluation(request)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("run_id", "other_run", "run_id"),
        ("iteration", 1, "iteration"),
        ("mode", "diagnosis", "mode"),
    ],
)
def test_prepare_evaluation_rejects_envelope_context_mismatch(
    evaluate_run_root: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    input_path = _input_path(evaluate_run_root)
    artifact = load_json(input_path)
    artifact[field] = value
    _write_json(input_path, artifact)
    request = _request_for(evaluate_run_root, input_path)

    with pytest.raises(ContractValidationError, match=message):
        prepare_evaluation(request)


def test_prepare_evaluation_rejects_schema_violation(
    evaluate_run_root: Path,
) -> None:
    input_path = _input_path(evaluate_run_root)
    artifact = load_json(input_path)
    artifact["unexpected"] = True
    _write_json(input_path, artifact)
    request = _request_for(evaluate_run_root, input_path)

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        prepare_evaluation(request)


def _input_path(run_root: Path) -> Path:
    return run_root / (
        "artifacts/iteration-000/scenario_generator/test_scenarios.json"
    )


def _request_for(run_root: Path, input_path: Path) -> EvaluationRequest:
    return parse_evaluate_request(
        input_paths={
            "test_scenarios": {
                "path": (
                    "artifacts/iteration-000/scenario_generator/"
                    "test_scenarios.json"
                ),
                "sha256": calculate_sha256(input_path),
            }
        },
        output_dir="artifacts/iteration-000/safety_policy",
        context={
            "run_id": "run_demo_001",
            "iteration": 0,
            "mode": "development",
            "run_root": run_root,
        },
    )


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
