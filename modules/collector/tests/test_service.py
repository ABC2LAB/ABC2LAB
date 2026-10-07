import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Browser, BrowserContext, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from modules.collector import service
from modules.collector.core.capture import RequestCapture
from modules.collector.core.config import (
    GUEST_ACCOUNT,
    GUEST_ROLE,
    AccountSettings,
    CrawlerConfig,
    load_config,
    secret_key_names,
)
from modules.collector.core.models import DiscoveredPage
from modules.collector.service import AccountCrawl, BrowserLaunchError, CollectOutcome, crawl_accounts
from modules.collector.tests.helpers import run_server
from modules.collector.tests.sites import (
    ADMIN_ALIAS,
    CLOCK_SITE_PATHS,
    CLOCK_STEP_AFTER,
    LOGIN_PATH,
    MINE_API_PREFIX,
    MINE_PATH,
    SESSION_COOKIE,
    SESSION_VALUE,
    SITE_USERS,
    USER_A_ALIAS,
    USER_B_ALIAS,
    USER_B_LOGIN_ID,
    USER_B_SESSION_VALUE,
    USER_LOGIN_ID,
    USER_PASSWORD,
    make_clock_site_handler,
    make_login_secrets,
    make_login_settings,
    make_login_site_handler,
)

SETTLE_MS = 300
SESSION_REF_PATTERN = re.compile(r"session:[0-9a-f]{16}")
CLOCK_WARNING_TEXT = "시계 역행"
ALIASES = [GUEST_ROLE, USER_A_ALIAS, USER_B_ALIAS, ADMIN_ALIAS]


@dataclass(frozen=True)
class LoginSiteRun:
    config: CrawlerConfig
    outcome: CollectOutcome
    # 서버가 받은 (경로, Cookie 헤더)
    cookie_log: list[tuple[str, str]]


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        yield chromium
        chromium.close()


@pytest.fixture(scope="module")
def site() -> Iterator[tuple[str, list[tuple[str, str]]]]:
    cookie_log: list[tuple[str, str]] = []
    with run_server(make_login_site_handler(cookie_log)) as url:
        yield url, cookie_log


@pytest.fixture(scope="module")
def config(site: tuple[str, list[tuple[str, str]]]) -> CrawlerConfig:
    return load_config(make_login_settings(site[0]), make_login_secrets())


@pytest.fixture(scope="module")
def site_run(browser: Browser, site: tuple[str, list[tuple[str, str]]], config: CrawlerConfig) -> LoginSiteRun:
    cookie_log = site[1]
    cookie_log.clear()
    outcome = crawl_accounts(browser, config)
    return LoginSiteRun(config, outcome, list(cookie_log))


def crawls_by_alias(outcome: CollectOutcome) -> dict[str, AccountCrawl]:
    return {account_crawl.alias: account_crawl for account_crawl in outcome.account_crawls}


def account_of(config: CrawlerConfig, alias: str) -> AccountSettings:
    return next(account for account in config.accounts if account.alias == alias)


def test_accounts_follow_config_and_failed_account_recorded(site_run: LoginSiteRun) -> None:
    crawls = crawls_by_alias(site_run.outcome)

    assert list(crawls) == ALIASES
    assert [(crawl.account_id, crawl.role) for crawl in crawls.values()] == [
        ("account:guest", GUEST_ROLE),
        ("account:user_a", "user"),
        ("account:user_b", "user"),
        ("account:admin_a", "admin"),
    ]
    assert [crawl.error is None for crawl in crawls.values()] == [True, True, True, False]
    assert "LoginError" in (crawls[ADMIN_ALIAS].error or "")
    assert crawls[ADMIN_ALIAS].pages == () and crawls[ADMIN_ALIAS].records == ()
    assert site_run.outcome.started_at <= site_run.outcome.finished_at


def test_logged_in_accounts_reach_private_page(site_run: LoginSiteRun) -> None:
    crawls = crawls_by_alias(site_run.outcome)

    def paths(alias: str) -> set[str]:
        return {urlsplit(page.url).path for page in crawls[alias].pages}

    assert MINE_PATH in paths(USER_A_ALIAS) and MINE_PATH in paths(USER_B_ALIAS)
    assert MINE_PATH not in paths(GUEST_ROLE)
    assert LOGIN_PATH in paths(GUEST_ROLE)


def test_records_and_pages_carry_their_own_account(site_run: LoginSiteRun) -> None:
    for crawl in site_run.outcome.account_crawls:
        assert {record.account_id for record in crawl.records} <= {crawl.account_id}
        assert {page.account_id for page in crawl.pages} <= {crawl.account_id}
        assert {record.role for record in crawl.records} <= {crawl.role}


def test_same_role_accounts_see_only_their_own_item(site_run: LoginSiteRun) -> None:
    """같은 역할이어도 계정마다 자기 화면의 자원만 요청한다. 다른 계정 자원을 일부러 부르지 않는다(규칙 5)."""
    crawls = crawls_by_alias(site_run.outcome)

    def api_paths(alias: str) -> set[str]:
        return {urlsplit(record.url).path for record in crawls[alias].records if MINE_API_PREFIX in record.url}

    assert api_paths(USER_A_ALIAS) == {SITE_USERS[USER_LOGIN_ID].api_path}
    assert api_paths(USER_B_ALIAS) == {SITE_USERS[USER_B_LOGIN_ID].api_path}


