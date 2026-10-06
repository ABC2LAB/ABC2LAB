"""Trusted-root path validation."""

from pathlib import Path, PurePosixPath

from modules.knowledge_graph.exceptions import PathValidationError


def resolve_trusted_relative_path(root: Path, relative_path: str) -> Path:
    candidate_path = PurePosixPath(relative_path)
    if candidate_path.is_absolute() or ".." in candidate_path.parts:
        raise PathValidationError(f"신뢰 경로를 벗어난 상대 경로: {relative_path}")
    if not candidate_path.parts:
        raise PathValidationError("빈 상대 경로는 허용되지 않음")

    resolved_root = root.resolve(strict=True)
    resolved_path = (resolved_root / Path(*candidate_path.parts)).resolve(strict=False)
    if not resolved_path.is_relative_to(resolved_root):
        raise PathValidationError(f"신뢰 경로를 벗어난 경로: {relative_path}")
    return resolved_path


def require_existing_file(root: Path, relative_path: str) -> Path:
    resolved_path = resolve_trusted_relative_path(root, relative_path)
    if not resolved_path.is_file():
        raise PathValidationError(f"입력 파일이 존재하지 않음: {relative_path}")
    return resolved_path
