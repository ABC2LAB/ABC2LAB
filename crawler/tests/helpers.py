"""여러 테스트가 같이 쓰는 가짜 서버 도구. 전부 127.0.0.1의 빈 포트에만 뜬다."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCALHOST = "127.0.0.1"


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
