"""Public argument validation for the knowledge graph ingest operation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from modules.knowledge_graph.exceptions import ContractValidationError
from modules.knowledge_graph.models import IngestRequest
from modules.knowledge_graph.utils.paths import (
    require_existing_file,
    resolve_trusted_relative_path,
)


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CONTEXT_KEYS = frozenset({"run_id", "iteration", "mode", "run_root"})
INPUT_DESCRIPTOR_KEYS = frozenset({"path", "sha256"})


def parse_ingest_request(
    input_paths: Mapping[str, Any],
    output_dir: str | Path,
    context: Mapping[str, Any],
) -> IngestRequest:
    input_paths = _require_mapping(input_paths, "input_paths")
    context = _require_mapping(context, "context")
    _require_exact_keys(input_paths, {"semantic_analysis"}, "input_paths")
    _require_exact_keys(context, CONTEXT_KEYS, "context")

    run_id = _require_non_empty_string(context["run_id"], "context.run_id")
    iteration = _require_non_negative_integer(
        context["iteration"],
        "context.iteration",
    )
    mode = context["mode"]
    if mode not in {"diagnosis", "development"}:
        raise ContractValidationError("context.mode가 허용값이 아님")

    run_root = _require_run_root(context["run_root"], run_id)
    _validate_output_directory(run_root, output_dir, iteration)
    descriptor = _require_mapping(
        input_paths["semantic_analysis"],
        "input_paths.semantic_analysis",
    )
    _require_exact_keys(
        descriptor,
        INPUT_DESCRIPTOR_KEYS,
        "input_paths.semantic_analysis",
    )
    relative_path = _require_non_empty_string(
        descriptor["path"],
        "input_paths.semantic_analysis.path",
    )
    expected_path = (
        f"artifacts/iteration-{iteration:03d}/"
        "semantic_analyzer/semantic_analysis.json"
    )
    if relative_path != expected_path:
        raise ContractValidationError("semantic_analysis 입력 경로가 실행 규약과 다름")
    expected_sha256 = _require_non_empty_string(
        descriptor["sha256"],
        "input_paths.semantic_analysis.sha256",
    )
    if SHA256_PATTERN.fullmatch(expected_sha256) is None:
        raise ContractValidationError("semantic_analysis SHA-256 형식이 올바르지 않음")

    return IngestRequest(
        input_path=require_existing_file(run_root, relative_path),
        expected_sha256=expected_sha256,
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
    actual_keys = set(value)
    if actual_keys != set(expected_keys):
        raise ContractValidationError(f"{label} 필드 구성이 올바르지 않음")


def _require_non_empty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractValidationError(f"{label}는 비어 있지 않은 문자열이어야 함")
    return value


def _require_non_negative_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractValidationError(f"{label}는 0 이상의 정수여야 함")
    return value


def _require_run_root(value: Any, run_id: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise ContractValidationError("context.run_root는 경로여야 함")
    run_root = Path(value).resolve(strict=True)
    if not run_root.is_dir():
        raise ContractValidationError("context.run_root가 디렉터리가 아님")
    if run_root.name != run_id:
        raise ContractValidationError("context.run_root와 run_id가 일치하지 않음")
    return run_root


def _validate_output_directory(
    run_root: Path,
    output_dir: str | Path,
    iteration: int,
) -> None:
    if not isinstance(output_dir, (str, Path)) or not str(output_dir):
        raise ContractValidationError("output_dir는 비어 있지 않은 경로여야 함")
    expected_relative = (
        f"artifacts/iteration-{iteration:03d}/knowledge_graph"
    )
    expected_path = resolve_trusted_relative_path(run_root, expected_relative)
    supplied_path = Path(output_dir)
    if supplied_path.is_absolute():
        resolved_path = supplied_path.resolve(strict=False)
        if not resolved_path.is_relative_to(run_root):
            raise ContractValidationError("output_dir가 신뢰된 run_root를 벗어남")
    else:
        resolved_path = resolve_trusted_relative_path(run_root, supplied_path.as_posix())
    if resolved_path != expected_path:
        raise ContractValidationError("output_dir가 knowledge_graph 실행 경로와 다름")
