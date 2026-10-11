"""브라우저가 따라가는 리다이렉트 hop도 허용 대상 밖으로 나가지 않는지 본다(절대 규칙 1번).

context.route 가드는 리다이렉트면 첫 URL에만 걸린다. 허용 origin의 페이지 이동·fetch·로그인 응답이
302로 다른 호스트를 가리키면 그 다음 hop은 가드를 거치지 않는다. 바깥 서버는 허용 서버와 다른 호스트로 띄우고
받은 경로를 센다. 한 번이라도 받으면 뚫린 것이다. 다른 호스트는 두 가지로 만든다:
IP 리터럴(127.0.0.2, 이름 해석 없음)과 이름(localhost, 127.0.0.1의 다른 포트, 이름 해석을 탐).
브라우저를 띄우는 쪽까지 같이 보려고 운영 경로(service.collect, entrypoint.run)로 돌린다.
"""

import json
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from modules.collector import entrypoint
from modules.collector.core.config import load_config, secret_key_names
from modules.collector.entrypoint import CONFIG_PATH_ENV, SECRETS_PATH_ENV
from modules.collector.service import collect
from modules.collector.tests.helpers import LOCALHOST, make_counting_handler, run_server
from modules.collector.tests.sites import write_config_files

# 이 PR 3단계(브라우저 모든 hop 제한)에서 통과한다. 그 커밋에서 마커를 지운다.
NOT_YET_BLOCKED = pytest.mark.xfail(strict=True, reason="리다이렉트 hop 외부 이동 차단 미구현 — 이 PR 3단계에서 해결")

HOME_PATH = "/"
PAGE_REDIRECT_PATH = "/go"
FETCH_REDIRECT_PATH = "/bounce"
LOGIN_PATH = "/signin"
OUTSIDE_PAGE_PATH = "/outside-page"
OUTSIDE_FETCH_PATH = "/outside-fetch"
OUTSIDE_LOGIN_PATH = "/outside-after-login"
REDIRECT_STATUS = 302
USERNAME_FIELD = "login_id"
PASSWORD_FIELD = "login_pw"
ROLE = "user"
ALIAS = "user_a"
USERNAME = "alice"
PASSWORD = "redirect-pw-do-not-log"
LOGIN_ID_KEY, PASSWORD_KEY = secret_key_names(ALIAS)
ACCOUNT_ID = f"account:{ALIAS}"
MAX_DEPTH = 2
RUN_ID = "redirect_scope_run"
ARTIFACT_RELATIVE_DIR = Path("artifacts") / "iteration-000" / "collector"
ARTIFACT_FILE_NAME = "crawl_result.json"
ACCOUNT_CRAWL_FAILED = "ACCOUNT_CRAWL_FAILED"
# collector 모듈 로거 이름(getLogger(__name__))의 공통 앞부분.
COLLECTOR_LOGGER_PREFIX = "modules.collector."
# 셸에 남은 COLLECTOR_*(설정 위치·계정 값)가 테스트 설정보다 우선하지 않게 치운다.
COLLECTOR_ENV_PREFIX = "COLLECTOR_"

LINK_HOME_HTML = f'<html><body><a href="{PAGE_REDIRECT_PATH}">go</a></body></html>'
FETCH_HOME_HTML = f'<html><body><script>fetch("{FETCH_REDIRECT_PATH}");</script></body></html>'
# 로그인 리다이렉트만 보려고 guest 탐색에서 리다이렉트를 타지 않는 홈.
PLAIN_HOME_HTML = "<html><body><p>home</p></body></html>"
LOGIN_HTML = (
    f'<html><body><form method="post" action="{LOGIN_PATH}">'
    f'<input name="{USERNAME_FIELD}"><input type="password" name="{PASSWORD_FIELD}">'
    "<button>Sign in</button></form></body></html>"
)


@dataclass(frozen=True)
class OutsideTarget:
    """바깥 서버를 띄울 주소와 리다이렉트 Location에 쓸 호스트."""

    bind_host: str
    url_host: str


# 허용 서버(127.0.0.1)와 다른 IP. 이름 해석 없이 바로 간다.
IP_LITERAL_TARGET = OutsideTarget(bind_host="127.0.0.2", url_host="127.0.0.2")
# 허용 서버와 같은 주소의 다른 포트를 이름으로 부른다. 이름 해석 경로를 탄다.
NAME_TARGET = OutsideTarget(bind_host=LOCALHOST, url_host="localhost")


@dataclass
class RedirectSite:
    """허용 서버. 리다이렉트 경로는 전부 바깥 서버를 가리킨다. 받은 요청을 'METHOD 경로'로 남긴다."""

    outside_url: str
    home_html: str = LINK_HOME_HTML
    received_requests: list[str] = field(default_factory=list)


@dataclass
class OutsideServer:
    url: str
    requested_paths: list[str]


def make_redirect_site_handler(site: RedirectSite) -> type[BaseHTTPRequestHandler]:
    redirect_targets = {
        PAGE_REDIRECT_PATH: OUTSIDE_PAGE_PATH,
        FETCH_REDIRECT_PATH: OUTSIDE_FETCH_PATH,
    }

    class RedirectSiteHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            site.received_requests.append(f"GET {self.path}")
            path = urlsplit(self.path).path
            if path == HOME_PATH:
                self._send_html(site.home_html)
            elif path == LOGIN_PATH:
                self._send_html(LOGIN_HTML)
            elif path in redirect_targets:
                self._redirect(f"{site.outside_url}{redirect_targets[path]}")
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            site.received_requests.append(f"POST {self.path}")
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            # 아이디·비밀번호와 상관없이 로그인 응답이 바깥으로 보낸다.
            self._redirect(f"{site.outside_url}{OUTSIDE_LOGIN_PATH}")

        def _send_html(self, body: str) -> None:
            encoded = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _redirect(self, location: str) -> None:
            self.send_response(REDIRECT_STATUS)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    return RedirectSiteHandler


