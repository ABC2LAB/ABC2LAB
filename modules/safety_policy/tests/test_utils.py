import json
from pathlib import Path

import pytest

from modules.safety_policy.exceptions import (
    ContractValidationError,
    OutputArtifactExistsError,
    PathValidationError,
    StorageError,
)
from modules.safety_policy.utils import atomic_writer
from modules.safety_policy.utils.atomic_writer import (
    write_bytes_atomically,
    write_json_atomically,
)
from modules.safety_policy.utils.hashing import calculate_sha256
from modules.safety_policy.utils.paths import (
    require_existing_file,
    resolve_trusted_relative_path,
)
from modules.safety_policy.utils.validation import load_json


def test_resolve_trusted_relative_path_accepts_child(tmp_path: Path) -> None:
    child = tmp_path / "artifacts" / "input.json"
    child.parent.mkdir()
    child.write_text("{}", encoding="utf-8")

    assert require_existing_file(tmp_path, "artifacts/input.json") == child.resolve()


@pytest.mark.parametrize("value", ["../outside.json", "/tmp/outside.json"])
def test_resolve_trusted_relative_path_rejects_escape(
    tmp_path: Path,
    value: str,
) -> None:
    with pytest.raises(PathValidationError):
        resolve_trusted_relative_path(tmp_path, value)


def test_resolve_trusted_relative_path_rejects_outside_symlink(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_text("{}", encoding="utf-8")
    link = tmp_path / "linked.json"
    link.symlink_to(outside)

    with pytest.raises(PathValidationError):
        resolve_trusted_relative_path(tmp_path, "linked.json")


def test_atomic_writer_publishes_json_and_hash(tmp_path: Path) -> None:
    output_path = tmp_path / "result.json"
    value = {"status": "completed", "items": [1, 2]}

    write_json_atomically(output_path, value)

    assert json.loads(output_path.read_text(encoding="utf-8")) == value
    assert len(calculate_sha256(output_path)) == 64
    assert list(tmp_path.iterdir()) == [output_path]


def test_atomic_writer_rejects_completed_file_overwrite(tmp_path: Path) -> None:
    output_path = tmp_path / "result.json"
    write_json_atomically(output_path, {"value": 1})

    with pytest.raises(StorageError, match="덮어쓸 수 없음"):
        write_json_atomically(output_path, {"value": 2})

    assert load_json(output_path) == {"value": 1}


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


def test_atomic_bytes_writer_preserves_source_bytes(tmp_path: Path) -> None:
    output_path = tmp_path / "private" / "policy.json"
    content = b'{\n  "policy_id": "example"\n}\n'

    write_bytes_atomically(output_path, content)

    assert output_path.read_bytes() == content
    assert list(output_path.parent.iterdir()) == [output_path]


def test_atomic_bytes_writer_rejects_existing_file(tmp_path: Path) -> None:
    output_path = tmp_path / "policy.json"
    output_path.write_bytes(b"existing policy")

    with pytest.raises(OutputArtifactExistsError, match="덮어쓸 수 없음"):
        write_bytes_atomically(output_path, b"replacement policy")

    assert output_path.read_bytes() == b"existing policy"
    assert list(tmp_path.iterdir()) == [output_path]


def test_atomic_bytes_writer_preserves_concurrently_published_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_path = tmp_path / "policy.json"
    original_link = atomic_writer.os.link

    def publish_competing_file(source_path: Path, destination_path: Path) -> None:
        destination_path.write_bytes(b"competing policy")
        original_link(source_path, destination_path)

    monkeypatch.setattr(atomic_writer.os, "link", publish_competing_file)

    with pytest.raises(OutputArtifactExistsError, match="덮어쓸 수 없음"):
        write_bytes_atomically(output_path, b"replacement policy")

    assert output_path.read_bytes() == b"competing policy"
    assert list(tmp_path.iterdir()) == [output_path]
