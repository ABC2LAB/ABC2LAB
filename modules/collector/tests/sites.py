"""collector 테스트용 가짜 대상 사이트. helpers.run_server로 127.0.0.1의 빈 포트에만 뜬다.

로그인 사이트: guest는 공개 페이지만 보고, user(alice)는 로그인해 /mine과 그 API를 본다.
admin은 서버가 모르는 비밀번호라 로그인에 실패한다(partial 결과를 만들기 위해).
"""

import time
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

LOGIN_PATH = "/signin"
HOME_PATH = "/"
PUBLIC_PATH = "/public"
MINE_PATH = "/mine"
MINE_API_PATH = "/api/mine/7"
QUERY_TOKEN = "query-token-value"
MINE_API_QUERY = f"page=1&access_token={QUERY_TOKEN}"
USERNAME_FIELD = "login_id"
PASSWORD_FIELD = "login_pw"
USER_LOGIN_ID = "alice"
USER_PASSWORD = "user-pw-do-not-store"
ADMIN_LOGIN_ID = "root"
ADMIN_PASSWORD = "admin-pw-do-not-store"
SESSION_COOKIE = "sid"
SESSION_VALUE = "alice-session"
MINE_API_BODY = '{"items": [{"id": 7, "owner_id": 2, "email": "alice@example.com"}]}'
# /mine의 POST 폼에 미리 채워 둔 값. 상태 변경 폼이라 실행하지 않고, DOM 근거에도 값은 남으면 안 된다.
PREFILLED_VALUE = "prefilled-input-value"
MINE_DELETE_PATH = "/mine/delete"
HTML_TYPE = "text/html; charset=utf-8"
JSON_TYPE = "application/json"

HOME_HTML = f"""<html><head><title>Home</title></head><body>
<a href="{PUBLIC_PATH}">public</a>
<a href="{MINE_PATH}">mine</a>
</body></html>"""
MINE_HTML = f"""<html><head><title>Mine</title></head><body>
<script>fetch("{MINE_API_PATH}?{MINE_API_QUERY}");</script>
<form method="post" action="{MINE_DELETE_PATH}">
<input type="hidden" name="csrf_token" value="{PREFILLED_VALUE}"><input name="note" value="{PREFILLED_VALUE}">
<button>Delete</button>
</form>
</body></html>"""
LOGIN_HTML = f"""<html><head><title>Sign in</title></head><body>
<form method="post" action="{LOGIN_PATH}">
<input name="{USERNAME_FIELD}"><input type="password" name="{PASSWORD_FIELD}"><button>Sign in</button>
</form></body></html>"""

# 시계 역행 사이트: 이만큼 응답한 뒤부터 Date 헤더를 뒤로 돌린다.
CLOCK_STEP_S = 3
CLOCK_STEP_AFTER = 2
CLOCK_SITE_PATHS = ("/p1", "/p2", "/p3")


def make_login_site_handler() -> type[BaseHTTPRequestHandler]:
    class LoginSiteHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            is_logged_in = f"{SESSION_COOKIE}={SESSION_VALUE}" in self.headers.get("Cookie", "")
            if path == HOME_PATH:
                self._send(HTML_TYPE, HOME_HTML)
            elif path == PUBLIC_PATH:
                self._send(HTML_TYPE, "<html><head><title>Public</title></head></html>")
            elif path == LOGIN_PATH:
                self._send(HTML_TYPE, LOGIN_HTML)
            elif path == MINE_PATH and is_logged_in:
                self._send(HTML_TYPE, MINE_HTML)
            elif path == MINE_API_PATH and is_logged_in:
                self._send(JSON_TYPE, MINE_API_BODY)
            elif path in (MINE_PATH, MINE_API_PATH):
                self._redirect(LOGIN_PATH)
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
            if form.get(USERNAME_FIELD) == [USER_LOGIN_ID] and form.get(PASSWORD_FIELD) == [USER_PASSWORD]:
                self._redirect(HOME_PATH, cookie=f"{SESSION_COOKIE}={SESSION_VALUE}; Path=/")
            else:
                self._send(HTML_TYPE, LOGIN_HTML)

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

    return LoginSiteHandler


def make_login_env(site_url: str) -> dict[str, str]:
    """user는 로그인되고 admin은 실패하는 설정."""
    return {
        "CRAWLER_TARGET_URL": f"{site_url}/",
        "CRAWLER_ROLES": "user,admin",
        "CRAWLER_ROLE_USER_USERNAME": USER_LOGIN_ID,
        "CRAWLER_ROLE_USER_PASSWORD": USER_PASSWORD,
        "CRAWLER_ROLE_ADMIN_USERNAME": ADMIN_LOGIN_ID,
        "CRAWLER_ROLE_ADMIN_PASSWORD": ADMIN_PASSWORD,
        "CRAWLER_LOGIN_PATH": LOGIN_PATH,
        "CRAWLER_LOGIN_USERNAME_FIELD": USERNAME_FIELD,
        "CRAWLER_LOGIN_PASSWORD_FIELD": PASSWORD_FIELD,
        "CRAWLER_LOGIN_SUCCESS_CHECK": "left_login_page",
        "CRAWLER_MAX_DEPTH": "2",
    }


def make_clock_site_handler(step_after: int | None) -> type[BaseHTTPRequestHandler]:
    served = [0]

    class ClockSiteHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            links = "".join(f'<a href="{path}">{path}</a>' for path in CLOCK_SITE_PATHS)
            encoded = f"<html><body>{links}</body></html>".encode()
            self.send_response(200)
            self.send_header("Content-Type", HTML_TYPE)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            served[0] += 1

        def date_time_string(self, timestamp: float | None = None) -> str:
            # 테스트 환경 VM 시계가 뒤로 점프한 것처럼 Date 헤더를 돌린다.
            is_stepped = step_after is not None and served[0] >= step_after
            return formatdate(time.time() - (CLOCK_STEP_S if is_stepped else 0), usegmt=True)

        def log_message(self, format: str, *args: object) -> None:
            pass

    return ClockSiteHandler
