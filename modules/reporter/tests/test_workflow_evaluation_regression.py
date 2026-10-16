"""Workflow profile regression through evaluation, publication, and public CLI."""

import json
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest

from modules.reporter import entrypoint
from modules.reporter.contracts import (
    EVALUATION_ARTIFACT_TYPES,
    INPUT_SCHEMA_BY_NAME,
    REPORT_ARTIFACT_TYPES,
    prepare_evaluation_inputs,
    prepare_report_inputs,
)
from modules.reporter.evaluation_config import EVALUATION_CONFIG_PATH_ENV
from modules.reporter.evaluation_output_adapter import (
    EVALUATION_RESULTS_SCHEMA,
    build_evaluation_artifact,
    publish_evaluation_artifact,
)
from modules.reporter.exceptions import ContractValidationError
from modules.reporter.input_adapter import parse_evaluate_request, parse_report_request
from modules.reporter.matching import (
    DEFAULT_MATCHING_PROFILE,
    WORKFLOW_SUBSEQUENCE_PROFILE,
)
from modules.reporter.models import ErrorItem, EvaluationInputs, InputArtifact
from modules.reporter.service import build_diagnosis_report
from modules.reporter.utils import atomic_writer
from modules.reporter.utils.hashing import calculate_sha256
from modules.reporter.utils.validation import load_json, validate_schema

Arguments = tuple[dict[str, Any], str, dict[str, Any]]
PROFILES = (DEFAULT_MATCHING_PROFILE, WORKFLOW_SUBSEQUENCE_PROFILE)
PARTIAL_METRIC_IDS = ("workflow_step_recall", "workflow_ordered_step_recall")
ERROR = ErrorItem("TEST_UPSTREAM_INCOMPLETE", "합성 입력의 일부 처리 실패", None, False)


@pytest.fixture(autouse=True)
def isolate_evaluation_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EVALUATION_CONFIG_PATH_ENV, raising=False)


@pytest.fixture
def v2_arguments(evaluate_arguments: Arguments) -> Arguments:
    input_paths, output_dir, context = evaluate_arguments
    return input_paths, output_dir, {
        **context, "matching_profile": WORKFLOW_SUBSEQUENCE_PROFILE,
    }


@pytest.fixture
def v2_inputs(v2_arguments: Arguments) -> EvaluationInputs:
    return prepare_evaluation_inputs(parse_evaluate_request(*v2_arguments))


def _with_status(source: InputArtifact, status: str) -> InputArtifact:
    return replace(
        source,
        source=replace(source.source, status=status),
        value={
            **source.value, "status": status, "errors": [ERROR.to_mapping()],
            "data": None if status == "failed" else source.data,
        },
        errors=(ERROR,),
    )


