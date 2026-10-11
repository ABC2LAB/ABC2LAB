"""브라우저 이름 해석 규칙(browser_scope)이 허용 대상은 막지 않는지 본다.

막히는 쪽(허용 밖 hop)은 test_redirect_scope.py가 본다. 여기서는 규칙 문자열 모양과, 설정한 이름·IPv6 대상이
규칙을 건 브라우저에서 실제로 수집되는지를 운영 경로(entrypoint.run, service.collect)로 확인한다.

이름 대상은 *.localhost를 쓴다. 운영 규칙은 EXCLUDE된 이름의 MAP을 무시하므로 테스트용 MAP을 끼울 수 없고,
Chromium 내장 loopback 해석으로 이름 경로를 대신한다. 실제 DNS 해석(Compose 서비스명 등)은 OS 몫이다.

프록시 테스트는 대상을 이 머신의 비-loopback IP에 띄운다. Chromium은 loopback 대상을 프록시에서 늘 빼므로
loopback 대상으로는 프록시를 타는지 볼 수 없다. 비-loopback IP가 없는 환경이면 skip한다.
"""

import json
import os
import socket
from types import SimpleNamespace
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from modules.collector import entrypoint, session_gateway
from modules.collector.core.browser_scope import build_host_resolver_rules, build_launch_args
from modules.collector.core.config import load_config
from modules.collector.entrypoint import CONFIG_PATH_ENV, SECRETS_PATH_ENV
from modules.collector.service import collect
from modules.collector.tests.helpers import LOCALHOST, make_counting_handler, run_server
from modules.collector.tests.sites import (
    HOME_PATH,
    MINE_API_PATH,
    PUBLIC_PATH,
    USER_A_ALIAS,
    make_login_secrets,
    make_login_settings,
    make_login_site_handler,
    write_config_files,
)

# 규칙 문자열 단위 테스트용 설정 값. 같은 호스트의 다른 포트는 EXCLUDE 하나로 합쳐져야 한다.
RULES_TARGET_URL = "http://testapp_vulnerable:8000/start"
RULES_EXTRA_ORIGINS = ["https://app.corp.internal", "http://[fd00::1]:8080", "http://testapp_vulnerable:9000"]
EXPECTED_RULES = "MAP * ~NOTFOUND, EXCLUDE app.corp.internal, EXCLUDE fd00::1, EXCLUDE testapp_vulnerable"
DIRECT_CONNECTION_ARG = "--proxy-server=direct://"
PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
NO_PROXY_ENV_KEYS = ("NO_PROXY", "no_proxy")
# 비-loopback 송신 IP를 찾을 때 연결해 보는 주소(UDP라 실제로 보내지 않는다).
ROUTE_PROBE_ADDRESS = ("10.255.255.255", 1)
HTTP_OK = 200
# Compose 서비스명형·내부 도메인형 이름. *.localhost라 Chromium이 loopback으로 푼다(모듈 docstring).
SERVICE_NAME_HOST = "testapp-svc.localhost"
INTERNAL_DOMAIN_HOST = "app.corp.localhost"
IPV6_LOOPBACK = "::1"
RUN_ID = "browser_scope_run"
ARTIFACT_RELATIVE_DIR = Path("artifacts") / "iteration-000" / "collector"
ARTIFACT_FILE_NAME = "crawl_result.json"
COLLECTOR_ENV_PREFIX = "COLLECTOR_"


@pytest.fixture
def clean_collector_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in [key for key in os.environ if key.startswith(COLLECTOR_ENV_PREFIX)]:
        monkeypatch.delenv(key)


@pytest.fixture
def site_port() -> Iterator[int]:
    """127.0.0.1에 뜬 로그인 사이트의 포트. 이름 대상 URL은 이 포트에 이름만 바꿔 붙인다."""
    with run_server(make_login_site_handler()) as url:
        port = int(url.rsplit(":", 1)[1])
        yield port


@pytest.fixture
def ipv6_site() -> Iterator[tuple[str, list[tuple[str, str]]]]:
    """::1에 뜬 사이트 URL과 서버가 받은 (경로, Cookie) 기록. 이 환경에서 ::1에 바인드하지 못하면 skip."""
    try:
        with socket.socket(socket.AF_INET6) as probe:
            probe.bind((IPV6_LOOPBACK, 0))
    except OSError as error:
        pytest.skip(f"::1에 바인드할 수 없는 환경이라 IPv6 대상 확인을 건너뜀 ({type(error).__name__})")
    request_log: list[tuple[str, str]] = []
    with run_server(make_login_site_handler(request_log), host=IPV6_LOOPBACK) as url:
        yield url, request_log


