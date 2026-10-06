"""entrypoint.run과 CLI를 가짜 로컬 사이트로 끝까지 돌려 본다.

실제 브라우저로 도는 경우: completed(guest만) / partial(admin 로그인 실패) / failed(서버 시계 역행) / CLI.
service를 대역으로 바꾸는 경우: 설정·브라우저 실패, 호출 오류, 재공개·경로·context 거절, 자기 출력 검증 실패.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from modules.collector import entrypoint, service
from modules.collector.core.config import GUEST_ROLE
from modules.collector.entrypoint import CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH, make_run_id, resolve_config_path, run
from modules.collector.service import BrowserLaunchError, CollectOutcome, RoleCrawl
from modules.collector.tests.helpers import run_server
from modules.collector.tests.sites import (
    ADMIN_LOGIN_ID,
    ADMIN_PASSWORD,
    CLOCK_STEP_AFTER,
    MINE_API_PATH,
    MINE_PATH,
    QUERY_TOKEN,
    SESSION_VALUE,
    USER_LOGIN_ID,
    USER_PASSWORD,
    make_clock_site_handler,
    make_login_env,
    make_login_site_handler,
)
from modules.collector.utils import export
from modules.collector.utils.validation import validate_crawl_result_file

ENV_PREFIX = "CRAWLER_"
ARTIFACT_RELATIVE_DIR = Path("artifacts") / "iteration-000" / "collector"
ARTIFACT_FILE_NAME = "crawl_result.json"
SESSION_REF_PATTERN = re.compile(r"session:[0-9a-f]{16}")
RUN_ID_PATTERN = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{4}")
RUN_ID_SAMPLES = 20
REPO_ROOT = Path(__file__).resolve().parents[3]
CLI_TIMEOUT_S = 120
SECRETS_IN_SITE = (USER_PASSWORD, ADMIN_PASSWORD, SESSION_VALUE, QUERY_TOKEN, USER_LOGIN_ID, ADMIN_LOGIN_ID)
BASE_TIME = datetime(2026, 10, 1, 5, 12, 3, tzinfo=UTC)
RESULT_KEYS = {"status", "artifact_path", "artifact_id", "sha256", "errors"}


@dataclass(frozen=True)
class Call:
    """run()에 넘길 값 한 벌. run_root는 tmp 아래 runs/<run_id>."""

    run_root: Path
    output_dir: Path
    context: dict[str, Any]
    config_path: Path

    @property
    def artifact_path(self) -> Path:
        return self.output_dir / ARTIFACT_FILE_NAME

    def run(self, operation: str = "collect", input_paths: list[str] | None = None) -> dict[str, Any]:
        """설정 파일 위치는 context가 아니라 환경변수로 넘긴다(호출하는 동안만)."""
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv(CONFIG_PATH_ENV, str(self.config_path))
            return run(operation, input_paths or [], self.output_dir, self.context)

    def load(self) -> dict[str, Any]:
        return json.loads(self.artifact_path.read_text(encoding="utf-8"))


def make_call(base_dir: Path, run_id: str, env: dict[str, str] | None) -> Call:
    """env가 None이면 없는 설정 파일을 가리킨다."""
    run_root = base_dir / "runs" / run_id
    config_path = base_dir / f"{run_id}.env"
    if env is not None:
        config_path.write_text("\n".join(f"{key}={value}" for key, value in env.items()), encoding="utf-8")
    context = {"run_id": run_id, "iteration": 0, "mode": "development", "run_root": str(run_root)}
    return Call(run_root, run_root / ARTIFACT_RELATIVE_DIR, context, config_path)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """셸에 CRAWLER_*가 있으면 설정 파일보다 우선하므로 테스트 동안 치운다. 설정 파일 위치도 테스트가 정한다."""
    for key in [key for key in os.environ if key.startswith(ENV_PREFIX)]:
        monkeypatch.delenv(key)
    monkeypatch.delenv(CONFIG_PATH_ENV, raising=False)


@pytest.fixture(scope="module")
def site_url() -> Iterator[str]:
    with run_server(make_login_site_handler()) as url:
        yield url


@dataclass(frozen=True)
class FinishedRun:
    call: Call
    result: dict[str, Any]
    document: dict[str, Any]
    raw: bytes


def _run_module_scenario(base_dir: Path, run_id: str, env: dict[str, str]) -> FinishedRun:
    with pytest.MonkeyPatch.context() as patch:
        for key in [key for key in os.environ if key.startswith(ENV_PREFIX)]:
            patch.delenv(key)
        patch.delenv(CONFIG_PATH_ENV, raising=False)
        call = make_call(base_dir, run_id, env)
        result = call.run()
    return FinishedRun(call, result, call.load(), call.artifact_path.read_bytes())


@pytest.fixture(scope="module")
def partial_run(site_url: str, tmp_path_factory: pytest.TempPathFactory) -> FinishedRun:
    return _run_module_scenario(tmp_path_factory.mktemp("partial"), "run_partial", make_login_env(site_url))


@pytest.fixture(scope="module")
def guest_run(site_url: str, tmp_path_factory: pytest.TempPathFactory) -> FinishedRun:
    return _run_module_scenario(tmp_path_factory.mktemp("guest"), "run_guest", {"CRAWLER_TARGET_URL": f"{site_url}/"})


def fake_outcome(*role_crawls: RoleCrawl) -> CollectOutcome:
    return CollectOutcome(BASE_TIME, BASE_TIME, role_crawls)


def error_codes(result: dict[str, Any]) -> list[str]:
    return [error["code"] for error in result["errors"]]


# ---- 실제 브라우저 ----


def test_guest_only_run_completed(guest_run: FinishedRun) -> None:
    result = guest_run.result

    assert set(result) == RESULT_KEYS
    assert (result["status"], result["errors"]) == ("completed", [])
    assert result["artifact_path"] == str(guest_run.call.artifact_path.resolve())
    assert result["sha256"] == hashlib.sha256(guest_run.raw).hexdigest()
    assert result["artifact_id"] == guest_run.document["artifact_id"]
    assert guest_run.document["status"] == "completed"
    assert validate_crawl_result_file(guest_run.call.artifact_path, guest_run.call.run_root) == []


def test_partial_when_one_account_cannot_log_in(partial_run: FinishedRun) -> None:
    document = partial_run.document

    assert partial_run.result["status"] == document["status"] == "partial"
    assert document["errors"] == partial_run.result["errors"]
    assert [(error["code"], error["item_ref"], error["retryable"]) for error in document["errors"]] == [
        ("ACCOUNT_CRAWL_FAILED", "account:admin", True)
    ]
    assert validate_crawl_result_file(partial_run.call.artifact_path, partial_run.call.run_root, [USER_PASSWORD]) == []


def test_requests_match_account_role_and_session(partial_run: FinishedRun) -> None:
    data = partial_run.document["data"]
    accounts = {account["account_id"]: account for account in data["accounts"]}

    assert list(accounts) == ["account:guest", "account:user", "account:admin"]
    assert accounts["account:guest"]["session_ref"] is None
    assert accounts["account:admin"]["session_ref"] is None
    assert SESSION_REF_PATTERN.fullmatch(accounts["account:user"]["session_ref"])
    assert {request["account_id"] for request in data["requests"]} == {"account:guest", "account:user"}
    for request in data["requests"]:
        account = accounts[request["account_id"]]
        assert (request["role_id"], request["session_ref"]) == (account["role_id"], account["session_ref"])


def test_api_request_linked_and_parameters_mapped(partial_run: FinishedRun) -> None:
    data = partial_run.document["data"]
    mine_page = next(page for page in data["pages"] if page["url"].endswith(MINE_PATH))
    api_request = next(request for request in data["requests"] if MINE_API_PATH in request["url"])
    parameters = {(item["name"], item["location"]): item for item in api_request["parameters"]}
    headers = {header["name"]: header for header in api_request["headers"]}

    assert (api_request["account_id"], api_request["page_id"], api_request["action_id"]) == (
        "account:user",
        mine_page["page_id"],
        None,
    )
    assert parameters[("path:2", "path")]["value"] == "7"
    assert parameters[("page", "query")]["value"] == "1"
    assert (parameters[("access_token", "query")]["value"], parameters[("access_token", "query")]["is_sensitive"]) == (
        None,
        True,
    )
    assert parameters[("sid", "cookie")]["is_sensitive"] is True
    assert headers["cookie"] == {"name": "cookie", "value": "[REDACTED]", "redacted": True}
    assert api_request["response"]["content_type"] == "application/json"
    assert api_request["response"]["body_ref"] is None


def test_output_has_no_secrets(partial_run: FinishedRun) -> None:
    text = partial_run.raw.decode("utf-8")

    assert [secret for secret in SECRETS_IN_SITE if secret in text] == []


def test_clock_regression_published_as_failed_without_data(tmp_path: Path) -> None:
    with run_server(make_clock_site_handler(CLOCK_STEP_AFTER)) as url:
        call = make_call(tmp_path, "run_clock", {"CRAWLER_TARGET_URL": f"{url}/"})
        result = call.run()
    document = call.load()

    assert result["status"] == document["status"] == "failed"
    assert document["data"] is None
    assert [(error["code"], error["retryable"]) for error in document["errors"]] == [("SERVER_CLOCK_REGRESSION", True)]
    assert validate_crawl_result_file(call.artifact_path, call.run_root) == []


# ---- service 대역 ----


def test_config_error_published_as_failed(tmp_path: Path) -> None:
    call = make_call(tmp_path, "run_config", None)

    result = call.run()

    assert (result["status"], error_codes(result)) == ("failed", ["CONFIG_INVALID"])
    assert result["errors"][0]["retryable"] is False
    assert call.load()["data"] is None
    assert validate_crawl_result_file(call.artifact_path, call.run_root) == []


def test_browser_launch_failure_published_as_failed(
    site_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_launch(playwright: object) -> None:
        raise BrowserLaunchError("브라우저를 띄우지 못함: Executable doesn't exist")

    monkeypatch.setattr(service, "_launch_browser", fail_launch)
    call = make_call(tmp_path, "run_browser", make_login_env(site_url))

    result = call.run()

    assert (result["status"], error_codes(result)) == ("failed", ["BROWSER_LAUNCH_FAILED"])
    assert result["errors"][0]["retryable"] is True
    assert call.load()["data"] is None


def _forbid_collect(monkeypatch: pytest.MonkeyPatch) -> None:
    def collect_must_not_run(config: object) -> None:
        raise AssertionError("탐색하면 안 되는 호출")

    monkeypatch.setattr(service, "collect", collect_must_not_run)


def test_unsupported_operation_and_inputs_fail_without_crawling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_collect(monkeypatch)
    call = make_call(tmp_path, "run_operation", None)

    result = call.run(operation="analyze", input_paths=["crawl_result.json"])

    assert (result["status"], error_codes(result)) == ("failed", ["OPERATION_UNSUPPORTED", "INPUT_UNEXPECTED"])
    assert call.load()["data"] is None


def test_all_accounts_failed_without_observations_is_failed(
    site_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed_guest = RoleCrawl(GUEST_ROLE, (), (), "TimeoutError: 대상 응답 없음", None)
    monkeypatch.setattr(service, "collect", lambda config: fake_outcome(failed_guest))
    call = make_call(tmp_path, "run_all_failed", {"CRAWLER_TARGET_URL": f"{site_url}/"})

    result = call.run()

    assert (result["status"], error_codes(result)) == ("failed", ["ACCOUNT_CRAWL_FAILED"])
    assert call.load()["data"] is None


def test_republish_refused_and_file_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    call = make_call(tmp_path, "run_again", None)
    first = call.run()
    first_bytes = call.artifact_path.read_bytes()
    _forbid_collect(monkeypatch)

    second = call.run()

    assert first["artifact_path"] is not None
    assert (second["status"], second["artifact_path"], error_codes(second)) == ("failed", None, ["ARTIFACT_EXISTS"])
    assert call.artifact_path.read_bytes() == first_bytes


def test_output_dir_outside_run_root_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_collect(monkeypatch)
    call = make_call(tmp_path, "run_outside", None)
    outside = tmp_path / "elsewhere" / ARTIFACT_RELATIVE_DIR

    result = run("collect", [], outside, call.context)

    assert (result["artifact_path"], error_codes(result)) == (None, ["OUTPUT_PATH_INVALID"])
    assert not outside.exists() and not call.run_root.exists()


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"iteration": -1}, id="negative_iteration"),
        pytest.param({"iteration": True}, id="bool_iteration"),
        pytest.param({"mode": "production"}, id="unknown_mode"),
        pytest.param({"run_id": "../escape"}, id="run_id_with_parent"),
        pytest.param({"run_id": "other_run"}, id="run_root_name_differs"),
        pytest.param({"extra": 1}, id="undefined_key"),
        # 설정 파일 위치는 명세 실행 값이 아니라 context로 받지 않는다.
        pytest.param({"config_path": ".env"}, id="config_path_not_a_context_key"),
        pytest.param({"mode": None}, id="mode_null"),
    ],
)
def test_invalid_context_rejected_without_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: dict[str, Any]
) -> None:
    _forbid_collect(monkeypatch)
    call = make_call(tmp_path, "run_context", None)

    result = run("collect", [], call.output_dir, call.context | change)

    assert (result["artifact_path"], error_codes(result)) == (None, ["CONTEXT_INVALID"])
    assert not call.run_root.exists()


def test_missing_context_key_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_collect(monkeypatch)
    call = make_call(tmp_path, "run_missing", None)
    context = {key: value for key, value in call.context.items() if key != "mode"}

    result = run("collect", [], call.output_dir, context)

    assert error_codes(result) == ["CONTEXT_INVALID"]


def test_contract_violation_not_published_as_completed(
    site_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_build_data = export.build_data
    monkeypatch.setattr(service, "collect", lambda config: fake_outcome(RoleCrawl(GUEST_ROLE, (), (), None, None)))
    monkeypatch.setattr(export, "build_data", lambda config, outcome: real_build_data(config, outcome) | {"extra": 1})
    call = make_call(tmp_path, "run_contract", {"CRAWLER_TARGET_URL": f"{site_url}/"})

    result = call.run()

    assert result["status"] == "failed"
    assert set(error_codes(result)) == {"OUTPUT_CONTRACT_INVALID"}
    assert call.load()["data"] is None
    assert validate_crawl_result_file(call.artifact_path, call.run_root) == []


def test_secret_in_output_not_published(site_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """키 이름으로 못 잡은 곳에 비밀번호가 섞여도 공개 전 검사에서 데이터째 막는다."""
    leaking = RoleCrawl("user", (), (), f"LoginError: {USER_PASSWORD}", None)
    monkeypatch.setattr(service, "collect", lambda config: fake_outcome(leaking))
    call = make_call(tmp_path, "run_leak", make_login_env(site_url))

    result = call.run()

    assert result["status"] == "failed"
    assert set(error_codes(result)) == {"OUTPUT_CONTRACT_INVALID"}
    assert USER_PASSWORD not in call.artifact_path.read_text(encoding="utf-8")


# ---- CLI ----


def _run_cli(cwd: Path, *args: str, config_env: str | None = None) -> subprocess.CompletedProcess[str]:
    process_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(ENV_PREFIX) and key != CONFIG_PATH_ENV
    }
    if config_env is not None:
        process_env[CONFIG_PATH_ENV] = config_env
    process_env["PYTHONPATH"] = str(REPO_ROOT)
    process_env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "modules.collector.entrypoint", *args],
        cwd=cwd,
        env=process_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=CLI_TIMEOUT_S,
        check=False,
    )


def test_cli_exit_codes_and_single_json_line(site_url: str, tmp_path: Path) -> None:
    env_lines = [f"{key}={value}" for key, value in make_login_env(site_url).items()]
    (tmp_path / ".env").write_text("\n".join(env_lines), encoding="utf-8")
    cli_args = ("collect", "--mode", "development", "--run-id", "run_cli")

    partial = _run_cli(tmp_path, *cli_args)
    repeated = _run_cli(tmp_path, *cli_args)
    missing_config = _run_cli(tmp_path, *cli_args[:-1], "run_cli_config", "--config", "missing.env")

    results = [json.loads(process.stdout) for process in (partial, repeated, missing_config)]
    assert [process.returncode for process in (partial, repeated, missing_config)] == [1, 3, 2]
    assert [len(process.stdout.splitlines()) for process in (partial, repeated, missing_config)] == [1, 1, 1]
    assert [result["status"] for result in results] == ["partial", "failed", "failed"]
    assert (tmp_path / "runs" / "run_cli" / ARTIFACT_RELATIVE_DIR / ARTIFACT_FILE_NAME).is_file()
    assert results[1]["artifact_path"] is None
    combined_output = "".join(process.stdout + process.stderr for process in (partial, repeated, missing_config))
    assert USER_PASSWORD not in combined_output and ADMIN_PASSWORD not in combined_output
    # runs/는 직접 열지 않으므로 stderr 요약 로그로 건수를 확인할 수 있어야 한다.
    assert "계정 3·페이지" in partial.stderr


def test_cli_config_precedence(tmp_path: Path) -> None:
    """--config > COLLECTOR_CONFIG_PATH > 기본 .env. 없는 파일을 가리키면 CONFIG_INVALID로 어느 쪽을 읽었는지 안다."""
    (tmp_path / ".env").write_text("CRAWLER_TARGET_URL=http://127.0.0.1:1/", encoding="utf-8")
    valid_config = tmp_path / ".env"

    env_wins_over_default = _run_cli(
        tmp_path, "collect", "--mode", "development", "--run-id", "run_env", config_env="missing-by-env.env"
    )
    flag_wins_over_env = _run_cli(
        tmp_path,
        "collect",
        "--mode",
        "development",
        "--run-id",
        "run_flag",
        "--config",
        "missing-by-flag.env",
        config_env=str(valid_config),
    )

    assert [process.returncode for process in (env_wins_over_default, flag_wins_over_env)] == [2, 2]
    for process in (env_wins_over_default, flag_wins_over_env):
        assert [error["code"] for error in json.loads(process.stdout)["errors"]] == ["CONFIG_INVALID"]


def test_config_path_resolved_from_env_or_default(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_config_path() == DEFAULT_CONFIG_PATH
    monkeypatch.setenv(CONFIG_PATH_ENV, "  ")
    assert resolve_config_path() == DEFAULT_CONFIG_PATH
    monkeypatch.setenv(CONFIG_PATH_ENV, "configs/collector.env")
    assert resolve_config_path() == Path("configs/collector.env")


def test_run_id_format() -> None:
    run_id = make_run_id(BASE_TIME)

    assert RUN_ID_PATTERN.fullmatch(run_id)
    assert run_id.startswith("20261001-051203-")
    # 같은 초에 여러 번 돌려도 난수 부분으로 갈린다.
    assert len({make_run_id(BASE_TIME) for _ in range(RUN_ID_SAMPLES)}) > 1


def test_exit_code_by_result() -> None:
    def result_of(status: str, artifact_path: str | None) -> dict[str, Any]:
        return {"status": status, "artifact_path": artifact_path}

    assert entrypoint._exit_code(result_of("completed", "a")) == 0
    assert entrypoint._exit_code(result_of("partial", "a")) == 1
    assert entrypoint._exit_code(result_of("failed", "a")) == 2
    assert entrypoint._exit_code(result_of("failed", None)) == 3
