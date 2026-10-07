"""출력 경로 검증과 덮어쓰지 않는 원자적 공개."""

import math
from pathlib import Path

import pytest

from modules.access_analyzer.utils import storage


def _valid_output_dir(run_root: Path) -> Path:
    return run_root / storage.ARTIFACTS_DIR_NAME / storage.ITERATION_DIR_FORMAT.format(0) / storage.PRODUCER


def test_prepare_output_dir_returns_expected(tmp_path: Path) -> None:
    run_root = tmp_path / "run_x"
    result = storage.prepare_output_dir(run_root, 0, _valid_output_dir(run_root))
    assert result == _valid_output_dir(run_root).resolve()
    assert result.is_dir()


def test_prepare_output_dir_rejects_wrong_producer(tmp_path: Path) -> None:
    run_root = tmp_path / "run_x"
    wrong = run_root / storage.ARTIFACTS_DIR_NAME / "iteration-000" / "collector"
    with pytest.raises(storage.OutputPathError):
        storage.prepare_output_dir(run_root, 0, wrong)


def test_prepare_output_dir_rejects_parent_part(tmp_path: Path) -> None:
    run_root = tmp_path / "run_x"
    with pytest.raises(storage.OutputPathError):
        storage.prepare_output_dir(run_root, 0, run_root / ".." / "escape")


def test_prepare_output_dir_rejects_symlink_escape(tmp_path: Path) -> None:
    run_root = tmp_path / "run_x"
    iteration_dir = run_root / storage.ARTIFACTS_DIR_NAME / storage.ITERATION_DIR_FORMAT.format(0)
    iteration_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = iteration_dir / storage.PRODUCER
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(storage.OutputPathError):
        storage.prepare_output_dir(run_root, 0, link)


def test_publish_file_writes_and_is_immutable(tmp_path: Path) -> None:
    run_root = tmp_path / "run_x"
    directory = storage.prepare_output_dir(run_root, 0, _valid_output_dir(run_root))
    path = storage.publish_file(directory, "graph_query.json", b"{}\n")
    assert path.read_bytes() == b"{}\n"
    with pytest.raises(storage.ArtifactExistsError):
        storage.publish_file(directory, "graph_query.json", b"{}\n")


def test_serialize_json_rejects_nan() -> None:
    with pytest.raises(ValueError):
        storage.serialize_json({"x": math.nan})


def test_serialize_json_is_utf8_with_newline() -> None:
    raw = storage.serialize_json({"name": "한글"})
    assert raw.endswith(b"\n")
    assert "한글" in raw.decode("utf-8")
