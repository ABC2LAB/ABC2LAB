"""Atomic JSON artifact writer."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomically(output_path: Path, value: dict[str, Any]) -> None:
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
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
