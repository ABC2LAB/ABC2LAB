from pathlib import Path

import pytest

from modules.collector.utils.storage import ArtifactExistsError, OutputPathError, prepare_output_dir, publish_file

RUN_ID = "run_storage"
FILE_NAME = "crawl_result.json"
EXPECTED_RELATIVE_DIR = Path("artifacts") / "iteration-000" / "collector"


@pytest.fixture
def run_root(tmp_path: Path) -> Path:
    return tmp_path / "runs" / RUN_ID


def test_output_dir_created_when_it_matches_iteration(run_root: Path) -> None:
    directory = prepare_output_dir(run_root, 0, run_root / EXPECTED_RELATIVE_DIR)

    assert directory == (run_root / EXPECTED_RELATIVE_DIR).resolve()
    assert directory.is_dir()


@pytest.mark.parametrize(
    "relative_output_dir",
    [
        pytest.param(Path("artifacts") / "iteration-001" / "collector", id="other_iteration"),
        pytest.param(Path("artifacts") / "iteration-000" / "verifier", id="other_producer"),
        pytest.param(Path("artifacts") / "iteration-000" / "collector" / ".." / "collector", id="parent_part"),
        pytest.param(Path("..") / "other_run" / EXPECTED_RELATIVE_DIR, id="other_run"),
    ],
)
def test_output_dir_not_matching_rejected(run_root: Path, relative_output_dir: Path) -> None:
    with pytest.raises(OutputPathError):
        prepare_output_dir(run_root, 0, run_root / relative_output_dir)

    assert not (run_root / "artifacts").exists()


def test_output_dir_outside_run_root_rejected(run_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere" / EXPECTED_RELATIVE_DIR

    with pytest.raises(OutputPathError):
        prepare_output_dir(run_root, 0, outside)

    assert not outside.exists()


def test_symlinked_artifacts_dir_outside_rejected(run_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside_artifacts"
    outside.mkdir()
    run_root.mkdir(parents=True)
    (run_root / "artifacts").symlink_to(outside)

    with pytest.raises(OutputPathError):
        prepare_output_dir(run_root, 0, run_root / EXPECTED_RELATIVE_DIR)

    assert list(outside.iterdir()) == []


def test_publish_writes_bytes_and_leaves_no_temp_file(tmp_path: Path) -> None:
    raw = '{"status": "completed"}\n'.encode()

    path = publish_file(tmp_path, FILE_NAME, raw)

    assert path == tmp_path / FILE_NAME
    assert path.read_bytes() == raw
    assert [child.name for child in tmp_path.iterdir()] == [FILE_NAME]


def test_publish_refuses_existing_file(tmp_path: Path) -> None:
    original = b"first\n"
    publish_file(tmp_path, FILE_NAME, original)

    with pytest.raises(ArtifactExistsError):
        publish_file(tmp_path, FILE_NAME, b"second\n")

    assert (tmp_path / FILE_NAME).read_bytes() == original
    assert [child.name for child in tmp_path.iterdir()] == [FILE_NAME]
