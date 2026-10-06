"""Byte-level hashing helpers."""

from hashlib import sha256
from pathlib import Path


def calculate_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
