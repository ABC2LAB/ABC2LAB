"""신뢰된 루트 안에서만 경로를 resolve한다 (명세 02: `..`·절대경로·루트 밖 symlink 거절)."""

from pathlib import Path, PurePosixPath


class UnsafePathError(ValueError):
    """신뢰된 루트 밖을 가리키거나 허용되지 않는 형식의 경로."""


def resolve_inside_root(root: Path, relative_path: str) -> Path:
    """`root` 기준 상대 경로를 실제 경로로 바꾼다. 루트를 벗어나면 UnsafePathError.

    ArtifactRef·EvidenceRef의 path는 run_root 기준이므로 root에는 run_root를 넣는다.
    그러면 다른 run으로 가는 `../run_other/...`도 같은 규칙으로 막힌다.
    """
    if not relative_path or "\x00" in relative_path:
        raise UnsafePathError("경로가 비었거나 허용되지 않는 문자를 포함한다")

    candidate = PurePosixPath(relative_path)
    if candidate.is_absolute():
        raise UnsafePathError("절대 경로는 허용하지 않는다")
    if ".." in candidate.parts:
        raise UnsafePathError("상위 경로(..)는 허용하지 않는다")

    resolved_root = root.resolve(strict=True)
    # symlink를 따라간 최종 위치로 검사해야 루트 밖을 가리키는 symlink를 잡는다.
    resolved_path = (resolved_root / candidate).resolve(strict=False)
    if not resolved_path.is_relative_to(resolved_root):
        raise UnsafePathError("경로가 신뢰된 루트 밖을 가리킨다")
    return resolved_path
