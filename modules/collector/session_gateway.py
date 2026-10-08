"""collector 소유의 세션 공개 창구. verifier가 재현 요청을 넘기면 그 계정 세션으로 대신 보낸다.

verifier의 Protocol(executor.py)을 import하지 않고 같은 모양(lease/is_valid/send/release)의 클래스를 자기 폴더에 둔다.
런너가 이 창구를 verifier에 주입한다(명세 03). 세션 쿠키·토큰은 이 모듈 안에만 머물고 ReplayRequest/응답·로그·근거에 값으로 넣지 않는다.

실행 모델: lease(account_id) 때 그 계정으로 로그인한 live 세션을 열고(재로그인 방식), send가 그 세션으로 전송한다.
리다이렉트는 따라가지 않고(max_redirects=0) 3xx를 그대로 돌려준다. 만료 판단·외부 주소 2차 차단은 아래 설계대로 한다.
"""

import logging
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Protocol, runtime_checkable
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Browser, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from modules.collector.core.auth import LoginError, open_account_context
from modules.collector.core.capture import AUTHORIZATION_HEADERS, COOKIE_HEADER, SET_COOKIE_HEADER, SENSITIVE_KEY_PARTS
from modules.collector.core.config import (
    AccountSettings,
    CrawlerConfig,
    LoginSettings,
    LoginSuccessCheck,
    is_request_allowed,
)

logger = logging.getLogger(__name__)

# 응답·요청에서 통째로 지우는 헤더 이름(소문자). 쿠키·인증은 collector 밖으로 내보내지 않는다.
DROPPED_HEADER_NAMES = frozenset({SET_COOKIE_HEADER, COOKIE_HEADER, *AUTHORIZATION_HEADERS})
LOCATION_HEADER = "location"
REDIRECT_STATUS_MIN = 300
REDIRECT_STATUS_MAX = 400


class SessionTransportError(RuntimeError):
    """창구가 요청을 끝내지 못했다(통신 실패, 또는 외부 주소 2차 차단). verifier는 판단불가로 기록한다.

    verifier executor.py의 동명 예외와 같은 역할이지만 import하지 않는다(모듈 독립).
    """


class SessionExpiredError(RuntimeError):
    """세션이 로그인 안 된 상태로 판명됐다(로그인 리다이렉트·성공 쿠키 소실). verifier는 판단불가로 기록한다."""


@dataclass(frozen=True)
class SessionResponse:
    """창구가 돌려주는 응답. 쿠키·인증 헤더는 담지 않는다."""

    status_code: int
    headers: tuple[tuple[str, str], ...] = ()
    body: Any | None = None


@runtime_checkable
class AccountBackend(Protocol):
    """계정 하나의 전송 수단. 창구 로직(만료 판단·2차 차단)과 분리해 브라우저 없이 테스트할 수 있게 한 seam."""

    def is_logged_in(self) -> bool:
        """로그인에 성공했고 아직 세션을 들고 있는가(대상 앱에 요청을 보내지 않는다)."""
        ...

    def request(self, method: str, url: str, headers: Sequence[tuple[str, str]], body: Any | None) -> SessionResponse:
        """리다이렉트를 따라가지 않고 한 번만 보낸다. 통신 실패는 SessionTransportError."""
        ...

    def cookie_names(self) -> frozenset[str]:
        """현재 세션에 있는 쿠키 이름들(값은 노출하지 않는다). cookie_present 만료 신호에만 쓴다."""
        ...

    def close(self) -> None:
        ...


def _trim_trailing_slash(path: str) -> str:
    return path.rstrip("/") or "/"


def _response_header(response: SessionResponse, name: str) -> str | None:
    for key, value in response.headers:
        if key.lower() == name:
            return value
    return None


