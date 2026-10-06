"""Public argument validation for apply_verification."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from modules.knowledge_graph.exceptions import ContractValidationError
from modules.knowledge_graph.models import VerificationRequest
from modules.knowledge_graph.utils.paths import (
    require_existing_file,
    resolve_trusted_relative_path,
)


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CONTEXT_KEYS = frozenset({"run_id", "iteration", "mode", "run_root", "graph_id"})


def parse_verification_request(
    input_paths: Mapping[str, Any],
    output_dir: str | Path,
    context: Mapping[str, Any],
) -> VerificationRequest:
    input_values = _require_mapping(input_paths, "input_paths")
    context_values = _require_mapping(context, "context")
    _require_exact_keys(input_values, {"verification_results"}, "input_paths")
    _require_exact_keys(context_values, CONTEXT_KEYS, "context")

    run_id = _require_string(context_values["run_id"], "context.run_id")
    graph_id = _require_string(context_values["graph_id"], "context.graph_id")
    iteration = _require_iteration(context_values["iteration"])
    mode = context_values["mode"]
    if mode not in {"diagnosis", "development"}:
        raise ContractValidationError("context.mode가 허용값이 아님")
    run_root = _require_run_root(context_values["run_root"], run_id)
    _validate_output_directory(run_root, output_dir, iteration)

    descriptor = _require_mapping(
        input_values["verification_results"],
        "verification_results",
    )
    _require_exact_keys(descriptor, {"path", "sha256"}, "verification_results")
    relative_path = _require_string(descriptor["path"], "verification_results.path")
    expected_path = (
        f"artifacts/iteration-{iteration:03d}/verifier/verification_results.json"
    )
    if relative_path != expected_path:
        raise ContractValidationError("verification_results 입력 경로가 실행 규약과 다름")
    expected_sha256 = _require_string(
        descriptor["sha256"],
        "verification_results.sha256",
    )
    if SHA256_PATTERN.fullmatch(expected_sha256) is None:
        raise ContractValidationError("verification_results SHA-256 형식이 올바르지 않음")

    return VerificationRequest(
        input_path=require_existing_file(run_root, relative_path),
        expected_sha256=expected_sha256,
        graph_id=graph_id,
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


def _require_run_root(value: Any, run_id: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise ContractValidationError("context.run_root는 경로여야 함")
    run_root = Path(value).resolve(strict=True)
    if not run_root.is_dir() or run_root.name != run_id:
        raise ContractValidationError("context.run_root와 run_id가 일치하지 않음")
    return run_root


def _validate_output_directory(
    run_root: Path,
    output_dir: str | Path,
    iteration: int,
) -> None:
    if not isinstance(output_dir, (str, Path)) or not str(output_dir):
        raise ContractValidationError("output_dir는 비어 있지 않은 경로여야 함")
    expected_relative = f"artifacts/iteration-{iteration:03d}/knowledge_graph"
    expected_path = resolve_trusted_relative_path(run_root, expected_relative)
    supplied_path = Path(output_dir)
    if supplied_path.is_absolute():
        resolved_path = supplied_path.resolve(strict=False)
        if not resolved_path.is_relative_to(run_root):
            raise ContractValidationError("output_dir가 run_root를 벗어남")
    else:
        resolved_path = resolve_trusted_relative_path(run_root, supplied_path.as_posix())
    if resolved_path != expected_path:
        raise ContractValidationError("output_dir가 knowledge_graph 실행 경로와 다름")
