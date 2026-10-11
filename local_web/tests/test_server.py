"""진단 모듈 없이 정적 Web·접근 경계·로그 비노출만 검증한다."""

import json
import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from http.client import HTTPConnection
from pathlib import Path

import pytest

from local_web.server import LocalWebServer, WebSettings
from local_web.inputs import MAX_INPUT_BYTES, InputSettings


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


@dataclass(frozen=True)
class RequestData:
    method: str = "GET"
    body: bytes | None = None


def request(
    server: LocalWebServer, path: str, headers: dict[str, str] | None = None,
    data: RequestData | None = None,
) -> tuple[int, dict[str, str], bytes]:
    data = data or RequestData()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request(data.method, path, body=data.body, headers=headers or {})
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


def test_bind_failure_preserves_startup_error(web_server: LocalWebServer) -> None:
    with pytest.raises(OSError):
        LocalWebServer(WebSettings("127.0.0.1", web_server.server_port, 8080))


@pytest.mark.parametrize("path", ["/", "/style.css", "/app.js"])
def test_static_assets(web_server: LocalWebServer, path: str) -> None:
    status, headers, body = request(web_server, path)
    assert status == 200
    assert len(body) == int(headers["Content-Length"])
    assert body


def test_head_has_no_body(web_server: LocalWebServer) -> None:
    status, headers, body = request(web_server, "/", data=RequestData("HEAD"))
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
    status, _, _ = request(web_server, "/health", data=RequestData(method))
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


@pytest.fixture
def input_server(input_settings: InputSettings) -> Iterator[LocalWebServer]:
    server = LocalWebServer(WebSettings("127.0.0.1", 0, 8080), input_settings)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def input_headers(server: LocalWebServer) -> tuple[dict[str, str], dict[str, object]]:
    status, headers, body = request(server, "/api/input-options")
    assert status == 200
    options = json.loads(body)
    host = f"127.0.0.1:{server.server_port}"
    return {
        "Cookie": headers["Set-Cookie"].split(";")[0],
        "Origin": f"http://{host}", "X-CSRF-Token": options["csrf_token"],
        "Content-Type": "application/json",
    }, options


def test_input_options_missing_settings_is_not_ready(web_server: LocalWebServer) -> None:
    status, headers, body = request(web_server, "/api/input-options")
    options = json.loads(body)
    assert status == 200 and not options["input_enabled"]
    assert options["issues"] == ["TEMPLATE_UNAVAILABLE", "DATASETS_UNAVAILABLE"]
    assert "HttpOnly" in headers["Set-Cookie"] and "SameSite=Strict" in headers["Set-Cookie"]
    assert not options["diagnosis_enabled"]


@pytest.mark.parametrize("site", ["same-site", "cross-site"])
def test_cross_site_api_options_rejected(web_server: LocalWebServer, site: str) -> None:
    status, _, _ = request(web_server, "/api/input-options", {"Sec-Fetch-Site": site})
    assert status == 403


