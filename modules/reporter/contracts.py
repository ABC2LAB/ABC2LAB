"""Reporter input contract loading and cross-artifact preparation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from modules.reporter.exceptions import ContractValidationError
from modules.reporter.input_validation import (
    build_crawl_index,
    build_report_models,
    extract_graph_snapshot,
    validate_evaluation_revisions,
    validate_evaluation_links,
    validate_ground_truth,
    validate_semantic_analysis,
)
from modules.reporter.models import (
    ArtifactSource,
    ErrorItem,
    EvaluationInputs,
    GroundTruth,
    InputArtifact,
    ReportInputs,
    ReporterRequest,
    ResolvedInput,
)
from modules.reporter.utils.paths import resolve_trusted_relative_path
from modules.reporter.utils.validation import (
    load_json,
    validate_schema,
    verify_file_sha256,
)

MODULE_DIRECTORY = Path(__file__).parent
SCHEMA_DIRECTORY = MODULE_DIRECTORY / "schemas"
INPUT_SCHEMA_BY_NAME = {
    "crawl_result": SCHEMA_DIRECTORY / "input/crawl_result.schema.json",
    "semantic_analysis": (
        SCHEMA_DIRECTORY / "input/semantic_analysis.schema.json"
    ),
    "graph_query_result": (
        SCHEMA_DIRECTORY / "input/graph_query_result.schema.json"
    ),
    "vulnerability_candidates": (
        SCHEMA_DIRECTORY / "input/vulnerability_candidates.schema.json"
    ),
    "test_scenarios": SCHEMA_DIRECTORY / "input/test_scenarios.schema.json",
    "safety_decisions": SCHEMA_DIRECTORY / "input/safety_decisions.schema.json",
    "verification_results": (
        SCHEMA_DIRECTORY / "input/verification_results.schema.json"
    ),
    "ground_truth": SCHEMA_DIRECTORY / "input/ground_truth.schema.json",
}
REPORT_ARTIFACT_TYPES = (
    "vulnerability_candidates",
    "test_scenarios",
    "safety_decisions",
    "verification_results",
)
EVALUATION_ARTIFACT_TYPES = (
    "crawl_result",
    "semantic_analysis",
    "graph_query_result",
    *REPORT_ARTIFACT_TYPES,
)


def prepare_report_inputs(request: ReporterRequest) -> ReportInputs:
    if request.operation not in {"report", "evaluate"}:
        raise ContractValidationError("report 입력에 지원하지 않는 operation")
    artifacts = tuple(
        _load_runtime_artifact(request, artifact_type)
        for artifact_type in REPORT_ARTIFACT_TYPES
    )
    artifact_by_type = {
        artifact.source.artifact_type: artifact for artifact in artifacts
    }
    scenarios_path = request.input_by_name("test_scenarios").path
    candidates, scenarios, decisions, results = build_report_models(
        artifact_by_type,
        scenarios_path,
    )
    return ReportInputs(
        request=request,
        artifacts=artifacts,
        candidates=candidates,
        scenarios=scenarios,
        decisions=decisions,
        verification_results=results,
    )


def prepare_evaluation_inputs(request: ReporterRequest) -> EvaluationInputs:
    if request.operation != "evaluate" or request.mode != "development":
        raise ContractValidationError("evaluate 입력 요청이 올바르지 않음")
    report_inputs = prepare_report_inputs(request)
    additional_types = EVALUATION_ARTIFACT_TYPES[:3]
    additional_artifacts = tuple(
        _load_runtime_artifact(request, artifact_type)
        for artifact_type in additional_types
    )
    artifact_by_type = {
        artifact.source.artifact_type: artifact
        for artifact in (*additional_artifacts, *report_inputs.artifacts)
    }
    crawl_result, semantic_analysis, graph_query_result = additional_artifacts
    _validate_target_url(crawl_result, request.target_url)
    validate_semantic_analysis(semantic_analysis)
    crawl_index = build_crawl_index(crawl_result)
    validate_evaluation_links(
        crawl_index,
        semantic_analysis,
        report_inputs,
    )
    graph_snapshot = extract_graph_snapshot(graph_query_result)
    validate_evaluation_revisions(artifact_by_type, graph_snapshot)
    ground_truth = _load_ground_truth(request)
    return EvaluationInputs(
        request=request,
        report_inputs=report_inputs,
        crawl_result=crawl_result,
        semantic_analysis=semantic_analysis,
        graph_query_result=graph_query_result,
        graph_snapshot=graph_snapshot,
        ground_truth=ground_truth,
    )


def _load_runtime_artifact(
    request: ReporterRequest,
    artifact_type: str,
) -> InputArtifact:
    location = request.input_by_name(artifact_type)
    verify_file_sha256(location.path, location.expected_sha256)
    value = load_json(location.path)
    validate_schema(value, INPUT_SCHEMA_BY_NAME[artifact_type])
    actual_sha256 = verify_file_sha256(location.path, location.expected_sha256)
    _validate_artifact_context(value, request)
    _validate_reference_paths(value, request.run_root)
    source = ArtifactSource(
        artifact_id=value["artifact_id"],
        artifact_type=value["artifact_type"],
        producer=value["producer"],
        relative_path=location.relative_path,
        sha256=actual_sha256,
        iteration=value["iteration"],
        status=value["status"],
    )
    return InputArtifact(
        source=source,
        value=value,
        errors=tuple(ErrorItem.from_mapping(item) for item in value["errors"]),
    )


def _load_ground_truth(request: ReporterRequest) -> GroundTruth:
    location = request.input_by_name("ground_truth")
    verify_file_sha256(location.path, location.expected_sha256)
    value = load_json(location.path)
    validate_schema(value, INPUT_SCHEMA_BY_NAME["ground_truth"])
    actual_sha256 = verify_file_sha256(location.path, location.expected_sha256)
    dataset_name = PurePosixPath(location.relative_path).parts[1]
    validate_ground_truth(value, dataset_name)
    data = value["data"]
    return GroundTruth(
        dataset_id=value["dataset_id"],
        dataset_version=value["dataset_version"],
        relative_path=location.relative_path,
        sha256=actual_sha256,
        entities=tuple(data["entities"]),
        relationships=tuple(data["relationships"]),
        workflows=tuple(data["workflows"]),
        cases=tuple(data["cases"]),
    )


def _validate_artifact_context(
    value: Mapping[str, Any],
    request: ReporterRequest,
) -> None:
    if value["run_id"] != request.run_id:
        raise ContractValidationError("입력 artifact run_id가 실행과 다름")
    if value["iteration"] != request.iteration:
        raise ContractValidationError("입력 artifact iteration이 실행과 다름")
    if value["mode"] != request.mode:
        raise ContractValidationError("입력 artifact mode가 실행과 다름")


def _validate_target_url(
    crawl_result: InputArtifact,
    expected_target_url: str | None,
) -> None:
    data = crawl_result.data
    if data is None or expected_target_url is None:
        return
    if data["target_url"] != expected_target_url:
        raise ContractValidationError("crawl_result target_url이 실행 인자와 다름")


def _validate_reference_paths(value: Any, run_root: Path) -> None:
    if isinstance(value, list):
        for item in value:
            _validate_reference_paths(item, run_root)
        return
    if not isinstance(value, Mapping):
        return
    if _is_evidence_reference(value):
        _validate_evidence_path(value["path"], run_root)
    elif _is_artifact_reference(value):
        _validate_artifact_reference_path(value, run_root)
    for item in value.values():
        _validate_reference_paths(item, run_root)


def _is_evidence_reference(value: Mapping[str, Any]) -> bool:
    return {"evidence_id", "kind", "path", "sha256", "redacted"}.issubset(value)


def _is_artifact_reference(value: Mapping[str, Any]) -> bool:
    return {"artifact_id", "artifact_type", "iteration", "path", "sha256"}.issubset(value)


def _validate_evidence_path(relative_path: str, run_root: Path) -> None:
    path = PurePosixPath(relative_path)
    if not path.parts or path.parts[0] != "evidence":
        raise ContractValidationError("EvidenceRef 경로가 evidence 아래가 아님")
    resolve_trusted_relative_path(run_root, relative_path)


def _validate_artifact_reference_path(
    value: Mapping[str, Any],
    run_root: Path,
) -> None:
    expected_prefix = PurePosixPath(
        f"artifacts/iteration-{value['iteration']:03d}"
    )
    path = PurePosixPath(value["path"])
    if path.parts[:2] != expected_prefix.parts:
        raise ContractValidationError("ArtifactRef iteration 경로가 일치하지 않음")
    resolve_trusted_relative_path(run_root, value["path"])
