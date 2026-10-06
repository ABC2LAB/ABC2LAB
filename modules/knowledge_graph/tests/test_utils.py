import json
from pathlib import Path

import pytest

from modules.knowledge_graph.exceptions import ContractValidationError, PathValidationError
from modules.knowledge_graph.utils.atomic_writer import write_json_atomically
from modules.knowledge_graph.utils.hashing import calculate_sha256
from modules.knowledge_graph.utils.paths import (
    require_existing_file,
    resolve_trusted_relative_path,
)
from modules.knowledge_graph.utils.validation import load_json


def test_resolve_trusted_relative_path_accepts_child(tmp_path: Path) -> None:
    child = tmp_path / "artifacts" / "input.json"
    child.parent.mkdir()
    child.write_text("{}", encoding="utf-8")

    assert require_existing_file(tmp_path, "artifacts/input.json") == child.resolve()


@pytest.mark.parametrize("value", ["../outside.json", "/tmp/outside.json"])
def test_resolve_trusted_relative_path_rejects_escape(tmp_path: Path, value: str) -> None:
    with pytest.raises(PathValidationError):
        resolve_trusted_relative_path(tmp_path, value)


def test_resolve_trusted_relative_path_rejects_outside_symlink(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    link = tmp_path / "linked.json"
    link.symlink_to(outside)

    with pytest.raises(PathValidationError):
        resolve_trusted_relative_path(tmp_path, "linked.json")


def test_atomic_writer_replaces_complete_json(tmp_path: Path) -> None:
    output_path = tmp_path / "result.json"
    value = {"status": "completed", "items": [1, 2]}

    write_json_atomically(output_path, value)

    assert json.loads(output_path.read_text(encoding="utf-8")) == value
    assert not list(tmp_path.glob("*.tmp"))
    assert len(calculate_sha256(output_path)) == 64


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