def test_input_prepare_clear_and_no_secrets_in_response_or_logs(
    input_server: LocalWebServer, input_payload: dict[str, object], caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    headers, options = input_headers(input_server)
    assert options["input_enabled"] and options["dataset_ids"] == ["case_v2"]
    assert options["accounts"] == [{"alias": "client_a", "role": "member"}, {"alias": "operator_a", "role": "staff"}]
    status, _, body = request(input_server, "/api/input-preparations", headers, RequestData("POST", json.dumps(input_payload).encode()))
    assert status == 200
    result = json.loads(body)
    assert result["status"] == "prepared" and result["account_count"] == 2
    assert not result["diagnosis_enabled"]
    assert b"LOGIN_SENTINEL" not in body and b"PASSWORD_SENTINEL" not in body
    assert "LOGIN_SENTINEL" not in caplog.text and "PASSWORD_SENTINEL" not in caplog.text
    status, _, body = request(input_server, "/api/input-preparations/clear", headers, RequestData("POST", b"{}"))
    assert status == 200 and json.loads(body)["status"] == "cleared"


@pytest.mark.parametrize("missing", ["Cookie", "Origin", "X-CSRF-Token"])
def test_input_requires_session_origin_and_csrf(
    input_server: LocalWebServer, input_payload: dict[str, object], missing: str,
) -> None:
    headers, _ = input_headers(input_server)
    del headers[missing]
    status, _, body = request(input_server, "/api/input-preparations", headers, RequestData("POST", json.dumps(input_payload).encode()))
    assert status == 403 and b"PASSWORD_SENTINEL" not in body


@pytest.mark.parametrize("name,value", [
    ("Origin", "http://attacker.example"), ("X-CSRF-Token", "wrong"),
    ("Cookie", "abc2lab_web_session=" + "0" * 64),
])
def test_input_rejects_untrusted_browser(
    input_server: LocalWebServer, input_payload: dict[str, object], name: str, value: str,
) -> None:
    headers, _ = input_headers(input_server)
    headers[name] = value
    status, _, _ = request(input_server, "/api/input-preparations", headers, RequestData("POST", json.dumps(input_payload).encode()))
    assert status == 403


@pytest.mark.parametrize("body", [
    b'{"password":"SECRET_SENTINEL", "password":"again"}',
    b'{"password":NaN}', b'{"password":Infinity}', b'\xff', b'[]', b'{bad SECRET_SENTINEL}',
])
def test_malformed_input_does_not_echo_values(
    input_server: LocalWebServer, body: bytes, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    headers, _ = input_headers(input_server)
    status, _, response = request(input_server, "/api/input-preparations", headers, RequestData("POST", body))
    assert status == 400
    assert b"SECRET_SENTINEL" not in response and "SECRET_SENTINEL" not in caplog.text


def test_oversized_input_rejected(input_server: LocalWebServer) -> None:
    headers, _ = input_headers(input_server)
    status, _, body = request(input_server, "/api/input-preparations", headers, RequestData("POST", b" " * (MAX_INPUT_BYTES + 1)))
    assert status == 413 and b" " * 10 not in body


def test_browser_sessions_do_not_share_prepared_input(
    input_server: LocalWebServer, input_payload: dict[str, object],
) -> None:
    first, _ = input_headers(input_server)
    second, _ = input_headers(input_server)
    request(input_server, "/api/input-preparations", first, RequestData("POST", json.dumps(input_payload).encode()))
    _, _, own_body = request(input_server, "/api/input-options", {"Cookie": first["Cookie"]})
    _, _, other_body = request(input_server, "/api/input-options", {"Cookie": second["Cookie"]})
    assert json.loads(own_body)["preparation"]["status"] == "prepared"
    assert json.loads(other_body)["preparation"]["status"] == "empty"
    second["X-CSRF-Token"] = first["X-CSRF-Token"]
    status, _, _ = request(input_server, "/api/input-preparations/clear", second, RequestData("POST", b"{}"))
    assert status == 403


@pytest.mark.parametrize("name,value", [("Content-Type", "text/plain"), ("Transfer-Encoding", "chunked")])
def test_input_framing_restrictions(input_server: LocalWebServer, name: str, value: str) -> None:
    headers, _ = input_headers(input_server)
    headers[name] = value
    status, _, _ = request(input_server, "/api/input-preparations", headers, RequestData("POST", b"{}"))
    assert status == 400


def test_duplicate_content_length_rejected(input_server: LocalWebServer) -> None:
    headers, _ = input_headers(input_server)
    connection = HTTPConnection("127.0.0.1", input_server.server_port, timeout=3)
    try:
        connection.putrequest("POST", "/api/input-preparations")
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.putheader("Content-Length", "2")
        connection.putheader("Content-Length", "2")
        connection.endheaders(b"{}")
        assert connection.getresponse().status == 400
    finally:
        connection.close()
