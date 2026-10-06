import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from pydantic import ValidationError

from modules.collector.core.auth import open_role_context
from modules.collector.core.capture import FILE_PART_VALUE, RequestCapture, select_stale_requests, start_capture
from modules.collector.core.config import GUEST_ROLE, CrawlerConfig, load_config
from modules.collector.core.schemas import CapturedRequest
from modules.collector.tests.helpers import make_counting_handler, run_server

ROLE = "user"
# 민감 키 목록에 걸리지 않는 이름이라, 설정의 비밀번호 필드명으로만 마스킹되는지 확인할 수 있다.
PASSWORD_FIELD = "login_pw"
ACCOUNT_PASSWORD = "account-pw-value"
TOKEN_VALUE = "tok-query-value"
AUTH_VALUE = "auth-bearer-value"
COOKIE_VALUE = "cookie-sid-value"
SET_COOKIE_VALUE = "set-cookie-value"
FORM_PASSWORD = "form-pw-value"
NEW_PASSWORD = "new-pw-value"
JSON_PASSWORD = "json-pw-value"
NESTED_TOKEN = "nested-token-value"
SECRET_VALUES = (
    ACCOUNT_PASSWORD,
    TOKEN_VALUE,
    AUTH_VALUE,
    COOKIE_VALUE,
    SET_COOKIE_VALUE,
    FORM_PASSWORD,
    NEW_PASSWORD,
    JSON_PASSWORD,
    NESTED_TOKEN,
)
MASK = "***"
SESSION_COOKIE = "sid"
ORDERS_PATH = "/api/orders/42"
ORDERS_QUERY = f"order_id=7&token={TOKEN_VALUE}"
STATIC_PATHS = ("/style.css", "/logo.png", "/font.woff2")
NESTED_JSON_PATH = "/api/account"
PROBLEM_JSON_PATH = "/api/problem"
PLAIN_TEXT_PATH = "/api/plain"
BROKEN_JSON_PATH = "/api/broken"
# 포트(최대 5자리)·captured_at 소수 초(6자리)와 우연히 겹치지 않게 숫자 값은 9자리 이상으로 둔다.
SLOW_JSON_PATH = "/api/slow-json"
SLOW_POST_PATH = "/api/slow-body"
HANG_PATH = "/api/hang"
SLOW_JSON_BODY = '{"id": 5, "name": "x"}'
SLOW_JSON_SHAPE = {"id": "int", "name": "str"}
# 헤더를 보낸 뒤 본문 뒷부분을 이만큼 늦게 보낸다. 그 사이에 페이지가 이동하게 만든다.
SLOW_BODY_DELAY_S = 0.8
# 끝나지 않는 응답을 붙잡아 둘 최대 시간. 테스트가 끝나면 그 전에 풀어 준다.
HANG_MAX_S = 30
# 응답 헤더를 받자마자 본문을 읽지 않고 다른 문서로 이동한다.
NAV_AWAY_HTML = f"""<html><body><button id="go">go</button><script>
document.getElementById("go").addEventListener("click", () => {{
  fetch("{SLOW_POST_PATH}", {{method: "POST"}}).then(() => {{ location.href = "/inline"; }});
}});
</script></body></html>"""
# 새 문서가 뜨면서 바로 보내는 느린 fetch. 옛 문서 요청을 정리할 때 같이 끊기면 안 된다.
INLINE_FETCH_HTML = f'<html><body><script>fetch("{SLOW_JSON_PATH}");</script></body></html>'
# 같은 문서 안 이동(pushState)은 진행 중 fetch를 끊지 않는다. fetch 본문이 오는 도중에 이동하도록 조금 늦춘다.
PUSH_STATE_DELAY_MS = 300
SPA_HTML = f"""<html><body><button id="spa">spa</button><script>
document.getElementById("spa").addEventListener("click", () => {{
  fetch("{SLOW_JSON_PATH}");
  setTimeout(() => history.pushState({{}}, "", "/spa-next"), {PUSH_STATE_DELAY_MS});
}});
</script></body></html>"""
NESTED_JSON_VALUES = ("carol@example.com", "carol-private-note", "3141.59265", "918273645")
NESTED_JSON_BODY = (
    '{"user": {"id": 918273645, "email": "carol@example.com", "roles": ["admin"]},'
    ' "orders": [{"id": 1, "total": 3141.59265}, {"id": 2, "note": "carol-private-note"}]}'
)
NESTED_JSON_SHAPE = {
    "user": {"id": "int", "email": "str", "roles": ["str"]},
    "orders": [{"id": "int", "total": "float", "note": "str"}],
}
WAIT_TIMEOUT_S = 5
POLL_INTERVAL_MS = 50
SETTLE_MS = 200

