"""출력 경로 검증과 덮어쓰지 않는 원자적 공개.

완료 파일은 한 번만 공개한다(명세 02). 같은 경로에 이미 파일이 있으면 바꾸지 않고 거절한다.
공개 순서: 같은 폴더 임시 파일 → flush·fsync → os.link(이미 있으면 실패) → 폴더 fsync → 임시 파일 삭제.
os.replace는 기존 파일을 조용히 덮어쓰므로 쓰지 않는다.
"""

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# 경로 규칙은 검증기와 같은 상수를 쓴다(공개하는 쪽과 검사하는 쪽이 어긋나지 않게).
from modules.collector.utils.validation import ARTIFACTS_DIR_NAME, EVIDENCE_ROOT, ITERATION_DIR_FORMAT, PRODUCER

PARENT_PART = ".."
TEMP_SUFFIX = ".tmp"
JSON_INDENT = 2


class StorageError(RuntimeError):
    """출력 위치를 쓸 수 없다. 메시지에는 경로 규칙만 넣는다."""


class OutputPathError(StorageError):
    """output_dir가 run_root/artifacts/iteration-<NNN>/collector가 아니다."""


class ArtifactExistsError(StorageError):
    """같은 경로에 이미 완료 파일이 있다."""


def prepare_output_dir(run_root: Path, iteration: int, output_dir: Path) -> Path:
    """output_dir가 run_root 안의 자기 회차 폴더인지 확인하고 만든다. 확인한 실제 경로를 돌려준다."""
    if PARENT_PART in run_root.parts or PARENT_PART in output_dir.parts:
        raise OutputPathError("경로에 ..를 쓸 수 없음")
    expected = run_root.resolve() / ARTIFACTS_DIR_NAME / ITERATION_DIR_FORMAT.format(iteration) / PRODUCER
    # resolve는 중간의 symlink를 따라간다. 바깥을 가리키는 symlink가 끼어 있으면 기대 경로와 달라진다.
    if output_dir.resolve() != expected:
        raise OutputPathError(f"output_dir는 run_root/{expected.relative_to(run_root.resolve())}여야 함")
    expected.mkdir(parents=True, exist_ok=True)
    if expected.resolve() != expected:
        raise OutputPathError("출력 폴더가 symlink로 run_root 밖을 가리킴")
    return expected


def prepare_evidence_dir(run_root: Path, kind: str) -> Path:
    """run_root/evidence/collector/<kind>를 만든다. 중간에 바깥을 가리키는 symlink가 있으면 만들기 전에 거절한다."""
    if PARENT_PART in run_root.parts or PARENT_PART in Path(kind).parts:
        raise OutputPathError("경로에 ..를 쓸 수 없음")
    expected = run_root.resolve() / EVIDENCE_ROOT / kind
    if expected.resolve() != expected:
        raise OutputPathError("근거 폴더가 symlink로 run_root 밖을 가리킴")
    expected.mkdir(parents=True, exist_ok=True)
    if expected.resolve() != expected:
        raise OutputPathError("근거 폴더가 symlink로 run_root 밖을 가리킴")
    return expected


def serialize_json(document: Mapping[str, Any]) -> bytes:
    """공개 파일의 바이트. NaN·Infinity는 계약 위반이라 직렬화 단계에서 막는다."""
    return (json.dumps(document, ensure_ascii=False, indent=JSON_INDENT, allow_nan=False) + "\n").encode("utf-8")


def publish_file(directory: Path, file_name: str, raw: bytes) -> Path:
    """raw를 directory/file_name으로 원자적으로 공개한다. 이미 있으면 ArtifactExistsError."""
    target = directory / file_name
    descriptor, temp_name = tempfile.mkstemp(dir=directory, prefix=f".{file_name}.", suffix=TEMP_SUFFIX)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_path, target)
        except FileExistsError:
            raise ArtifactExistsError(f"이미 공개된 파일이 있음: {file_name}") from None
        _fsync_directory(directory)
    finally:
        temp_path.unlink(missing_ok=True)
    return target


def _fsync_directory(directory: Path) -> None:
    # 새 이름(link)이 디스크에 남도록 폴더도 동기화한다.
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
