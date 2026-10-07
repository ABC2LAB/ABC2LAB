import logging
import socket
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Browser, BrowserContext, sync_playwright

from modules.collector.core.auth import LoginError, open_account_context
from modules.collector.core.config import (
    GUEST_ACCOUNT,
    AccountSettings,
    CrawlerConfig,
    load_config,
    secret_key_names,
)
from modules.collector.tests.helpers import LOCALHOST, make_counting_handler, run_server

# 테스트 앱과 일부러 다른 경로·필드명을 써서 auth.py에 앱 전용 값이 없는지 확인한다.
LOGIN_PATH = "/signin"
HOME_PATH = "/home"
USERNAME_FIELD = "login_id"
PASSWORD_FIELD = "login_pw"
USERNAME = "alice"
PASSWORD = "s3cret-value-do-not-log"
ROLE = "user"
ALIAS = "user_a"
LOGIN_ID_KEY, PASSWORD_KEY = secret_key_names(ALIAS)
SESSION_COOKIE = "sid"
SESSION_VALUE = "session-token-1"
WELCOME_TEXT = "Welcome alice"
INVALID_TEXT = "invalid credentials"


@dataclass
class FakeSite:
    received_requests: list[str] = field(default_factory=list)
    form_action: str = ""
    extra_html: str = ""


def render_login_page(site: FakeSite, notice: str = "") -> str:
    return (
        f"<html><body><p>{notice}</p>"
        f'<form method="post" action="{site.form_action}">'
        f'<input name="{USERNAME_FIELD}">'
        f'<input type="password" name="{PASSWORD_FIELD}">'
        "<button>Sign in</button></form>"
        f"{site.extra_html}</body></html>"
    )


def make_site_handler(site: FakeSite) -> type[BaseHTTPRequestHandler]:
    class SiteHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            site.received_requests.append(f"{self.command} {self.path}")
            path = urlsplit(self.path).path
            if path == LOGIN_PATH:
                self._send_html(render_login_page(site))
            elif path == HOME_PATH and f"{SESSION_COOKIE}={SESSION_VALUE}" in self.headers.get("Cookie", ""):
                self._send_html(f"<html><body><h1>{WELCOME_TEXT}</h1></body></html>")
            elif path == HOME_PATH:
                self._redirect(LOGIN_PATH)
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            site.received_requests.append(f"{self.command} {self.path}")
            length = int(self.headers.get("Content-Length", "0"))
            form = parse_qs(self.rfile.read(length).decode())
            is_valid = form.get(USERNAME_FIELD) == [USERNAME] and form.get(PASSWORD_FIELD) == [PASSWORD]
            if is_valid:
                self._redirect(HOME_PATH, cookie=f"{SESSION_COOKIE}={SESSION_VALUE}; Path=/")
            else:
                self._send_html(render_login_page(site, notice=INVALID_TEXT))

        def _send_html(self, body: str) -> None:
            encoded = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _redirect(self, location: str, cookie: str | None = None) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    return SiteHandler


@dataclass
class OutsideServer:
    url: str
    requested_paths: list[str]


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        yield chromium
        chromium.close()


@pytest.fixture
def site() -> FakeSite:
    return FakeSite()


@pytest.fixture
def site_url(site: FakeSite) -> Iterator[str]:
    with run_server(make_site_handler(site)) as url:
        yield url


@pytest.fixture
def outside() -> Iterator[OutsideServer]:
    """허용 목록에 없는 origin. 요청이 한 번이라도 오면 가드가 뚫린 것."""
    requested_paths: list[str] = []
    with run_server(make_counting_handler(requested_paths)) as url:
        yield OutsideServer(url=url, requested_paths=requested_paths)


def make_config(
    site_url: str, login_overrides: dict[str, str] | None = None, secrets: dict[str, str] | None = None
) -> CrawlerConfig:
    login = {
        "path": LOGIN_PATH,
        "username_field": USERNAME_FIELD,
        "password_field": PASSWORD_FIELD,
        "success_check": "left_login_page",
        **(login_overrides or {}),
    }
    settings = {"target_url": site_url, "roles": [ROLE], "login": login, "accounts": [{"alias": ALIAS, "role": ROLE}]}
    return load_config(settings, {LOGIN_ID_KEY: USERNAME, PASSWORD_KEY: PASSWORD} if secrets is None else secrets)


def account_of(config: CrawlerConfig) -> AccountSettings:
    return next(account for account in config.accounts if account.alias == ALIAS)


@contextmanager
def open_context(browser: Browser, config: CrawlerConfig, account: AccountSettings) -> Iterator[BrowserContext]:
    context = open_account_context(browser, config, account)
    try:
        yield context
    finally:
        context.close()


def assert_password_hidden(error: LoginError, caplog: pytest.LogCaptureFixture) -> None:
    formatted = "".join(traceback.format_exception(error))
    assert PASSWORD not in formatted
    assert PASSWORD not in caplog.text


def test_guest_gets_empty_context_without_requests(browser: Browser, site: FakeSite, site_url: str) -> None:
    config = make_config(site_url)

    with open_context(browser, config, GUEST_ACCOUNT) as context:
        assert context.cookies() == []
    assert site.received_requests == []