PAGE_HTML = f"""<html><head><link rel="stylesheet" href="/style.css"></head><body>
<img src="/logo.png">
<button id="load">load orders</button>
<script>
document.getElementById("load").addEventListener("click", () => {{
  fetch("{ORDERS_PATH}?{ORDERS_QUERY}", {{headers: {{"Authorization": "Bearer {AUTH_VALUE}"}}}});
}});
</script>
</body></html>"""
STYLE_CSS = "@font-face { font-family: F; src: url(/font.woff2); } body { font-family: F; }"
FORM_HTML = f"""<html><body><form method="post" action="/submit">
<input name="login_id" value="alice">
<input type="password" name="{PASSWORD_FIELD}" value="{FORM_PASSWORD}">
<input type="password" name="new_password" value="{NEW_PASSWORD}">
<input name="comment" value="hello">
<button>go</button>
</form></body></html>"""
JSON_FETCH_SCRIPT = f"""() => fetch("/api/profile", {{
  method: "POST",
  headers: {{"Content-Type": "application/json"}},
  body: JSON.stringify({{password: "{JSON_PASSWORD}", nickname: "bob", nested: {{api_token: "{NESTED_TOKEN}"}}}}),
}}).then(response => response.status)"""


@dataclass
class FakeSite:
    received_paths: list[str] = field(default_factory=list)
    # 끝나지 않는 응답을 테스트 끝에 풀어 준다.
    release: threading.Event = field(default_factory=threading.Event)


def make_site_handler(site: FakeSite) -> type[BaseHTTPRequestHandler]:
    get_routes: dict[str, tuple[int, str, str]] = {
        "/page": (200, "text/html; charset=utf-8", PAGE_HTML),
        "/form": (200, "text/html; charset=utf-8", FORM_HTML),
        "/style.css": (200, "text/css", STYLE_CSS),
        "/logo.png": (200, "image/png", ""),
        "/font.woff2": (200, "font/woff2", ""),
        ORDERS_PATH: (200, "application/json", '{"id": 42}'),
        NESTED_JSON_PATH: (200, "application/json; charset=utf-8", NESTED_JSON_BODY),
        PROBLEM_JSON_PATH: (400, "application/problem+json", '{"title": "bad"}'),
        PLAIN_TEXT_PATH: (200, "text/plain", '{"id": 1}'),
        BROKEN_JSON_PATH: (200, "application/json", '{"id": '),
        "/forbidden": (403, "text/html; charset=utf-8", "<html><body>no</body></html>"),
        "/nav-away": (200, "text/html; charset=utf-8", NAV_AWAY_HTML),
        "/inline": (200, "text/html; charset=utf-8", INLINE_FETCH_HTML),
        "/spa": (200, "text/html; charset=utf-8", SPA_HTML),
    }

    class SiteHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            site.received_paths.append(path)
            if path == "/moved":
                self._redirect("/page")
            elif path == SLOW_JSON_PATH:
                self._send_slowly(SLOW_JSON_BODY)
            elif path == HANG_PATH:
                # 헤더조차 보내지 않고 붙잡아 둔다.
                site.release.wait(HANG_MAX_S)
            elif path in get_routes:
                self._send(*get_routes[path])
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            site.received_paths.append(path)
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if path == "/submit":
                self._redirect("/page", cookie=f"{SESSION_COOKIE}={SET_COOKIE_VALUE}; Path=/; HttpOnly")
            elif path == SLOW_POST_PATH:
                self._send_slowly(SLOW_JSON_BODY)
            else:
                self._send(200, "application/json", "{}")

        def _send(self, status: int, content_type: str, body: str) -> None:
            encoded = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_slowly(self, body: str) -> None:
            encoded = body.encode()
            half = len(encoded) // 2
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded[:half])
            self.wfile.flush()
            time.sleep(SLOW_BODY_DELAY_S)
            try:
                self.wfile.write(encoded[half:])
            except OSError:
                # 브라우저가 이동하며 연결을 끊었으면 나머지를 보낼 곳이 없다.
                pass

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
        try:
            yield url
        finally:
            site.release.set()


