"""SHA-256 계산. 명세는 파일 내용이 아니라 실제 저장된 바이트의 해시를 요구한다."""

import hashlib
from pathlib import Path

# 큰 파일을 통째로 메모리에 올리지 않으려고 나눠 읽는다.
READ_CHUNK_SIZE_BYTES = 1024 * 1024


def compute_sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(READ_CHUNK_SIZE_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()
