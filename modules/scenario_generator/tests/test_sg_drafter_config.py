"""drafter 설정: 설정 파일 위치·우선순위·provider별 동작·오류 거절·CLI 조합 규칙.

네트워크 없이 본다. Ollama 전송 함수는 이 파일 전체에서 기록용 대역으로 바꾼다(호출되면 기록하고 DrafterError).
기대값은 README·커밋된 설정 파일에 적힌 값(literal)으로 적는다. 코드 상수로 기대값을 만들지 않는다.
"""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from modules.scenario_generator import ollama_drafter
from modules.scenario_generator.entrypoint import build_drafter, main, run
from modules.scenario_generator.replay_drafter import ReplayScenarioDrafter
from modules.scenario_generator.scenario_drafter import DrafterError
from modules.scenario_generator.utils.config import load_drafter_settings

ARTIFACTS = "artifacts/iteration-000"
CONFIG_PATH_ENV = "SCENARIO_GENERATOR_CONFIG_PATH"
DEFAULT_CONFIG_RELATIVE = Path("modules") / "scenario_generator" / "configs" / "scenario_generator.toml"
REPO_ROOT = Path(__file__).resolve().parents[3]
REPLAY_MODEL_ID = "replay_file"  # README: replay drafter는 model_info.model_id를 replay_file로 남긴다
LEAK_MARKER = "leak-marker"


@dataclass(frozen=True)
class RunFolder:
    """fixture 입력만 복사한 임시 run 폴더."""

    run_root: Path
    run_id: str

    @property
    def input_paths(self) -> dict[str, Path]:
        return {
            "vulnerability_candidates": self.run_root / ARTIFACTS / "access_analyzer" / "vulnerability_candidates.json",
            "crawl_result": self.run_root / ARTIFACTS / "collector" / "crawl_result.json",
        }

    @property
    def output_dir(self) -> Path:
        return self.run_root / ARTIFACTS / "scenario_generator"

    def context(self, **extra: Any) -> dict[str, Any]:
        return {"run_root": self.run_root, "run_id": self.run_id, "iteration": 0, "mode": "development", **extra}

    def run(self, drafter: Any = None, **extra: Any) -> dict[str, Any]:
        return run("generate", self.input_paths, self.output_dir, self.context(**extra), drafter=drafter)

    def read_output(self) -> dict[str, Any]:
        return json.loads((self.output_dir / "test_scenarios.json").read_text(encoding="utf-8"))


@dataclass
class TransportRecorder:
    """Ollama 전송 대역. 받은 URL·본문·타임아웃을 기록하고 DrafterError로 끝낸다(네트워크 0)."""

    calls: list[tuple[str, dict[str, Any], float]]

    def __call__(self, url: str, payload: bytes, timeout: float) -> bytes:
        self.calls.append((url, json.loads(payload), timeout))
        raise DrafterError("테스트 대역: 네트워크 호출 없음")


@pytest.fixture(autouse=True)
def transport(monkeypatch: pytest.MonkeyPatch) -> TransportRecorder:
    recorder = TransportRecorder(calls=[])
    monkeypatch.setattr(ollama_drafter, "_default_transport", recorder)
    return recorder


@pytest.fixture
def folder(tmp_path: Path, fixture_run_root: Path, run_id: str) -> RunFolder:
    run_root = tmp_path / "run"
    for producer in ("collector", "access_analyzer"):
        shutil.copytree(fixture_run_root / ARTIFACTS / producer, run_root / ARTIFACTS / producer)
    return RunFolder(run_root, run_id)


def write_config(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def use_config(monkeypatch: pytest.MonkeyPatch, path: Path, text: str) -> Path:
    write_config(path, text)
    monkeypatch.setenv(CONFIG_PATH_ENV, str(path))
    return path


def replay_config(drafts_path: Path | str) -> str:
    return f'[llm]\nprovider = "replay"\ndrafts_path = "{drafts_path}"\n'


# ---- 설정 위치와 우선순위 ----


def test_replay_config_from_env_path_reaches_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, folder: RunFolder, drafts_path: Path
) -> None:
    # conftest 기본 env는 provider=none이다. env 경로를 따르지 않으면 failed가 된다.
    use_config(monkeypatch, tmp_path / "custom.toml", replay_config(drafts_path))

    response = folder.run()

    document = folder.read_output()
    assert response["status"] == document["status"] == "completed"
    assert response["scenario_count"] > 0
    assert document["data"]["model_info"]["model_id"] == REPLAY_MODEL_ID


