"""진단 모듈 없이 정적 Web·접근 경계·로그 비노출만 검증한다."""

import json
import logging
import threading
from collections.abc import Iterator
from http.client import HTTPConnection
from pathlib import Path

import pytest

from local_web.server import LocalWebServer, WebSettings


@pytest.fixture
def web_server() -> Iterator[LocalWebServer]:
    server = LocalWebServer(WebSettings("127.0.0.1", 0, 8080))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def request(
    server: LocalWebServer, path: str, headers: dict[str, str] | None = None,
    method: str = "GET",
) -> tuple[int, dict[str, str], bytes]:
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_health_is_web_only(web_server: LocalWebServer) -> None:
    status, headers, body = request(web_server, "/health")
    assert status == 200
    assert json.loads(body) == {
        "status": "ok", "scope": "web_only", "diagnosis_enabled": False,
    }
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


@pytest.mark.parametrize("path", ["/", "/style.css", "/app.js"])
def test_static_assets(web_server: LocalWebServer, path: str) -> None:
    status, headers, body = request(web_server, path)
    assert status == 200
    assert len(body) == int(headers["Content-Length"])
    assert body


def test_head_has_no_body(web_server: LocalWebServer) -> None:
    status, headers, body = request(web_server, "/", method="HEAD")
    assert status == 200
    assert int(headers["Content-Length"]) > 0
    assert body == b""


@pytest.mark.parametrize("path", [
    "/.env", "/pipeline.py", "/modules/", "/runs/run/private/",
    "/datasets/example/ground_truth.json", "/../README.md",
    "/%2e%2e/README.md", "/health?password=SECRET_SENTINEL",
    "http://localhost:8080/health",
])
def test_non_public_paths_rejected(web_server: LocalWebServer, path: str) -> None:
    status, _, body = request(web_server, path)
    assert status == 404
    assert b"SECRET_SENTINEL" not in body


@pytest.mark.parametrize("host", ["attacker.example:8080", "0.0.0.0:8080", "localhost:1"])
def test_untrusted_host_rejected(web_server: LocalWebServer, host: str) -> None:
    status, _, _ = request(web_server, "/health", {"Host": host})
    assert status == 403


def test_cross_origin_rejected(web_server: LocalWebServer) -> None:
    status, _, _ = request(web_server, "/health", {"Origin": "https://attacker.example"})
    assert status == 403


def test_same_origin_accepted(web_server: LocalWebServer) -> None:
    host = f"127.0.0.1:{web_server.server_port}"
    status, _, _ = request(web_server, "/health", {"Origin": f"http://{host}"})
    assert status == 200


def test_duplicate_host_rejected(web_server: LocalWebServer) -> None:
    connection = HTTPConnection("127.0.0.1", web_server.server_port, timeout=3)
    try:
        connection.putrequest("GET", "/health")
        connection.putheader("Host", "localhost:8080")
        connection.endheaders()
        assert connection.getresponse().status == 403
    finally:
        connection.close()


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "OPTIONS", "PATCH"])
def test_mutations_disabled(web_server: LocalWebServer, method: str) -> None:
    status, _, _ = request(web_server, "/health", method=method)
    assert status == 405


def test_request_values_not_logged(
    web_server: LocalWebServer, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="local_web.server")
    status, _, body = request(web_server, "/health?password=SECRET_SENTINEL")
    assert status == 404
    assert "SECRET_SENTINEL" not in caplog.text
    assert b"SECRET_SENTINEL" not in body
    assert "http_status=404" in caplog.text


def test_static_symlink_escape_rejected(
    web_server: LocalWebServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "frontend"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("SECRET_SENTINEL", encoding="utf-8")
    (root / "index.html").symlink_to(outside)
    monkeypatch.setattr("local_web.server.FRONTEND_ROOT", root)
    status, _, body = request(web_server, "/")
    assert status == 503
    assert b"SECRET_SENTINEL" not in body


@pytest.mark.parametrize("value", ["0", "-1", "65536", "text", "８０８０", " 8080", ""])
def test_invalid_port_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="포트 설정 오류"):
        WebSettings.from_environment({"LOCAL_WEB_PORT": value})


def test_environment_defaults_and_container_ports() -> None:
    assert WebSettings.from_environment({}) == WebSettings("127.0.0.1", 8080, 8080)
    assert WebSettings.from_environment({
        "LOCAL_WEB_BIND_HOST": "0.0.0.0", "LOCAL_WEB_PORT": "8080",
        "LOCAL_WEB_PUBLIC_PORT": "18080",
    }) == WebSettings("0.0.0.0", 8080, 18080)


def test_unsupported_bind_host_rejected() -> None:
    with pytest.raises(ValueError, match="BIND_HOST"):
        WebSettings.from_environment({"LOCAL_WEB_BIND_HOST": "target-app"})
