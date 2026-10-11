"""실제 진단 모듈 없이 Runner CLI 프로세스·공개 응답·소유권 경계를 검증한다."""

import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from local_web.inputs import InputError, InputSettings, InputStore, prepare_input
from local_web.runner import (
    ARTIFACT_FILES, MAX_RUN_RECORDS, MAX_SUMMARY_BYTES, STEP_ARTIFACTS,
    RunnerError, RunnerSettings, RunnerStore, artifact_path, public_artifacts, safe_summary,
)


def prepared_authorization(store: InputStore, payload: dict[str, object]) -> tuple[str, str, str]:
    session_id, session = store.open_session(None)
    result = store.prepare(session_id, session.csrf_token, payload)
    return session_id, session.csrf_token, result["preparation_id"]


def wait_for_finish(store: RunnerStore, owner: str) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = store.status(owner)
        if status["state"] != "running":
            return status
        time.sleep(0.01)
    pytest.fail("Runner test double did not finish")


@pytest.mark.parametrize("case, state, exit_code", [
    ("completed", "completed", 0), ("partial", "partial", 1),
    ("failed", "failed", 2), ("session_failed", "partial", 1),
])
def test_real_child_public_summary_and_secrets(
    input_settings: InputSettings, runner_settings: RunnerSettings, input_payload: dict[str, object],
    monkeypatch: pytest.MonkeyPatch, case: str, state: str, exit_code: int,
) -> None:
    monkeypatch.setenv("LOCAL_WEB_TEST_RUNNER_CASE", case)
    monkeypatch.setenv("COLLECTOR_ACCOUNT_LEAK_PASSWORD", "OTHER_RUN_SECRET")
    inputs, runs = InputStore(input_settings), RunnerStore(runner_settings)
    try:
        authorization = prepared_authorization(inputs, input_payload)
        prepared = inputs.authenticate(*authorization[:2]).prepared
        temporary_path = prepared.collector_config_path.parent
        record = runs.start(inputs, authorization, None)
        assert prepared.accounts == ()
        result = wait_for_finish(runs, record.owner_token)
        assert result["state"] == state and result["exit_code"] == exit_code
        assert len(result["summary"]["steps"]) == 12
        assert result["run_id"].startswith("web-")
        assert result["elapsed_seconds"] >= 0
        assert result["summary"]["session_window"] == ("failed" if case == "session_failed" else "opened")
        assert not temporary_path.exists()
        assert record.run_root.stat().st_mode & 0o777 == 0o700
        assert (record.run_root / artifact_path("crawl_result")).stat().st_mode & 0o777 == 0o600
        assert inputs.authenticate(*authorization[:2]).prepared is None
        serialized = json.dumps(result)
        for forbidden in ("LOGIN_SENTINEL", "PASSWORD_SENTINEL", "PRIVATE", "SECRET", str(runner_settings.runs_dir), record.owner_token):
            assert forbidden not in serialized
        assert os.environ["COLLECTOR_ACCOUNT_LEAK_PASSWORD"] == "OTHER_RUN_SECRET"
        assert runs.readiness()["is_busy"] is False
        with pytest.raises(InputError, match="PREPARATION_UNAVAILABLE"):
            runs.start(inputs, authorization, record.owner_token)
    finally:
        runs.close()
        inputs.close()


