"""OpenRouterScenarioDrafter 테스트. 네트워크 0 — 가짜 transport를 주입한다.

API key는 테스트용 표식 문자열만 쓰고, 그 값이 요청 헤더 밖(오류 메시지·model_info·출력 파일)으로 새지 않는지 본다.
"""
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from modules.scenario_generator import openrouter_drafter
from modules.scenario_generator.entrypoint import build_drafter, main
from modules.scenario_generator.ollama_drafter import PROMPT_VERSION, SYSTEM_PROMPT
from modules.scenario_generator.openrouter_drafter import (
    API_KEY_ENV,
    OpenRouterDrafterConfig,
    OpenRouterScenarioDrafter,
    _default_transport,
)
from modules.scenario_generator.scenario_drafter import DrafterError, build_draft_json_schema
from modules.scenario_generator.service import generate_scenarios
from modules.scenario_generator.utils.config import load_drafter_settings

ARTIFACTS = "artifacts/iteration-000"
CONFIG_PATH_ENV = "SCENARIO_GENERATOR_CONFIG_PATH"
FAKE_API_KEY = "test-key-marker"


def _config() -> OpenRouterDrafterConfig:
    return OpenRouterDrafterConfig(model_id="vendor/test-model", temperature=0.0, seed=3)


def _response_bytes(generated: Any, *, in_tokens: int | None = None, out_tokens: int | None = None) -> bytes:
    envelope: dict[str, Any] = {"choices": [{"message": {"role": "assistant", "content": json.dumps(generated)}}]}
    usage = {key: value for key, value in (("prompt_tokens", in_tokens), ("completion_tokens", out_tokens))
             if value is not None}
    if usage:
        envelope["usage"] = usage
    return json.dumps(envelope).encode("utf-8")


def _request() -> dict[str, Any]:
    return {"target_url": "http://localhost", "candidate": {"candidate_id": "c1"},
            "actor_account": {}, "reference_account": None, "source_requests": []}


def _drafter(transport: Any) -> OpenRouterScenarioDrafter:
    return OpenRouterScenarioDrafter(_config(), FAKE_API_KEY, transport=transport)


# ---- 요청 모양 ----

def test_request_uses_chat_completions_with_shared_prompt_and_schema():
    captured: dict[str, Any] = {}

    def transport(url: str, payload: bytes, headers: dict[str, str], timeout: float) -> bytes:
        captured.update(url=url, body=json.loads(payload), headers=headers, timeout=timeout)
        return _response_bytes({"steps": []})

    _drafter(transport).draft(_request())

    body = captured["body"]
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["timeout"] == 60.0
    assert captured["headers"]["Authorization"] == f"Bearer {FAKE_API_KEY}"
    assert body["model"] == "vendor/test-model" and body["stream"] is False
    # Ollama와 같은 프롬프트·초안 Schema여야 모델만 바꿔 비교할 수 있다.
    assert body["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert json.loads(body["messages"][1]["content"])["candidate"]["candidate_id"] == "c1"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"] == build_draft_json_schema()
    assert body["provider"] == {"require_parameters": True}
    assert body["temperature"] == 0.0 and body["seed"] == 3


def test_optional_sampling_values_left_out_when_none():
    captured: dict[str, Any] = {}

    def transport(url: str, payload: bytes, headers: dict[str, str], timeout: float) -> bytes:
        captured["body"] = json.loads(payload)
        return _response_bytes({"steps": []})

    config = OpenRouterDrafterConfig(model_id="m", temperature=None, seed=None, base_url="http://127.0.0.1:9/v1/")
    OpenRouterScenarioDrafter(config, FAKE_API_KEY, transport=transport).draft(_request())
    assert "temperature" not in captured["body"] and "seed" not in captured["body"]


# ---- 응답 파싱 ----

def test_parses_draft_and_tokens():
    draft_obj = {"steps": []}
    result = _drafter(lambda u, p, h, t: _response_bytes(draft_obj, in_tokens=11, out_tokens=22)).draft(_request())
    assert result.scenario == draft_obj
    assert result.input_tokens == 11 and result.output_tokens == 22