def is_expired_response(
    login: LoginSettings, request_url: str, response: SessionResponse, cookie_names_after: frozenset[str]
) -> bool:
    """send 응답이 '로그인 안 된 상태'를 뜻하는지 설정 기준으로 판단한다. 기준값은 전부 LoginSettings에서 온다.

    신호 1(항상): 3xx이고 Location의 경로(쿼리·fragment 제외)가 login.path와 같으면 만료.
    신호 2(success_check=cookie_present일 때만): 성공 쿠키 이름이 더 이상 없으면 만료.
    상태 코드 401·403은 쓰지 않는다 — 403은 인가 거부(정상 failure)라 만료와 섞으면 안 된다.
    """
    if _is_redirect_to_login(login, request_url, response):
        return True
    if login.success_check is LoginSuccessCheck.COOKIE_PRESENT and login.success_value is not None:
        return login.success_value not in cookie_names_after
    return False


def _is_redirect_to_login(login: LoginSettings, request_url: str, response: SessionResponse) -> bool:
    if not REDIRECT_STATUS_MIN <= response.status_code < REDIRECT_STATUS_MAX:
        return False
    location = _response_header(response, LOCATION_HEADER)
    if location is None:
        return False
    target_path = urlsplit(urljoin(request_url, location)).path
    return _trim_trailing_slash(target_path) == _trim_trailing_slash(login.path)


