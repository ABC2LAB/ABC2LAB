from pathlib import Path

import pytest

from modules.scenario_generator.utils.paths import UnsafePathError, resolve_inside_root


def test_nested_relative_path_resolves_inside_root(tmp_path: Path) -> None:
    resolved = resolve_inside_root(tmp_path, "artifacts/iteration-000/producer/file.json")
    assert resolved == tmp_path.resolve() / "artifacts/iteration-000/producer/file.json"


def test_missing_target_file_is_allowed(tmp_path: Path) -> None:
    # 아직 만들지 않은 출력 경로도 resolve할 수 있어야 한다.
    assert resolve_inside_root(tmp_path, "not/created/yet.json").parent.name == "created"


@pytest.mark.parametrize("bad_path", ["", "/etc/passwd", "../outside", "a/../b", "a/../../b", "a\x00b"])
def test_unsafe_relative_paths_are_rejected(tmp_path: Path, bad_path: str) -> None:
    with pytest.raises(UnsafePathError):
        resolve_inside_root(tmp_path, bad_path)


def test_path_into_another_run_is_rejected(tmp_path: Path) -> None:
    run_root = tmp_path / "run_a"
    (tmp_path / "run_b").mkdir()
    run_root.mkdir()
    with pytest.raises(UnsafePathError):
        resolve_inside_root(run_root, "../run_b/artifacts/x.json")


def test_symlink_pointing_outside_root_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafePathError):
        resolve_inside_root(root, "link/secret.json")


def test_symlink_staying_inside_root_is_allowed(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (tmp_path / "alias").symlink_to(real_dir, target_is_directory=True)
    resolved = resolve_inside_root(tmp_path, "alias/file.json")
    assert resolved == real_dir.resolve() / "file.json"


def test_missing_root_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_inside_root(tmp_path / "no_such_root", "file.json")