def test_default_config_path_and_cwd_relative_drafts_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, folder: RunFolder, drafts_path: Path
) -> None:
    monkeypatch.delenv(CONFIG_PATH_ENV)
    monkeypatch.chdir(tmp_path)
    shutil.copy(drafts_path, tmp_path / "drafts.json")
    write_config(tmp_path / DEFAULT_CONFIG_RELATIVE, replay_config("drafts.json"))

    response = folder.run()

    assert response["status"] == "completed"
    assert folder.read_output()["data"]["model_info"]["model_id"] == REPLAY_MODEL_ID


def test_drafter_argument_skips_config_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, folder: RunFolder, drafts: dict[str, Any]
) -> None:
    use_config(monkeypatch, tmp_path / "broken.toml", '[llm]\nprovider = "none')

    response = folder.run(ReplayScenarioDrafter(drafts))

    assert response["status"] == "completed"


@pytest.mark.parametrize("value", [None, object()], ids=["none_value", "object_value"])
def test_context_drafter_key_is_rejected(folder: RunFolder, value: Any) -> None:
    with pytest.raises(ValueError, match="drafter= 인자"):
        run("generate", folder.input_paths, folder.output_dir, folder.context(drafter=value))
    assert not folder.output_dir.exists()


# ---- provider별 동작 ----


def test_provider_none_writes_failed_file_without_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, folder: RunFolder, transport: TransportRecorder
) -> None:
    use_config(monkeypatch, tmp_path / "none.toml", '[llm]\nprovider = "none"\n')

    response = folder.run()

    document = folder.read_output()
    assert response["status"] == document["status"] == "failed"
    assert document["data"] is None
    assert [error["code"] for error in document["errors"]] == ["DRAFTER_NOT_CONFIGURED"]
    assert transport.calls == []


def test_ollama_settings_reach_the_http_call(tmp_path: Path, transport: TransportRecorder) -> None:
    config_path = write_config(
        tmp_path / "ollama.toml",
        "[llm]\n"
        'provider = "ollama"\n'
        'model_id = "probe-model:1b"\n'
        'base_url = "http://127.0.0.1:11500/"\n'
        "temperature = 0.3\n"
        "timeout_seconds = 12.5\n"
        "seed = 7\n"
        'model_version = "probe-v2"\n',
    )
    drafter = build_drafter(load_drafter_settings(config_path))

    with pytest.raises(DrafterError):
        drafter.draft({"probe": 1})

    [(url, payload, timeout)] = transport.calls
    assert url == "http://127.0.0.1:11500/api/generate"
    assert timeout == 12.5
    assert payload["model"] == "probe-model:1b"
    assert payload["options"] == {"temperature": 0.3, "seed": 7}
    assert drafter.model_info["model_version"] == "probe-v2"


def test_ollama_keys_left_out_behave_like_committed_template(tmp_path: Path, transport: TransportRecorder) -> None:
    # 커밋된 설정 파일의 안내 값(base_url·temperature·timeout_seconds)과 키를 뺐을 때 동작이 같아야 한다.
    config_path = write_config(tmp_path / "ollama.toml", '[llm]\nprovider = "ollama"\nmodel_id = "probe-model:1b"\n')
    drafter = build_drafter(load_drafter_settings(config_path))

    with pytest.raises(DrafterError):
        drafter.draft({"probe": 1})

    [(url, payload, timeout)] = transport.calls
    assert url == "http://localhost:11434/api/generate"
    assert timeout == 60.0
    assert payload["options"] == {"temperature": 0.0}


@pytest.mark.parametrize(
    "line",
    ["temperature = 0", "temperature = 0.0", "timeout_seconds = 1", "timeout_seconds = 0.001", "seed = 0"],
)
def test_number_boundaries_and_toml_integers_are_accepted(tmp_path: Path, line: str) -> None:
    config_path = write_config(tmp_path / "ok.toml", f'[llm]\nprovider = "ollama"\nmodel_id = "m"\n{line}\n')
    assert load_drafter_settings(config_path).provider == "ollama"


def test_committed_default_config_is_provider_none() -> None:
    settings = load_drafter_settings(REPO_ROOT / DEFAULT_CONFIG_RELATIVE)
    assert settings.provider == "none"
    assert build_drafter(settings) is None


# ---- 잘못된 설정: run() 즉시 ValueError, 출력 파일 없음, 값은 메시지에 없음 ----