@pytest.fixture
def config(site_url: str) -> CrawlerConfig:
    return load_config(
        {
            "CRAWLER_TARGET_URL": site_url,
            "CRAWLER_ROLES": ROLE,
            "CRAWLER_ROLE_USER_USERNAME": "alice",
            "CRAWLER_ROLE_USER_PASSWORD": ACCOUNT_PASSWORD,
            "CRAWLER_LOGIN_PATH": "/signin",
            "CRAWLER_LOGIN_USERNAME_FIELD": "login_id",
            "CRAWLER_LOGIN_PASSWORD_FIELD": PASSWORD_FIELD,
            "CRAWLER_LOGIN_SUCCESS_CHECK": "left_login_page",
        }
    )


@dataclass
class CaptureSession:
    page: Page
    capture: RequestCapture
    context: BrowserContext


@contextmanager
def open_capture(browser: Browser, config: CrawlerConfig, role: str = ROLE) -> Iterator[CaptureSession]:
    # 로그인 없이 가드만 있는 guest context에 캡처를 붙인다. 역할 이름은 capture가 받은 대로 기록한다.
    context = open_role_context(browser, config, GUEST_ROLE)
    try:
        capture = start_capture(context, config, role)
        yield CaptureSession(page=context.new_page(), capture=capture, context=context)
    finally:
        context.close()


@pytest.fixture
def session(browser: Browser, config: CrawlerConfig) -> Iterator[CaptureSession]:
    with open_capture(browser, config) as opened:
        yield opened


def wait_for_record(session: CaptureSession, predicate: Callable[[CapturedRequest], bool]) -> CapturedRequest:
    """sync API는 이벤트를 다음 Playwright 호출 때 처리하므로 기다리며 이벤트를 흘려보낸다."""
    deadline = time.monotonic() + WAIT_TIMEOUT_S
    while time.monotonic() < deadline:
        for record in session.capture.records:
            if predicate(record):
                return record
        session.page.wait_for_timeout(POLL_INTERVAL_MS)
    raise AssertionError(f"조건에 맞는 기록 없음. 기록된 URL: {[r.url for r in session.capture.records]}")


def path_is(path: str) -> Callable[[CapturedRequest], bool]:
    return lambda record: urlsplit(record.url).path == path


def fetch_path(session: CaptureSession, site_url: str, path: str) -> CapturedRequest:
    session.page.goto(f"{site_url}/page")
    session.page.evaluate(f'() => fetch("{path}").then(response => response.status)')
    return wait_for_record(session, lambda record: path_is(path)(record) and record.resource_type == "fetch")


def load_orders(session: CaptureSession, site_url: str) -> CapturedRequest:
    session.page.goto(f"{site_url}/page")
    session.page.click("#load")
    return wait_for_record(session, path_is(ORDERS_PATH))


def test_records_documents_and_fetch_but_not_static_resources(
    session: CaptureSession, site: FakeSite, site_url: str
) -> None:
    load_orders(session, site_url)
    wait_for_record(session, path_is("/page"))
    deadline = time.monotonic() + WAIT_TIMEOUT_S
    while not set(STATIC_PATHS) <= set(site.received_paths) and time.monotonic() < deadline:
        session.page.wait_for_timeout(POLL_INTERVAL_MS)
    session.page.wait_for_timeout(SETTLE_MS)

    assert set(STATIC_PATHS) <= set(site.received_paths)
    recorded = {(urlsplit(r.url).path, r.resource_type) for r in session.capture.records}
    assert recorded == {("/page", "document"), (ORDERS_PATH, "fetch")}


def test_requests_outside_allowed_origin_are_not_recorded(
    browser: Browser, config: CrawlerConfig, site_url: str
) -> None:
    requested_paths: list[str] = []
    # 가드 없는 context라 요청은 실제로 나간다. 그래도 capture가 스스로 걸러야 한다.
    context = browser.new_context()
    try:
        capture = start_capture(context, config, ROLE)
        page = context.new_page()
        with run_server(make_counting_handler(requested_paths)) as outside_url:
            page.goto(f"{site_url}/page")
            page.evaluate(f'() => fetch("{outside_url}/outside", {{mode: "no-cors"}}).then(() => 0)')
            session = CaptureSession(page=page, capture=capture, context=context)
            wait_for_record(session, path_is("/page"))
            page.wait_for_timeout(SETTLE_MS)
    finally:
        context.close()

    assert requested_paths == ["/outside"]
    assert all(not record.url.startswith(outside_url) for record in capture.records)


