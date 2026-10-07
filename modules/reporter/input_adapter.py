"""Public argument validation for reporter operations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from modules.reporter.exceptions import ContractValidationError
from modules.reporter.models import ReporterRequest, ResolvedInput
from modules.reporter.utils.paths import (
    require_existing_directory,
    require_existing_file,
    resolve_expected_output_directory,
)
from modules.reporter.utils.validation import require_sha256

INPUT_DESCRIPTOR_KEYS = frozenset({"path", "sha256"})
BASE_CONTEXT_KEYS = frozenset(
    {"run_id", "iteration", "mode", "run_root", "target_url"}
)
REPORT_INPUT_PATH_BY_NAME = {
    "vulnerability_candidates": (
        "access_analyzer/vulnerability_candidates.json"
    ),
    "test_scenarios": "scenario_generator/test_scenarios.json",
    "safety_decisions": "safety_policy/safety_decisions.json",
    "verification_results": "verifier/verification_results.json",
}
EVALUATION_INPUT_PATH_BY_NAME = {
    "crawl_result": "collector/crawl_result.json",
    "semantic_analysis": "semantic_analyzer/semantic_analysis.json",
    "graph_query_result": "knowledge_graph/graph_query_result.json",
    **REPORT_INPUT_PATH_BY_NAME,
}


@dataclass(frozen=True)
class RequestSpecification:
    operation: str
    expected_inputs: Mapping[str, str]
    output_filename: str


@dataclass(frozen=True)
class RunInputSpecification:
    name: str
    expected_tail: str
    run_root: Path
    iteration: int


def parse_report_request(
    input_paths: Mapping[str, Any],
    output_dir: str | Path,
    context: Mapping[str, Any],
) -> ReporterRequest:
    context_values = _require_mapping(context, "context")
    _require_exact_keys(context_values, BASE_CONTEXT_KEYS, "context")
    return _parse_request(
        RequestSpecification(
            operation="report",
            expected_inputs=REPORT_INPUT_PATH_BY_NAME,
            output_filename="diagnosis_report.json",
        ),
        input_paths,
        output_dir,
        context_values,
    )


def parse_evaluate_request(
    input_paths: Mapping[str, Any],
    output_dir: str | Path,
    context: Mapping[str, Any],
) -> ReporterRequest:
    context_values = _require_mapping(context, "context")
    required_keys = BASE_CONTEXT_KEYS | {"project_root", "matching_profile"}
    _require_exact_keys(context_values, required_keys, "context")
    if context_values["mode"] != "development":
        raise ContractValidationError("evaluate는 development mode에서만 실행 가능")

    request = _parse_request(
        RequestSpecification(
            operation="evaluate",
            expected_inputs=EVALUATION_INPUT_PATH_BY_NAME,
            output_filename="evaluation_results.json",
        ),
        input_paths,
        output_dir,
        context_values,
    )
    project_root = require_existing_directory(
        context_values["project_root"],
        "context.project_root",
    )
    ground_truth = _parse_ground_truth_input(input_paths, project_root)
    matching_profile = _require_string(
        context_values["matching_profile"],
        "context.matching_profile",
    )
    return ReporterRequest(
        operation=request.operation,
        inputs=(*request.inputs, ground_truth),
        output_path=request.output_path,
        output_relative_path=request.output_relative_path,
        run_root=request.run_root,
        project_root=project_root,
        run_id=request.run_id,
        iteration=request.iteration,
        mode=request.mode,
        target_url=request.target_url,
        matching_profile=matching_profile,
    )


def _parse_request(
    specification: RequestSpecification,
    input_paths: Mapping[str, Any],
    output_dir: str | Path,
    context: Mapping[str, Any],
) -> ReporterRequest:
    input_values = _require_mapping(input_paths, "input_paths")
    expected_names = set(specification.expected_inputs)
    if specification.operation == "evaluate":
        expected_names.add("ground_truth")
    _require_exact_keys(input_values, expected_names, "input_paths")

    run_id = _require_string(context["run_id"], "context.run_id")
    iteration = _require_iteration(context["iteration"])
    mode = _require_mode(context["mode"])
    run_root = require_existing_directory(context["run_root"], "context.run_root")
    if run_root.name != run_id:
        raise ContractValidationError("context.run_root와 run_id가 일치하지 않음")
    target_url = _require_target_url(context["target_url"])

    inputs = tuple(
        _parse_run_input(
            input_values[name],
            RunInputSpecification(
                name=name,
                expected_tail=expected_tail,
                run_root=run_root,
                iteration=iteration,
            ),
        )
        for name, expected_tail in specification.expected_inputs.items()
    )
    output_relative_dir = f"artifacts/iteration-{iteration:03d}/reporter"
    resolved_output_dir = resolve_expected_output_directory(
        run_root,
        output_dir,
        output_relative_dir,
    )
    return ReporterRequest(
        operation=specification.operation,
        inputs=inputs,
        output_path=resolved_output_dir / specification.output_filename,
        output_relative_path=(
            f"{output_relative_dir}/{specification.output_filename}"
        ),
        run_root=run_root,
        project_root=None,
        run_id=run_id,
        iteration=iteration,
        mode=mode,
        target_url=target_url,
        matching_profile=None,
    )


def _parse_run_input(
    value: Any,
    specification: RunInputSpecification,
) -> ResolvedInput:
    name = specification.name
    descriptor = _require_mapping(value, f"input_paths.{name}")
    _require_exact_keys(descriptor, INPUT_DESCRIPTOR_KEYS, f"input_paths.{name}")
    relative_path = _require_string(descriptor["path"], f"{name}.path")
    expected_path = (
        f"artifacts/iteration-{specification.iteration:03d}/"
        f"{specification.expected_tail}"
    )
    if relative_path != expected_path:
        raise ContractValidationError(f"{name} 입력 경로가 실행 규약과 다름")
    expected_sha256 = require_sha256(descriptor["sha256"], f"{name}.sha256")
    return ResolvedInput(
        name=name,
        path=require_existing_file(specification.run_root, relative_path),
        relative_path=relative_path,
        expected_sha256=expected_sha256,
    )


def _parse_ground_truth_input(
    input_paths: Mapping[str, Any],
    project_root: Path,
) -> ResolvedInput:
    descriptor = _require_mapping(
        input_paths["ground_truth"],
        "input_paths.ground_truth",
    )
    _require_exact_keys(
        descriptor,
        INPUT_DESCRIPTOR_KEYS,
        "input_paths.ground_truth",
    )
    relative_path = _require_string(descriptor["path"], "ground_truth.path")
    path_parts = PurePosixPath(relative_path).parts
    if (
        len(path_parts) != 3
        or path_parts[0] != "datasets"
        or not path_parts[1]
        or path_parts[2] != "ground_truth.json"
    ):
        raise ContractValidationError("ground_truth 입력 경로가 실행 규약과 다름")
    return ResolvedInput(
        name="ground_truth",
        path=require_existing_file(project_root, relative_path),
        relative_path=relative_path,
        expected_sha256=require_sha256(
            descriptor["sha256"],
            "ground_truth.sha256",
        ),
    )


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError(f"{label}는 object여야 함")
    return value


def _require_exact_keys(
    value: Mapping[str, Any],
    expected_keys: set[str] | frozenset[str],
    label: str,
) -> None:
    if set(value) != set(expected_keys):
        raise ContractValidationError(f"{label} 필드 구성이 올바르지 않음")


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractValidationError(f"{label}는 비어 있지 않은 문자열이어야 함")
    return value


def _require_iteration(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractValidationError("context.iteration은 0 이상의 정수여야 함")
    return value


def _require_mode(value: Any) -> str:
    if value not in {"diagnosis", "development"}:
        raise ContractValidationError("context.mode가 허용값이 아님")
    return value


def _require_target_url(value: Any) -> str:
    target_url = _require_string(value, "context.target_url")
    parsed_url = urlsplit(target_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ContractValidationError("context.target_url이 유효한 HTTP URL이 아님")
    return target_url
