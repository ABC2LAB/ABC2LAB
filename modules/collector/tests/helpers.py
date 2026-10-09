"""여러 테스트가 같이 쓰는 대역. 가짜 서버(127.0.0.1의 빈 포트에만 뜬다)와 세션 창구 전송 대역."""

import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from modules.collector.session_gateway import SessionResponse

LOCALHOST = "127.0.0.1"


@dataclass
class FakeBackend:
    """스크립트된 전송 대역. 보낸 요청을 기록하고, 응답·쿠키 이름·로그인 상태를 테스트가 정한다."""

    response: SessionResponse = field(default_factory=lambda: SessionResponse(200, (), {"ok": True}))
    logged_in: bool = True
    cookies: frozenset[str] = frozenset()
    requests: list[tuple[str, str, tuple[tuple[str, str], ...], Any]] = field(default_factory=list)
    close_calls: int = 0

    def is_logged_in(self) -> bool:
        return self.logged_in

    def request(self, method: str, url: str, headers: Sequence[tuple[str, str]], body: Any | None) -> SessionResponse:
        self.requests.append((method, url, tuple(headers), body))
        return self.response

    def cookie_names(self) -> frozenset[str]:
        return self.cookies

    def close(self) -> None:
        self.close_calls += 1


def make_counting_handler(requested_paths: list[str]) -> type[BaseHTTPRequestHandler]:
    class CountingHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requested_paths.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = do_GET

        def log_message(self, format: str, *args: object) -> None:
            pass

    return CountingHandler


@contextmanager
def run_server(handler_class: type[BaseHTTPRequestHandler]) -> Iterator[str]:
    server = ThreadingHTTPServer((LOCALHOST, 0), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{LOCALHOST}:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
