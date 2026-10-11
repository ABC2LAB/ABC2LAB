"""정적 화면과 Web 자체 상태만 제공하는 개발용 서버. 진단 실행은 아직 미연결."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8080
REQUEST_TIMEOUT_SECONDS = 10
FRONTEND_ROOT = Path(__file__).resolve().parent / "frontend"
STATIC_ROUTES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self'; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'self'"
    ),
}


@dataclass(frozen=True)
class WebSettings:
    bind_host: str
    port: int
    public_port: int

    @classmethod
    def from_environment(cls, values: Mapping[str, str]) -> WebSettings:
        bind_host = values.get("LOCAL_WEB_BIND_HOST", "127.0.0.1")
        if bind_host not in {"127.0.0.1", "0.0.0.0"}:
            raise ValueError("LOCAL_WEB_BIND_HOST 설정 오류")
        port = parse_port(values.get("LOCAL_WEB_PORT", str(DEFAULT_PORT)))
        public_port = parse_port(values.get("LOCAL_WEB_PUBLIC_PORT", str(port)))
        return cls(bind_host, port, public_port)


def parse_port(value: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise ValueError("Web 포트 설정 오류")
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("Web 포트 설정 오류")
    return port


class LocalWebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, settings: WebSettings) -> None:
        super().__init__((settings.bind_host, settings.port), RequestHandler)
        # 컨테이너 내부 healthcheck 포트와 브라우저 공개 포트를 모두 검사한다.
        ports = {self.server_port, settings.public_port}
        self.allowed_authorities = {
            f"{host}:{port}" for host in ("localhost", "127.0.0.1") for port in ports
        }
        if 80 in ports:
            self.allowed_authorities.update({"localhost", "127.0.0.1"})


class RequestHandler(BaseHTTPRequestHandler):
    server: LocalWebServer
    server_version = "ABC2LAB-local-web"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        hosts = self.headers.get_all("Host", [])
        origins = self.headers.get_all("Origin", [])
        if len(hosts) != 1 or hosts[0] not in self.server.allowed_authorities:
            self.send_error(HTTPStatus.FORBIDDEN)
            return False
        if len(origins) > 1 or (origins and origins[0] != f"http://{hosts[0]}"):
            self.send_error(HTTPStatus.FORBIDDEN)
            return False
        if self.command not in {"GET", "HEAD"}:
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)
            return False
        return True

    def do_GET(self) -> None:
        if self.path == "/health":
            # Web 응답을 모델 준비·인증·KG 접속·전체 실행의 성공으로 해석하지 않는다.
            payload = {"status": "ok", "scope": "web_only", "diagnosis_enabled": False}
            self.respond(HTTPStatus.OK, json.dumps(payload).encode(), "application/json")
            return
        route = STATIC_ROUTES.get(self.path)
        if route is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        file_name, content_type = route
        try:
            root = FRONTEND_ROOT.resolve(strict=True)
            path = (root / file_name).resolve(strict=True)
            if not path.is_relative_to(root):
                raise OSError("static asset outside frontend")
            body = path.read_bytes()
        except OSError:
            logger.error("static_asset_unavailable")
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        self.respond(HTTPStatus.OK, body, content_type)

    def do_HEAD(self) -> None:
        self.do_GET()

    def respond(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.close_connection = True
        if getattr(self, "command", None) != "HEAD":
            self.wfile.write(body)

    def send_error(
        self, code: int, message: str | None = None, explain: str | None = None
    ) -> None:
        # 파서가 넘기는 raw request·URL·header를 오류 응답과 로그에 반영하지 않는다.
        body = json.dumps({"error": HTTPStatus(code).phrase}).encode()
        self.respond(code, body, "application/json; charset=utf-8")

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        logger.info("http_status=%s", code)

    def log_message(self, format: str, *args: object) -> None:
        logger.warning("http_protocol_warning")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        settings = WebSettings.from_environment(os.environ)
        server = LocalWebServer(settings)
    except (ValueError, OSError):
        logger.error("web_startup_failed")
        return 1
    logger.info("local_web_started")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("local_web_stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
