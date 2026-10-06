"""Public argument validation for the safety policy evaluate operation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from modules.safety_policy.exceptions import ContractValidationError
from modules.safety_policy.models import EvaluationRequest
from modules.safety_policy.utils.paths import (
    require_existing_file,
    resolve_trusted_relative_path,
)

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CONTEXT_KEYS = frozenset(
    {"run_id", "iteration", "mode", "run_root", "policy_config"}
)
INPUT_DESCRIPTOR_KEYS = frozenset({"path", "sha256"})
POLICY_CONFIG_RELATIVE_PATH = "private/safety_policy/policy.json"


def parse_evaluate_request(
    input_paths: Mapping[str, Any],
    output_dir: str | Path,
    context: Mapping[str, Any],
) -> EvaluationRequest:
    input_values = _require_mapping(input_paths, "input_paths")
    context_values = _require_mapping(context, "context")
    _require_exact_keys(input_values, {"test_scenarios"}, "input_paths")
    _require_exact_keys(context_values, CONTEXT_KEYS, "context")

    run_id = _require_string(context_values["run_id"], "context.run_id")
    iteration = _require_iteration(context_values["iteration"])
    mode = context_values["mode"]
    if mode not in {"diagnosis", "development"}:
        raise ContractValidationError("context.mode가 허용값이 아님")
    run_root = _require_run_root(context_values["run_root"], run_id)

    descriptor = _require_mapping(
        input_values["test_scenarios"],
        "input_paths.test_scenarios",
    )
    _require_exact_keys(
        descriptor,
        INPUT_DESCRIPTOR_KEYS,
        "input_paths.test_scenarios",
    )
    relative_path = _require_string(
        descriptor["path"],
        "input_paths.test_scenarios.path",
    )
    expected_relative_path = (
        f"artifacts/iteration-{iteration:03d}/"
        "scenario_generator/test_scenarios.json"
    )
    if relative_path != expected_relative_path:
        raise ContractValidationError("test_scenarios 입력 경로가 실행 규약과 다름")
    expected_sha256 = _require_sha256(
        descriptor["sha256"], "input_paths.test_scenarios.sha256"
    )

    policy_descriptor = _require_mapping(
        context_values["policy_config"],
        "context.policy_config",
    )
    _require_exact_keys(
        policy_descriptor,
        INPUT_DESCRIPTOR_KEYS,
        "context.policy_config",
    )
    policy_relative_path = _require_string(
        policy_descriptor["path"],
        "context.policy_config.path",
    )
    if policy_relative_path != POLICY_CONFIG_RELATIVE_PATH:
        raise ContractValidationError("Policy 설정 경로가 실행 규약과 다름")
    policy_expected_sha256 = _require_sha256(
        policy_descriptor["sha256"],
        "context.policy_config.sha256",
    )

    output_relative_dir = f"artifacts/iteration-{iteration:03d}/safety_policy"
    resolved_output_dir = _resolve_output_directory(
        run_root,
        output_dir,
        output_relative_dir,
    )
    output_relative_path = f"{output_relative_dir}/safety_decisions.json"
    return EvaluationRequest(
        input_path=require_existing_file(run_root, relative_path),
        input_relative_path=relative_path,
        expected_sha256=expected_sha256,
        policy_config_path=require_existing_file(
            run_root,
            policy_relative_path,
        ),
        policy_config_expected_sha256=policy_expected_sha256,
        output_path=resolved_output_dir / "safety_decisions.json",
        output_relative_path=output_relative_path,
        run_id=run_id,
        iteration=iteration,
        mode=mode,
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


def _require_sha256(value: Any, label: str) -> str:
    digest = _require_string(value, label)
    if SHA256_PATTERN.fullmatch(digest) is None:
        raise ContractValidationError(f"{label} SHA-256 형식이 올바르지 않음")
    return digest


def _require_run_root(value: Any, run_id: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise ContractValidationError("context.run_root는 경로여야 함")
    try:
        run_root = Path(value).resolve(strict=True)
    except OSError as error:
        raise ContractValidationError("context.run_root가 존재하지 않음") from error
    if not run_root.is_dir() or run_root.name != run_id:
        raise ContractValidationError("context.run_root와 run_id가 일치하지 않음")
    return run_root


def _resolve_output_directory(
    run_root: Path,
    output_dir: str | Path,
    expected_relative: str,
) -> Path:
    if not isinstance(output_dir, (str, Path)) or not str(output_dir):
        raise ContractValidationError("output_dir는 비어 있지 않은 경로여야 함")
    expected_path = resolve_trusted_relative_path(run_root, expected_relative)
    supplied_path = Path(output_dir)
    if supplied_path.is_absolute():
        resolved_path = supplied_path.resolve(strict=False)
        if not resolved_path.is_relative_to(run_root):
            raise ContractValidationError("output_dir가 run_root를 벗어남")
    else:
        resolved_path = resolve_trusted_relative_path(
            run_root,
            supplied_path.as_posix(),
        )
    if resolved_path != expected_path:
        raise ContractValidationError("output_dir가 safety_policy 실행 경로와 다름")
    return resolved_path