BROKEN_DRAFTS = "{broken_drafts}"
INVALID_CONFIGS = [
    pytest.param(None, None, id="missing_file"),
    pytest.param('[llm]\nprovider = "none', None, id="broken_toml"),
    pytest.param("", None, id="no_llm_table"),
    pytest.param('[llm]\nprovider = "none"\nextra_key = 1\n', None, id="undefined_llm_key"),
    pytest.param('[other]\nx = 1\n[llm]\nprovider = "none"\n', None, id="undefined_table"),
    pytest.param('[llm]\nmodel_id = ""\n', None, id="provider_missing"),
    pytest.param(f'[llm]\nprovider = "{LEAK_MARKER}-provider"\n', LEAK_MARKER, id="provider_not_allowed"),
    pytest.param("[llm]\nprovider = 1\n", None, id="provider_not_string"),
    pytest.param('[llm]\nprovider = "ollama"\nmodel_id = 1\n', None, id="model_id_not_string"),
    pytest.param('[llm]\nprovider = "none"\nbase_url = 11434\n', None, id="base_url_not_string"),
    pytest.param('[llm]\nprovider = "none"\ntemperature = "0.5"\n', None, id="temperature_string"),
    pytest.param('[llm]\nprovider = "none"\ntemperature = true\n', None, id="temperature_bool"),
    pytest.param('[llm]\nprovider = "none"\ntimeout_seconds = true\n', None, id="timeout_bool"),
    pytest.param('[llm]\nprovider = "none"\nseed = true\n', None, id="seed_bool"),
    pytest.param('[llm]\nprovider = "none"\nseed = 1.5\n', None, id="seed_float"),
    pytest.param('[llm]\nprovider = "none"\ntemperature = -0.1\n', None, id="temperature_negative"),
    pytest.param('[llm]\nprovider = "none"\ntimeout_seconds = 0\n', None, id="timeout_zero"),
    pytest.param('[llm]\nprovider = "none"\ntimeout_seconds = -1\n', None, id="timeout_negative"),
    pytest.param('[llm]\nprovider = "ollama"\nmodel_id = ""\n', None, id="ollama_model_id_empty"),
    pytest.param('[llm]\nprovider = "ollama"\nmodel_id = "   "\n', None, id="ollama_model_id_blank"),
    pytest.param('[llm]\nprovider = "replay"\ndrafts_path = ""\n', None, id="replay_drafts_path_empty"),
    pytest.param('[llm]\nprovider = "replay"\n', None, id="replay_drafts_path_missing_key"),
    pytest.param(replay_config(f"{LEAK_MARKER}-dir/nope.json"), LEAK_MARKER, id="replay_drafts_file_missing"),
    pytest.param(replay_config(BROKEN_DRAFTS), None, id="replay_drafts_content_broken"),
]


@pytest.mark.parametrize(("text", "forbidden"), INVALID_CONFIGS)
def test_invalid_config_raises_before_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    folder: RunFolder,
    transport: TransportRecorder,
    text: str | None,
    forbidden: str | None,
) -> None:
    config_path = tmp_path / "invalid.toml"
    monkeypatch.setenv(CONFIG_PATH_ENV, str(config_path))
    if text is not None:
        broken_drafts = write_config(tmp_path / "broken_drafts.json", '{"a": 1,}')
        write_config(config_path, text.replace(BROKEN_DRAFTS, str(broken_drafts)))

    with pytest.raises(ValueError) as caught:
        folder.run()

    assert not folder.output_dir.exists()
    assert transport.calls == []
    if forbidden is not None:
        assert forbidden not in str(caught.value)


def test_unreadable_config_file_raises_value_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, folder: RunFolder, transport: TransportRecorder
) -> None:
    # 권한 오류는 chmod 대신 읽기 함수로 낸다(root로 돌면 chmod 000 파일도 읽혀서 테스트가 무의미해진다).
    config_path = use_config(monkeypatch, tmp_path / "locked.toml", '[llm]\nprovider = "none"\n')
    original_read_text = Path.read_text

    def read_text_denied(self: Path, *args: Any, **kwargs: Any) -> str:
        if self == config_path:
            raise PermissionError(13, "Permission denied")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text_denied)

    with pytest.raises(ValueError, match="locked.toml") as caught:
        folder.run()

    assert isinstance(caught.value.__cause__, PermissionError)
    assert not folder.output_dir.exists()
    assert transport.calls == []