def _metrics(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    assert artifact["data"] is not None
    return {item["metric_id"]: item for item in artifact["data"]["metrics"]}


def _clear_fixture_output(
    arguments: Arguments, filename: str = "evaluation_results.json",
) -> Path:
    _, output_dir, context = arguments
    output_path = Path(context["run_root"]) / output_dir / filename
    output_path.unlink(missing_ok=True)
    return output_path


def _input_hashes(arguments: Arguments) -> dict[str, str]:
    input_paths, _, context = arguments
    return {
        name: calculate_sha256(
            Path(context["project_root"] if name == "ground_truth"
                 else context["run_root"]) / descriptor["path"]
        )
        for name, descriptor in input_paths.items()
    }


def _write_fixture_artifact(
    arguments: Arguments, artifact_type: str, value: dict[str, Any],
) -> None:
    input_paths, _, context = arguments
    validate_schema(value, INPUT_SCHEMA_BY_NAME[artifact_type])
    path = Path(context["run_root"]) / input_paths[artifact_type]["path"]
    path.write_text(json.dumps(value), encoding="utf-8")
    input_paths[artifact_type]["sha256"] = calculate_sha256(path)


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("status", ["partial", "failed"])
@pytest.mark.parametrize("artifact_type", ["semantic_analysis", "graph_query_result"])
def test_workflow_profiles_preserve_upstream_status_and_available_scores(
    v2_inputs: EvaluationInputs, artifact_type: str, status: str, profile: str,
) -> None:
    inputs = replace(v2_inputs, request=replace(v2_inputs.request, matching_profile=profile))
    original = deepcopy(inputs)
    baseline = build_evaluation_artifact(inputs, perf_counter())
    source = _with_status(getattr(inputs, artifact_type), status)
    changes = {artifact_type: source}
    has_failed_snapshot = artifact_type == "graph_query_result" and status == "failed"
    if has_failed_snapshot:
        changes["graph_snapshot"] = None
    artifact = build_evaluation_artifact(replace(inputs, **changes), perf_counter())

    validate_schema(artifact, EVALUATION_RESULTS_SCHEMA)
    assert any(item["code"].startswith("UPSTREAM_") for item in artifact["errors"])
    if has_failed_snapshot:
        assert artifact["status"] == "failed"
        assert artifact["data"] is None
    else:
        assert artifact["status"] == "partial"
        baseline_metrics, metrics = _metrics(baseline), _metrics(artifact)
        for metric_id in ("workflow_recall", *PARTIAL_METRIC_IDS):
            if metric_id in baseline_metrics:
                assert metrics[metric_id] == baseline_metrics[metric_id]
            else:
                assert metric_id not in metrics
        if artifact_type == "semantic_analysis" and status == "failed":
            assert metrics["endpoint_recall"]["value"] is None
            assert metrics["parameter_recall"]["value"] is None
        for field in ("candidate_counts", "confirmed_counts", "unverified_counts"):
            assert artifact["data"][field] == baseline["data"][field]
    assert inputs == original


@pytest.mark.parametrize("kind", ["absent", "failed_query", "failed_artifact"])
def test_public_v2_missing_snapshot_publishes_failure_not_zero_metrics(
    v2_arguments: Arguments, kind: str,
) -> None:
    input_paths, _, context = v2_arguments
    graph = load_json(Path(context["run_root"]) / input_paths["graph_query_result"]["path"])
    if kind == "failed_artifact":
        graph.update(status="failed", errors=[ERROR.to_mapping()], data=None)
    elif kind == "absent":
        graph["data"]["results"] = [
            result for result in graph["data"]["results"]
            if result["query_key"] != "structure_snapshot"
        ]
    else:
        graph.update(status="partial", errors=[ERROR.to_mapping()])
        for result in graph["data"]["results"]:
            if result["query_key"] == "structure_snapshot":
                result.update(status="failed", rows=[], errors=[ERROR.to_mapping()])
    _write_fixture_artifact(v2_arguments, "graph_query_result", graph)
    output_path = _clear_fixture_output(v2_arguments)

    response = entrypoint.run("evaluate", *v2_arguments)

    assert response["status"] == "failed"
    assert response["artifact_id"] is not None
    assert response["sha256"] == calculate_sha256(output_path)
    artifact = load_json(output_path)
    validate_schema(artifact, EVALUATION_RESULTS_SCHEMA)
    assert artifact["status"] == "failed"
    assert artifact["data"] is None
    assert any(item["code"] == "EVALUATION_INPUT_UNAVAILABLE" for item in artifact["errors"])


def test_public_v2_empty_observed_workflows_measure_zero_not_failure(
    v2_arguments: Arguments, v2_inputs: EvaluationInputs,
) -> None:
    graph = deepcopy(v2_inputs.graph_query_result.value)
    for result in graph["data"]["results"]:
        if result["query_key"] == "structure_snapshot":
            for row in result["rows"]:
                row["workflows"] = []
    _write_fixture_artifact(v2_arguments, "graph_query_result", graph)
    output_path = _clear_fixture_output(v2_arguments)

    response = entrypoint.run("evaluate", *v2_arguments)

    assert response["status"] == "completed"
    artifact = load_json(output_path)
    validate_schema(artifact, EVALUATION_RESULTS_SCHEMA)
    metrics = _metrics(artifact)
    expected_step_count = sum(len(item["ordered_actions"]) for item in v2_inputs.ground_truth.workflows)
    assert expected_step_count > 0
    for metric_id in PARTIAL_METRIC_IDS:
        assert metrics[metric_id] == {
            "metric_id": metric_id, "value": 0,
            "numerator": 0, "denominator": expected_step_count,
        }


@pytest.mark.parametrize("artifact_type", (*EVALUATION_ARTIFACT_TYPES, "ground_truth"))
def test_v2_publication_rejects_any_input_changed_after_preparation(
    v2_arguments: Arguments, v2_inputs: EvaluationInputs, artifact_type: str,
) -> None:
    output_path = _clear_fixture_output(v2_arguments)
    artifact = build_evaluation_artifact(v2_inputs, perf_counter())
    path = v2_inputs.request.input_by_name(artifact_type).path
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ContractValidationError, match="변경됨"):
        publish_evaluation_artifact(v2_inputs, artifact)

    assert not output_path.exists()
    assert not list(output_path.parent.glob(f".{output_path.name}.*.tmp"))


