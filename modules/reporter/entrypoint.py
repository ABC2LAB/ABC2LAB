"""Public report/evaluate operations and CLI for reporter."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import perf_counter
from typing import Any

from modules.reporter.contracts import (
    prepare_evaluation_inputs,
    prepare_report_inputs,
)
from modules.reporter.evaluation_output_adapter import (
    build_evaluation_artifact,
    publish_evaluation_artifact,
)
from modules.reporter.exceptions import (
    ContractValidationError,
    HashMismatchError,
    OutputArtifactExistsError,
    PathValidationError,
)
from modules.reporter.html_renderer import (
    publish_diagnosis_html,
    remove_diagnosis_html,
)
from modules.reporter.input_adapter import (
    EVALUATION_INPUT_PATH_BY_NAME,
    REPORT_INPUT_PATH_BY_NAME,
    parse_evaluate_request,
    parse_report_request,
)
from modules.reporter.models import ErrorItem, ReporterRequest
from modules.reporter.output_adapter import (
    build_diagnosis_artifact,
    publish_diagnosis_artifact,
)
from modules.reporter.utils.hashing import calculate_sha256
from modules.reporter.utils.paths import (
    require_existing_directory,
    require_existing_file,
)

LOGGER = logging.getLogger(__name__)


def run(
    operation: str,
    input_paths: Mapping[str, Any],
    output_dir: str | Path,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    if operation == "report":
        try:
            request = parse_report_request(input_paths, output_dir, context)
        except Exception as error:
            return _input_failure_response(error, operation)
        return _run_report(request)
    if operation == "evaluate":
        try:
            request = parse_evaluate_request(input_paths, output_dir, context)
        except Exception as error:
            return _input_failure_response(error, operation)
        return _run_evaluate(request)
    return _control_failure(
        "OPERATION_UNSUPPORTED",
        "지원하지 않는 reporter operation",
        retryable=False,
    )


def _run_report(request: ReporterRequest) -> dict[str, Any]:
    started_at = perf_counter()
    try:
        inputs = prepare_report_inputs(request)
    except Exception as error:
        return _input_failure_response(error, "report")
    try:
        artifact = build_diagnosis_artifact(inputs, started_at)
    except Exception:
        LOGGER.exception("예상하지 못한 reporter report 처리 실패")
        return _control_failure(
            "REPORT_FAILED",
            "예상하지 못한 진단 리포트 처리 오류가 발생함",
            retryable=True,
        )

    report_path: str | None = None
    try:
        report_path = publish_diagnosis_html(inputs, artifact)
        response = publish_diagnosis_artifact(inputs, artifact).to_mapping()
    except Exception as error:
        if report_path is not None:
            _rollback_html(request.run_root, report_path)
        return _publication_failure_response(error, "diagnosis_report")
    response["report_path"] = report_path
    return response


def _run_evaluate(request: ReporterRequest) -> dict[str, Any]:
    started_at = perf_counter()
    try:
        inputs = prepare_evaluation_inputs(request)
    except Exception as error:
        return _input_failure_response(error, "evaluate")
    try:
        artifact = build_evaluation_artifact(inputs, started_at)
    except Exception:
        LOGGER.exception("예상하지 못한 reporter evaluate 처리 실패")
        return _control_failure(
            "EVALUATION_FAILED",
            "예상하지 못한 개발 평가 처리 오류가 발생함",
            retryable=True,
        )
    try:
        return publish_evaluation_artifact(inputs, artifact).to_mapping()
    except Exception as error:
        return _publication_failure_response(error, "evaluation_results")


def _input_failure_response(error: Exception, operation: str) -> dict[str, Any]:
    if isinstance(error, HashMismatchError):
        return _control_failure(
            "INPUT_HASH_MISMATCH",
            "입력 파일 SHA-256이 전달값과 다름",
            retryable=False,
        )
    if isinstance(error, PathValidationError):
        LOGGER.warning("reporter %s 입력 경로 검증 실패: %s", operation, error)
        return _control_failure(
            "PATH_INVALID",
            "입력 또는 실행 경로에 접근할 수 없음",
            retryable=False,
        )
    if isinstance(error, ContractValidationError):
        LOGGER.warning("reporter %s 입력 계약 검증 실패: %s", operation, error)
        return _control_failure(
            "CONTRACT_INVALID",
            "입력 산출물 또는 실행 인자가 계약을 위반함",
            retryable=False,
        )
    if isinstance(error, OSError):
        LOGGER.warning("reporter %s 입력 파일 접근 실패: %s", operation, error)
        return _control_failure(
            "INPUT_UNAVAILABLE",
            "입력 파일을 읽을 수 없음",
            retryable=True,
        )
    LOGGER.exception("예상하지 못한 reporter %s 입력 처리 실패", operation)
    return _control_failure(
        "REPORTER_FAILED",
        "예상하지 못한 reporter 입력 처리 오류가 발생함",
        retryable=True,
    )


def _publication_failure_response(
    error: Exception,
    artifact_type: str,
) -> dict[str, Any]:
    if isinstance(error, OutputArtifactExistsError):
        return _control_failure(
            "OUTPUT_EXISTS",
            f"{artifact_type} 출력이 이미 존재함",
            retryable=False,
        )
    if isinstance(error, (ContractValidationError, PathValidationError)):
        LOGGER.warning("%s 출력 검증 실패: %s", artifact_type, error)
        return _control_failure(
            "OUTPUT_INVALID",
            f"{artifact_type} 출력 검증에 실패함",
            retryable=False,
        )
    if isinstance(error, OSError):
        LOGGER.warning("%s 저장 실패: %s", artifact_type, error)
        return _control_failure(
            "STORAGE_FAILED",
            f"{artifact_type} 저장에 실패함",
            retryable=True,
        )
    LOGGER.exception("예상하지 못한 %s 공개 실패", artifact_type)
    return _control_failure(
        "PUBLICATION_FAILED",
        f"예상하지 못한 {artifact_type} 공개 오류가 발생함",
        retryable=True,
    )


def _rollback_html(run_root: Path, report_path: str) -> None:
    try:
        remove_diagnosis_html(run_root, report_path)
    except OSError:
        LOGGER.exception("진단 HTML rollback 실패: %s", report_path)


def _control_failure(
    code: str,
    message: str,
    retryable: bool,
) -> dict[str, Any]:
    return {
        "status": "failed",
        "artifact_id": None,
        "output_path": None,
        "sha256": None,
        "errors": [ErrorItem(code, message, None, retryable).to_mapping()],
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_argument_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    response = _run_cli(arguments)
    json.dump(response, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0 if response["status"] in {"completed", "partial"} else 1


def _run_cli(arguments: argparse.Namespace) -> dict[str, Any]:
    run_root = Path(arguments.run_root)
    output_dir = arguments.output_dir or (
        f"artifacts/iteration-{arguments.iteration:03d}/reporter"
    )
    paths_by_name = (
        REPORT_INPUT_PATH_BY_NAME
        if arguments.operation == "report"
        else EVALUATION_INPUT_PATH_BY_NAME
    )
    try:
        input_paths = _runtime_input_descriptors(
            run_root,
            arguments.iteration,
            paths_by_name,
        )
        context: dict[str, Any] = {
            "run_id": arguments.run_id,
            "iteration": arguments.iteration,
            "mode": arguments.mode,
            "run_root": run_root,
            "target_url": arguments.target_url,
        }
        if arguments.operation == "evaluate":
            project_root = require_existing_directory(
                arguments.project_root,
                "--project-root",
            )
            ground_truth_relative = (
                f"datasets/{arguments.dataset_id}/ground_truth.json"
            )
            ground_truth_path = require_existing_file(
                project_root,
                ground_truth_relative,
            )
            input_paths["ground_truth"] = {
                "path": ground_truth_relative,
                "sha256": calculate_sha256(ground_truth_path),
            }
            context.update(
                {
                    "project_root": project_root,
                    "matching_profile": arguments.matching_profile,
                }
            )
    except Exception as error:
        return _input_failure_response(error, arguments.operation)
    return run(arguments.operation, input_paths, output_dir, context)


def _runtime_input_descriptors(
    run_root: Path,
    iteration: int,
    paths_by_name: Mapping[str, str],
) -> dict[str, dict[str, str]]:
    input_paths: dict[str, dict[str, str]] = {}
    prefix = f"artifacts/iteration-{iteration:03d}"
    for name, tail in paths_by_name.items():
        relative_path = f"{prefix}/{tail}"
        path = require_existing_file(run_root, relative_path)
        input_paths[name] = {
            "path": relative_path,
            "sha256": calculate_sha256(path),
        }
    return input_paths


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reporter")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    report_parser = subparsers.add_parser("report")
    _add_common_arguments(report_parser, ("diagnosis", "development"))

    evaluate_parser = subparsers.add_parser("evaluate")
    _add_common_arguments(evaluate_parser, ("development",))
    evaluate_parser.add_argument("--project-root", required=True)
    evaluate_parser.add_argument("--dataset-id", required=True)
    evaluate_parser.add_argument("--matching-profile", default="default-v1")
    return parser


def _add_common_arguments(
    parser: argparse.ArgumentParser,
    modes: tuple[str, ...],
) -> None:
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--iteration", type=int, required=True)
    parser.add_argument("--mode", choices=modes, required=True)
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--output-dir")


if __name__ == "__main__":
    raise SystemExit(main())
