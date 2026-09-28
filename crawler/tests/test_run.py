import os
import re
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Browser, BrowserContext, sync_playwright

from crawler import run
from crawler.capture import RequestCapture
from crawler.config import GUEST_ROLE, CrawlerConfig, load_config
from crawler.run import RoleCrawl, RunInfo, assign_evidence_ids, main, make_run_id, run_crawl, save_result
from crawler.schemas import SCHEMA_VERSION, CapturedRequest, CrawlResult, DiscoveredPage
from crawler.tests.helpers import run_server

LOGIN_PATH = "/signin"
HOME_PATH = "/"
PUBLIC_PATH = "/public"
MINE_PATH = "/mine"
MINE_API_PATH = "/api/mine"
USERNAME_FIELD = "login_id"
PASSWORD_FIELD = "login_pw"
USER_PASSWORD = "user-pw-do-not-store"
# admin 계정은 서버가 모르는 비밀번호라 로그인에 실패한다.
ADMIN_PASSWORD = "admin-pw-do-not-store"
SESSION_COOKIE = "sid"
SESSION_VALUE = "alice-session"
RUN_ID_PATTERN = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{4}")
BASE_TIME = datetime(2026, 10, 1, 5, 12, 3, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_TIMEOUT_S = 120
RUN_ID_SAMPLES = 20
SETTLE_MS = 300
PAGE_A = "http://127.0.0.1:1/a"
PAGE_B = "http://127.0.0.1:1/b"

HOME_HTML = f"""<html><head><title>Home</title></head><body>
<a href="{PUBLIC_PATH}">public</a>
<a href="{MINE_PATH}">mine</a>
</body></html>"""
MINE_HTML = f"""<html><head><title>Mine</title></head><body>
<script>fetch("{MINE_API_PATH}");</script>
</body></html>"""
LOGIN_HTML = f"""<html><head><title>Sign in</title></head><body>
<form method="post" action="{LOGIN_PATH}">
<input name="{USERNAME_FIELD}"><input type="password" name="{PASSWORD_FIELD}"><button>Sign in</button>
</form></body></html>"""


def make_site_handler() -> type[BaseHTTPRequestHandler]:
    class SiteHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            is_logged_in = f"{SESSION_COOKIE}={SESSION_VALUE}" in self.headers.get("Cookie", "")
            if path == HOME_PATH:
                self._send("text/html; charset=utf-8", HOME_HTML)
            elif path == PUBLIC_PATH:
                self._send("text/html; charset=utf-8", "<html><head><title>Public</title></head></html>")
            elif path == LOGIN_PATH:
                self._send("text/html; charset=utf-8", LOGIN_HTML)
            elif path == MINE_PATH and is_logged_in:
                self._send("text/html; charset=utf-8", MINE_HTML)
            elif path == MINE_API_PATH and is_logged_in:
                self._send("application/json", '{"id": 1, "email": "alice@example.com"}')
            elif path in (MINE_PATH, MINE_API_PATH):
                self._redirect(LOGIN_PATH)
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
            if form.get(USERNAME_FIELD) == ["alice"] and form.get(PASSWORD_FIELD) == [USER_PASSWORD]:
                self._redirect(HOME_PATH, cookie=f"{SESSION_COOKIE}={SESSION_VALUE}; Path=/")
            else:
                self._send("text/html; charset=utf-8", LOGIN_HTML)

        def _send(self, content_type: str, body: str) -> None:
            encoded = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
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


def make_env(site_url: str) -> dict[str, str]:
    return {
        "CRAWLER_TARGET_URL": f"{site_url}/",
        "CRAWLER_ROLES": "user,admin",
        "CRAWLER_ROLE_USER_USERNAME": "alice",
        "CRAWLER_ROLE_USER_PASSWORD": USER_PASSWORD,
        "CRAWLER_ROLE_ADMIN_USERNAME": "root",
        "CRAWLER_ROLE_ADMIN_PASSWORD": ADMIN_PASSWORD,
        "CRAWLER_LOGIN_PATH": LOGIN_PATH,
        "CRAWLER_LOGIN_USERNAME_FIELD": USERNAME_FIELD,
        "CRAWLER_LOGIN_PASSWORD_FIELD": PASSWORD_FIELD,
        "CRAWLER_LOGIN_SUCCESS_CHECK": "left_login_page",
        "CRAWLER_MAX_DEPTH": "2",
    }


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        yield chromium
        chromium.close()


@pytest.fixture(scope="module")
def site_url() -> Iterator[str]:
    with run_server(make_site_handler()) as url:
        yield url


@pytest.fixture(scope="module")
def config(site_url: str) -> CrawlerConfig:
    return load_config(make_env(site_url))


@pytest.fixture(scope="module")
def result(browser: Browser, config: CrawlerConfig) -> CrawlResult:
    return run_crawl(browser, config)


# ---- 가짜 레코드로 evidence ID만 확인 ----


def make_page(role: str, url: str, source_page: str | None) -> DiscoveredPage:
    return DiscoveredPage(
        role=role,
        url=url,
        endpoint=urlsplit(url).path,
        title=None,
        status=200,
        depth=0 if source_page is None else 1,
        source_page=source_page,
        source_action=None if source_page is None else "link:0",
        links=[],
        actions=[],
    )


def make_request(role: str, url: str, source_page: str | None, offset_s: int) -> CapturedRequest:
    return CapturedRequest(
        role=role,
        method="GET",
        resource_type="fetch",
        url=url,
        endpoint=urlsplit(url).path,
        status=200,
        query_params={},
        body_params={},
        resource_ids=[],
        request_headers={},
        response_headers={},
        response_shape=None,
        source_page=source_page,
        source_action=None if source_page is None else "load",
        captured_at=BASE_TIME + timedelta(seconds=offset_s),
    )


def make_run_info() -> RunInfo:
    return RunInfo(
        run_id="20261001-051203-a3f9",
        target_base_url="http://127.0.0.1:1/",
        started_at=BASE_TIME,
        finished_at=BASE_TIME + timedelta(minutes=1),
    )


def make_role_crawls() -> list[RoleCrawl]:
    guest = RoleCrawl(
        role=GUEST_ROLE,
        pages=(make_page(GUEST_ROLE, PAGE_A, None), make_page(GUEST_ROLE, PAGE_B, PAGE_A)),
        records=(
            make_request(GUEST_ROLE, f"{PAGE_B}/api", PAGE_B, 5),
            make_request(GUEST_ROLE, PAGE_A, None, 1),
            make_request(GUEST_ROLE, f"{PAGE_A}/api", "http://127.0.0.1:1/unknown", 3),
        ),
        error=None,
    )
    # user에는 PAGE_B 페이지가 없다. 같은 URL이 guest에 있어도 이어지면 안 된다.
    # PAGE_A가 두 번 나오면(리다이렉트로 이미 본 페이지에 다시 옴) 처음 페이지로 잇는다.
    user = RoleCrawl(
        role="user",
        pages=(make_page("user", PAGE_A, None), make_page("user", PAGE_A, PAGE_A)),
        records=(make_request("user", f"{PAGE_B}/api", PAGE_B, 2), make_request("user", f"{PAGE_A}/x", PAGE_A, 4)),
        error=None,
    )
    admin = RoleCrawl(role="admin", pages=(), records=(), error="LoginError: admin 로그인 실패")
    return [guest, user, admin]


def test_ids_numbered_from_one_across_roles() -> None:
    crawl_result = assign_evidence_ids(make_run_info(), make_role_crawls())

    assert [role.id for role in crawl_result.roles] == ["role:guest", "role:user", "role:admin"]
    assert [page.id for role in crawl_result.roles for page in role.pages] == ["page:1", "page:2", "page:3", "page:4"]
    request_ids = [request.id for role in crawl_result.roles for request in role.requests]
    assert request_ids == ["request:1", "request:2", "request:3", "request:4", "request:5"]


def test_run_info_and_errors_kept() -> None:
    crawl_result = assign_evidence_ids(make_run_info(), make_role_crawls())

    assert crawl_result.schema_version == SCHEMA_VERSION == "1.0"
    assert crawl_result.run_id == "20261001-051203-a3f9"
    assert [role.error for role in crawl_result.roles] == [None, None, "LoginError: admin 로그인 실패"]


def test_requests_sorted_by_sent_time() -> None:
    guest = assign_evidence_ids(make_run_info(), make_role_crawls()).roles[0]

    assert [request.captured_at for request in guest.requests] == sorted(r.captured_at for r in guest.requests)
    assert guest.requests[0].url == PAGE_A


def test_request_source_page_id_links_same_role_page() -> None:
    guest, user, _ = assign_evidence_ids(make_run_info(), make_role_crawls()).roles

    assert [request.source_page_id for request in guest.requests] == [None, None, "page:2"]
    # user의 /b 요청은 guest의 page:2와 URL이 같아도 역할이 다르니 null
    assert [request.source_page_id for request in user.requests] == [None, "page:3"]


def test_page_source_page_id_links_parent() -> None:
    guest, user, _ = assign_evidence_ids(make_run_info(), make_role_crawls()).roles

    assert [page.source_page_id for page in guest.pages] == [None, "page:1"]
    assert [page.source_page_id for page in user.pages] == [None, "page:3"]


def test_run_id_format() -> None:
    run_id = make_run_id(BASE_TIME)

    assert RUN_ID_PATTERN.fullmatch(run_id)
    assert run_id.startswith("20261001-051203-")
    # 같은 초에 여러 번 돌려도 난수 부분으로 갈린다.
    assert len({make_run_id(BASE_TIME) for _ in range(RUN_ID_SAMPLES)}) > 1


# ---- 가짜 사이트에서 실제로 돌림 ----


def test_roles_follow_config_and_failed_role_recorded(result: CrawlResult, config: CrawlerConfig) -> None:
    roles = {role.role: role for role in result.roles}

    assert [role.role for role in result.roles] == list(config.roles) == [GUEST_ROLE, "user", "admin"]
    assert roles[GUEST_ROLE].error is None
    assert roles["user"].error is None
    assert roles["admin"].error is not None and "LoginError" in roles["admin"].error
    assert roles["admin"].pages == [] and roles["admin"].requests == []


def test_logged_in_role_reaches_private_page(result: CrawlResult) -> None:
    roles = {role.role: role for role in result.roles}
    user_paths = {urlsplit(page.url).path for page in roles["user"].pages}
    guest_paths = {urlsplit(page.url).path for page in roles[GUEST_ROLE].pages}

    assert MINE_PATH in user_paths
    assert MINE_PATH not in guest_paths
    assert LOGIN_PATH in guest_paths


def test_real_requests_linked_to_page_ids(result: CrawlResult) -> None:
    user = next(role for role in result.roles if role.role == "user")
    mine_page = next(page for page in user.pages if urlsplit(page.url).path == MINE_PATH)
    mine_api = next(request for request in user.requests if urlsplit(request.url).path == MINE_API_PATH)

    assert mine_api.source_page_id == mine_page.id
    assert mine_api.response_shape == {"id": "int", "email": "str"}
    assert mine_page.source_page_id is not None


def test_ids_unique_in_run(result: CrawlResult) -> None:
    page_ids = [page.id for role in result.roles for page in role.pages]
    request_ids = [request.id for role in result.roles for request in role.requests]

    assert len(page_ids) == len(set(page_ids)) > 0
    assert len(request_ids) == len(set(request_ids)) > 0
    assert RUN_ID_PATTERN.fullmatch(result.run_id)
    assert result.started_at <= result.finished_at


def test_crawl_failure_keeps_requests_and_continues(
    browser: Browser, config: CrawlerConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_crawl = run.crawl

    def crawl_then_fail(
        context: BrowserContext, cfg: CrawlerConfig, role: str, capture: RequestCapture
    ) -> list[DiscoveredPage]:
        if role != GUEST_ROLE:
            return real_crawl(context, cfg, role, capture)
        page = context.new_page()
        page.goto(cfg.start_url)
        page.wait_for_timeout(SETTLE_MS)
        raise RuntimeError(f"boom {USER_PASSWORD}")

    monkeypatch.setattr(run, "crawl", crawl_then_fail)
    crawl_result = run_crawl(browser, config)
    guest, user, _ = crawl_result.roles

    assert guest.error is not None and guest.error.startswith("RuntimeError: boom")
    assert USER_PASSWORD not in guest.error
    assert guest.pages == [] and len(guest.requests) > 0
    assert user.error is None and len(user.pages) > 0


# ---- 저장과 main ----


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """셸에 CRAWLER_* 가 있으면 .env보다 우선하므로 테스트 동안 치운다."""
    for key in [key for key in os.environ if key.startswith("CRAWLER_")]:
        monkeypatch.delenv(key)


def test_save_creates_parent_and_overwrites(tmp_path: Path) -> None:
    crawl_result = assign_evidence_ids(make_run_info(), make_role_crawls())
    output = tmp_path / "nested" / "dir" / "crawl_result.json"

    save_result(crawl_result, output)
    save_result(crawl_result, output)

    assert CrawlResult.model_validate_json(output.read_text(encoding="utf-8")) == crawl_result
    assert list(output.parent.iterdir()) == [output]


def test_save_rejects_file_that_reloads_differently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    crawl_result = assign_evidence_ids(make_run_info(), make_role_crawls())
    dumped = crawl_result.model_dump_json()
    # 직렬화가 값을 잃는 상황을 흉내 낸다. 재로드 검증이 없으면 그대로 저장되고 끝난다.
    monkeypatch.setattr(
        CrawlResult, "model_dump_json", lambda self, **_: dumped.replace(crawl_result.run_id, "changed")
    )

    with pytest.raises(ValueError, match="다시 읽었더니"):
        save_result(crawl_result, tmp_path / "crawl_result.json")


def test_cli_writes_valid_file_without_secrets(site_url: str, tmp_path: Path) -> None:
    # 이 모듈의 browser fixture가 sync Playwright를 쥐고 있어 같은 스레드에서 main을 부를 수 없다.
    # 실제 사용처럼 별도 프로세스로 돌리고, cwd의 .env를 읽는지도 같이 본다.
    env_lines = [f"{key}={value}" for key, value in make_env(site_url).items()]
    (tmp_path / ".env").write_text("\n".join(env_lines), encoding="utf-8")
    process_env = {key: value for key, value in os.environ.items() if not key.startswith("CRAWLER_")}
    process_env["PYTHONPATH"] = str(REPO_ROOT)
    process_env["PYTHONIOENCODING"] = "utf-8"
    output = Path("out") / "secure.json"

    completed = subprocess.run(
        [sys.executable, "-m", "crawler.run", "--output", str(output)],
        cwd=tmp_path,
        env=process_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=CLI_TIMEOUT_S,
        check=False,
    )

    text = (tmp_path / output).read_text(encoding="utf-8")
    saved = CrawlResult.model_validate_json(text)
    # admin 로그인이 실패했으니 파일은 남기되 종료 코드로 알린다.
    assert completed.returncode == 1
    assert saved.schema_version == "1.0"
    assert [role.id for role in saved.roles] == ["role:guest", "role:user", "role:admin"]
    assert USER_PASSWORD not in text and ADMIN_PASSWORD not in text
    assert SESSION_VALUE not in text
    # data/는 직접 열지 않으므로 요약 로그로 결과를 확인할 수 있어야 한다.
    assert "role=user pages=" in completed.stderr
    assert USER_PASSWORD not in completed.stderr and ADMIN_PASSWORD not in completed.stderr


@pytest.mark.usefixtures("clean_env")
def test_main_without_config_fails_without_file(tmp_path: Path) -> None:
    output = tmp_path / "crawl_result.json"

    exit_code = main(["--output", str(output)], env_path=tmp_path / "missing.env")

    assert exit_code == 2
    assert not output.exists()
