from pathlib import Path

from modules.scenario_generator.utils import hashing

# SHA-256("abc")의 공개된 표준 테스트 벡터
SHA256_OF_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_bytes_hash_matches_known_vector() -> None:
    assert hashing.compute_sha256_of_bytes(b"abc") == SHA256_OF_ABC


def test_file_hash_equals_bytes_hash(tmp_path: Path) -> None:
    path = tmp_path / "small.bin"
    path.write_bytes(b"abc")
    assert hashing.compute_sha256_of_file(path) == SHA256_OF_ABC


def test_file_hash_is_correct_across_chunk_boundaries(tmp_path: Path) -> None:
    data = b"x" * (hashing.READ_CHUNK_SIZE_BYTES * 2 + 1)
    path = tmp_path / "large.bin"
    path.write_bytes(data)
    assert hashing.compute_sha256_of_file(path) == hashing.compute_sha256_of_bytes(data)


def test_empty_file_has_hash_of_empty_bytes(tmp_path: Path) -> None:
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")
    assert hashing.compute_sha256_of_file(path) == hashing.compute_sha256_of_bytes(b"")
