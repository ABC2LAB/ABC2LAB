"""Atomic JSON artifact writer."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from modules.safety_policy.exceptions import OutputArtifactExistsError


def write_json_atomically(output_path: Path, value: dict[str, Any]) -> None:
    if output_path.exists():
        raise OutputArtifactExistsError(
            f"완료 파일은 덮어쓸 수 없음: {output_path}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output_file:
            json.dump(
                value,
                output_file,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            output_file.flush()
            os.fsync(output_file.fileno())
        if output_path.exists():
            raise OutputArtifactExistsError(
                f"완료 파일은 덮어쓸 수 없음: {output_path}"
            )
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def write_bytes_atomically(output_path: Path, content: bytes) -> None:
    """Publish unchanged configuration bytes without replacing an existing file."""
    if output_path.exists() or output_path.is_symlink():
        raise OutputArtifactExistsError("기존 파일은 덮어쓸 수 없음")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output_file:
            output_file.write(content)
            output_file.flush()
            os.fsync(output_file.fileno())
        # A second writer must not replace a policy published after our check.
        os.link(temporary_path, output_path)
    except FileExistsError as error:
        raise OutputArtifactExistsError("기존 파일은 덮어쓸 수 없음") from error
    finally:
        temporary_path.unlink(missing_ok=True)