@pytest.mark.parametrize("metric_id", PARTIAL_METRIC_IDS)
@pytest.mark.parametrize("mutation", ["value", "numerator", "denominator", "remove"])
def test_v2_publication_rejects_tampered_or_missing_partial_metric(
    v2_arguments: Arguments, v2_inputs: EvaluationInputs, metric_id: str, mutation: str,
) -> None:
    output_path = _clear_fixture_output(v2_arguments)
    artifact = build_evaluation_artifact(v2_inputs, perf_counter())
    metric = _metrics(artifact)[metric_id]
    if mutation == "remove":
        artifact["data"]["metrics"].remove(metric)
    elif mutation == "value":
        metric["value"] = 0.5 if metric["value"] != 0.5 else 0.25
    else:
        metric[mutation] += 1
    validate_schema(artifact, EVALUATION_RESULTS_SCHEMA)

    with pytest.raises(ContractValidationError, match="지표"):
        publish_evaluation_artifact(v2_inputs, artifact)

    assert not output_path.exists()
    assert not list(output_path.parent.glob(f".{output_path.name}.*.tmp"))


@pytest.mark.parametrize("failure_at", ["fsync", "rename"])
def test_v2_storage_failure_leaves_neither_output_nor_temporary_file(
    v2_arguments: Arguments, monkeypatch: pytest.MonkeyPatch, failure_at: str,
) -> None:
    output_path = _clear_fixture_output(v2_arguments)
    original_hashes = _input_hashes(v2_arguments)

    def fail_storage(*args: Any, **kwargs: Any) -> None:
        raise OSError("합성 저장 실패")

    if failure_at == "fsync":
        monkeypatch.setattr(atomic_writer.os, "fsync", fail_storage)
    else:
        monkeypatch.setattr(Path, "replace", fail_storage)

    response = entrypoint.run("evaluate", *v2_arguments)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "STORAGE_FAILED"
    assert response["artifact_id"] is None
    assert not output_path.exists()
    assert not list(output_path.parent.glob(f".{output_path.name}.*.tmp"))
    assert _input_hashes(v2_arguments) == original_hashes


def test_public_v2_output_is_immutable_and_preserves_all_input_references(
    v2_arguments: Arguments,
) -> None:
    output_path = _clear_fixture_output(v2_arguments)
    original_hashes = _input_hashes(v2_arguments)

    response = entrypoint.run("evaluate", *v2_arguments)

    assert response["status"] == "completed"
    artifact = load_json(output_path)
    validate_schema(artifact, EVALUATION_RESULTS_SCHEMA)
    output_hash = calculate_sha256(output_path)
    assert response["sha256"] == output_hash
    references = {item["artifact_type"]: item["sha256"] for item in artifact["input_refs"]}
    assert len(references) == len(artifact["input_refs"])
    assert references == {name: digest for name, digest in original_hashes.items() if name != "ground_truth"}
    assert artifact["data"]["ground_truth_ref"]["sha256"] == original_hashes["ground_truth"]
    assert set(PARTIAL_METRIC_IDS) <= _metrics(artifact).keys()

    repeated = entrypoint.run("evaluate", *v2_arguments)

    assert repeated["status"] == "failed"
    assert repeated["errors"][0]["code"] == "OUTPUT_EXISTS"
    assert calculate_sha256(output_path) == output_hash
    assert _input_hashes(v2_arguments) == original_hashes
    assert not list(output_path.parent.glob(f".{output_path.name}.*.tmp"))


def _run_cli(arguments: Arguments, profile: str | None) -> subprocess.CompletedProcess[str]:
    input_paths, output_dir, context = arguments
    command = [
        sys.executable, "-m", "modules.reporter.entrypoint", "evaluate",
        "--run-root", str(context["run_root"]), "--run-id", context["run_id"],
        "--iteration", str(context["iteration"]), "--mode", "development",
        "--target-url", context["target_url"], "--project-root", str(context["project_root"]),
        "--dataset-id", Path(input_paths["ground_truth"]["path"]).parent.name,
        "--output-dir", output_dir,
    ]
    if profile is not None:
        command.extend(["--matching-profile", profile])
    return subprocess.run(
        command, cwd=Path(__file__).resolve().parents[3],
        capture_output=True, text=True, timeout=30, check=False,
    )


