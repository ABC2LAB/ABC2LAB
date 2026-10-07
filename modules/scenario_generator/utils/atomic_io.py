"""원자적 저장: 임시 파일에 완성 → flush·fsync·닫기 → 같은 폴더에서 rename → 공개 (명세 02)."""

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from modules.scenario_generator.utils.hashing import compute_sha256_of_file
from modules.scenario_generator.utils.json_io import dump_json_bytes

logger = logging.getLogger(__name__)

TEMP_FILE_SUFFIX = ".tmp"


def _remove_temp_file(temp_path: Path) -> None:
    try:
        temp_path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        # 정리 실패가 원래 오류를 가리면 안 되므로 로그만 남긴다.
        logger.warning("임시 파일을 지우지 못했다: %s", temp_path.name, exc_info=True)


def write_bytes_atomically(target_path: Path, data: bytes) -> str:
    """바이트를 저장하고, 실제 저장된 파일의 SHA-256을 돌려준다.

    완료 파일은 불변이므로 이미 있는 파일은 덮어쓰지 않는다 (재실행은 새 경로·새 artifact_id).
    """
    if target_path.exists():
        raise FileExistsError(f"완료된 파일은 덮어쓰지 않는다: {target_path.name}")
    target_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    is_published = False
    try:
        # rename이 원자적이려면 임시 파일이 목적지와 같은 파일시스템(같은 폴더)에 있어야 한다.
        with tempfile.NamedTemporaryFile(
            dir=target_path.parent,
            prefix=f".{target_path.name}.",
            suffix=TEMP_FILE_SUFFIX,
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(data)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, target_path)
        is_published = True
    finally:
        if temp_path is not None and not is_published:
            _remove_temp_file(temp_path)

    return compute_sha256_of_file(target_path)


def write_json_atomically(target_path: Path, document: Any) -> str:
    return write_bytes_atomically(target_path, dump_json_bytes(document))