@pytest.mark.parametrize(
    ("check", "value"),
    [
        ("left_login_page", ""),
        ("url_contains", HOME_PATH),
        ("cookie_present", SESSION_COOKIE),
        ("text_present", WELCOME_TEXT),
    ],
)
def test_login_succeeds_for_each_success_check(
    browser: Browser, site: FakeSite, site_url: str, check: str, value: str
) -> None:
    config = make_config(site_url, {"success_check": check, "success_value": value})

    with open_context(browser, config, account_of(config)) as context:
        cookie_names = {cookie["name"] for cookie in context.cookies(site_url)}
        assert SESSION_COOKIE in cookie_names
    assert f"POST {LOGIN_PATH}" in site.received_requests


def test_returned_context_stays_logged_in(browser: Browser, site_url: str) -> None:
    config = make_config(site_url)

    with open_context(browser, config, account_of(config)) as context:
        page = context.new_page()
        page.goto(f"{site_url}{HOME_PATH}")
        assert WELCOME_TEXT in page.locator("body").inner_text()


def test_wrong_password_raises_without_leaking_password(
    browser: Browser, site_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    wrong_password = f"{PASSWORD}-wrong"
    config = make_config(site_url, secrets={LOGIN_ID_KEY: USERNAME, PASSWORD_KEY: wrong_password})

    with pytest.raises(LoginError) as error_info:
        open_account_context(browser, config, account_of(config))

    assert error_info.value.alias == ALIAS
    assert ALIAS in str(error_info.value)
    assert USERNAME not in str(error_info.value)
    assert "left_login_page" in str(error_info.value)
    formatted = "".join(traceback.format_exception(error_info.value))
    assert wrong_password not in formatted
    assert wrong_password not in caplog.text


@pytest.mark.parametrize(
    ("check", "value"),
    [
        ("url_contains", "/dashboard"),
        ("cookie_present", "no_such_cookie"),
        ("text_present", "Hello bob"),
    ],
)
def test_unmet_success_check_raises(
    browser: Browser, site_url: str, caplog: pytest.LogCaptureFixture, check: str, value: str
) -> None:
    caplog.set_level(logging.DEBUG)
    config = make_config(site_url, {"success_check": check, "success_value": value})

    with pytest.raises(LoginError) as error_info:
        open_account_context(browser, config, account_of(config))

    assert check in str(error_info.value)
    assert_password_hidden(error_info.value, caplog)


@pytest.mark.parametrize("field_key", ["username_field", "password_field"])
def test_missing_form_field_raises(
    browser: Browser, site_url: str, caplog: pytest.LogCaptureFixture, field_key: str
) -> None:
    caplog.set_level(logging.DEBUG)
    config = make_config(site_url, {field_key: "no_such_field"})

    with pytest.raises(LoginError) as error_info:
        open_account_context(browser, config, account_of(config))

    assert "no_such_field" in str(error_info.value)
    assert_password_hidden(error_info.value, caplog)


def test_form_action_outside_allowed_origin_is_not_submitted(
    browser: Browser, site: FakeSite, site_url: str, outside: OutsideServer, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    site.form_action = f"{outside.url}{LOGIN_PATH}"
    config = make_config(site_url)

    with pytest.raises(LoginError) as error_info:
        open_account_context(browser, config, account_of(config))

    assert outside.requested_paths == []
    assert_password_hidden(error_info.value, caplog)


def test_requests_outside_allowed_origin_are_blocked(
    browser: Browser, site: FakeSite, site_url: str, outside: OutsideServer
) -> None:
    site.extra_html = f'<img src="{outside.url}/pixel.png">'
    config = make_config(site_url)

    with open_context(browser, config, account_of(config)):
        pass

    assert outside.requested_paths == []


def test_unknown_account_raises(browser: Browser, site_url: str) -> None:
    config = make_config(site_url)
    stranger = AccountSettings(alias="manager_a", role=ROLE, login_id=USERNAME, password=PASSWORD)

    with pytest.raises(LoginError) as error_info:
        open_account_context(browser, config, stranger)

    assert error_info.value.alias == "manager_a"


def test_account_without_secrets_fails_before_any_request(browser: Browser, site: FakeSite, site_url: str) -> None:
    config = make_config(site_url, secrets={LOGIN_ID_KEY: USERNAME})

    with pytest.raises(LoginError) as error_info:
        open_account_context(browser, config, account_of(config))

    # 어떤 값이 빠졌는지 키 이름으로 알린다. 값은 넣지 않는다.
    assert PASSWORD_KEY in str(error_info.value)
    assert USERNAME not in str(error_info.value)
    assert site.received_requests == []


def test_unreachable_server_raises(browser: Browser, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with socket.socket() as probe:
        probe.bind((LOCALHOST, 0))
        closed_port = probe.getsockname()[1]
    config = make_config(f"http://{LOCALHOST}:{closed_port}")

    with pytest.raises(LoginError) as error_info:
        open_account_context(browser, config, account_of(config))

    assert_password_hidden(error_info.value, caplog)