@pytest.mark.parametrize("profile", [None, *PROFILES])
def test_cli_keeps_legacy_default_and_exposes_partial_metrics_only_for_v2(
    v2_arguments: Arguments, profile: str | None,
) -> None:
    output_path = _clear_fixture_output(v2_arguments)
    original_hashes = _input_hashes(v2_arguments)

    process = _run_cli(v2_arguments, profile)

    assert process.returncode == 0, process.stderr
    response = json.loads(process.stdout)
    assert response["status"] == "completed"
    artifact = load_json(output_path)
    validate_schema(artifact, EVALUATION_RESULTS_SCHEMA)
    assert artifact["schema_version"] == "0.3.0"
    assert artifact["data"]["matching_profile"] == (profile or DEFAULT_MATCHING_PROFILE)
    assert response["sha256"] == calculate_sha256(output_path)
    for metric_id in PARTIAL_METRIC_IDS:
        assert (metric_id in _metrics(artifact)) is (profile == WORKFLOW_SUBSEQUENCE_PROFILE)
    assert _input_hashes(v2_arguments) == original_hashes


def test_cli_rejects_unknown_profile_without_publishing(v2_arguments: Arguments) -> None:
    output_path = _clear_fixture_output(v2_arguments)

    process = _run_cli(v2_arguments, "unsupported-profile")

    assert process.returncode == 1
    response = json.loads(process.stdout)
    assert response["status"] == "failed"
    # Profile support is checked by the service, after request preparation.
    assert response["errors"][0]["code"] == "EVALUATION_FAILED"
    assert not output_path.exists()


def test_v2_evaluation_is_not_enabled_in_diagnosis_mode(v2_arguments: Arguments) -> None:
    output_path = _clear_fixture_output(v2_arguments)
    input_paths, output_dir, context = v2_arguments

    response = entrypoint.run(
        "evaluate", input_paths, output_dir, {**context, "mode": "diagnosis"},
    )

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "CONTRACT_INVALID"
    assert not output_path.exists()


def _diagnosis_arguments(arguments: Arguments) -> Arguments:
    input_paths, output_dir, context = arguments
    updated_hashes: dict[str, str] = {}
    for artifact_type in REPORT_ARTIFACT_TYPES:
        descriptor = input_paths[artifact_type]
        path = Path(context["run_root"]) / descriptor["path"]
        value = load_json(path)
        value["mode"] = "diagnosis"
        for reference in value["input_refs"]:
            if reference["artifact_type"] in updated_hashes:
                reference["sha256"] = updated_hashes[reference["artifact_type"]]
        if artifact_type in {"safety_decisions", "verification_results"}:
            value["data"]["scenarios_sha256"] = updated_hashes["test_scenarios"]
        validate_schema(value, INPUT_SCHEMA_BY_NAME[artifact_type])
        path.write_text(json.dumps(value), encoding="utf-8")
        descriptor["sha256"] = updated_hashes[artifact_type] = calculate_sha256(path)
    return input_paths, output_dir, {**context, "mode": "diagnosis"}


def test_diagnosis_report_remains_independent_of_ground_truth_and_workflow_scoring(
    report_arguments: Arguments, monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _diagnosis_arguments(report_arguments)
    output_path = _clear_fixture_output(arguments, "diagnosis_report.json")
    evaluation_path = _clear_fixture_output(arguments)
    assert not evaluation_path.exists()
    expected = build_diagnosis_report(prepare_report_inputs(parse_report_request(*arguments)))
    assert expected.data is not None
    original_hashes = _input_hashes(arguments)
    monkeypatch.setenv(EVALUATION_CONFIG_PATH_ENV, "missing-evaluation-config.json")

    def fail_evaluation(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("diagnosis report must not evaluate workflows or Ground Truth")

    monkeypatch.setattr(entrypoint, "prepare_evaluation_inputs", fail_evaluation)
    monkeypatch.setattr("modules.reporter.evaluation_service.select_workflow_match", fail_evaluation)

    response = entrypoint.run("report", *arguments)

    assert response["status"] == "completed"
    assert response["sha256"] == calculate_sha256(output_path)
    artifact = load_json(output_path)
    assert artifact["mode"] == "diagnosis"
    assert artifact["data"] == expected.data.to_mapping()
    assert Path(arguments[2]["run_root"], response["report_path"]).is_file()
    assert not evaluation_path.exists()
    assert _input_hashes(arguments) == original_hashes