@pytest.fixture
def outside(request: pytest.FixtureRequest) -> Iterator[OutsideServer]:
    """허용 목록에 없는 호스트. 요청이 한 번이라도 오면 리다이렉트 hop이 뚫린 것. 기본은 IP 리터럴."""
    target: OutsideTarget = getattr(request, "param", IP_LITERAL_TARGET)
    requested_paths: list[str] = []
    with run_server(make_counting_handler(requested_paths), host=target.bind_host) as bound_url:
        port = urlsplit(bound_url).port
        yield OutsideServer(url=f"http://{target.url_host}:{port}", requested_paths=requested_paths)


@pytest.fixture
def site(outside: OutsideServer) -> RedirectSite:
    return RedirectSite(outside_url=outside.url)


@pytest.fixture
def site_url(site: RedirectSite) -> Iterator[str]:
    with run_server(make_redirect_site_handler(site)) as url:
        yield url


@pytest.fixture
def clean_collector_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in [key for key in os.environ if key.startswith(COLLECTOR_ENV_PREFIX)]:
        monkeypatch.delenv(key)


def make_guest_settings(site_url: str) -> dict[str, Any]:
    return {"target_url": f"{site_url}{HOME_PATH}", "max_depth": MAX_DEPTH}


def make_login_settings(site_url: str) -> dict[str, Any]:
    return {
        **make_guest_settings(site_url),
        "roles": [ROLE],
        "login": {
            "path": LOGIN_PATH,
            "username_field": USERNAME_FIELD,
            "password_field": PASSWORD_FIELD,
            "success_check": "left_login_page",
        },
        "accounts": [{"alias": ALIAS, "role": ROLE}],
    }


def run_collect_entrypoint(base_dir: Path, settings: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> Path:
    """설정·비밀값 파일을 쓰고 entrypoint.run으로 collect를 돌린다. run_root를 돌려준다."""
    run_root = base_dir / "runs" / RUN_ID
    config_path, secrets_path = write_config_files(
        base_dir, RUN_ID, settings, {LOGIN_ID_KEY: USERNAME, PASSWORD_KEY: PASSWORD}
    )
    monkeypatch.setenv(CONFIG_PATH_ENV, str(config_path))
    monkeypatch.setenv(SECRETS_PATH_ENV, str(secrets_path))
    context = {"run_id": RUN_ID, "iteration": 0, "mode": "development", "run_root": str(run_root)}
    entrypoint.run("collect", [], run_root / ARTIFACT_RELATIVE_DIR, context)
    return run_root


@NOT_YET_BLOCKED
@pytest.mark.parametrize("outside", [IP_LITERAL_TARGET, NAME_TARGET], indirect=True, ids=["ip_literal", "name"])
def test_page_redirect_to_other_host_is_not_requested(
    site: RedirectSite, site_url: str, outside: OutsideServer
) -> None:
    site.home_html = LINK_HOME_HTML

    collect(load_config(make_guest_settings(site_url)))

    # 허용 서버가 리다이렉트 경로를 받았어야 이 테스트가 리다이렉트를 실제로 시험한 것이다.
    assert f"GET {PAGE_REDIRECT_PATH}" in site.received_requests
    assert outside.requested_paths == []


@NOT_YET_BLOCKED
def test_fetch_redirect_to_other_host_is_not_requested(
    site: RedirectSite, site_url: str, outside: OutsideServer
) -> None:
    site.home_html = FETCH_HOME_HTML

    collect(load_config(make_guest_settings(site_url)))

    assert f"GET {FETCH_REDIRECT_PATH}" in site.received_requests
    assert outside.requested_paths == []


@NOT_YET_BLOCKED
@pytest.mark.usefixtures("clean_collector_env")
def test_login_redirect_to_other_host_is_not_requested_and_login_fails(
    site: RedirectSite,
    site_url: str,
    outside: OutsideServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    site.home_html = PLAIN_HOME_HTML

    run_root = run_collect_entrypoint(tmp_path, make_login_settings(site_url), monkeypatch)

    assert f"POST {LOGIN_PATH}" in site.received_requests
    document = json.loads((run_root / ARTIFACT_RELATIVE_DIR / ARTIFACT_FILE_NAME).read_text(encoding="utf-8"))
    account_errors = [error for error in document["errors"] if error["item_ref"] == ACCOUNT_ID]
    assert [error["code"] for error in account_errors] == [ACCOUNT_CRAWL_FAILED]
    assert "LoginError" in account_errors[0]["message"]
    account = next(item for item in document["data"]["accounts"] if item["account_id"] == ACCOUNT_ID)
    assert account["session_ref"] is None
    # caplog가 collector 로그를 실제로 받고 있어야 아래 로그 검사가 의미 있다.
    assert [record for record in caplog.records if record.name.startswith(COLLECTOR_LOGGER_PREFIX)] != []
    # 비밀번호는 출력 폴더(crawl_result·근거 파일 전부)와 로그 어디에도 없다.
    output_files = [path for path in run_root.rglob("*") if path.is_file()]
    assert any(path.name == ARTIFACT_FILE_NAME for path in output_files)
    assert [path for path in output_files if PASSWORD.encode() in path.read_bytes()] == []
    assert PASSWORD not in caplog.text
    assert outside.requested_paths == []
