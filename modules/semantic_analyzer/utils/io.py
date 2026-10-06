"""원자적 저장·해시·신뢰 경로 resolve. 모듈 자체 구현(공유 utils 금지)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

_UTF8 = "utf-8"


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_json(path: Path) -> tuple[dict, str]:
    """JSON을 읽어 (파싱 결과, 실제 바이트 SHA-256)을 돌려준다. 해시는 입력 참조 검증에 쓴다."""
    raw = path.read_bytes()
    return json.loads(raw.decode(_UTF8)), sha256_hex(raw)


def write_json_atomic(path: Path, payload: dict) -> str:
    """임시 파일에 쓰고 flush·close 뒤 같은 디렉토리로 rename한다. 완료 파일만 공개하기 위함.

    저장한 실제 바이트의 SHA-256을 돌려준다(완료 통지·ArtifactRef용).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(_UTF8)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)  # 같은 파일시스템 내 원자적 교체
    return sha256_hex(raw)


def resolve_within_root(root: Path, relative: str) -> Path:
    """신뢰된 루트 안에서만 상대 경로를 resolve한다. .. ·절대경로·루트 밖 symlink를 거절한다."""
    if os.path.isabs(relative):
        raise ValueError(f"절대경로 거절: {relative}")
    root_resolved = root.resolve()
    target = (root_resolved / relative).resolve()
    if root_resolved != target and root_resolved not in target.parents:
        raise ValueError(f"신뢰 루트 밖 경로 거절: {relative}")
    return target
