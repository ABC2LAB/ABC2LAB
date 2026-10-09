"""entrypoint.open_session_executor(): 런너가 collector 설정 타입 없이 세션 창구를 여는 공개 함수.

브라우저 없이 본다. 창구를 여는 session_gateway.open_session_executor를 대역으로 바꿔, 받은 설정으로 실제
BrowserSessionExecutor를 만들고 전송만 FakeBackend로 한다. 그래서 lease·send의 origin 검사는 진짜 창구 로직이 한다.
런너 쪽 약속을 고정한다: 실패는 collector 예외 클래스가 아니라 기본 타입(ValueError·RuntimeError)으로 잡힌다.
그래서 이 파일은 ConfigError·SessionTransportError를 import하지 않는다.
"""

import logging
import os
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from playwright.sync_api import Error as PlaywrightError

from modules.collector import entrypoint, session_gateway
from modules.collector.session_gateway import BrowserSessionExecutor
from modules.collector.tests.helpers import FakeBackend
from modules.collector.tests.sites import to_toml, write_config_files

TARGET_URL = "http://localhost:18001"
OTHER_ORIGIN_URL = "http://localhost:18002"
ACCOUNT_ID = "account:user_a"
LOGIN_ID_VALUE = "leak-check-login-7f3a"
PASSWORD_VALUE = "leak-check-pw-9c1e"
# 설정 위치 규칙(README·collect와 같음). 코드 상수가 아니라 문서의 값으로 적는다.
CONFIG_PATH_ENV = "COLLECTOR_CONFIG_PATH"
SECRETS_PATH_ENV = "COLLECTOR_SECRETS_PATH"
DEFAULT_CONFIG_RELATIVE = Path("modules") / "collector" / "configs" / "collector.toml"
DEFAULT_SECRETS_RELATIVE = Path(".env")


@dataclass(frozen=True)
class _Request:
    """send가 받는 요청의 최소 모양(verifier ReplayRequest를 import하지 않는다)."""

    method: str
    url: str


@dataclass
class FakeGateway:
    """브라우저 대신 쓰는 창구 열기. 호출·진입 횟수와 계정별 백엔드를 기록한다."""

    calls: int = 0
    entered: int = 0
    backends_by_account: dict[str, FakeBackend] = field(default_factory=dict)

    def open(self, config: Any) -> AbstractContextManager[BrowserSessionExecutor]:
        self.calls += 1
        return self._session(config)

    @contextmanager
    def _session(self, config: Any) -> Iterator[BrowserSessionExecutor]:
        self.entered += 1
        executor = BrowserSessionExecutor(config, self._make_backend)
        try:
            yield executor
        finally:
            executor.close()

    def _make_backend(self, account_id: str) -> FakeBackend:
        backend = FakeBackend()
        self.backends_by_account[account_id] = backend
        return backend


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """셸의 COLLECTOR_*가 테스트 설정보다 우선하므로 치운다. 기본 경로는 빈 tmp 기준이 되게 한다."""
    for key in [key for key in os.environ if key.startswith("COLLECTOR_")]:
        monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> FakeGateway:
    fake = FakeGateway()
    monkeypatch.setattr(session_gateway, "open_session_executor", fake.open)
    return fake


def _settings(target_url: str = TARGET_URL) -> dict[str, Any]:
    return {
        "target_url": target_url,
        "roles": ["user"],
        "login": {
            "path": "/login",
            "username_field": "email",
            "password_field": "password",
            "success_check": "left_login_page",
        },
        "accounts": [{"alias": "user_a", "role": "user"}],
    }


def _settings_without_login() -> dict[str, Any]:
    return {key: value for key, value in _settings().items() if key != "login"}


def _secrets(login_id: str = LOGIN_ID_VALUE) -> dict[str, str]:
    return {"COLLECTOR_ACCOUNT_USER_A_LOGIN_ID": login_id, "COLLECTOR_ACCOUNT_USER_A_PASSWORD": PASSWORD_VALUE}


def _use_env_paths(
    monkeypatch: pytest.MonkeyPatch, directory: Path, settings: dict[str, Any] | None, secrets: dict[str, str]
) -> Path:
    config_path, secrets_path = write_config_files(directory, "custom", settings, secrets)
    monkeypatch.setenv(CONFIG_PATH_ENV, str(config_path))
    monkeypatch.setenv(SECRETS_PATH_ENV, str(secrets_path))
    return config_path


def _write_default_files(directory: Path, settings: dict[str, Any], secrets: dict[str, str]) -> None:
    config_path = directory / DEFAULT_CONFIG_RELATIVE
    config_path.parent.mkdir(parents=True)
    config_path.write_text(to_toml(settings), encoding="utf-8")
    (directory / DEFAULT_SECRETS_RELATIVE).write_text(
        "".join(f"{key}={value}\n" for key, value in secrets.items()), encoding="utf-8"
    )


def _sent_urls(gateway: FakeGateway) -> list[str]:
    return [url for _, url, _, _ in gateway.backends_by_account[ACCOUNT_ID].requests]