@pytest.mark.parametrize("case", ["bad_json", "oversized", "missing_summary", "exit_mismatch"])
def test_invalid_or_missing_summary_is_not_success(
    input_settings: InputSettings, runner_settings: RunnerSettings, input_payload: dict[str, object],
    monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    monkeypatch.setenv("LOCAL_WEB_TEST_RUNNER_CASE", case)
    inputs, runs = InputStore(input_settings), RunnerStore(runner_settings)
    try:
        record = runs.start(inputs, prepared_authorization(inputs, input_payload), None)
        result = wait_for_finish(runs, record.owner_token)
        assert result["state"] == "failed"
        assert result["summary"] is None and result["issue"] == "RUNNER_RESULT_UNAVAILABLE"
        assert "SENTINEL" not in json.dumps(result)
    finally:
        runs.close()
        inputs.close()


def test_single_active_run_and_expired_input_do_not_affect_run(
    input_settings: InputSettings, runner_settings: RunnerSettings, input_payload: dict[str, object],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = tmp_path / "release"
    monkeypatch.setenv("LOCAL_WEB_TEST_RUNNER_RELEASE", str(release))
    inputs, runs = InputStore(input_settings), RunnerStore(runner_settings)
    try:
        first = prepared_authorization(inputs, input_payload)
        second = prepared_authorization(inputs, input_payload)
        record = runs.start(inputs, first, None)
        with pytest.raises(RunnerError, match="RUNNER_BUSY"):
            runs.start(inputs, second, None)
        assert inputs.authenticate(*second[:2]).prepared is not None
        inputs.clear_session(first[0])
        assert runs.status(record.owner_token)["state"] == "running"
        assert runs.status(None) == {"state": "idle", "is_busy": True}
        with pytest.raises(RunnerError, match="RUN_UNAVAILABLE"):
            runs.status("f" * 64)
        release.touch()
        wait_for_finish(runs, record.owner_token)
        next_record = runs.start(inputs, second, record.owner_token)
        assert next_record.owner_token == record.owner_token and next_record.run_id != record.run_id
        wait_for_finish(runs, next_record.owner_token)
        assert len(runs._records) == 1
    finally:
        runs.close()
        inputs.close()


def test_owned_process_shutdown_and_temporary_cleanup(
    input_settings: InputSettings, runner_settings: RunnerSettings, input_payload: dict[str, object],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_WEB_TEST_RUNNER_RELEASE", str(tmp_path / "never-release"))
    inputs, runs = InputStore(input_settings), RunnerStore(runner_settings)
    try:
        authorization = prepared_authorization(inputs, input_payload)
        temporary_path = inputs.authenticate(*authorization[:2]).prepared.collector_config_path.parent
        record = runs.start(inputs, authorization, None)
        runs.close()
        assert record.process.poll() is not None
        assert not record.worker.is_alive() and not temporary_path.exists()
        result = runs.status(record.owner_token)
        assert result["state"] == "stopped" and result["issue"] == "WEB_SHUTDOWN"
        runs.close()
    finally:
        runs.close()
        inputs.close()


def test_start_failure_discards_consumed_input(
    input_settings: InputSettings, runner_settings: RunnerSettings, input_payload: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_start(*args: object, **kwargs: object) -> None:
        raise OSError("PASSWORD_SENTINEL")

    monkeypatch.setattr("local_web.runner.subprocess.Popen", fail_start)
    inputs, runs = InputStore(input_settings), RunnerStore(runner_settings)
    try:
        authorization = prepared_authorization(inputs, input_payload)
        prepared = inputs.authenticate(*authorization[:2]).prepared
        temporary_path = prepared.collector_config_path.parent
        with pytest.raises(RunnerError, match="RUNNER_START_FAILED"):
            runs.start(inputs, authorization, None)
        assert prepared.accounts == () and not temporary_path.exists()
        assert runs.readiness()["is_busy"] is False
    finally:
        runs.close()
        inputs.close()


@pytest.mark.parametrize("provider", ["fake", "none", "replay", "", "unknown"])
def test_no_fake_model_fallback(runner_settings: RunnerSettings, provider: str) -> None:
    runner_settings.semantic_config_path.write_text(f'[llm]\nprovider="{provider}"\nmodel_id="MODEL"\n')
    assert "SEMANTIC_CONFIG_INVALID" in runner_settings.issues()


@pytest.mark.parametrize("body", ["broken", "[llm]\nprovider='ollama'", "[llm]\nprovider='ollama'\nmodel_id=' '", "x" * (64 * 1024 + 1)])
def test_invalid_model_config(runner_settings: RunnerSettings, body: str) -> None:
    runner_settings.scenario_config_path.write_text(body)
    assert "SCENARIO_CONFIG_INVALID" in runner_settings.issues()


def test_readiness_checks_only_declared_config_and_metadata(
    runner_settings: RunnerSettings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = Path.open

    def forbid_private_or_truth(path: Path, *args: object, **kwargs: object) -> object:
        assert path.name not in {"ground_truth.json", "policy.json"}
        assert "private" not in path.parts
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbid_private_or_truth)
    assert runner_settings.issues() == []
    assert RunnerStore(runner_settings).readiness() == {"diagnosis_enabled": True, "is_busy": False, "issues": []}


@pytest.mark.parametrize("field_name, code", [
    ("runs_dir", "RUN_STORAGE_UNAVAILABLE"), ("semantic_config_path", "SEMANTIC_CONFIG_INVALID"),
    ("scenario_config_path", "SCENARIO_CONFIG_INVALID"), ("policy_config_path", "POLICY_CONFIG_UNAVAILABLE"),
])
def test_missing_paths_are_not_ready(runner_settings: RunnerSettings, field_name: str, code: str) -> None:
    assert code in replace(runner_settings, **{field_name: None}).issues()


def test_settings_environment(runner_settings: RunnerSettings) -> None:
    settings = RunnerSettings.from_environment({
        "LOCAL_WEB_RUNS_PATH": str(runner_settings.runs_dir),
        "SEMANTIC_ANALYZER_CONFIG_PATH": str(runner_settings.semantic_config_path),
        "SCENARIO_GENERATOR_CONFIG_PATH": str(runner_settings.scenario_config_path),
        "SAFETY_POLICY_CONFIG_PATH": str(runner_settings.policy_config_path),
        "LOCAL_WEB_MATCHING_PROFILE": "chosen-profile",
    })
    assert settings.runs_dir == runner_settings.runs_dir and settings.matching_profile == "chosen-profile"
    assert "MATCHING_PROFILE_INVALID" in replace(settings, matching_profile="../profile").issues()


def test_artifact_presence_does_not_read_content_or_follow_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "owned-run"
    path = root / artifact_path("crawl_result")
    path.parent.mkdir(parents=True)
    path.write_text("NOT_JSON_AND_MUST_NOT_BE_READ")
    public = {item["artifact_type"]: item["is_present"] for item in public_artifacts(root)}
    assert public["crawl_result"] and sum(public.values()) == 1
    outside = tmp_path / "outside"
    outside.write_text("PRIVATE")
    path.unlink()
    path.symlink_to(outside)
    assert not any(item["is_present"] for item in public_artifacts(root))
    assert len(public_artifacts(root)) == len(ARTIFACT_FILES)


def summary_fixture(root: Path) -> dict[str, object]:
    return {"run_id": root.name, "run_root": str(root), "mode": "development", "iteration": 0,
            "exit_code": 0, "session_window": "opened", "halted_by": None,
            "steps": [{"step": name, "status": "completed", "artifact": None, "sha256": None,
                       "error_count": 0} for name in STEP_ARTIFACTS]}


@pytest.mark.parametrize("field_name, value", [
    ("run_id", "other"), ("run_root", "/outside"), ("mode", "diagnosis"), ("iteration", True),
    ("iteration", 1), ("exit_code", True), ("session_window", "PASSWORD_SENTINEL"),
    ("halted_by", "unknown"), ("steps", []), ("steps", None),
])
def test_summary_identity_and_shape(tmp_path: Path, field_name: str, value: object) -> None:
    raw = summary_fixture(tmp_path)
    raw[field_name] = value
    with pytest.raises(RunnerError, match="RUNNER_SUMMARY_INVALID"):
        safe_summary(json.dumps(raw).encode(), tmp_path, 0)


@pytest.mark.parametrize("field_name, value", [
    ("step", "arbitrary"), ("status", "success"), ("status", []), ("error_count", -1),
    ("error_count", True), ("artifact", "../../private/password.json"), ("sha256", "SECRET"),
])
def test_summary_never_forwards_untrusted_step_data(tmp_path: Path, field_name: str, value: object) -> None:
    raw = summary_fixture(tmp_path)
    raw["steps"][0][field_name] = value
    with pytest.raises(RunnerError):
        safe_summary(json.dumps(raw).encode(), tmp_path, 0)


@pytest.mark.parametrize("body", [b"", b"{} {}", b'{"run_id":"a","run_id":"b"}', b'{"iteration":NaN}', b"\xff", b"[]", b"x" * (MAX_SUMMARY_BYTES + 1)])
def test_invalid_summary_json(tmp_path: Path, body: bytes) -> None:
    with pytest.raises(RunnerError):
        safe_summary(body, tmp_path, 0)


def test_run_storage_permissions_and_symlink_rejection(runner_settings: RunnerSettings, tmp_path: Path) -> None:
    runner_settings.runs_dir.chmod(0o777)
    assert "RUN_STORAGE_UNAVAILABLE" in runner_settings.issues()
    runner_settings.runs_dir.chmod(0o700)
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(runner_settings.runs_dir, target_is_directory=True)
    assert "RUN_STORAGE_UNAVAILABLE" in replace(runner_settings, runs_dir=linked_directory).issues()
    linked_config = tmp_path / "linked.toml"
    linked_config.symlink_to(runner_settings.semantic_config_path)
    assert "SEMANTIC_CONFIG_INVALID" in replace(runner_settings, semantic_config_path=linked_config).issues()


def test_record_limit_keeps_prepared_input(
    input_settings: InputSettings, runner_settings: RunnerSettings, input_payload: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("local_web.runner.MAX_RUN_RECORDS", 1)
    assert MAX_RUN_RECORDS == 16
    inputs, runs = InputStore(input_settings), RunnerStore(runner_settings)
    try:
        record = runs.start(inputs, prepared_authorization(inputs, input_payload), None)
        wait_for_finish(runs, record.owner_token)
        authorization = prepared_authorization(inputs, input_payload)
        with pytest.raises(RunnerError, match="RUN_RECORD_LIMIT"):
            runs.start(inputs, authorization, None)
        assert inputs.authenticate(*authorization[:2]).prepared is not None
    finally:
        runs.close()
        inputs.close()


def test_shutdown_escalates_only_owned_group(
    input_settings: InputSettings, runner_settings: RunnerSettings, input_payload: dict[str, object],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = tmp_path / "started"
    monkeypatch.setenv("LOCAL_WEB_TEST_RUNNER_CASE", "ignore_term")
    monkeypatch.setenv("LOCAL_WEB_TEST_RUNNER_RELEASE", str(tmp_path / "never-release"))
    monkeypatch.setenv("LOCAL_WEB_TEST_RUNNER_STARTED", str(started))
    monkeypatch.setattr("local_web.runner.STOP_GRACE_SECONDS", 0.1)
    inputs, runs = InputStore(input_settings), RunnerStore(runner_settings)
    try:
        record = runs.start(inputs, prepared_authorization(inputs, input_payload), None)
        deadline = time.monotonic() + 5
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.exists()
        runs.close()
        assert record.process.returncode == -9
        assert not record.worker.is_alive()
        assert runs.status(record.owner_token)["state"] == "stopped"
    finally:
        runs.close()
        inputs.close()


def test_worker_start_failure_does_not_leave_child_or_input(
    input_settings: InputSettings, runner_settings: RunnerSettings, input_payload: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs, runs = InputStore(input_settings), RunnerStore(runner_settings)
    try:
        authorization = prepared_authorization(inputs, input_payload)
        temporary_path = inputs.authenticate(*authorization[:2]).prepared.collector_config_path.parent

        def fail_thread_start(thread: object) -> None:
            raise RuntimeError("PASSWORD_SENTINEL")

        monkeypatch.setattr("local_web.runner.threading.Thread.start", fail_thread_start)
        with pytest.raises(RunnerError, match="RUNNER_START_FAILED"):
            runs.start(inputs, authorization, None)
        assert not temporary_path.exists() and runs._active is None
        assert all(record.process.poll() is not None for record in runs._records.values())
    finally:
        runs.close()
        inputs.close()
