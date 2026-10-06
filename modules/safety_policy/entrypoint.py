"""Public evaluate operation and CLI for safety policy."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import perf_counter
from typing import Any

from modules.safety_policy.approval_adapter import load_approval_record
from modules.safety_policy.config_adapter import load_policy_configuration
from modules.safety_policy.evaluate_adapter import (
    APPROVAL_RECORD_DIRECTORY,
    POLICY_CONFIG_RELATIVE_PATH,
    parse_evaluate_request,
)
from modules.safety_policy.exceptions import (
    ApprovalRecordError,
    ApprovalRecordHashMismatchError,
    ContractValidationError,
    InputHashMismatchError,
    OutputArtifactExistsError,
    PathValidationError,
    PolicyConfigHashMismatchError,
    PolicyConfigurationError,
    SourceArtifactFailedError,
    StorageError,
)
from modules.safety_policy.models import (
    ErrorItem,
    EvaluationControlResponse,
    EvaluationInput,
    EvaluationRequest,
)
from modules.safety_policy.output_adapter import (
    build_evaluation_artifact,
    build_failed_evaluation_artifact,
    publish_evaluation_artifact,
)
from modules.safety_policy.policy import evaluate_policy
from modules.safety_policy.service import prepare_evaluation
from modules.safety_policy.utils.hashing import calculate_sha256
from modules.safety_policy.utils.paths import require_existing_file

LOGGER = logging.getLogger(__name__)


def run(
    operation: str,
    input_paths: Mapping[str, Any],
    output_dir: str | Path,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    if operation != "evaluate":
        return _control_failure(
            "OPERATION_UNSUPPORTED",
            "지원하지 않는 safety_policy operation",
            retryable=False,
        ).to_mapping()
    try:
        request = parse_evaluate_request(input_paths, output_dir, context)
    except Exception as error:
        return _input_failure_response(error).to_mapping()
    return _run_evaluate(request).to_mapping()


def _run_evaluate(request: EvaluationRequest) -> EvaluationControlResponse:
    started_at = perf_counter()
    try:
        prepared = prepare_evaluation(request)
    except Exception as error:
        return _input_failure_response(error)
    try:
        configuration = load_policy_configuration(request)
        approval_record = load_approval_record(
            request,
            prepared,
            configuration,
        )
        data = evaluate_policy(prepared, configuration, approval_record)
        artifact = build_evaluation_artifact(prepared, data, started_at)
    except Exception as error:
        return _handle_evaluation_failure(prepared, error, started_at)
    return _publish(prepared, artifact)


def _handle_evaluation_failure(
    prepared: EvaluationInput,
    error: Exception,
    started_at: float,
) -> EvaluationControlResponse:
    if isinstance(error, PolicyConfigHashMismatchError):
        code = "CONFIG_HASH_MISMATCH"
        message = "Policy 설정 파일 해시가 전달값과 다름"
        is_retryable = False
    elif isinstance(error, PolicyConfigurationError):
        code = "CONFIG_INVALID"
        message = "Policy 설정이 누락됐거나 올바르지 않음"
        is_retryable = False
    elif isinstance(error, ApprovalRecordHashMismatchError):
        code = "APPROVAL_HASH_MISMATCH"
        message = "승인 기록 파일 해시가 전달값과 다름"
        is_retryable = False
    elif isinstance(error, ApprovalRecordError):
        code = "APPROVAL_INVALID"
        message = "승인 기록이 현재 계획을 허용할 수 없음"
        is_retryable = False
    elif isinstance(error, OSError):
        code = "CONFIG_UNAVAILABLE"
        message = "Policy 설정 파일을 읽을 수 없음"
        is_retryable = True
    else:
        LOGGER.exception("예상하지 못한 safety_policy 평가 실패")
        code = "POLICY_EVALUATION_FAILED"
        message = "예상하지 못한 Policy 평가 오류가 발생함"
        is_retryable = True
    failure = ErrorItem(code, message, None, is_retryable)
    artifact = build_failed_evaluation_artifact(prepared, failure, started_at)
    return _publish(prepared, artifact)


def _publish(
    prepared: EvaluationInput,
    artifact: dict[str, Any],
) -> EvaluationControlResponse:
    try:
        return publish_evaluation_artifact(prepared, artifact)
    except OutputArtifactExistsError:
        return _control_failure(
            "OUTPUT_EXISTS",
            "safety_decisions 출력이 이미 존재함",
            retryable=False,
        )
    except (ContractValidationError, PathValidationError) as error:
        LOGGER.warning("safety_decisions 출력 검증 실패: %s", error)
        return _control_failure(
            "OUTPUT_INVALID",
            "safety_decisions 출력 검증에 실패함",
            retryable=False,
        )
    except (StorageError, OSError) as error:
        LOGGER.warning("safety_decisions 저장 실패: %s", error)
        return _control_failure(
            "STORAGE_FAILED",
            "safety_decisions 저장에 실패함",
            retryable=True,
        )


def _input_failure_response(error: Exception) -> EvaluationControlResponse:
    if isinstance(error, SourceArtifactFailedError):
        return _control_failure(
            "INPUT_STATUS_FAILED",
            "test_scenarios가 사용할 수 없는 failed 상태임",
            retryable=False,
        )
    if isinstance(error, InputHashMismatchError):
        return _control_failure(
            "INPUT_HASH_MISMATCH",
            "test_scenarios 파일 해시가 전달값과 다름",
            retryable=False,
        )
    if isinstance(error, (PathValidationError, OSError)):
        LOGGER.warning("safety_policy 입력 경로 검증 실패: %s", error)
        return _control_failure(
            "PATH_INVALID",
            "입력 또는 실행 경로에 접근할 수 없음",
            retryable=False,
        )
    if isinstance(error, ContractValidationError):
        LOGGER.warning("safety_policy 입력 계약 검증 실패: %s", error)
        return _control_failure(
            "CONTRACT_INVALID",
            "test_scenarios 또는 실행 인자가 계약을 위반함",
            retryable=False,
        )
    LOGGER.exception("예상하지 못한 safety_policy 입력 처리 실패")
    return _control_failure(
        "POLICY_EVALUATION_FAILED",
        "예상하지 못한 입력 처리 오류가 발생함",
        retryable=True,
    )


def _control_failure(
    code: str,
    message: str,
    retryable: bool,
) -> EvaluationControlResponse:
    return EvaluationControlResponse(
        status="failed",
        artifact_id=None,
        output_path=None,
        sha256=None,
        errors=(ErrorItem(code, message, None, retryable),),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_argument_parser()
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    response = _run_cli(arguments)
    json.dump(response, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0 if response["status"] in {"completed", "partial"} else 1


def _run_cli(arguments: argparse.Namespace) -> dict[str, Any]:
    run_root = Path(arguments.run_root)
    scenario_relative = (
        f"artifacts/iteration-{arguments.iteration:03d}/"
        "scenario_generator/test_scenarios.json"
    )
    output_dir = (
        arguments.output_dir
        or f"artifacts/iteration-{arguments.iteration:03d}/safety_policy"
    )
    try:
        scenario_path = require_existing_file(run_root, scenario_relative)
        policy_path = require_existing_file(run_root, POLICY_CONFIG_RELATIVE_PATH)
        scenario_sha256 = calculate_sha256(scenario_path)
        policy_sha256 = calculate_sha256(policy_path)
        approval_descriptor = _load_cli_approval_descriptor(
            run_root,
            arguments.approval_record,
        )
    except (PathValidationError, OSError) as error:
        return _input_failure_response(error).to_mapping()
    context: dict[str, Any] = {
        "run_id": arguments.run_id,
        "iteration": arguments.iteration,
        "mode": arguments.mode,
        "run_root": run_root,
        "policy_config": {
            "path": POLICY_CONFIG_RELATIVE_PATH,
            "sha256": policy_sha256,
        },
    }
    if approval_descriptor is not None:
        context["approval_record"] = approval_descriptor
    return run(
        operation=arguments.operation,
        input_paths={
            "test_scenarios": {
                "path": scenario_relative,
                "sha256": scenario_sha256,
            }
        },
        output_dir=output_dir,
        context=context,
    )


def _load_cli_approval_descriptor(
    run_root: Path,
    relative_path: str | None,
) -> dict[str, str] | None:
    if relative_path is None:
        return None
    approval_path = require_existing_file(run_root, relative_path)
    expected_parent = run_root / APPROVAL_RECORD_DIRECTORY.as_posix()
    if approval_path.parent != expected_parent.resolve(strict=True):
        raise PathValidationError("승인 기록이 승인 전용 경로에 있지 않음")
    return {
        "path": relative_path,
        "sha256": calculate_sha256(approval_path),
    }


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="safety_policy")
    parser.add_argument("operation", choices=("evaluate",))
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--iteration", type=int, required=True)
    parser.add_argument(
        "--mode",
        choices=("diagnosis", "development"),
        required=True,
    )
    parser.add_argument("--output-dir")
    parser.add_argument(
        "--approval-record",
        help=(
            "run_root 기준 private/safety_policy/approvals/ 아래 승인 기록 경로"
        ),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