def test_endpoint_template_and_path_ids(session: CaptureSession, site_url: str) -> None:
    record = load_orders(session, site_url)

    assert record.endpoint == "/api/orders/{id}"
    assert record.resource_ids == ["42"]
    assert record.method == "GET"


def test_query_values_kept_and_sensitive_ones_masked(session: CaptureSession, site_url: str) -> None:
    record = load_orders(session, site_url)

    assert record.query_params == {"order_id": ["7"], "token": [MASK]}
    assert TOKEN_VALUE not in record.url
    assert "order_id=7" in record.url


def test_cookie_authorization_and_set_cookie_values_masked(session: CaptureSession, site_url: str) -> None:
    session.context.add_cookies([{"name": SESSION_COOKIE, "value": COOKIE_VALUE, "url": site_url}])
    orders = load_orders(session, site_url)
    session.page.goto(f"{site_url}/form")
    with session.page.expect_navigation():
        session.page.click("button")
    submit = wait_for_record(session, path_is("/submit"))

    assert orders.request_headers["cookie"] == f"{SESSION_COOKIE}={MASK}"
    assert orders.request_headers["authorization"] == f"Bearer {MASK}"
    assert submit.response_headers["set-cookie"] == f"{SESSION_COOKIE}={MASK}; Path=/; HttpOnly"


def test_form_password_fields_masked(session: CaptureSession, site_url: str) -> None:
    session.page.goto(f"{site_url}/form")
    with session.page.expect_navigation():
        session.page.click("button")
    record = wait_for_record(session, path_is("/submit"))

    assert record.method == "POST"
    assert record.body_params == {
        "login_id": ["alice"],
        PASSWORD_FIELD: [MASK],
        "new_password": [MASK],
        "comment": ["hello"],
    }


def test_json_body_sensitive_keys_masked(session: CaptureSession, site_url: str) -> None:
    session.page.goto(f"{site_url}/page")
    assert session.page.evaluate(JSON_FETCH_SCRIPT) == 200
    record = wait_for_record(session, path_is("/api/profile"))

    assert record.body_params["password"] == [MASK]
    assert record.body_params["nickname"] == ["bob"]
    assert NESTED_TOKEN not in record.body_params["nested"][0]


def test_configured_account_password_masked_under_any_key(session: CaptureSession, site_url: str) -> None:
    session.page.goto(f"{site_url}{ORDERS_PATH}?note={ACCOUNT_PASSWORD}")
    record = wait_for_record(session, path_is(ORDERS_PATH))

    assert record.query_params == {"note": [MASK]}
    assert ACCOUNT_PASSWORD not in record.url


@pytest.mark.parametrize(("path", "status"), [("/page", 200), ("/forbidden", 403), ("/missing", 404), ("/moved", 303)])
def test_status_code_recorded(session: CaptureSession, site_url: str, path: str, status: int) -> None:
    session.page.goto(f"{site_url}{path}")
    record = wait_for_record(session, path_is(path))

    assert record.status == status


def test_role_recorded(browser: Browser, config: CrawlerConfig, site_url: str) -> None:
    with open_capture(browser, config, role=GUEST_ROLE) as session:
        session.page.goto(f"{site_url}/page")
        record = wait_for_record(session, path_is("/page"))

    assert record.role == GUEST_ROLE


def test_unknown_role_rejected(browser: Browser, config: CrawlerConfig) -> None:
    context = browser.new_context()
    try:
        with pytest.raises(ValueError):
            start_capture(context, config, "manager")
    finally:
        context.close()


def test_source_attached_only_while_set(session: CaptureSession, site_url: str) -> None:
    session.page.goto(f"{site_url}/page")
    page_record = wait_for_record(session, path_is("/page"))

    session.capture.set_source("/page", "click:load orders")
    session.page.click("#load")
    orders = wait_for_record(session, path_is(ORDERS_PATH))
    session.capture.set_source(None, None)
    session.page.goto(f"{site_url}/forbidden")
    later = wait_for_record(session, path_is("/forbidden"))

    assert (page_record.source_page, page_record.source_action) == (None, None)
    assert (orders.source_page, orders.source_action) == ("/page", "click:load orders")
    assert (later.source_page, later.source_action) == (None, None)


