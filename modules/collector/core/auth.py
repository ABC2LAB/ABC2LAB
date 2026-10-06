"""역할 이름을 받아 로그인된 Playwright context를 만든다.

로그인 경로·폼 필드명·성공 판단은 전부 config에서 온다. 로그인 방식은 LoginMethod별 함수로 갈라서,
헤더 토큰 같은 방식은 함수를 하나 더 등록하는 것으로 붙인다.
"""

import logging
from collections.abc import Callable
from enum import Enum
from urllib.parse import quote, quote_plus, urljoin, urlsplit

from playwright.sync_api import Browser, BrowserContext, Locator, Page, Route
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from modules.collector.core.config import (
    GUEST_ROLE,
    CrawlerConfig,
    LoginSettings,
    LoginSuccessCheck,
    RoleAccount,
    is_request_allowed,
)

logger = logging.getLogger(__name__)

LOGIN_TIMEOUT_MS = 10_000
HTTP_ERROR_STATUS_MIN = 400
SECRET_MASK = "***"
# form.action 프로퍼티는 name="action"인 input에 가려질 수 있어서 속성을 직접 읽어 절대 URL로 푼다.
READ_FORM_ACTION_SCRIPT = "form => new URL(form.getAttribute('action') ?? '', document.baseURI).href"
# 같은 이유로 form.requestSubmit 대신 프로토타입 메서드를 부른다. 제출 버튼 셀렉터를 몰라도 되고 submit 핸들러도 돈다.
SUBMIT_FORM_SCRIPT = "form => HTMLFormElement.prototype.requestSubmit.call(form)"
ENCLOSING_FORM_SELECTOR = "xpath=ancestor::form[1]"


class LoginError(RuntimeError):
    """어떤 역할이 왜 로그인에 실패했는지. 메시지에 비밀번호는 넣지 않는다."""

    def __init__(self, role: str, reason: str) -> None:
        super().__init__(f"{role} 로그인 실패: {reason}")
        self.role = role
        self.reason = reason


class LoginMethod(Enum):
    FORM_COOKIE = "form_cookie"


LoginHandler = Callable[[BrowserContext, CrawlerConfig, RoleAccount], None]

# TODO(minjun): 두 번째 방식이 생기면 CRAWLER_LOGIN_METHOD 설정 키를 config.py에 추가하고 거기서 고른다.
DEFAULT_LOGIN_METHOD = LoginMethod.FORM_COOKIE


def open_role_context(browser: Browser, config: CrawlerConfig, role: str) -> BrowserContext:
    """role로 로그인된 새 context를 돌려준다. guest면 로그인 없이 빈 context. 닫는 건 호출한 쪽이 한다."""
    if role != GUEST_ROLE and role not in config.accounts:
        raise LoginError(role, "설정에 없는 역할")
    # 서비스워커가 받은 요청은 context.route를 거치지 않으므로 막아 둔다.
    context = browser.new_context(service_workers="block")
    _install_request_guard(context, config)
    if role == GUEST_ROLE:
        return context
    try:
        _log_in(context, config, config.accounts[role])
    except BaseException:
        context.close()
        raise
    logger.info("%s 로그인 성공 (%s)", role, _describe_success_check(config.login))
    return context


def _install_request_guard(context: BrowserContext, config: CrawlerConfig) -> None:
    """절대 규칙 1번: 허용 origin 밖으로 나가는 요청은 브라우저 단에서 끊는다. 이후 탐색에도 그대로 적용된다."""

    def guard(route: Route) -> None:
        url = route.request.url
        if is_request_allowed(config, url):
            route.continue_()
            return
        parts = urlsplit(url)
        logger.warning("허용 밖 요청 차단: %s://%s", parts.scheme, parts.hostname)
        route.abort("blockedbyclient")

    context.route("**/*", guard)


def _log_in(context: BrowserContext, config: CrawlerConfig, account: RoleAccount) -> None:
    handler = LOGIN_HANDLERS[DEFAULT_LOGIN_METHOD]
    try:
        handler(context, config, account)
    except PlaywrightError as error:
        first_line = error.message.splitlines()[0] if error.message else ""
        reason = _mask_secret(f"브라우저 오류 {type(error).__name__}: {first_line}", account.password)
        # 원 예외를 체인하면 traceback에 가리지 않은 메시지가 그대로 찍히므로 끊는다.
        raise LoginError(account.role, reason) from None


