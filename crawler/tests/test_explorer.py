from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

import logging
import threading

import pytest
from playwright.sync_api import Browser, sync_playwright

from crawler.auth import open_role_context
from crawler.capture import start_capture
from crawler.config import GUEST_ROLE, CrawlerConfig, load_config
from crawler import explorer
from crawler.explorer import crawl
from crawler.schemas import CapturedRequest, DiscoveredPage, FormField, PageAction, PageLink
from crawler.tests.helpers import make_counting_handler, run_server

DEFAULT_MAX_DEPTH = "2"
BUTTON_FETCH_DELAY_MS = 150
# 기본 설정(max_depth=2)에서 기대하는 BFS 방문 순서. /items/2는 /items/1과 같은 템플릿이라 없다.
EXPECTED_ORDER = ["/", "/a", "/b", "/items/1", "/search", "/c", "/a/deep"]
# 입력칸 값은 어떤 결과에도 남으면 안 된다.
FIELD_VALUES = ("nick-field-value", "pw-field-value", "note-field-value", "mail-field-value")
PROFILE_FORM_HTML = f"""<form method="post" action="/profile">
<input name="nickname" value="{FIELD_VALUES[0]}">
<input type="PASSWORD" name="new_pw" value="{FIELD_VALUES[1]}">
<input type="bogus" name="mail" value="{FIELD_VALUES[3]}">
<input type="radio" name="color" value="red"><input type="radio" name="color" value="blue">
<select name="size"><option value="s">S</option></select>
<textarea name="bio">{FIELD_VALUES[2]}</textarea>
<input type="checkbox" name="agree">
<button>save profile</button>
</form>"""
EXPECTED_PROFILE_FIELDS = [
    FormField(name="nickname", type="text"),
    FormField(name="new_pw", type="password"),
    # 브라우저가 모르는 type은 text로 본다.
    FormField(name="mail", type="text"),
    FormField(name="color", type="radio"),
    FormField(name="size", type="select"),
    FormField(name="bio", type="textarea"),
    FormField(name="agree", type="checkbox"),
]
CART_API_PATH = "/api/cart-add"
LOOKUP_API_PATH = "/api/lookup"
TRACK_API_PATH = "/api/track"
# alert 다음 줄에서 보낸다. 이 요청이 기록되면 대화상자가 닫혀 스크립트가 이어졌다는 증거다.
AFTER_ALERT_PATH = "/api/after-alert"
# JS가 submit을 가로채는 폼들. method·action이 없어 GET 폼으로 보이지만 제출해야 요청이 나간다.
# 장바구니 폼은 실제 앱처럼 confirm → POST fetch → alert 순으로 대화상자를 띄운다.
A_HTML = f"""<html><head><title>A</title></head><body>
<a href="/a/deep">deep</a>
<form id="cart"><input type="hidden" name="product_id" value="3"><input name="note" required>
<button>add to cart</button></form>
<form id="lookup"><button>lookup</button></form>
<form method="get" action="/items/9"><button>open item</button></form>
<form id="track" method="get" action="/items/9"><button>track and open</button></form>
<script>
const afterAlert = () => fetch("{AFTER_ALERT_PATH}");
document.getElementById("cart").addEventListener("submit", event => {{
  event.preventDefault();
  confirm("add to cart?");
  fetch("{CART_API_PATH}", {{method: "POST", body: new FormData(event.target)}})
    .then(() => {{ alert("added"); afterAlert(); }})
    .catch(() => {{ alert("failed"); afterAlert(); }});
}});
// POST를 보낸 뒤 결과와 상관없이 원래 GET 이동을 한다. 막힌 요청이 있으면 이동보다 그쪽을 outcome으로 남겨야 한다.
// 응답 본문을 읽지 않고 바로 이동한다. 본문을 받는 중에 문서가 바뀌면 끝남 이벤트가 오지 않는데, 그래도 기록되고 기다리지 않아야 한다.
document.getElementById("track").addEventListener("submit", event => {{
  event.preventDefault();
  fetch("{TRACK_API_PATH}", {{method: "POST"}}).finally(() => event.target.submit());
}});
document.getElementById("lookup").addEventListener("submit", event => {{
  event.preventDefault();
  fetch("{LOOKUP_API_PATH}");
}});
</script>
</body></html>"""
TIMEOUT_WARNING_TEXT = "미완료로 기록"
STATE_CHANGING_REQUESTS = [
    ("GET", "/logout"),
    ("POST", "/orders"),
    ("POST", "/api/remove-item"),
    ("POST", "/api/save"),
    ("POST", "/profile"),
    ("POST", CART_API_PATH),
    ("POST", TRACK_API_PATH),
]