def test_unreadable_drafts_path_raises_value_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, folder: RunFolder, transport: TransportRecorder
) -> None:
    drafts_path = tmp_path / "locked" / "drafts.json"
    use_config(monkeypatch, tmp_path / "replay.toml", replay_config(drafts_path))
    original_is_file = Path.is_file

    def is_file_denied(self: Path, *args: Any, **kwargs: Any) -> bool:
        if self == drafts_path:
            raise PermissionError(13, "Permission denied")
        return original_is_file(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", is_file_denied)

    with pytest.raises(ValueError, match="drafts_path") as caught:
        folder.run()

    assert isinstance(caught.value.__cause__, PermissionError)
    assert "locked" not in str(caught.value)
    assert not folder.output_dir.exists()
    assert transport.calls == []


def test_config_mode_build_error_names_config_file_and_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, folder: RunFolder
) -> None:
    # 초안 파일 내용이 깨지면 원래 예외(JSON 오류)가 아니라 설정 파일·키 이름을 가리키는 ValueError로 알린다.
    broken_drafts = write_config(tmp_path / "broken_drafts.json", '{"a": 1,}')
    use_config(monkeypatch, tmp_path / "replay_broken.toml", replay_config(broken_drafts))

    with pytest.raises(ValueError, match=r"replay_broken\.toml \[llm\]: drafts_path") as caught:
        folder.run()

    assert caught.value.__cause__ is not None
    assert not folder.output_dir.exists()


# ---- CLI: 준 drafter 옵션이 조용히 무시되는 조합은 종료 코드 2, 파일 없음 ----


def cli_args(folder: RunFolder, *extra: str) -> list[str]:
    return [
        "generate",
        "--run-root", str(folder.run_root),
        "--run-id", folder.run_id,
        "--iteration", "0",
        "--mode", "development",
        "--candidates", str(folder.input_paths["vulnerability_candidates"]),
        "--crawl-result", str(folder.input_paths["crawl_result"]),
        "--output-dir", str(folder.output_dir),
        *extra,
    ]


DRAFTS_TOKEN = "{drafts}"
CONFIG_TOKEN = "{config}"
CLI_REJECTED = [
    pytest.param(["--model-id", "m"], id="ollama_option_without_provider"),
    pytest.param(["--temperature", "0.5"], id="temperature_without_provider"),
    pytest.param(["--llm-provider", "replay"], id="replay_without_drafts"),
    pytest.param(["--llm-provider", "replay", "--drafts", DRAFTS_TOKEN, "--seed", "1"], id="replay_with_ollama_option"),
    pytest.param(["--drafts", DRAFTS_TOKEN, "--base-url", "http://127.0.0.1:1"], id="drafts_with_ollama_option"),
    pytest.param(["--llm-provider", "ollama", "--model-id", "m", "--drafts", DRAFTS_TOKEN], id="ollama_with_drafts"),
    pytest.param(["--llm-provider", "ollama"], id="ollama_without_model_id"),
    pytest.param(["--llm-provider", "ollama", "--model-id", "m", "--timeout", "0"], id="ollama_timeout_zero"),
    pytest.param(["--config", CONFIG_TOKEN, "--drafts", DRAFTS_TOKEN], id="config_with_drafts"),
    pytest.param(["--config", CONFIG_TOKEN, "--llm-provider", "ollama", "--model-id", "m"], id="config_with_ollama"),
]


@pytest.mark.parametrize("extra", CLI_REJECTED)
def test_cli_combination_that_would_ignore_an_option_exits_2(
    tmp_path: Path, folder: RunFolder, drafts_path: Path, transport: TransportRecorder, extra: list[str]
) -> None:
    config_path = write_config(tmp_path / "replay.toml", replay_config(drafts_path))
    replacements = {DRAFTS_TOKEN: str(drafts_path), CONFIG_TOKEN: str(config_path)}
    args = [replacements.get(arg, arg) for arg in extra]

    assert main(cli_args(folder, *args)) == 2
    assert not folder.output_dir.exists()
    assert transport.calls == []


def test_cli_config_option_selects_the_config_file(
    tmp_path: Path, folder: RunFolder, drafts_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # conftest 기본 env는 provider=none이다. --config를 따르지 않으면 failed가 된다.
    config_path = write_config(tmp_path / "replay.toml", replay_config(drafts_path))

    exit_code = main(cli_args(folder, "--config", str(config_path)))

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
    assert folder.read_output()["data"]["model_info"]["model_id"] == REPLAY_MODEL_ID
