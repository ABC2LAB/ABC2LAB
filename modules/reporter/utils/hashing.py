"""SHA-256 helpers for immutable inputs and outputs."""

from hashlib import sha256
from pathlib import Path


READ_CHUNK_SIZE = 64 * 1024


def calculate_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(READ_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()
