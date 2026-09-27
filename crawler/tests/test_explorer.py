from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Browser, sync_playwright

from crawler.auth import open_role_context
from crawler.capture import start_capture
from crawler.config import GUEST_ROLE, CrawlerConfig, load_config
from crawler.explorer import crawl
from crawler.schemas import CapturedRequest, DiscoveredPage, PageAction, PageLink
from crawler.tests.helpers import make_counting_handler, run_server

DEFAULT_MAX_DEPTH = "2"
BUTTON_FETCH_DELAY_MS = 150
# 기본 설정(max_depth=2)에서 기대하는 BFS 방문 순서. /items/2는 /items/1과 같은 템플릿이라 없다.
EXPECTED_ORDER = ["/", "/a", "/b", "/items/1", "/search", "/c", "/a/deep"]
STATE_CHANGING_REQUESTS = [
    ("GET", "/logout"),
    ("POST", "/orders"),
    ("POST", "/api/remove-item"),
    ("POST", "/api/save"),
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
<form method="post" action="/orders"><input name="csrf_token" value="csrf-value"><button>order</button></form>
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
        "/a": make_simple_html("A", "/a/deep"),
        "/a/deep": make_simple_html("Deep", "/a/deep/deeper"),
        "/a/deep/deeper": make_simple_html("Deeper"),
        "/b": B_HTML,
        "/c": make_simple_html("C"),
        "/items/1": make_simple_html("Item"),
        "/items/2": make_simple_html("Item"),
        "/search": make_simple_html("Search"),
        "/logout": make_simple_html("Bye"),
    }
    json_routes = {"/api/data", "/api/b-load"}

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
    assert order_form.field_names == ["csrf_token"]
    assert order_form.outcome == "not_executed_state_changing"
    assert action_labeled(root, "삭제").outcome == "not_executed_state_changing"
    save = action_labeled(root, "save")
    assert save.outcome == "blocked_state_changing_request"
    assert save.is_state_changing is True
    # 서버에는 안 갔지만 버튼이 무엇을 보내려 했는지는 KG 재료로 남는다.
    blocked = record_for(default_run, "POST", "/api/save")
    assert blocked.status is None
    assert (blocked.source_page, blocked.source_action) == (root.url, save.action_id)


def test_state_changing_actions_executed_when_allowed(browser: Browser) -> None:
    with serve_and_crawl(browser, CRAWLER_ALLOW_STATE_CHANGING="true") as crawl_run:
        root = page_at(crawl_run, "/")

        for request in STATE_CHANGING_REQUESTS:
            assert request in crawl_run.site.received
        assert action_labeled(root, "order").outcome == "executed"
        assert action_labeled(root, "save").outcome == "executed"


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