def test_missing_usage_gives_none_tokens():
    result = _drafter(lambda u, p, h, t: _response_bytes({"steps": []})).draft(_request())
    assert result.input_tokens is None and result.output_tokens is None


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"not json", id="not_json"),
        pytest.param(b"[]", id="not_object"),
        pytest.param(b'{"choices": []}', id="no_choices"),
        pytest.param(b'{"choices": [{"message": {"content": null}}]}', id="null_content"),
        pytest.param(json.dumps({"choices": [{"message": {"content": "steps: []"}}]}).encode(), id="content_not_json"),
        pytest.param(_response_bytes([1, 2, 3]), id="draft_not_object"),
    ],
)
def test_malformed_response_raises(raw: bytes):
    with pytest.raises(DrafterError):
        _drafter(lambda u, p, h, t: raw).draft(_request())


def test_error_body_with_200_is_retryable_and_hides_message():
    raw = json.dumps({"error": {"code": 502, "message": "upstream said something"}}).encode()
    with pytest.raises(DrafterError) as caught:
        _drafter(lambda u, p, h, t: raw).draft(_request())
    assert caught.value.is_retryable is True
    assert "upstream" not in str(caught.value)


# ---- 전송 실패 ----

def test_default_transport_wraps_network_error_as_retryable():
    with pytest.raises(DrafterError) as caught:
        _default_transport("http://127.0.0.1:1/chat/completions", b"{}", {"Authorization": f"Bearer {FAKE_API_KEY}"}, 0.5)
    assert caught.value.is_retryable is True
    assert FAKE_API_KEY not in str(caught.value)


# ---- API key ----

def test_empty_api_key_rejected():
    with pytest.raises(DrafterError, match=API_KEY_ENV):
        OpenRouterScenarioDrafter(_config(), "")


def test_empty_model_id_rejected():
    with pytest.raises(DrafterError):
        OpenRouterScenarioDrafter(OpenRouterDrafterConfig(model_id=""), FAKE_API_KEY)


def test_model_info_has_required_fields_and_no_key():
    info = OpenRouterScenarioDrafter(_config(), FAKE_API_KEY).model_info
    assert set(info) == {"model_id", "model_version", "prompt_version", "temperature", "seed"}
    assert info["model_id"] == "vendor/test-model" and info["prompt_version"] == PROMPT_VERSION
    assert FAKE_API_KEY not in json.dumps(info)


# ---- service 흐름 ----

def test_service_flow_with_openrouter_valid_drafts(candidates_artifact, crawl_artifact, drafts):
    def transport(url: str, payload: bytes, headers: dict[str, str], timeout: float) -> bytes:
        candidate_id = json.loads(json.loads(payload)["messages"][1]["content"])["candidate"]["candidate_id"]
        return _response_bytes(drafts[candidate_id], in_tokens=5, out_tokens=9)

    outcome = generate_scenarios(candidates_artifact, crawl_artifact, _drafter(transport))
    assert outcome.scenarios, "유효 초안인데 시나리오가 하나도 안 나옴"
    assert not [e for e in outcome.errors if e["code"] in ("DRAFTER_FAILED", "DRAFT_INVALID")]
    assert outcome.input_tokens is not None and outcome.output_tokens is not None


# ---- 설정·entrypoint ----

def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_config_settings_and_env_key_reach_the_http_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls: list[tuple[str, dict[str, Any], dict[str, str], float]] = []

    def recorder(url: str, payload: bytes, headers: dict[str, str], timeout: float) -> bytes:
        calls.append((url, json.loads(payload), dict(headers), timeout))
        raise DrafterError("테스트 대역: 네트워크 호출 없음")

    monkeypatch.setattr(openrouter_drafter, "_default_transport", recorder)
    monkeypatch.setenv(API_KEY_ENV, FAKE_API_KEY)
    config_path = _write(
        tmp_path / "openrouter.toml",
        '[llm]\nprovider = "openrouter"\nmodel_id = "vendor/probe"\n'
        'base_url = "http://127.0.0.1:9/api/v1"\ntemperature = 0.2\ntimeout_seconds = 30.0\n',
    )
    drafter = build_drafter(load_drafter_settings(config_path))

    with pytest.raises(DrafterError):
        drafter.draft({"probe": 1})

    [(url, payload, headers, timeout)] = calls
    assert url == "http://127.0.0.1:9/api/v1/chat/completions"
    assert timeout == 30.0
    assert payload["model"] == "vendor/probe" and payload["temperature"] == 0.2
    assert headers["Authorization"] == f"Bearer {FAKE_API_KEY}"


