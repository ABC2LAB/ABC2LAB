"""재현 설정 로드. 기본값·파일 override·잘못된 값 처리를 본다."""

from pathlib import Path

from modules.verifier.utils.config import DEFAULT_MAX_REDIRECTS, load_replay_config


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "verifier.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_missing_file_uses_default(tmp_path: Path) -> None:
    assert load_replay_config(tmp_path / "none.toml").max_redirects == DEFAULT_MAX_REDIRECTS


def test_default_is_zero() -> None:
    # 패키지 기본 configs/verifier.toml도 0이어야 한다(로그인 리다이렉트 200 오탐 방지).
    assert load_replay_config().max_redirects == 0


def test_file_override(tmp_path: Path) -> None:
    path = _write(tmp_path, "[replay]\nmax_redirects = 3\n")
    assert load_replay_config(path).max_redirects == 3


def test_invalid_value_falls_back_to_default(tmp_path: Path) -> None:
    path = _write(tmp_path, "[replay]\nmax_redirects = -1\n")
    assert load_replay_config(path).max_redirects == DEFAULT_MAX_REDIRECTS
