from pathlib import Path

import pytest

from modules.scenario_generator.utils import atomic_io
from modules.scenario_generator.utils.hashing import compute_sha256_of_bytes
from modules.scenario_generator.utils.json_io import InvalidJsonError, parse_json_strict


def test_write_creates_file_and_returns_hash_of_stored_bytes(tmp_path: Path) -> None:
    target = tmp_path / "out" / "nested" / "file.bin"
    digest = atomic_io.write_bytes_atomically(target, b"payload")
    assert target.read_bytes() == b"payload"
    assert digest == compute_sha256_of_bytes(b"payload")


def test_no_temp_file_is_left_after_success(tmp_path: Path) -> None:
    atomic_io.write_bytes_atomically(tmp_path / "file.bin", b"payload")
    assert [p.name for p in tmp_path.iterdir()] == ["file.bin"]


def test_existing_file_is_not_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "file.bin"
    atomic_io.write_bytes_atomically(target, b"first")
    with pytest.raises(FileExistsError):
        atomic_io.write_bytes_atomically(target, b"second")
    assert target.read_bytes() == b"first"


def test_failed_publish_leaves_no_target_and_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr(atomic_io.os, "replace", fail_replace)
    target = tmp_path / "file.bin"
    with pytest.raises(OSError, match="simulated"):
        atomic_io.write_bytes_atomically(target, b"payload")
    assert list(tmp_path.iterdir()) == []


def test_write_json_stores_parseable_json_and_matching_hash(tmp_path: Path) -> None:
    target = tmp_path / "doc.json"
    document = {"status": "completed", "items": [1, 2, 3]}
    digest = atomic_io.write_json_atomically(target, document)
    assert parse_json_strict(target.read_bytes()) == document
    assert digest == compute_sha256_of_bytes(target.read_bytes())


def test_write_json_rejects_nan_without_creating_file(tmp_path: Path) -> None:
    target = tmp_path / "doc.json"
    with pytest.raises(InvalidJsonError):
        atomic_io.write_json_atomically(target, {"value": float("nan")})
    assert list(tmp_path.iterdir()) == []