def make_root_html(outside_url: str) -> str:
    return f"""<html><head><title>Home</title></head><body>
<a href="/a">A</a>
<a href="/b">B</a>
<a href="/items/1">item 1</a>
<a href="/items/2">item 2</a>
<a href="/logout">Log out</a>
<a href="{outside_url}/steal">outside</a>
<form method="get" action="/search"><input name="q" value="shoes"><button>search</button></form>
<form method="post" action="/orders"><input type="hidden" name="csrf_token" value="csrf-value"><button>order</button></form>
{PROFILE_FORM_HTML}
<button id="load">load</button>
<button id="remove">삭제</button>
<button id="save">save</button>
<button id="next">next</button>
<script>
document.getElementById("load").addEventListener("click", () => {{
  setTimeout(() => fetch("/api/data"), {BUTTON_FETCH_DELAY_MS});
}});
document.getElementById("remove").addEventListener("click", () => fetch("/api/remove-item", {{method: "POST"}}));
document.getElementById("save").addEventListener("click", () => fetch("/api/save", {{method: "POST"}}));
document.getElementById("next").addEventListener("click", () => {{ location.href = "/c"; }});
</script>
</body></html>"""


B_HTML = """<html><head><title>B</title></head><body>
<a href="/">home</a>
<script>fetch("/api/b-load");</script>
</body></html>"""


def make_simple_html(title: str, *links: str) -> str:
    anchors = "".join(f'<a href="{link}">{link}</a>' for link in links)
    return f"<html><head><title>{title}</title></head><body>{anchors}</body></html>"


@dataclass
class FakeSite:
    outside_url: str
    received: list[tuple[str, str]] = field(default_factory=list)


def make_site_handler(site: FakeSite) -> type[BaseHTTPRequestHandler]:
    html_routes = {
        "/": make_root_html(site.outside_url),
        "/a": A_HTML,
        "/a/deep": make_simple_html("Deep", "/a/deep/deeper"),
        "/a/deep/deeper": make_simple_html("Deeper"),
        "/b": B_HTML,
        "/c": make_simple_html("C"),
        "/items/1": make_simple_html("Item"),
        "/items/2": make_simple_html("Item"),
        "/items/9": make_simple_html("Item"),
        "/search": make_simple_html("Search"),
        "/logout": make_simple_html("Bye"),
    }
    json_routes = {"/api/data", "/api/b-load", LOOKUP_API_PATH, AFTER_ALERT_PATH}

    class SiteHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            site.received.append(("GET", path))
            if path in html_routes:
                self._send("text/html; charset=utf-8", html_routes[path])
            elif path in json_routes:
                self._send("application/json", "{}")
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            site.received.append(("POST", path))
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self._send("text/html; charset=utf-8", make_simple_html("Done"))

        def _send(self, content_type: str, body: str) -> None:
            encoded = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            pass

    return SiteHandler


@dataclass
class CrawlRun:
    site_url: str
    site: FakeSite
    outside_paths: list[str]
    pages: list[DiscoveredPage]
    records: tuple[CapturedRequest, ...]


@contextmanager
def serve_and_crawl(browser: Browser, **overrides: str) -> Iterator[CrawlRun]:
    outside_paths: list[str] = []
    with run_server(make_counting_handler(outside_paths)) as outside_url:
        site = FakeSite(outside_url=outside_url)
        with run_server(make_site_handler(site)) as site_url:
            env = {"CRAWLER_TARGET_URL": f"{site_url}/", "CRAWLER_MAX_DEPTH": DEFAULT_MAX_DEPTH}
            env.update(overrides)
            pages, records = run_crawl(browser, load_config(env))
            yield CrawlRun(site_url, site, outside_paths, pages, records)


def run_crawl(browser: Browser, config: CrawlerConfig) -> tuple[list[DiscoveredPage], tuple[CapturedRequest, ...]]:
    context = open_role_context(browser, config, GUEST_ROLE)
    try:
        capture = start_capture(context, config, GUEST_ROLE)
        pages = crawl(context, config, GUEST_ROLE, capture)
        return pages, capture.records
    finally:
        context.close()


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        yield chromium
        chromium.close()


@pytest.fixture(scope="module")
def default_run(browser: Browser) -> Iterator[CrawlRun]:
    with serve_and_crawl(browser) as crawl_run:
        yield crawl_run