def test_session_ref_per_logged_in_context(site_run: LoginSiteRun) -> None:
    crawls = crawls_by_alias(site_run.outcome)
    user_refs = [crawls[USER_A_ALIAS].session_ref, crawls[USER_B_ALIAS].session_ref]

    assert crawls[GUEST_ROLE].session_ref is None
    # 로그인에 실패해 context를 못 연 계정은 세션이 없다.
    assert crawls[ADMIN_ALIAS].session_ref is None
    assert all(ref is not None and SESSION_REF_PATTERN.fullmatch(ref) for ref in user_refs)
    assert user_refs[0] != user_refs[1]
    # 불투명 참조여야 한다. 실제 쿠키 값에서 만들지 않는다.
    assert all(SESSION_VALUE not in ref and USER_B_SESSION_VALUE not in ref for ref in user_refs if ref)


def test_account_contexts_do_not_share_cookies(site_run: LoginSiteRun) -> None:
    sessions_per_request = [
        {value for value in (SESSION_VALUE, USER_B_SESSION_VALUE) if f"{SESSION_COOKIE}={value}" in cookie}
        for _, cookie in site_run.cookie_log
    ]

    # 두 세션 쿠키가 한 요청에 같이 실린 적이 없고, 둘 다 실제로 쓰였다.
    assert all(len(sessions) <= 1 for sessions in sessions_per_request)
    assert set().union(*sessions_per_request) == {SESSION_VALUE, USER_B_SESSION_VALUE}
    # guest가 시작 페이지를 연 첫 요청에는 세션 쿠키가 없다.
    assert sessions_per_request[0] == set()


def test_session_ref_differs_per_context(browser: Browser, config: CrawlerConfig) -> None:
    account = account_of(config, USER_A_ALIAS)

    first = service.crawl_account(browser, config, account)
    second = service.crawl_account(browser, config, account)

    assert first.session_ref is not None and second.session_ref is not None
    assert first.session_ref != second.session_ref


def test_account_missing_password_fails_alone(browser: Browser, site: tuple[str, list[tuple[str, str]]]) -> None:
    password_key = secret_key_names(USER_B_ALIAS)[1]
    secrets = make_login_secrets()
    del secrets[password_key]
    config = load_config(make_login_settings(site[0]), secrets)

    crawls = crawls_by_alias(crawl_accounts(browser, config))

    assert password_key in (crawls[USER_B_ALIAS].error or "")
    assert crawls[USER_B_ALIAS].records == () and crawls[USER_B_ALIAS].session_ref is None
    assert crawls[USER_A_ALIAS].error is None and len(crawls[USER_A_ALIAS].pages) > 0


def test_crawl_failure_keeps_requests_and_continues(
    browser: Browser, config: CrawlerConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_crawl = service.crawl

    def crawl_then_fail(
        context: BrowserContext, cfg: CrawlerConfig, account: AccountSettings, capture: RequestCapture
    ) -> list[DiscoveredPage]:
        if not account.is_guest:
            return real_crawl(context, cfg, account, capture)
        page = context.new_page()
        page.goto(cfg.start_url)
        page.wait_for_timeout(SETTLE_MS)
        raise RuntimeError(f"boom {USER_PASSWORD}")

    monkeypatch.setattr(service, "crawl", crawl_then_fail)
    crawls = crawls_by_alias(crawl_accounts(browser, config))
    guest = crawls[GUEST_ROLE]

    assert guest.error is not None and guest.error.startswith("RuntimeError: boom")
    assert USER_PASSWORD not in guest.error
    assert guest.pages == () and len(guest.records) > 0
    assert crawls[USER_A_ALIAS].error is None and len(crawls[USER_A_ALIAS].pages) > 0


@pytest.mark.parametrize(("step_after", "expected_regressions"), [(CLOCK_STEP_AFTER, 1), (None, 0)])
def test_server_clock_regression_counted_and_warned(
    browser: Browser, caplog: pytest.LogCaptureFixture, step_after: int | None, expected_regressions: int
) -> None:
    caplog.set_level(logging.WARNING, logger=service.__name__)
    with run_server(make_clock_site_handler(step_after)) as url:
        clock_outcome = crawl_accounts(browser, load_config({"target_url": f"{url}/"}))

    warnings = [record.getMessage() for record in caplog.records if CLOCK_WARNING_TEXT in record.getMessage()]
    assert len(clock_outcome.account_crawls[0].pages) == 1 + len(CLOCK_SITE_PATHS)
    assert clock_outcome.clock_regressions == expected_regressions
    # 계정별 경고 1줄 + 실행 전체 경고 1줄
    assert len(warnings) == expected_regressions * 2
    if expected_regressions:
        assert GUEST_ACCOUNT.alias in warnings[0] and "1회" in warnings[0]
        assert "다시 돌리" in warnings[-1]


def test_browser_launch_error_wrapped() -> None:
    def fail_launch() -> None:
        raise PlaywrightError("Executable doesn't exist at /tmp/chrome\n자세한 설치 안내 여러 줄")

    playwright = SimpleNamespace(chromium=SimpleNamespace(launch=fail_launch))

    with pytest.raises(BrowserLaunchError) as raised:
        service._launch_browser(playwright)

    assert "Executable doesn't exist" in str(raised.value)
    assert "설치 안내" not in str(raised.value)