def _log_in_with_form(context: BrowserContext, config: CrawlerConfig, account: RoleAccount) -> None:
    """로그인 폼을 채워 제출하고, 세션 쿠키는 context에 남긴다."""
    login = _require_login_settings(config, account.role)
    login_url = urljoin(config.start_url, login.path)
    _ensure_allowed(config, account.role, login_url, "로그인 페이지")
    page = context.new_page()
    page.set_default_timeout(LOGIN_TIMEOUT_MS)
    try:
        _open_login_page(page, login_url, account.role)
        form = _fill_login_form(page, login, account)
        _ensure_allowed(config, account.role, form.evaluate(READ_FORM_ACTION_SCRIPT), "로그인 폼 제출 대상")
        _submit_form(page, form, account.role)
        # 서버 리다이렉트는 route를 거치지 않아서 도착한 곳을 따로 확인한다.
        _ensure_allowed(config, account.role, page.url, "로그인 후 도착한 페이지")
        _check_login_succeeded(page, config, login, account.role)
    finally:
        page.close()


LOGIN_HANDLERS: dict[LoginMethod, LoginHandler] = {
    LoginMethod.FORM_COOKIE: _log_in_with_form,
}


def _require_login_settings(config: CrawlerConfig, role: str) -> LoginSettings:
    if config.login is None:
        raise LoginError(role, "로그인 설정이 없음")
    return config.login


def _ensure_allowed(config: CrawlerConfig, role: str, url: str, what: str) -> None:
    if not is_request_allowed(config, url):
        parts = urlsplit(url)
        raise LoginError(role, f"{what}가 허용 origin 밖 ({parts.scheme}://{parts.hostname})")


def _open_login_page(page: Page, login_url: str, role: str) -> None:
    response = page.goto(login_url)
    if response is not None and response.status >= HTTP_ERROR_STATUS_MIN:
        raise LoginError(role, f"로그인 페이지 응답 {response.status} ({urlsplit(login_url).path})")


def _fill_login_form(page: Page, login: LoginSettings, account: RoleAccount) -> Locator:
    """비밀번호 필드가 든 form을 찾아 채우고 그 form을 돌려준다. 아이디 필드도 같은 form 안에서만 찾는다."""
    password_input = page.locator(_build_input_selector(login.password_field)).first
    if password_input.count() == 0:
        raise LoginError(account.role, f"로그인 페이지에 비밀번호 필드 없음 (name={login.password_field!r})")
    form = password_input.locator(ENCLOSING_FORM_SELECTOR)
    if form.count() == 0:
        raise LoginError(account.role, f"비밀번호 필드가 form 안에 없음 (name={login.password_field!r})")
    username_input = form.locator(_build_input_selector(login.username_field)).first
    if username_input.count() == 0:
        raise LoginError(account.role, f"로그인 폼에 아이디 필드 없음 (name={login.username_field!r})")
    username_input.fill(account.username)
    password_input.fill(account.password)
    return form


def _build_input_selector(field_name: str) -> str:
    escaped = field_name.replace("\\", "\\\\").replace('"', '\\"')
    return f'input[name="{escaped}"]'


def _submit_form(page: Page, form: Locator, role: str) -> None:
    try:
        with page.expect_navigation():
            form.evaluate(SUBMIT_FORM_SCRIPT)
    except PlaywrightTimeoutError:
        raise LoginError(role, f"폼 제출 후 {LOGIN_TIMEOUT_MS}ms 안에 페이지 이동이 없음") from None


def _check_login_succeeded(page: Page, config: CrawlerConfig, login: LoginSettings, role: str) -> None:
    if _is_login_succeeded(page, config, login):
        return
    # 쿼리에는 토큰 같은 값이 있을 수 있어 경로만 남긴다.
    current_path = urlsplit(page.url).path
    raise LoginError(role, f"성공 판단 {_describe_success_check(login)} 불충족 (현재 경로 {current_path})")


def _is_login_succeeded(page: Page, config: CrawlerConfig, login: LoginSettings) -> bool:
    value = login.success_value or ""
    match login.success_check:
        case LoginSuccessCheck.LEFT_LOGIN_PAGE:
            return _trim_trailing_slash(urlsplit(page.url).path) != _trim_trailing_slash(login.path)
        case LoginSuccessCheck.URL_CONTAINS:
            return value in page.url
        case LoginSuccessCheck.COOKIE_PRESENT:
            return any(cookie["name"] == value for cookie in page.context.cookies(config.start_url))
        case LoginSuccessCheck.TEXT_PRESENT:
            return value in page.locator("body").inner_text()
    raise ValueError(f"처리하지 않은 성공 판단 기준: {login.success_check}")


def _describe_success_check(login: LoginSettings | None) -> str:
    if login is None:
        return "없음"
    if login.success_value is None:
        return login.success_check.value
    return f"{login.success_check.value}={login.success_value!r}"


def _trim_trailing_slash(path: str) -> str:
    return path.rstrip("/") or "/"


def _mask_secret(text: str, secret: str) -> str:
    # GET 폼이면 비밀번호가 URL 인코딩된 채로 오류 메시지의 URL에 들어갈 수 있다.
    for variant in {secret, quote(secret, safe=""), quote_plus(secret)}:
        text = text.replace(variant, SECRET_MASK)
    return text
