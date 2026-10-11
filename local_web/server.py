"""입력·기존 Runner CLI·표현을 연결하는 로컬 개발용 서버."""

from __future__ import annotations

import json
import logging
import os
import signal
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from local_web.inputs import (
    MAX_ACCOUNTS, MAX_INPUT_BYTES, TOKEN_PATTERN, InputError, InputSettings, InputStore,
    list_dataset_ids, load_template, template_accounts,
)
from local_web.runner import RunnerError, RunnerSettings, RunnerStore

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8080
REQUEST_TIMEOUT_SECONDS = 10
SESSION_COOKIE = "abc2lab_web_session"
RUN_COOKIE = "abc2lab_web_run"
INPUT_POST_ROUTES = {"/api/input-preparations", "/api/input-preparations/clear", "/api/runs"}
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

    def __init__(self, settings: WebSettings, input_settings: InputSettings | None = None,
                 runner_settings: RunnerSettings | None = None) -> None:
        self.inputs = InputStore(input_settings or InputSettings())
        self.runs = RunnerStore(runner_settings or RunnerSettings())
        super().__init__((settings.bind_host, settings.port), RequestHandler)
        # 컨테이너 내부 healthcheck 포트와 브라우저 공개 포트를 모두 검사한다.
        ports = {self.server_port, settings.public_port}
        self.allowed_authorities = {
            f"{host}:{port}" for host in ("localhost", "127.0.0.1") for port in ports
        }
        if 80 in ports:
            self.allowed_authorities.update({"localhost", "127.0.0.1"})

    def server_close(self) -> None:
        self.runs.close()
        self.inputs.close()
        super().server_close()

    def handle_error(self, request: object, client_address: object) -> None:
        # 기본 traceback 대신 고정 메시지로 기록해 입력·내부 경로 노출을 막는다.
        logger.error("http_request_failed")


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
        sites = self.headers.get_all("Sec-Fetch-Site", [])
        if self.path.startswith("/api/") and (
            len(sites) > 1 or (sites and sites[0] not in {"same-origin", "none"})
        ):
            self.send_error(HTTPStatus.FORBIDDEN)
            return False
        if self.command not in {"GET", "HEAD", "POST"} or (
            self.command == "POST" and self.path not in INPUT_POST_ROUTES
        ):
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)
            return False
        return True

    def do_GET(self) -> None:
        if self.path == "/api/run-status":
            try:
                payload = self.server.runs.status(self.cookie_token(RUN_COOKIE))
                self.respond_json(HTTPStatus.OK, payload)
            except RunnerError:
                self.respond_json(HTTPStatus.NOT_FOUND, {"error": "RUN_UNAVAILABLE"})
            return
        if self.path == "/api/input-options":
            self.show_input_options()
            return
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

    def session_id(self) -> str | None:
        token = self.cookie_token(SESSION_COOKIE)
        return token if token and TOKEN_PATTERN.fullmatch(token) else None

    def cookie_token(self, name: str) -> str | None:
        cookies = self.headers.get_all("Cookie", [])
        if len(cookies) != 1:
            return None
        try:
            parsed = SimpleCookie(cookies[0])
        except CookieError:
            return None
        cookie = parsed.get(name)
        # 잘못된 cookie를 '없음'으로 바꾸어 소유권 오류를 숨기지 않는다.
        return cookie.value if cookie else None

    def show_input_options(self) -> None:
        try:
            session_id, session = self.server.inputs.open_session(self.session_id())
        except InputError:
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        issues, accounts, dataset_ids = [], [], []
        try:
            accounts = template_accounts(load_template(self.server.inputs.settings))
        except InputError as error:
            issues.append(error.code)
        try:
            dataset_ids = list_dataset_ids(self.server.inputs.settings)
            if not dataset_ids:
                issues.append("DATASETS_EMPTY")
        except InputError as error:
            issues.append(error.code)
        readiness = self.server.runs.readiness()
        payload = {
            "input_enabled": not issues, "dataset_ids": dataset_ids, "accounts": accounts,
            "issues": issues, "csrf_token": session.csrf_token,
            "limits": {"max_accounts": MAX_ACCOUNTS},
            "preparation": self.server.inputs.preparation_summary(session),
            "diagnosis_enabled": readiness["diagnosis_enabled"] and not issues,
            "runner": readiness,
        }
        self.respond_json(HTTPStatus.OK, payload, {
            "Set-Cookie": f"{SESSION_COOKIE}={session_id}; HttpOnly; SameSite=Strict; Path=/",
        })

    def do_POST(self) -> None:
        origins = self.headers.get_all("Origin", [])
        tokens = self.headers.get_all("X-CSRF-Token", [])
        session_id = self.session_id()
        if len(origins) != 1 or len(tokens) != 1 or session_id is None:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        try:
            self.server.inputs.authenticate(session_id, tokens[0])
        except InputError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        try:
            payload = self.read_input_json()
            if self.path == "/api/runs":
                self.start_run(session_id, tokens[0], payload)
                return
            if self.path.endswith("/clear"):
                if payload != {}:
                    raise InputError("INPUT_INVALID")
                self.server.inputs.clear_preparation(session_id, tokens[0])
                result = {"status": "cleared", "diagnosis_enabled": False}
            else:
                result = self.server.inputs.prepare(session_id, tokens[0], payload)
                readiness = self.server.runs.readiness()
                result["diagnosis_enabled"] = readiness["diagnosis_enabled"] and not readiness["is_busy"]
        except InputError as error:
            logger.info("input_rejected code=%s", error.code)
            status = HTTPStatus.BAD_REQUEST
            if error.code == "INPUT_TOO_LARGE":
                status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
            elif error.code in {"TEMPLATE_INVALID", "TEMPLATE_UNAVAILABLE", "TEMPLATE_LOGIN_MISSING", "DATASETS_UNAVAILABLE", "INPUT_PREPARATION_FAILED"}:
                status = HTTPStatus.SERVICE_UNAVAILABLE
            elif error.code == "INPUT_SESSION_INVALID":
                status = HTTPStatus.FORBIDDEN
            self.respond_json(status, {"error": error.code})
            return
        except OSError:
            logger.error("input_storage_failed")
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        self.respond_json(HTTPStatus.OK, result)

    def start_run(self, session_id: str, csrf_token: str, payload: object) -> None:
        if (not isinstance(payload, dict) or set(payload) != {"preparation_id"}
                or not isinstance(payload["preparation_id"], str)
                or not TOKEN_PATTERN.fullmatch(payload["preparation_id"])):
            raise InputError("INPUT_INVALID")
        try:
            record = self.server.runs.start(self.server.inputs,
                (session_id, csrf_token, payload["preparation_id"]), self.cookie_token(RUN_COOKIE))
        except RunnerError as error:
            logger.info("runner_rejected code=%s", error.code)
            status = HTTPStatus.CONFLICT if error.code == "RUNNER_BUSY" else HTTPStatus.SERVICE_UNAVAILABLE
            self.respond_json(status, {"error": error.code})
            return
        self.respond_json(HTTPStatus.ACCEPTED, self.server.runs.status(record.owner_token), {
            "Set-Cookie": f"{RUN_COOKIE}={record.owner_token}; HttpOnly; SameSite=Strict; Path=/",
        })

    def read_input_json(self) -> object:
        lengths = self.headers.get_all("Content-Length", [])
        types = self.headers.get_all("Content-Type", [])
        if self.headers.get_all("Transfer-Encoding", []):
            raise InputError("INPUT_INVALID")
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal():
            raise InputError("INPUT_INVALID")
        length = int(lengths[0]) if len(lengths[0]) < 10 else MAX_INPUT_BYTES + 1
        if length > MAX_INPUT_BYTES:
            raise InputError("INPUT_TOO_LARGE")
        if length < 2 or len(types) != 1 or types[0].lower() not in {"application/json", "application/json; charset=utf-8"}:
            raise InputError("INPUT_INVALID")
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                raise InputError("INPUT_INVALID")
            return json.loads(body.decode("utf-8"), object_pairs_hook=unique_json_object, parse_constant=reject_json_constant)
        except (OSError, UnicodeError, ValueError, RecursionError):
            raise InputError("INPUT_INVALID") from None

    def respond_json(self, status: int, payload: object, headers: Mapping[str, str] | None = None) -> None:
        self.respond(status, json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                     "application/json; charset=utf-8", headers)

    def respond(self, status: int, body: bytes, content_type: str, headers: Mapping[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.close_connection = True
        if getattr(self, "command", None) != "HEAD":
            try:
                self.wfile.write(body)
            except OSError:
                logger.info("http_client_disconnected")

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


def unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for name, value in pairs:
        if name in result:
            raise InputError("INPUT_INVALID")
        result[name] = value
    return result


def reject_json_constant(value: str) -> None:
    raise InputError("INPUT_INVALID")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        settings = WebSettings.from_environment(os.environ)
        server = LocalWebServer(settings, InputSettings.from_environment(os.environ),
                                RunnerSettings.from_environment(os.environ))
    except (ValueError, OSError):
        logger.error("web_startup_failed")
        return 1
    logger.info("local_web_started")
    # Docker의 SIGTERM도 finally를 통과해 소유 자식과 임시 입력을 정리한다.
    previous_handler = signal.signal(signal.SIGTERM, stop_web)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("local_web_stopped")
    finally:
        server.server_close()
        signal.signal(signal.SIGTERM, previous_handler)
    return 0


def stop_web(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    raise SystemExit(main())