# ---- 설정 위치: collect와 같은 규칙 ----


def test_config_comes_from_env_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gateway: FakeGateway) -> None:
    # 기본 경로(tmp 기준)에는 설정이 없다. env 경로를 안 따르면 설정 파일 없음으로 실패한다.
    _use_env_paths(monkeypatch, tmp_path, _settings(), _secrets())

    with entrypoint.open_session_executor() as executor:
        lease = executor.lease(ACCOUNT_ID)
        response = lease.send(_Request("GET", f"{TARGET_URL}/orders/7"))
        # 그 설정의 대상 origin만 허용된다: 다른 포트는 보내지 않는다.
        with pytest.raises(RuntimeError):
            lease.send(_Request("GET", f"{OTHER_ORIGIN_URL}/orders/7"))

    assert response.status_code == 200
    assert _sent_urls(gateway) == [f"{TARGET_URL}/orders/7"]


def test_config_comes_from_default_path_when_env_unset(tmp_path: Path, gateway: FakeGateway) -> None:
    _write_default_files(tmp_path, _settings(), _secrets())

    with entrypoint.open_session_executor() as executor:
        executor.lease(ACCOUNT_ID).send(_Request("GET", f"{TARGET_URL}/orders/7"))

    assert _sent_urls(gateway) == [f"{TARGET_URL}/orders/7"]


# 계정 값 파일을 실제로 읽는지는 별칭==로그인 ID 거절로 드러난다(읽지 않으면 거절할 근거가 없다).
def test_account_secrets_come_from_env_secrets_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gateway: FakeGateway
) -> None:
    _use_env_paths(monkeypatch, tmp_path, _settings(), _secrets(login_id="user_a"))

    with pytest.raises(ValueError, match="alias"):
        entrypoint.open_session_executor()
    assert gateway.calls == 0


def test_account_secrets_come_from_default_env_file_when_env_unset(tmp_path: Path, gateway: FakeGateway) -> None:
    _write_default_files(tmp_path, _settings(), _secrets(login_id="user_a"))

    with pytest.raises(ValueError, match="alias"):
        entrypoint.open_session_executor()
    assert gateway.calls == 0


# ---- 수명: 브라우저는 with 안에서만 ----


def test_browser_starts_only_when_with_block_is_entered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gateway: FakeGateway
) -> None:
    _use_env_paths(monkeypatch, tmp_path, _settings(), _secrets())

    session = entrypoint.open_session_executor()
    assert gateway.entered == 0
    with session:
        assert gateway.entered == 1


# ---- 실패: 기본 타입으로 잡히고 값은 새지 않는다 ----


def test_missing_config_raises_value_error_before_browser(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gateway: FakeGateway
) -> None:
    _use_env_paths(monkeypatch, tmp_path, None, _secrets())

    with pytest.raises(ValueError, match="설정 파일이 없음"):
        entrypoint.open_session_executor()
    assert gateway.calls == 0


def _break_toml(config_path: Path) -> None:
    config_path.write_text('target_url = "http://localhost:18001\nroles = [', encoding="utf-8")


def _no_change(config_path: Path) -> None:
    pass


@pytest.mark.parametrize(
    ("settings", "edit_config", "expected_in_message"),
    [
        pytest.param(_settings(), _break_toml, "custom.toml", id="broken_toml"),
        pytest.param(_settings_without_login(), _no_change, "login", id="accounts_without_login"),
        pytest.param({**_settings(), "unknown_key": 1}, _no_change, "unknown_key", id="undefined_key"),
    ],
)
def test_invalid_config_raises_value_error_without_secret_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    gateway: FakeGateway,
    settings: dict[str, Any],
    edit_config: Callable[[Path], None],
    expected_in_message: str,
) -> None:
    config_path = _use_env_paths(monkeypatch, tmp_path, settings, _secrets())
    edit_config(config_path)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(ValueError) as caught:
        entrypoint.open_session_executor()

    message = str(caught.value)
    assert expected_in_message in message
    for secret in (LOGIN_ID_VALUE, PASSWORD_VALUE):
        assert secret not in message
        assert secret not in caplog.text
    assert gateway.calls == 0


def test_browser_launch_failure_raises_runtime_error_on_enter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # 실제 창구 열기를 쓰고, 그 아래 Playwright 경계만 브라우저가 없는 것처럼 바꾼다.
    @contextmanager
    def playwright_without_browser() -> Iterator[SimpleNamespace]:
        def launch() -> None:
            raise PlaywrightError("Executable doesn't exist at /missing/chrome")

        yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))

    monkeypatch.setattr(session_gateway, "sync_playwright", playwright_without_browser)
    _use_env_paths(monkeypatch, tmp_path, _settings(), _secrets())
    caplog.set_level(logging.DEBUG)

    session = entrypoint.open_session_executor()
    with pytest.raises(RuntimeError, match="브라우저를 띄우지 못함"):
        with session:
            pass

    for secret in (LOGIN_ID_VALUE, PASSWORD_VALUE):
        assert secret not in caplog.text
