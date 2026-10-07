import json
from pathlib import Path

import pytest

from modules.reporter.exceptions import (
    ContractValidationError,
    HashMismatchError,
    OutputArtifactExistsError,
    PathValidationError,
)
from modules.reporter.utils.atomic_writer import (
    write_json_atomically,
    write_text_atomically,
)
from modules.reporter.utils.hashing import calculate_sha256
from modules.reporter.utils.paths import (
    require_existing_file,
    resolve_trusted_relative_path,
)
from modules.reporter.utils.validation import (
    load_json,
    verify_file_sha256,
)


def test_trusted_path_accepts_child(tmp_path: Path) -> None:
    child = tmp_path / "artifacts/input.json"
    child.parent.mkdir()
    child.write_text("{}", encoding="utf-8")

    assert require_existing_file(tmp_path, "artifacts/input.json") == child.resolve()


@pytest.mark.parametrize("value", ["../outside.json", "/tmp/outside.json", ""])
def test_trusted_path_rejects_escape(tmp_path: Path, value: str) -> None:
    with pytest.raises(PathValidationError):
        resolve_trusted_relative_path(tmp_path, value)


def test_trusted_path_rejects_outside_symlink(tmp_path: Path) -> None:
    trusted_root = tmp_path / "trusted"
    trusted_root.mkdir()
    outside_path = tmp_path / "outside.json"
    outside_path.write_text("{}", encoding="utf-8")
    (trusted_root / "linked.json").symlink_to(outside_path)

    with pytest.raises(PathValidationError):
        resolve_trusted_relative_path(trusted_root, "linked.json")


def test_hash_verification_rejects_changed_file(tmp_path: Path) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text("{}", encoding="utf-8")

    with pytest.raises(HashMismatchError):
        verify_file_sha256(input_path, "0" * 64)


def test_atomic_json_writer_publishes_complete_file(tmp_path: Path) -> None:
    output_path = tmp_path / "nested/result.json"
    value = {"status": "completed", "items": [1, 2]}

    write_json_atomically(output_path, value)

    assert json.loads(output_path.read_text(encoding="utf-8")) == value
    assert len(calculate_sha256(output_path)) == 64
    assert not list(output_path.parent.glob("*.tmp"))


def test_atomic_text_writer_rejects_overwrite(tmp_path: Path) -> None:
    output_path = tmp_path / "report.html"
    write_text_atomically(output_path, "first")

    with pytest.raises(OutputArtifactExistsError):
        write_text_atomically(output_path, "second")

    assert output_path.read_text(encoding="utf-8") == "first"


def test_atomic_writer_cleans_temporary_file_on_invalid_json(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "result.json"

    with pytest.raises(ValueError):
        write_json_atomically(output_path, {"value": float("nan")})

    assert not output_path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_load_json_rejects_duplicate_keys(tmp_path: Path) -> None:
    input_path = tmp_path / "duplicate.json"
    input_path.write_text('{"key": 1, "key": 2}', encoding="utf-8")

    with pytest.raises(ContractValidationError, match="중복 JSON 키"):
        load_json(input_path)


def test_load_json_rejects_non_finite_number(tmp_path: Path) -> None:
    input_path = tmp_path / "nan.json"
    input_path.write_text('{"value": NaN}', encoding="utf-8")

    with pytest.raises(ContractValidationError, match="유한하지 않은"):
        load_json(input_path)
