"""입력 adapter: 받은 JSON을 경로·해시·JSON·버전·Schema·run_id·상태 순으로 검증한다.

검증을 통과한 문서는 읽기 전용으로만 쓴다. 입력이 틀리면 고치지 않고 InputError로 거절해서
정상 결과처럼 보이지 않게 한다 (명세 02: 입력 오류를 성공·정상 빈 결과로 숨기지 않는다).
"""

import functools
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from modules.scenario_generator.utils.hashing import compute_sha256_of_bytes
from modules.scenario_generator.utils.json_io import InvalidJsonError, parse_json_strict
from modules.scenario_generator.utils.schema_errors import summarize_schema_errors

SUPPORTED_SCHEMA_VERSION = "0.1.0"
INPUT_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas" / "input"
SUPPORTED_INPUT_TYPES = ("crawl_result", "vulnerability_candidates")


class InputErrorCode(StrEnum):
    UNREADABLE = "INPUT_UNREADABLE"
    PATH_UNSAFE = "INPUT_PATH_UNSAFE"
    JSON_INVALID = "INPUT_JSON_INVALID"
    VERSION_UNSUPPORTED = "INPUT_VERSION_UNSUPPORTED"
    SCHEMA_INVALID = "INPUT_SCHEMA_INVALID"
    DUPLICATE_ID = "INPUT_DUPLICATE_ID"
    RUN_MISMATCH = "INPUT_RUN_MISMATCH"
    HASH_MISMATCH = "INPUT_HASH_MISMATCH"
    UPSTREAM_FAILED = "INPUT_UPSTREAM_FAILED"
    UPSTREAM_PARTIAL = "INPUT_UPSTREAM_PARTIAL"


class InputError(Exception):
    """입력 파일 하나를 쓸 수 없다. message에는 비밀값·입력 값을 넣지 않는다."""

    def __init__(self, code: InputErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_error_item(self) -> dict[str, Any]:
        # 파일 전체 오류이므로 item_ref는 null이다. 같은 입력으로 재시도해도 결과가 같아서 retryable=false.
        return {"code": self.code.value, "message": self.message, "item_ref": None, "retryable": False}


@dataclass(frozen=True)
class InputSource:
    artifact_type: str
    path: Path
    expected_sha256: str | None = None


@dataclass(frozen=True)
class LoadedArtifact:
    artifact_type: str
    status: str
    document: dict[str, Any]
    artifact_ref: dict[str, Any]


@functools.cache
def _get_validator(artifact_type: str) -> Draft202012Validator:
    schema_path = INPUT_SCHEMA_DIR / f"{artifact_type}.schema.json"
    return Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))


def _find_relative_path(path: Path, run_root: Path) -> tuple[Path, str]:
    resolved_root = run_root.resolve(strict=True)
    try:
        resolved_path = path.resolve(strict=True)
    except OSError as error:
        raise InputError(InputErrorCode.UNREADABLE, f"입력 파일을 찾을 수 없다: {path.name}") from error
    # symlink를 따라간 실제 위치로 검사해야 루트 밖을 가리키는 입력을 잡는다.
    if not resolved_path.is_relative_to(resolved_root):
        raise InputError(InputErrorCode.PATH_UNSAFE, f"입력 파일이 run 루트 밖에 있다: {path.name}")
    return resolved_path, resolved_path.relative_to(resolved_root).as_posix()


def load_input_artifact(source: InputSource, run_root: Path, run_id: str) -> LoadedArtifact:
    if source.artifact_type not in SUPPORTED_INPUT_TYPES:
        raise ValueError(f"지원하지 않는 입력 종류: {source.artifact_type}")
    label = source.artifact_type

    resolved_path, relative_path = _find_relative_path(source.path, run_root)
    try:
        raw = resolved_path.read_bytes()
    except OSError as error:
        raise InputError(InputErrorCode.UNREADABLE, f"{label}을(를) 읽지 못했다") from error

    # 읽은 바이트 하나로 해시와 파싱을 모두 처리해서, 그 사이 파일이 바뀌는 틈을 없앤다.
    actual_sha256 = compute_sha256_of_bytes(raw)
    if source.expected_sha256 is not None and actual_sha256 != source.expected_sha256:
        raise InputError(InputErrorCode.HASH_MISMATCH, f"{label}의 SHA-256이 기대값과 다르다")

    try:
        document = parse_json_strict(raw)
    except InvalidJsonError as error:
        raise InputError(InputErrorCode.JSON_INVALID, f"{label}: {error}") from error
    if not isinstance(document, dict):
        raise InputError(InputErrorCode.SCHEMA_INVALID, f"{label}: 최상위가 JSON 객체가 아니다")

    if document.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        raise InputError(
            InputErrorCode.VERSION_UNSUPPORTED,
            f"{label}: 지원하는 schema_version은 {SUPPORTED_SCHEMA_VERSION}뿐이다",
        )

    validator = _get_validator(source.artifact_type)
    if not validator.is_valid(document):
        raise InputError(
            InputErrorCode.SCHEMA_INVALID, f"{label}: {summarize_schema_errors(validator, document)}"
        )

    if document["run_id"] != run_id:
        raise InputError(
            InputErrorCode.RUN_MISMATCH,
            f"{label}의 run_id({document['run_id']})가 이번 실행({run_id})과 다르다",
        )
    if document["status"] == "failed":
        raise InputError(InputErrorCode.UPSTREAM_FAILED, f"{label}이(가) 실패한 산출물이다")

    artifact_ref = {
        "artifact_id": document["artifact_id"],
        "artifact_type": document["artifact_type"],
        "iteration": document["iteration"],
        "path": relative_path,
        "sha256": actual_sha256,
    }
    return LoadedArtifact(
        artifact_type=source.artifact_type,
        status=document["status"],
        document=document,
        artifact_ref=artifact_ref,
    )