@pytest.fixture
def proxy_requests(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """프록시 환경변수를 전부 카운팅 서버로 지정한다. 받은 요청이 있으면 프록시를 탄 것."""
    requested_paths: list[str] = []
    with run_server(make_counting_handler(requested_paths)) as proxy_url:
        for key in NO_PROXY_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        for key in PROXY_ENV_KEYS:
            monkeypatch.setenv(key, proxy_url)
        yield requested_paths


@pytest.fixture
def lan_site_url() -> Iterator[str]:
    """비-loopback IP에 뜬 로그인 사이트. 그런 IP가 없으면 skip."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(ROUTE_PROBE_ADDRESS)
            address = probe.getsockname()[0]
    except OSError as error:
        pytest.skip(f"비-loopback IP를 찾지 못해 프록시 확인을 건너뜀 ({type(error).__name__})")
    if address.startswith("127.") or address == LOCALHOST:
        pytest.skip("비-loopback IP가 없어 프록시 확인을 건너뜀")
    with run_server(make_login_site_handler(), host=address) as url:
        yield url


def run_collect_entrypoint(base_dir: Path, settings: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    run_root = base_dir / "runs" / RUN_ID
    config_path, secrets_path = write_config_files(base_dir, RUN_ID, settings, {})
    monkeypatch.setenv(CONFIG_PATH_ENV, str(config_path))
    monkeypatch.setenv(SECRETS_PATH_ENV, str(secrets_path))
    context = {"run_id": RUN_ID, "iteration": 0, "mode": "development", "run_root": str(run_root)}
    entrypoint.run("collect", [], run_root / ARTIFACT_RELATIVE_DIR, context)
    return json.loads((run_root / ARTIFACT_RELATIVE_DIR / ARTIFACT_FILE_NAME).read_text(encoding="utf-8"))


def test_rules_exclude_only_allowed_hosts() -> None:
    config = load_config({"target_url": RULES_TARGET_URL, "extra_allowed_origins": RULES_EXTRA_ORIGINS})

    assert build_host_resolver_rules(config) == EXPECTED_RULES
    assert build_launch_args(config) == [f"--host-resolver-rules={EXPECTED_RULES}", DIRECT_CONNECTION_ARG]


@pytest.mark.usefixtures("clean_collector_env")
@pytest.mark.parametrize("host", [SERVICE_NAME_HOST, INTERNAL_DOMAIN_HOST], ids=["service_name", "internal_domain"])
def test_name_target_is_collected(
    host: str, site_port: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_url = f"http://{host}:{site_port}{HOME_PATH}"

    document = run_collect_entrypoint(tmp_path, {"target_url": target_url}, monkeypatch)

    assert document["status"] == "completed"
    assert document["data"]["target_url"] == target_url
    page_urls = [page["url"] for page in document["data"]["pages"]]
    assert len(page_urls) >= 1
    assert any(url.endswith(PUBLIC_PATH) for url in page_urls)
    assert len(document["data"]["requests"]) >= 1


def test_ipv6_target_is_collected(ipv6_site: tuple[str, list[tuple[str, str]]]) -> None:
    site_url, request_log = ipv6_site

    outcome = collect(load_config({"target_url": f"{site_url}{HOME_PATH}"}))

    guest = outcome.account_crawls[0]
    assert guest.error is None
    assert len(guest.pages) >= 1
    assert HOME_PATH in [path for path, _ in request_log]


def test_crawl_ignores_proxy_env(lan_site_url: str, proxy_requests: list[str]) -> None:
    outcome = collect(load_config(make_login_settings(lan_site_url), make_login_secrets()))

    user_a = next(crawl for crawl in outcome.account_crawls if crawl.alias == USER_A_ALIAS)
    assert user_a.error is None
    assert user_a.session_ref is not None
    assert len(user_a.pages) >= 1
    assert proxy_requests == []


def test_gateway_lease_and_send_ignore_proxy_env(lan_site_url: str, proxy_requests: list[str]) -> None:
    config = load_config(make_login_settings(lan_site_url), make_login_secrets())
    request = SimpleNamespace(method="GET", url=f"{lan_site_url}{MINE_API_PATH}", headers=(), body=None)

    with session_gateway.open_session_executor(config) as executor:
        lease = executor.lease(f"account:{USER_A_ALIAS}")
        is_valid = lease.is_valid()
        response = lease.send(request)

    assert is_valid is True
    assert response.status_code == HTTP_OK
    assert proxy_requests == []

