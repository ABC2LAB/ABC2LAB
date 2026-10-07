"""Trusted-root path handling for reporter inputs and outputs."""

from pathlib import Path, PurePosixPath

from modules.reporter.exceptions import PathValidationError


def resolve_trusted_relative_path(root: Path, relative_path: str) -> Path:
    candidate_path = PurePosixPath(relative_path)
    if not relative_path or candidate_path.is_absolute():
        raise PathValidationError("절대경로나 빈 상대 경로는 허용되지 않음")
    if ".." in candidate_path.parts:
        raise PathValidationError(f"신뢰 경로를 벗어난 상대 경로: {relative_path}")

    resolved_root = root.resolve(strict=True)
    resolved_path = (resolved_root / Path(*candidate_path.parts)).resolve(
        strict=False
    )
    if not resolved_path.is_relative_to(resolved_root):
        raise PathValidationError(f"신뢰 경로를 벗어난 경로: {relative_path}")
    return resolved_path


def require_existing_file(root: Path, relative_path: str) -> Path:
    resolved_path = resolve_trusted_relative_path(root, relative_path)
    if not resolved_path.is_file():
        raise PathValidationError(f"입력 파일이 존재하지 않음: {relative_path}")
    return resolved_path


def require_existing_directory(value: str | Path, label: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value):
        raise PathValidationError(f"{label}는 비어 있지 않은 경로여야 함")
    try:
        resolved_path = Path(value).resolve(strict=True)
    except OSError as error:
        raise PathValidationError(f"{label}가 존재하지 않음") from error
    if not resolved_path.is_dir():
        raise PathValidationError(f"{label}는 디렉터리여야 함")
    return resolved_path


def resolve_expected_output_directory(
    run_root: Path,
    supplied_output_dir: str | Path,
    expected_relative_path: str,
) -> Path:
    expected_path = resolve_trusted_relative_path(
        run_root,
        expected_relative_path,
    )
    supplied_path = Path(supplied_output_dir)
    if supplied_path.is_absolute():
        resolved_path = supplied_path.resolve(strict=False)
        if not resolved_path.is_relative_to(run_root):
            raise PathValidationError("output_dir가 run_root를 벗어남")
    else:
        resolved_path = resolve_trusted_relative_path(
            run_root,
            supplied_path.as_posix(),
        )
    if resolved_path != expected_path:
        raise PathValidationError("output_dir가 reporter 실행 경로와 다름")
    return resolved_path
