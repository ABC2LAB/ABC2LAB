"""설정의 계정마다 로그인 → 탐색 → 캡처를 돌려 내부 결과(CollectOutcome)로 모은다.

계정마다 브라우저 context를 따로 열어 쿠키가 섞이지 않는다. 같은 역할에 계정이 여럿이어도 각자 따로 탐색하고,
한 계정이 실패해도 error에 남기고 다음 계정으로 넘어간다. 각 계정은 자기 화면에서 보이는 것만 따라간다.
공개 형식으로 바꾸는 건 utils/export.py, 검증·저장은 entrypoint 몫이다.

로그인한 context마다 불투명 session_ref를 붙인다. 지금은 세션 상태를 보관하지 않아서 이 값으로
세션을 되살릴 수는 없다. 세션 창구(대여·반납·만료)는 verifier와 합의한 뒤에 붙인다.
"""

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from playwright.sync_api import Browser, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from modules.collector.core.auth import SECRET_MASK, open_account_context
from modules.collector.core.capture import RequestCapture, list_secret_variants, start_capture
from modules.collector.core.config import AccountSettings, CrawlerConfig
from modules.collector.core.explorer import crawl
from modules.collector.core.models import CapturedRequest, DiscoveredPage
from modules.collector.core.server_clock import count_clock_regressions

logger = logging.getLogger(__name__)

SESSION_REF_PREFIX = "session"
SESSION_REF_RANDOM_BYTES = 8


class BrowserLaunchError(RuntimeError):
    """브라우저를 띄우지 못해 탐색을 시작하지 못했다."""


@dataclass(frozen=True)
class AccountCrawl:
    """계정 하나의 탐색 결과. 페이지·행동·요청 ID는 export가 붙인다."""

    account_id: str
    alias: str
    role: str
    pages: tuple[DiscoveredPage, ...]
    records: tuple[CapturedRequest, ...]
    error: str | None
    # 로그인한 context의 불투명 참조. guest이거나 로그인 전에 실패했으면 None
    session_ref: str | None
    # 대상 서버 시계가 뒤로 간 횟수
    clock_regressions: int = 0


@dataclass(frozen=True)
class CollectOutcome:
    started_at: datetime
    finished_at: datetime
    account_crawls: tuple[AccountCrawl, ...]

    @property
    def clock_regressions(self) -> int:
        return sum(account_crawl.clock_regressions for account_crawl in self.account_crawls)


def collect(config: CrawlerConfig) -> CollectOutcome:
    """브라우저를 띄워 설정의 계정 순서대로 탐색한다. 브라우저를 못 띄우면 BrowserLaunchError."""
    with sync_playwright() as playwright:
        browser = _launch_browser(playwright)
        try:
            return crawl_accounts(browser, config)
        finally:
            browser.close()


def crawl_accounts(browser: Browser, config: CrawlerConfig) -> CollectOutcome:
    started_at = datetime.now(UTC)
    logger.info("탐색 시작: 계정 %s", ", ".join(account.alias for account in config.accounts))
    account_crawls = tuple(crawl_account(browser, config, account) for account in config.accounts)
    outcome = CollectOutcome(started_at, datetime.now(UTC), account_crawls)
    if outcome.clock_regressions:
        logger.warning(
            "대상 서버 시계 역행 총 %d회: 시간 기반 세션이 끊겼을 수 있으니 이 결과는 쓰지 말고 다시 돌리세요",
            outcome.clock_regressions,
        )
    return outcome


def crawl_account(browser: Browser, config: CrawlerConfig, account: AccountSettings) -> AccountCrawl:
    """계정 하나를 새 context로 탐색한다. 실패는 예외 대신 error로 돌려줘 다음 계정이 계속 돈다."""
    capture: RequestCapture | None = None
    session_ref: str | None = None
    pages: list[DiscoveredPage] = []
    error_message: str | None = None
    try:
        context = open_account_context(browser, config, account)
        if not account.is_guest:
            session_ref = make_session_ref()
        try:
            capture = start_capture(context, config, account)
            pages = crawl(context, config, account, capture)
        finally:
            context.close()
    except Exception as error:
        error_message = _describe_error(config, error)
        logger.warning("%s 계정 실패, 다음 계정으로 진행: %s", account.alias, error_message)
        # traceback에는 URL·입력값이 섞일 수 있어 평소 로그에는 남기지 않는다.
        logger.debug("%s 계정 실패 상세", account.alias, exc_info=True)
        pages = []
    return AccountCrawl(
        account_id=account.account_id,
        alias=account.alias,
        role=account.role,
        pages=tuple(pages),
        records=capture.records if capture is not None else (),
        error=error_message,
        session_ref=session_ref,
        clock_regressions=_count_clock_regressions(account.alias, capture),
    )


def make_session_ref() -> str:
    """세션을 찾는 불투명 참조. 쿠키·토큰 값에서 만들지 않고 무작위로 만든다(값이 새지 않게)."""
    return f"{SESSION_REF_PREFIX}:{secrets.token_hex(SESSION_REF_RANDOM_BYTES)}"


def _launch_browser(playwright: Playwright) -> Browser:
    try:
        return playwright.chromium.launch()
    except PlaywrightError as error:
        raise BrowserLaunchError(f"브라우저를 띄우지 못함: {_first_line(str(error))}") from None


def _count_clock_regressions(alias: str, capture: RequestCapture | None) -> int:
    count = count_clock_regressions(capture.clock_samples) if capture is not None else 0
    if count:
        logger.warning("%s 계정 중 대상 서버 시계 역행 %d회 감지", alias, count)
    return count


def _describe_error(config: CrawlerConfig, error: Exception) -> str:
    text = f"{type(error).__name__}: {_first_line(str(error))}"
    # LoginError는 이미 가려져 있지만 Playwright 메시지에 URL·입력값이 섞일 수 있어 계정 비밀번호를 한 번 더 지운다.
    for secret in list_secret_variants(config.known_passwords):
        text = text.replace(secret, SECRET_MASK)
    return text


def _first_line(text: str) -> str:
    return text.splitlines()[0] if text else ""