def test_records_hide_secrets_and_round_trip_as_json(session: CaptureSession, site_url: str) -> None:
    session.context.add_cookies([{"name": SESSION_COOKIE, "value": COOKIE_VALUE, "url": site_url}])
    load_orders(session, site_url)
    session.page.evaluate(JSON_FETCH_SCRIPT)
    wait_for_record(session, path_is("/api/profile"))
    session.page.goto(f"{site_url}{ORDERS_PATH}?note={ACCOUNT_PASSWORD}")
    session.page.goto(f"{site_url}/form")
    with session.page.expect_navigation():
        session.page.click("button")
    wait_for_record(session, path_is("/submit"))
    session.page.wait_for_timeout(SETTLE_MS)

    dumped = [record.model_dump_json() for record in session.capture.records]

    for text in dumped:
        for secret in SECRET_VALUES:
            assert secret not in text
    assert [CapturedRequest.model_validate_json(text) for text in dumped] == list(session.capture.records)
    first = session.capture.records[0].model_dump(mode="json")
    assert "source_page" in first and first["source_page"] is None


def test_fetch_json_response_recorded_as_shape(session: CaptureSession, site_url: str) -> None:
    orders = load_orders(session, site_url)
    nested = fetch_path(session, site_url, NESTED_JSON_PATH)

    assert orders.response_shape == {"id": "int"}
    assert nested.response_shape == NESTED_JSON_SHAPE


def test_json_suffix_content_type_recorded(session: CaptureSession, site_url: str) -> None:
    record = fetch_path(session, site_url, PROBLEM_JSON_PATH)

    assert record.response_shape == {"title": "str"}


def test_document_json_recorded_but_html_is_null(session: CaptureSession, site_url: str) -> None:
    # 링크로 바로 연 JSON API도 문서 요청이다. 판단은 resource_type이 아니라 content-type으로 한다.
    session.page.goto(f"{site_url}{NESTED_JSON_PATH}")
    json_document = wait_for_record(session, path_is(NESTED_JSON_PATH))
    session.page.goto(f"{site_url}/page")
    html_document = wait_for_record(session, path_is("/page"))

    assert json_document.resource_type == "document"
    assert json_document.response_shape == NESTED_JSON_SHAPE
    assert html_document.response_shape is None


@pytest.mark.parametrize("path", [PLAIN_TEXT_PATH, BROKEN_JSON_PATH])
def test_non_json_or_broken_json_is_null(session: CaptureSession, site_url: str, path: str) -> None:
    record = fetch_path(session, site_url, path)

    assert record.status == 200
    assert record.response_shape is None


def test_redirect_response_is_null(session: CaptureSession, site_url: str) -> None:
    session.page.goto(f"{site_url}/moved")
    moved = wait_for_record(session, path_is("/moved"))

    assert moved.response_shape is None


def test_response_values_not_stored(session: CaptureSession, site_url: str) -> None:
    fetch_path(session, site_url, NESTED_JSON_PATH)
    session.page.goto(f"{site_url}{NESTED_JSON_PATH}")
    wait_for_record(session, lambda record: path_is(NESTED_JSON_PATH)(record) and record.resource_type == "document")

    for record in session.capture.records:
        text = record.model_dump_json()
        for value in NESTED_JSON_VALUES:
            assert value not in text


def test_nested_shape_round_trips_as_json(session: CaptureSession, site_url: str) -> None:
    record = fetch_path(session, site_url, NESTED_JSON_PATH)

    reloaded = CapturedRequest.model_validate_json(record.model_dump_json())

    assert reloaded == record
    assert reloaded.response_shape == NESTED_JSON_SHAPE


def test_shape_with_non_type_value_rejected(session: CaptureSession, site_url: str) -> None:
    record = load_orders(session, site_url)
    # 재귀 검증이 동작해야 깊은 곳에 값이 섞인 shape도 거부된다.
    broken = record.model_dump(mode="json") | {"response_shape": {"user": {"id": 77}}}

    with pytest.raises(ValidationError):
        CapturedRequest.model_validate(broken)


def test_request_cut_by_navigation_still_recorded(session: CaptureSession, site_url: str) -> None:
    session.page.goto(f"{site_url}/nav-away")
    session.capture.set_source("/nav-away", "button:0")
    session.page.click("#go")
    # 새 문서의 느린 fetch는 정상으로 끝까지 받아야 한다.
    inline = wait_for_record(session, path_is(SLOW_JSON_PATH))
    cut = wait_for_record(session, path_is(SLOW_POST_PATH))

    assert cut.status == 200
    assert cut.response_shape is None
    assert (cut.source_page, cut.source_action) == ("/nav-away", "button:0")
    assert inline.response_shape == SLOW_JSON_SHAPE
    assert not session.capture.has_pending_requests