def test_config_rejects_api_key_as_setting(tmp_path: Path):
    # key는 설정 파일 키가 아니다(TOML에 비밀값을 두지 않는다).
    config_path = _write(tmp_path / "openrouter.toml",
                         '[llm]\nprovider = "openrouter"\nmodel_id = "m"\napi_key = "x"\n')
    with pytest.raises(ValueError, match="api_key"):
        load_drafter_settings(config_path)


def test_config_requires_model_id(tmp_path: Path):
    config_path = _write(tmp_path / "openrouter.toml", '[llm]\nprovider = "openrouter"\nmodel_id = ""\n')
    with pytest.raises(ValueError, match="model_id"):
        load_drafter_settings(config_path)


@pytest.fixture
def run_root(tmp_path: Path, fixture_run_root: Path) -> Path:
    root = tmp_path / "run"
    for producer in ("collector", "access_analyzer"):
        shutil.copytree(fixture_run_root / ARTIFACTS / producer, root / ARTIFACTS / producer)
    return root


def _cli_args(run_root: Path, run_id: str, *extra: str) -> list[str]:
    return [
        "generate", "--run-root", str(run_root), "--run-id", run_id, "--iteration", "0", "--mode", "development",
        "--candidates", str(run_root / ARTIFACTS / "access_analyzer" / "vulnerability_candidates.json"),
        "--crawl-result", str(run_root / ARTIFACTS / "collector" / "crawl_result.json"),
        "--output-dir", str(run_root / ARTIFACTS / "scenario_generator"),
        *extra,
    ]


def test_missing_api_key_in_config_mode_exits_2_without_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run_root: Path, run_id: str, caplog: pytest.LogCaptureFixture
):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.setenv(CONFIG_PATH_ENV, str(_write(tmp_path / "openrouter.toml",
                                                    '[llm]\nprovider = "openrouter"\nmodel_id = "m"\n')))

    assert main(_cli_args(run_root, run_id)) == 2
    assert not (run_root / ARTIFACTS / "scenario_generator").exists()
    assert API_KEY_ENV in caplog.text


def test_cli_openrouter_without_model_id_exits_2(monkeypatch: pytest.MonkeyPatch, run_root: Path, run_id: str):
    monkeypatch.setenv(API_KEY_ENV, FAKE_API_KEY)
    assert main(_cli_args(run_root, run_id, "--llm-provider", "openrouter")) == 2
    assert not (run_root / ARTIFACTS / "scenario_generator").exists()


def test_cli_openrouter_drafter_failure_is_recorded_without_key(
    monkeypatch: pytest.MonkeyPatch, run_root: Path, run_id: str, capsys: pytest.CaptureFixture[str]
):
    def failing(url: str, payload: bytes, headers: dict[str, str], timeout: float) -> bytes:
        raise DrafterError("테스트 대역: 네트워크 호출 없음", is_retryable=True)

    monkeypatch.setattr(openrouter_drafter, "_default_transport", failing)
    monkeypatch.setenv(API_KEY_ENV, FAKE_API_KEY)

    exit_code = main(_cli_args(run_root, run_id, "--llm-provider", "openrouter", "--model-id", "vendor/probe"))

    output_path = run_root / ARTIFACTS / "scenario_generator" / "test_scenarios.json"
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] in ("partial", "failed")
    assert "DRAFTER_FAILED" in output_path.read_text(encoding="utf-8")
    assert FAKE_API_KEY not in output_path.read_text(encoding="utf-8")