def page_at(crawl_run: CrawlRun, path: str) -> DiscoveredPage:
    return next(page for page in crawl_run.pages if urlsplit(page.url).path == path)


def link_to(page: DiscoveredPage, path: str) -> PageLink:
    return next(link for link in page.links if urlsplit(link.url).path == path)


def action_labeled(page: DiscoveredPage, label: str) -> PageAction:
    return next(action for action in page.actions if action.label == label)


def record_for(crawl_run: CrawlRun, method: str, path: str) -> CapturedRequest:
    return next(
        record for record in crawl_run.records if record.method == method and urlsplit(record.url).path == path
    )


def test_pages_visited_in_bfs_order(default_run: CrawlRun) -> None:
    paths = [urlsplit(page.url).path for page in default_run.pages]
    depths = [page.depth for page in default_run.pages]

    assert paths == EXPECTED_ORDER
    assert depths == sorted(depths)
    assert page_at(default_run, "/").title == "Home"
    assert all(page.role == GUEST_ROLE for page in default_run.pages)


def test_stops_at_max_depth(browser: Browser) -> None:
    with serve_and_crawl(browser, CRAWLER_MAX_DEPTH="1") as crawl_run:
        assert ("GET", "/a/deep") not in crawl_run.site.received
        assert link_to(page_at(crawl_run, "/a"), "/a/deep").outcome == "beyond_max_depth"
        assert max(page.depth for page in crawl_run.pages) == 1


def test_each_template_visited_once(default_run: CrawlRun) -> None:
    endpoints = [page.endpoint for page in default_run.pages]

    assert len(endpoints) == len(set(endpoints))
    assert ("GET", "/items/2") not in default_run.site.received
    assert link_to(page_at(default_run, "/"), "/items/2").outcome == "already_visited"
    assert link_to(page_at(default_run, "/b"), "/").outcome == "already_visited"
    assert page_at(default_run, "/items/1").endpoint == "/items/{id}"


def test_state_changing_actions_not_executed(default_run: CrawlRun) -> None:
    root = page_at(default_run, "/")

    for request in STATE_CHANGING_REQUESTS:
        assert request not in default_run.site.received
    assert link_to(root, "/logout").outcome == "not_executed_state_changing"
    order_form = action_labeled(root, "order")
    assert order_form.kind == "form"
    assert order_form.method == "POST"
    assert order_form.fields == [FormField(name="csrf_token", type="hidden")]
    assert order_form.outcome == "not_executed_state_changing"
    assert action_labeled(root, "삭제").outcome == "not_executed_state_changing"
    save = action_labeled(root, "save")
    assert save.outcome == "blocked_state_changing_request"
    assert save.is_state_changing is True
    # 서버에는 안 갔지만 버튼이 무엇을 보내려 했는지는 KG 재료로 남는다.
    blocked = record_for(default_run, "POST", "/api/save")
    assert blocked.status is None
    assert (blocked.source_page, blocked.source_action) == (root.url, save.action_id)


def test_form_fields_recorded_as_name_and_type(default_run: CrawlRun) -> None:
    root = page_at(default_run, "/")

    assert action_labeled(root, "save profile").fields == EXPECTED_PROFILE_FIELDS
    assert action_labeled(root, "search").fields == [FormField(name="q", type="text")]
    assert all(action.fields == [] for action in root.actions if action.kind == "button")


def test_form_field_values_not_stored(default_run: CrawlRun) -> None:
    for page in default_run.pages:
        text = page.model_dump_json()
        for value in FIELD_VALUES:
            assert value not in text


def test_state_changing_actions_executed_when_allowed(browser: Browser, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger=explorer.__name__)
    with serve_and_crawl(browser, CRAWLER_ALLOW_STATE_CHANGING="true") as crawl_run:
        root = page_at(crawl_run, "/")
        a_page = page_at(crawl_run, "/a")
        cart = action_labeled(a_page, "add to cart")

        for request in STATE_CHANGING_REQUESTS:
            assert request in crawl_run.site.received
        assert action_labeled(root, "order").outcome == "executed"
        assert action_labeled(root, "save").outcome == "executed"
        # POST가 성공한 뒤 뜨는 alert도 탐색을 멈추지 않는다.
        assert cart.outcome == "executed"
        assert record_for(crawl_run, "POST", CART_API_PATH).status == 200
        assert_dialogs_did_not_stop_crawl(crawl_run, a_page, cart)
        # 본문을 받는 중에 이동한 요청도 기록되고, 이후 행동이 그 요청을 기다리지 않는다.
        assert record_for(crawl_run, "POST", TRACK_API_PATH).status == 200
        assert timeout_warnings(caplog) == []