def test_same_document_navigation_keeps_request(session: CaptureSession, site_url: str) -> None:
    session.page.goto(f"{site_url}/spa")
    session.page.click("#spa")
    record = wait_for_record(session, path_is(SLOW_JSON_PATH))

    assert record.response_shape == SLOW_JSON_SHAPE


def test_flush_pending_records_unfinished_request(session: CaptureSession, site_url: str) -> None:
    session.page.goto(f"{site_url}/page")
    session.page.evaluate(f'() => {{ fetch("{HANG_PATH}"); }}')
    deadline = time.monotonic() + WAIT_TIMEOUT_S
    while not session.capture.has_pending_requests and time.monotonic() < deadline:
        session.page.wait_for_timeout(POLL_INTERVAL_MS)

    endpoints = session.capture.flush_pending()

    record = wait_for_record(session, path_is(HANG_PATH))
    assert endpoints == [HANG_PATH]
    assert record.status is None
    assert record.response_shape is None
    assert not session.capture.has_pending_requests


def test_slow_json_document_keeps_shape(session: CaptureSession, site_url: str) -> None:
    # 문서 자신(navigation 요청)은 새 문서가 뜰 때 옛 요청으로 정리되면 안 된다.
    session.page.goto(f"{site_url}{SLOW_JSON_PATH}")
    record = wait_for_record(session, path_is(SLOW_JSON_PATH))

    assert record.resource_type == "document"
    assert record.response_shape == SLOW_JSON_SHAPE


@dataclass
class FakeRequest:
    """후보 고르기가 쓰는 두 가지만 흉내 낸다."""

    name: str
    frame: object
    is_navigation: bool

    def is_navigation_request(self) -> bool:
        return self.is_navigation


def test_stale_candidates_exclude_navigation_and_other_frames() -> None:
    main_frame = object()
    child_frame = object()
    old_fetch = FakeRequest("old fetch", main_frame, is_navigation=False)
    new_document = FakeRequest("new document", main_frame, is_navigation=True)
    iframe_fetch = FakeRequest("iframe fetch", child_frame, is_navigation=False)

    candidates = select_stale_requests([old_fetch, new_document, iframe_fetch], main_frame)  # type: ignore[arg-type]

    assert candidates == [old_fetch]


MULTIPART_PATH = "/api/multipart"
MULTIPART_PASSWORD = "multipart-pw-value"
MULTIPART_CSRF = "multipart-csrf-value"
FILE_CONTENT = "secret-file-content"
FILE_NAME = "private-name.txt"
BINARY_FILE_NAME = "private-photo.png"
# fetch body에 FormData를 넣으면 multipart/form-data로 나간다.
MULTIPART_FETCH_SCRIPT = f"""() => {{
  const data = new FormData();
  data.append("nickname", "bob");
  data.append("tag", "a");
  data.append("tag", "b");
  data.append("상품명", "신발");
  data.append("{PASSWORD_FIELD}", "{MULTIPART_PASSWORD}");
  data.append("csrf_token", "{MULTIPART_CSRF}");
  data.append("memo", "");
  data.append("upload", new File(["{FILE_CONTENT}"], "{FILE_NAME}", {{type: "text/plain"}}));
  data.append("photo", new File([new Uint8Array([0, 255, 128, 10, 13])], "{BINARY_FILE_NAME}", {{type: "image/png"}}));
  return fetch("{MULTIPART_PATH}", {{method: "POST", body: data}}).then(response => response.status);
}}"""


def test_multipart_body_params_parsed_and_masked(session: CaptureSession, site_url: str) -> None:
    session.page.goto(f"{site_url}/page")
    assert session.page.evaluate(MULTIPART_FETCH_SCRIPT) == 200
    record = wait_for_record(session, path_is(MULTIPART_PATH))

    assert record.request_headers["content-type"].startswith("multipart/form-data")
    assert record.body_params == {
        "nickname": ["bob"],
        "tag": ["a", "b"],
        "상품명": ["신발"],
        PASSWORD_FIELD: [MASK],
        "csrf_token": [MASK],
        "memo": [""],
        "upload": [FILE_PART_VALUE],
        "photo": [FILE_PART_VALUE],
    }
    text = record.model_dump_json()
    for secret in (MULTIPART_PASSWORD, MULTIPART_CSRF, FILE_CONTENT, FILE_NAME, BINARY_FILE_NAME):
        assert secret not in text
