import logging
import re
from collections.abc import Iterator
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Browser, BrowserContext, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from modules.collector import service
from modules.collector.core.capture import RequestCapture
from modules.collector.core.config import GUEST_ROLE, CrawlerConfig, load_config
from modules.collector.core.models import DiscoveredPage
from modules.collector.service import BrowserLaunchError, CollectOutcome, crawl_roles
from modules.collector.tests.helpers import run_server
from modules.collector.tests.sites import (
    CLOCK_SITE_PATHS,
    CLOCK_STEP_AFTER,
    LOGIN_PATH,
    MINE_PATH,
    SESSION_VALUE,
    USER_PASSWORD,
    make_clock_site_handler,
    make_login_env,
    make_login_site_handler,
)

SETTLE_MS = 300
SESSION_REF_PATTERN = re.compile(r"session:[0-9a-f]{16}")
CLOCK_WARNING_TEXT = "시계 역행"


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        yield chromium
        chromium.close()


@pytest.fixture(scope="module")
def site_url() -> Iterator[str]:
    with run_server(make_login_site_handler()) as url:
        yield url


@pytest.fixture(scope="module")
def config(site_url: str) -> CrawlerConfig:
    return load_config(make_login_env(site_url))


@pytest.fixture(scope="module")
def outcome(browser: Browser, config: CrawlerConfig) -> CollectOutcome:
    return crawl_roles(browser, config)


def test_roles_follow_config_and_failed_role_recorded(outcome: CollectOutcome, config: CrawlerConfig) -> None:
    crawls = {role_crawl.role: role_crawl for role_crawl in outcome.role_crawls}
    roles = [role_crawl.role for role_crawl in outcome.role_crawls]

    assert roles == list(config.roles) == [GUEST_ROLE, "user", "admin"]
    assert crawls[GUEST_ROLE].error is None
    assert crawls["user"].error is None
    assert crawls["admin"].error is not None and "LoginError" in crawls["admin"].error
    assert crawls["admin"].pages == () and crawls["admin"].records == ()
    assert outcome.started_at <= outcome.finished_at


def test_logged_in_role_reaches_private_page(outcome: CollectOutcome) -> None:
    crawls = {role_crawl.role: role_crawl for role_crawl in outcome.role_crawls}
    user_paths = {urlsplit(page.url).path for page in crawls["user"].pages}
    guest_paths = {urlsplit(page.url).path for page in crawls[GUEST_ROLE].pages}

    assert MINE_PATH in user_paths
    assert MINE_PATH not in guest_paths
    assert LOGIN_PATH in guest_paths


def test_session_ref_only_for_logged_in_context(outcome: CollectOutcome) -> None:
    crawls = {role_crawl.role: role_crawl for role_crawl in outcome.role_crawls}
    user_ref = crawls["user"].session_ref

    assert crawls[GUEST_ROLE].session_ref is None
    # 로그인에 실패해 context를 못 연 계정은 세션이 없다.
    assert crawls["admin"].session_ref is None
    assert user_ref is not None and SESSION_REF_PATTERN.fullmatch(user_ref)
    # 불투명 참조여야 한다. 실제 쿠키 값에서 만들지 않는다.
    assert SESSION_VALUE not in user_ref


def test_session_ref_differs_per_context(browser: Browser, config: CrawlerConfig) -> None:
    first = service.crawl_role(browser, config, "user")
    second = service.crawl_role(browser, config, "user")

    assert first.session_ref is not None and second.session_ref is not None
    assert first.session_ref != second.session_ref


def test_crawl_failure_keeps_requests_and_continues(
    browser: Browser, config: CrawlerConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_crawl = service.crawl

    def crawl_then_fail(
        context: BrowserContext, cfg: CrawlerConfig, role: str, capture: RequestCapture
    ) -> list[DiscoveredPage]:
        if role != GUEST_ROLE:
            return real_crawl(context, cfg, role, capture)
        page = context.new_page()
        page.goto(cfg.start_url)
        page.wait_for_timeout(SETTLE_MS)
        raise RuntimeError(f"boom {USER_PASSWORD}")

    monkeypatch.setattr(service, "crawl", crawl_then_fail)
    guest, user, _ = crawl_roles(browser, config).role_crawls

    assert guest.error is not None and guest.error.startswith("RuntimeError: boom")
    assert USER_PASSWORD not in guest.error
    assert guest.pages == () and len(guest.records) > 0
    assert user.error is None and len(user.pages) > 0


@pytest.mark.parametrize(("step_after", "expected_regressions"), [(CLOCK_STEP_AFTER, 1), (None, 0)])
def test_server_clock_regression_counted_and_warned(
    browser: Browser, caplog: pytest.LogCaptureFixture, step_after: int | None, expected_regressions: int
) -> None:
    caplog.set_level(logging.WARNING, logger=service.__name__)
    with run_server(make_clock_site_handler(step_after)) as url:
        clock_outcome = crawl_roles(browser, load_config({"CRAWLER_TARGET_URL": f"{url}/"}))

    warnings = [record.getMessage() for record in caplog.records if CLOCK_WARNING_TEXT in record.getMessage()]
    assert len(clock_outcome.role_crawls[0].pages) == 1 + len(CLOCK_SITE_PATHS)
    assert clock_outcome.clock_regressions == expected_regressions
    # 역할별 경고 1줄 + 실행 전체 경고 1줄
    assert len(warnings) == expected_regressions * 2
    if expected_regressions:
        assert "guest" in warnings[0] and "1회" in warnings[0]
        assert "다시 돌리" in warnings[-1]


def test_browser_launch_error_wrapped() -> None:
    def fail_launch() -> None:
        raise PlaywrightError("Executable doesn't exist at /tmp/chrome\n자세한 설치 안내 여러 줄")

    playwright = SimpleNamespace(chromium=SimpleNamespace(launch=fail_launch))

    with pytest.raises(BrowserLaunchError) as raised:
        service._launch_browser(playwright)

    assert "Executable doesn't exist" in str(raised.value)
    assert "설치 안내" not in str(raised.value)