def timeout_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if TIMEOUT_WARNING_TEXT in record.getMessage()]


def assert_dialogs_did_not_stop_crawl(crawl_run: CrawlRun, a_page: DiscoveredPage, cart: PageAction) -> None:
    after_alert = record_for(crawl_run, "GET", AFTER_ALERT_PATH)
    assert (after_alert.source_page, after_alert.source_action) == (a_page.url, cart.action_id)
    # 장바구니 폼 뒤의 행동과 그다음 페이지도 계속 처리됐다.
    assert action_labeled(a_page, "lookup").outcome == "executed"
    assert action_labeled(a_page, "open item").outcome == "already_visited"
    assert "/a/deep" in [urlsplit(page.url).path for page in crawl_run.pages]


def test_intercepted_post_form_recorded_and_blocked(default_run: CrawlRun) -> None:
    a_page = page_at(default_run, "/a")
    cart = action_labeled(a_page, "add to cart")

    assert cart.kind == "form"
    assert cart.method == "GET"
    assert cart.outcome == "blocked_state_changing_request"
    assert cart.is_state_changing is True
    blocked = record_for(default_run, "POST", CART_API_PATH)
    assert blocked.status is None
    assert (blocked.source_page, blocked.source_action) == (a_page.url, cart.action_id)
    # 비어 있는 required 칸이 있어도 값을 채우지 않고 제출 이벤트까지 간다.
    assert blocked.body_params == {"product_id": ["3"], "note": [""]}


def test_dialogs_after_blocked_request_do_not_stop_crawl(default_run: CrawlRun) -> None:
    a_page = page_at(default_run, "/a")

    assert_dialogs_did_not_stop_crawl(default_run, a_page, action_labeled(a_page, "add to cart"))


def test_blocked_request_outranks_navigation(default_run: CrawlRun) -> None:
    a_page = page_at(default_run, "/a")
    track = action_labeled(a_page, "track and open")

    assert track.outcome == "blocked_state_changing_request"
    assert track.is_state_changing is True
    blocked = record_for(default_run, "POST", TRACK_API_PATH)
    assert (blocked.source_page, blocked.source_action) == (a_page.url, track.action_id)
    # 막힌 뒤의 이동도 실제로 일어났다.
    assert any(
        urlsplit(record.url).path == "/items/9" and record.source_action == track.action_id
        for record in default_run.records
    )


def test_intercepted_form_without_navigation_links_fetch(default_run: CrawlRun) -> None:
    a_page = page_at(default_run, "/a")
    lookup = action_labeled(a_page, "lookup")
    record = record_for(default_run, "GET", LOOKUP_API_PATH)

    assert lookup.outcome == "executed"
    assert record.status == 200
    assert (record.source_page, record.source_action) == (a_page.url, lookup.action_id)


def test_get_form_navigation_enqueued_or_already_visited(default_run: CrawlRun) -> None:
    root = page_at(default_run, "/")
    a_page = page_at(default_run, "/a")
    search = action_labeled(root, "search")
    open_item = action_labeled(a_page, "open item")

    assert search.outcome == "enqueued"
    search_page = page_at(default_run, "/search")
    assert (search_page.source_page, search_page.source_action) == (root.url, search.action_id)
    # 이미 본 템플릿이라 큐에는 안 들어가지만 제출은 실제로 해서 문서 요청이 그 폼에 붙는다.
    assert open_item.outcome == "already_visited"
    item_request = record_for(default_run, "GET", "/items/9")
    assert (item_request.source_page, item_request.source_action) == (a_page.url, open_item.action_id)


def test_requests_linked_to_actions(default_run: CrawlRun) -> None:
    root = page_at(default_run, "/")
    b_page = page_at(default_run, "/b")

    start = record_for(default_run, "GET", "/")
    assert (start.source_page, start.source_action) == (None, "start")
    a_document = record_for(default_run, "GET", "/a")
    assert (a_document.source_page, a_document.source_action) == (root.url, link_to(root, "/a").action_id)
    search = record_for(default_run, "GET", "/search")
    assert search.query_params == {"q": ["shoes"]}
    assert (search.source_page, search.source_action) == (root.url, action_labeled(root, "search").action_id)
    # 클릭 뒤 늦게 나가는 fetch도 다음 행동이 아니라 그 버튼에 붙어야 한다.
    data = record_for(default_run, "GET", "/api/data")
    assert (data.source_page, data.source_action) == (root.url, action_labeled(root, "load").action_id)
    b_load = record_for(default_run, "GET", "/api/b-load")
    assert (b_load.source_page, b_load.source_action) == (b_page.url, "load")


