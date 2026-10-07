"""collector 테스트용 가짜 대상 사이트와 설정 파일 도구. 사이트는 helpers.run_server로 127.0.0.1의 빈 포트에만 뜬다.

로그인 사이트: guest는 공개 페이지만 본다. user 역할 계정 둘(alice·bob)은 각자 로그인해 /mine과 자기 항목 API를 본다.
admin은 서버가 모르는 비밀번호라 로그인에 실패한다(partial 결과를 만들기 위해).
세션 쿠키는 사용자마다 다르다. cookie_log를 넘기면 서버가 받은 (경로, Cookie 헤더)를 남겨 쿠키가 섞이는지 볼 수 있다.
"""

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from modules.collector.core.config import secret_key_names

LOGIN_PATH = "/signin"
HOME_PATH = "/"
PUBLIC_PATH = "/public"
MINE_PATH = "/mine"
MINE_API_PREFIX = "/api/mine/"
QUERY_TOKEN = "query-token-value"
MINE_API_QUERY = f"page=1&access_token={QUERY_TOKEN}"
USERNAME_FIELD = "login_id"
PASSWORD_FIELD = "login_pw"
SESSION_COOKIE = "sid"
USER_ROLE = "user"
ADMIN_ROLE = "admin"
USER_A_ALIAS = "user_a"
USER_B_ALIAS = "user_b"
ADMIN_ALIAS = "admin_a"
USER_LOGIN_ID = "alice"
USER_PASSWORD = "user-pw-do-not-store"
SESSION_VALUE = "alice-session"
USER_B_LOGIN_ID = "bob"
USER_B_PASSWORD = "bob-pw-do-not-store"
USER_B_SESSION_VALUE = "bob-session"
ADMIN_LOGIN_ID = "root"
ADMIN_PASSWORD = "admin-pw-do-not-store"
# /mine의 POST 폼에 미리 채워 둔 값. 상태 변경 폼이라 실행하지 않고, DOM 근거에도 값은 남으면 안 된다.
PREFILLED_VALUE = "prefilled-input-value"
MINE_DELETE_PATH = "/mine/delete"
HTML_TYPE = "text/html; charset=utf-8"
JSON_TYPE = "application/json"
MAX_DEPTH = 2


@dataclass(frozen=True)
class SiteUser:
    password: str
    session_value: str
    item_id: int
    owner_id: int

    @property
    def api_path(self) -> str:
        return f"{MINE_API_PREFIX}{self.item_id}"


SITE_USERS = {
    USER_LOGIN_ID: SiteUser(USER_PASSWORD, SESSION_VALUE, item_id=7, owner_id=2),
    USER_B_LOGIN_ID: SiteUser(USER_B_PASSWORD, USER_B_SESSION_VALUE, item_id=8, owner_id=3),
}
# user_a(alice)의 API 경로. 계정별로 볼 때는 SITE_USERS[...].api_path를 쓴다.
MINE_API_PATH = SITE_USERS[USER_LOGIN_ID].api_path

HOME_HTML = f"""<html><head><title>Home</title></head><body>
<a href="{PUBLIC_PATH}">public</a>
<a href="{MINE_PATH}">mine</a>
</body></html>"""
LOGIN_HTML = f"""<html><head><title>Sign in</title></head><body>
<form method="post" action="{LOGIN_PATH}">
<input name="{USERNAME_FIELD}"><input type="password" name="{PASSWORD_FIELD}"><button>Sign in</button>
</form></body></html>"""

# 시계 역행 사이트: 이만큼 응답한 뒤부터 Date 헤더를 뒤로 돌린다.
CLOCK_STEP_S = 3
CLOCK_STEP_AFTER = 2
CLOCK_SITE_PATHS = ("/p1", "/p2", "/p3")


def render_mine_html(user: SiteUser) -> str:
    """사용자마다 자기 항목 API만 부른다. 다른 사용자의 항목 경로는 화면에 없다."""
    return f"""<html><head><title>Mine</title></head><body>
<script>fetch("{user.api_path}?{MINE_API_QUERY}");</script>
<form method="post" action="{MINE_DELETE_PATH}">
<input type="hidden" name="csrf_token" value="{PREFILLED_VALUE}"><input name="note" value="{PREFILLED_VALUE}">
<button>Delete</button>
</form>
</body></html>"""


def render_mine_api(user: SiteUser) -> str:
    item = {"id": user.item_id, "owner_id": user.owner_id, "email": f"owner{user.owner_id}@example.com"}
    return json.dumps({"items": [item]})