def _is_sensitive_header(name: str) -> bool:
    lowered = name.lower()
    if lowered in DROPPED_HEADER_NAMES:
        return True
    normalized = lowered.replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def strip_secret_headers(headers: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """쿠키·인증·민감 이름 헤더를 통째로 뺀다(값 마스킹이 아니라 제거). 이름만 보고 판단한다."""
    return tuple((name, value) for name, value in headers if not _is_sensitive_header(name))


def _safe_url_for_log(url: str) -> str:
    """로그용 URL: 스킴·호스트·경로만. 쿼리·fragment는 토큰 같은 값이 섞일 수 있어 뺀다."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}{parts.path}"


class BrowserSessionLease:
    """계정 하나의 대여된 세션. 창구가 소유한 세션으로 요청을 대신 보낸다."""

    def __init__(self, config: CrawlerConfig, backend: AccountBackend) -> None:
        self._config = config
        self._backend = backend
        self._expired = False

    def is_valid(self) -> bool:
        """창구가 세션을 들고 있고(로그인 성공·미반납) 만료 감지가 없었는가. 대상 앱에 요청하지 않는다.

        실제 세션 만료는 send 응답 신호로 잡아 _expired를 올린다(명세 m7 능동 재확인 대신).
        """
        return self._backend.is_logged_in() and not self._expired

    def send(self, request: Any) -> SessionResponse:
        """resolve된 요청을 그 계정 세션으로 보낸다. 리다이렉트는 따라가지 않는다.

        전송 직전 collector 허용 origin을 한 번 더 검사한다(verifier effective_origins와 별개의 2차 방어).
        """
        url = request.url
        if not is_request_allowed(self._config, url):
            # verifier 1차 방어를 통과한 요청이 여기 오면 추적할 수 있게 남긴다(비밀값 없이 URL·사유만).
            logger.warning("세션 창구 2차 차단: 허용 origin 밖 요청 %s", _safe_url_for_log(url))
            raise SessionTransportError("요청 URL이 collector 허용 origin 밖")
        headers = strip_secret_headers(tuple(getattr(request, "headers", ()) or ()))
        response = self._backend.request(request.method, url, headers, getattr(request, "body", None))
        login = self._config.login
        if login is not None and is_expired_response(login, url, response, self._backend.cookie_names()):
            self._expired = True
            logger.info("세션 창구 만료 감지: %s (로그인 안 된 응답)", _safe_url_for_log(url))
            raise SessionExpiredError("세션이 로그인 안 된 상태")
        return SessionResponse(response.status_code, strip_secret_headers(response.headers), response.body)

    def release(self) -> None:
        self._backend.close()


BackendFactory = Callable[[str], AccountBackend]


class BrowserSessionExecutor:
    """세션 공개 창구. account_id로 세션을 대여한다. 브라우저 생성은 backend_factory에 맡겨 창구 로직만 테스트한다."""

    def __init__(self, config: CrawlerConfig, backend_factory: BackendFactory) -> None:
        self._config = config
        self._backend_factory = backend_factory
        self._backends: list[AccountBackend] = []

    def lease(self, account_id: str) -> BrowserSessionLease:
        backend = self._backend_factory(account_id)
        self._backends.append(backend)
        return BrowserSessionLease(self._config, backend)

    def close(self) -> None:
        """반납되지 않은 세션까지 모두 닫는다(창구 종료)."""
        for backend in self._backends:
            backend.close()
        self._backends.clear()


class PlaywrightAccountBackend:
    """실제 전송 수단: core/auth.py로 로그인한 Playwright context를 들고, 그 context의 쿠키로 보낸다.

    context.request(APIRequestContext)는 context 쿠키를 공유하므로 쿠키가 collector 밖으로 나가지 않는다.
    max_redirects=0이라 3xx를 그대로 돌려준다(실측 확인). 브라우저가 필요해 단위 테스트 대신 실제 실행으로 검증한다.
    """

    def __init__(self, browser: Browser, config: CrawlerConfig, account: AccountSettings) -> None:
        self._config = config
        self._context = None
        self._login_ok = False
        try:
            self._context = open_account_context(browser, config, account)
            self._login_ok = True
        except LoginError as error:
            logger.warning("세션 창구 로그인 실패(%s): %s", account.alias, error.reason)

    def is_logged_in(self) -> bool:
        return self._login_ok and self._context is not None

    def request(self, method: str, url: str, headers: Sequence[tuple[str, str]], body: Any | None) -> SessionResponse:
        if self._context is None:
            raise SessionTransportError("로그인되지 않아 전송할 수 없음")
        try:
            response = self._context.request.fetch(
                url, method=method, headers=dict(headers), data=body, max_redirects=0
            )
        except PlaywrightError as error:
            raise SessionTransportError(f"전송 실패: {type(error).__name__}") from None
        return SessionResponse(response.status, tuple(response.headers.items()), _read_body(response))

    def cookie_names(self) -> frozenset[str]:
        if self._context is None:
            return frozenset()
        return frozenset(cookie["name"] for cookie in self._context.cookies())

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None


def _read_body(response: Any) -> Any | None:
    content_type = response.headers.get("content-type", "")
    if "json" in content_type.lower():
        try:
            return response.json()
        except (ValueError, PlaywrightError):
            return None
    try:
        return response.text()
    except PlaywrightError:
        return None


@contextmanager
def open_session_executor(config: CrawlerConfig) -> Iterator[BrowserSessionExecutor]:
    """런너가 verifier에 주입할 실물 창구. 브라우저를 띄우고, account_id마다 로그인 백엔드를 만든다.

    브라우저가 필요하므로 단위 테스트가 아니라 실제 실행에서 쓴다.
    """
    accounts_by_id = {account.account_id: account for account in config.accounts}

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright)
        executor = BrowserSessionExecutor(config, lambda account_id: _make_backend(browser, config, accounts_by_id, account_id))
        try:
            yield executor
        finally:
            executor.close()
            browser.close()


def _make_backend(
    browser: Browser, config: CrawlerConfig, accounts_by_id: dict[str, AccountSettings], account_id: str
) -> PlaywrightAccountBackend:
    account = accounts_by_id.get(account_id)
    if account is None:
        raise SessionTransportError(f"설정에 없는 계정 참조: {account_id}")
    return PlaywrightAccountBackend(browser, config, account)


def _launch_browser(playwright: Playwright) -> Browser:
    try:
        return playwright.chromium.launch()
    except PlaywrightError as error:
        raise SessionTransportError(f"브라우저를 띄우지 못함: {str(error).splitlines()[0]}") from None