def test_button_navigation_enqueues_target(default_run: CrawlRun) -> None:
    root = page_at(default_run, "/")
    next_button = action_labeled(root, "next")

    assert next_button.outcome == "executed"
    assert next_button.target_url == f"{default_run.site_url}/c"
    c_page = page_at(default_run, "/c")
    assert (c_page.source_page, c_page.source_action) == (root.url, next_button.action_id)


def test_never_requests_outside_origin(default_run: CrawlRun) -> None:
    root = page_at(default_run, "/")

    assert default_run.outside_paths == []
    assert all(urlsplit(link.url).netloc == urlsplit(default_run.site_url).netloc for link in root.links)


def test_pages_round_trip_as_json(default_run: CrawlRun) -> None:
    for page in default_run.pages:
        assert DiscoveredPage.model_validate_json(page.model_dump_json()) == page
    assert '"source_page":null' in page_at(default_run, "/").model_dump_json()


# ---- 끝나지 않는 요청 안전망 ----

HANG_API_PATH = "/api/hang"
QUICK_API_PATHS = ("/api/quick-1", "/api/quick-2")
# 시간 초과를 빨리 보려고 이 테스트에서만 줄인다.
SHORT_QUIET_TIMEOUT_S = 1.0
HANG_MAX_S = 30
HANG_ROOT_HTML = f"""<html><head><title>Hang</title></head><body>
<a href="/next">next</a>
<button id="hang">hang</button>
<button id="quick1">quick one</button>
<button id="quick2">quick two</button>
<script>
document.getElementById("hang").addEventListener("click", () => fetch("{HANG_API_PATH}"));
document.getElementById("quick1").addEventListener("click", () => fetch("{QUICK_API_PATHS[0]}"));
document.getElementById("quick2").addEventListener("click", () => fetch("{QUICK_API_PATHS[1]}"));
</script>
</body></html>"""


def make_hang_handler(release: threading.Event) -> type[BaseHTTPRequestHandler]:
    class HangHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == HANG_API_PATH:
                # 롱 폴링처럼 헤더와 본문 일부만 보내고 끝내지 않는다.
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.write(b'{"partial":')
                self.wfile.flush()
                release.wait(HANG_MAX_S)
                return
            body = HANG_ROOT_HTML if path == "/" else make_simple_html("Next")
            content_type = "application/json" if path in QUICK_API_PATHS else "text/html; charset=utf-8"
            encoded = ("{}" if path in QUICK_API_PATHS else body).encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            pass

    return HangHandler


def test_unfinished_request_waited_once_then_recorded(
    browser: Browser, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(explorer, "QUIET_TIMEOUT_S", SHORT_QUIET_TIMEOUT_S)
    caplog.set_level(logging.WARNING, logger=explorer.__name__)
    release = threading.Event()
    with run_server(make_hang_handler(release)) as site_url:
        try:
            pages, records = run_crawl(browser, load_config({"CRAWLER_TARGET_URL": f"{site_url}/"}))
        finally:
            release.set()
    root = next(page for page in pages if urlsplit(page.url).path == "/")
    hang_button = action_labeled(root, "hang")
    hang = next(record for record in records if urlsplit(record.url).path == HANG_API_PATH)

    # 한 번만 기다리고, 경고에는 건수와 endpoint만 남는다.
    assert len(timeout_warnings(caplog)) == 1
    assert "1개" in timeout_warnings(caplog)[0] and HANG_API_PATH in timeout_warnings(caplog)[0]
    assert hang.status == 200
    assert hang.response_shape is None
    assert (hang.source_page, hang.source_action) == (root.url, hang_button.action_id)
    # 뒤의 행동과 페이지도 제 행동에 붙어 정상 기록된다.
    for label, path in zip(("quick one", "quick two"), QUICK_API_PATHS, strict=True):
        quick = next(record for record in records if urlsplit(record.url).path == path)
        assert quick.source_action == action_labeled(root, label).action_id
        assert quick.response_shape == {}
    assert "/next" in [urlsplit(page.url).path for page in pages]