def make_login_site_handler(cookie_log: list[tuple[str, str]] | None = None) -> type[BaseHTTPRequestHandler]:
    class LoginSiteHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            cookie_header = self.headers.get("Cookie", "")
            if cookie_log is not None:
                cookie_log.append((path, cookie_header))
            user = _find_logged_in_user(cookie_header)
            if path == HOME_PATH:
                self._send(HTML_TYPE, HOME_HTML)
            elif path == PUBLIC_PATH:
                self._send(HTML_TYPE, "<html><head><title>Public</title></head></html>")
            elif path == LOGIN_PATH:
                self._send(HTML_TYPE, LOGIN_HTML)
            elif path == MINE_PATH and user is not None:
                self._send(HTML_TYPE, render_mine_html(user))
            elif user is not None and path == user.api_path:
                self._send(JSON_TYPE, render_mine_api(user))
            elif path == MINE_PATH or path.startswith(MINE_API_PREFIX):
                self._redirect(LOGIN_PATH)
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
            login_id = (form.get(USERNAME_FIELD) or [""])[0]
            user = SITE_USERS.get(login_id)
            if user is not None and form.get(PASSWORD_FIELD) == [user.password]:
                self._redirect(HOME_PATH, cookie=f"{SESSION_COOKIE}={user.session_value}; Path=/")
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


def _find_logged_in_user(cookie_header: str) -> SiteUser | None:
    for user in SITE_USERS.values():
        if f"{SESSION_COOKIE}={user.session_value}" in cookie_header:
            return user
    return None


def make_login_settings(site_url: str) -> dict[str, Any]:
    """user 역할 계정 둘(user_a=alice, user_b=bob)과 로그인에 실패하는 admin_a."""
    return {
        "target_url": f"{site_url}/",
        "max_depth": MAX_DEPTH,
        "roles": [USER_ROLE, ADMIN_ROLE],
        "login": {
            "path": LOGIN_PATH,
            "username_field": USERNAME_FIELD,
            "password_field": PASSWORD_FIELD,
            "success_check": "left_login_page",
        },
        "accounts": [
            {"alias": USER_A_ALIAS, "role": USER_ROLE},
            {"alias": USER_B_ALIAS, "role": USER_ROLE},
            {"alias": ADMIN_ALIAS, "role": ADMIN_ROLE},
        ],
    }


def make_login_secrets() -> dict[str, str]:
    secrets: dict[str, str] = {}
    for alias, login_id, password in (
        (USER_A_ALIAS, USER_LOGIN_ID, USER_PASSWORD),
        (USER_B_ALIAS, USER_B_LOGIN_ID, USER_B_PASSWORD),
        (ADMIN_ALIAS, ADMIN_LOGIN_ID, ADMIN_PASSWORD),
    ):
        login_id_key, password_key = secret_key_names(alias)
        secrets[login_id_key] = login_id
        secrets[password_key] = password
    return secrets


def to_toml(settings: Mapping[str, Any]) -> str:
    """테스트 설정 dict를 TOML로. 문자열·정수·bool·문자열 목록·표·표 목록만 다룬다(JSON 문자열은 TOML 기본 문자열과 호환)."""
    lines: list[str] = []
    tables: list[tuple[str, Mapping[str, Any]]] = []
    for key, value in settings.items():
        if isinstance(value, Mapping):
            tables.append((f"[{key}]", value))
        elif isinstance(value, list) and value and all(isinstance(item, Mapping) for item in value):
            tables.extend((f"[[{key}]]", item) for item in value)
        else:
            lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
    for header, table in tables:
        lines.append(f"\n{header}")
        lines.extend(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in table.items())
    return "\n".join(lines) + "\n"


def write_config_files(
    directory: Path, name: str, settings: Mapping[str, Any] | None, secrets: Mapping[str, str]
) -> tuple[Path, Path]:
    """name.toml과 name.env를 쓰고 경로를 돌려준다. settings가 None이면 설정 파일을 만들지 않는다(없는 파일)."""
    config_path = directory / f"{name}.toml"
    secrets_path = directory / f"{name}.env"
    if settings is not None:
        config_path.write_text(to_toml(settings), encoding="utf-8")
    secrets_path.write_text("".join(f"{key}={value}\n" for key, value in secrets.items()), encoding="utf-8")
    return config_path, secrets_path


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
