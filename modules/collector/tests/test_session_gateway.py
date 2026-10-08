"""세션 공개 창구 단위 테스트. 브라우저·앱 없이 FakeBackend로 창구 로직만 본다.

각 테스트는 '이 검사를 빼면 통과해 버리는' 버그 하나를 겨눈다. 실제 Playwright 백엔드·verifier 연결은 실제 실행으로 본다.
verifier 쪽(SessionExpiredError→indeterminate)은 verifier가 자기 대역으로 이미 검증한다(test_execution.py).
"""

from dataclasses import dataclass, field
from typing import Any, Sequence

from modules.collector.core.config import CrawlerConfig, load_config
from modules.collector.session_gateway import (
    BrowserSessionExecutor,
    BrowserSessionLease,
    SessionExpiredError,
    SessionResponse,
    SessionTransportError,
)

ALLOWED_URL = "http://localhost:8001/orders/7"


@dataclass
class _Req:
    """lease.send가 받는 요청의 최소 모양(verifier ReplayRequest를 import하지 않고 덕타이핑으로 맞춘다)."""

    method: str
    url: str
    headers: tuple[tuple[str, str], ...] = ()
    body: Any | None = None


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


def _config(success_check: str = "left_login_page", success_value: str | None = None) -> CrawlerConfig:
    login = {"path": "/login", "username_field": "email", "password_field": "password", "success_check": success_check}
    if success_value is not None:
        login["success_value"] = success_value
    settings = {
        "target_url": "http://localhost:8001",
        "roles": ["user"],
        "accounts": [{"alias": "user_a", "role": "user"}],
        "login": login,
    }
    return load_config(settings)


def _lease(backend: FakeBackend, **config_kwargs: Any) -> BrowserSessionLease:
    return BrowserSessionLease(_config(**config_kwargs), backend)


# --- 외부 주소 2차 차단 (decision 1) ---

def test_out_of_scope_url_blocked_before_dispatch() -> None:
    backend = FakeBackend()
    lease = _lease(backend)
    try:
        lease.send(_Req("GET", "http://evil.example/orders/7"))
    except SessionTransportError:
        pass
    else:
        raise AssertionError("허용 밖 URL인데 차단되지 않음")
    # 검사가 dispatch 앞에 있어야 한다: 백엔드로 한 건도 가면 안 된다.
    assert backend.requests == []


def test_in_scope_url_is_dispatched() -> None:
    backend = FakeBackend(response=SessionResponse(200, (), {"id": 7}))
    response = _lease(backend).send(_Req("GET", ALLOWED_URL))
    assert response.status_code == 200
    assert [(method, url) for method, url, _, _ in backend.requests] == [("GET", ALLOWED_URL)]


# --- is_valid 의미 ---

def test_is_valid_reflects_backend_login() -> None:
    assert _lease(FakeBackend(logged_in=True)).is_valid() is True
    assert _lease(FakeBackend(logged_in=False)).is_valid() is False


# --- 만료 감지: 로그인 리다이렉트 (신호 1) ---

def test_login_redirect_raises_expired_and_invalidates() -> None:
    backend = FakeBackend(response=SessionResponse(302, (("Location", "/login"),), None))
    lease = _lease(backend)
    try:
        lease.send(_Req("GET", ALLOWED_URL))
    except SessionExpiredError:
        pass
    else:
        raise AssertionError("로그인 리다이렉트인데 만료로 안 잡힘")
    # 만료 감지 후 그 lease는 무효(백엔드는 아직 logged_in True여도).
    assert backend.logged_in is True
    assert lease.is_valid() is False


def test_login_redirect_ignores_query_and_fragment() -> None:
    # /login?next=... 도 경로만 보고 잡아야 한다(쿼리·fragment 제외).
    backend = FakeBackend(response=SessionResponse(302, (("Location", "/login?next=/orders/7"),), None))
    try:
        _lease(backend).send(_Req("GET", ALLOWED_URL))
    except SessionExpiredError:
        return
    raise AssertionError("쿼리 붙은 로그인 리다이렉트를 못 잡음")


def test_non_login_redirect_returned_as_is() -> None:
    # 로그인 경로가 아닌 3xx는 만료가 아니라 응답 그대로 돌려준다(verifier가 Location을 재검사).
    backend = FakeBackend(response=SessionResponse(302, (("Location", "/orders/8"),), None))
    response = _lease(backend).send(_Req("GET", ALLOWED_URL))
    assert response.status_code == 302
    assert ("Location", "/orders/8") in response.headers


def test_status_401_403_not_expiry() -> None:
    # 403은 인가 거부(정상 failure)라 만료로 처리하면 안 된다. 401도 상태 코드로는 만료로 보지 않는다.
    for status in (401, 403):
        response = _lease(FakeBackend(response=SessionResponse(status, (), {"detail": "no"}))).send(
            _Req("GET", ALLOWED_URL)
        )
        assert response.status_code == status


# --- 만료 감지: 성공 쿠키 소실 (신호 2, cookie_present 설정일 때만) ---

def test_cookie_present_expiry_on_cookie_drop() -> None:
    backend = FakeBackend(response=SessionResponse(200, (), {"ok": True}), cookies=frozenset({"other"}))
    try:
        _lease(backend, success_check="cookie_present", success_value="sid").send(_Req("GET", ALLOWED_URL))
    except SessionExpiredError:
        return
    raise AssertionError("성공 쿠키(sid)가 사라졌는데 만료로 안 잡힘")


def test_cookie_present_ok_when_cookie_kept() -> None:
    backend = FakeBackend(response=SessionResponse(200, (), {"ok": True}), cookies=frozenset({"sid"}))
    response = _lease(backend, success_check="cookie_present", success_value="sid").send(_Req("GET", ALLOWED_URL))
    assert response.status_code == 200


def test_cookie_drop_not_expiry_without_cookie_check() -> None:
    # 기본 설정(left_login_page)에서는 쿠키 소실을 만료 신호로 쓰지 않는다.
    backend = FakeBackend(response=SessionResponse(200, (), {"ok": True}), cookies=frozenset())
    response = _lease(backend).send(_Req("GET", ALLOWED_URL))
    assert response.status_code == 200


# --- 비밀 헤더 제거 ---

def test_response_secret_headers_stripped() -> None:
    backend = FakeBackend(
        response=SessionResponse(
            200,
            (("Set-Cookie", "sid=abc"), ("Authorization", "Bearer x"), ("X-Session-Token", "t"), ("Content-Type", "application/json")),
            {"ok": True},
        )
    )
    response = _lease(backend).send(_Req("GET", ALLOWED_URL))
    names = {name.lower() for name, _ in response.headers}
    assert names == {"content-type"}


def test_request_secret_headers_stripped_before_backend() -> None:
    lease_backend = FakeBackend()
    _lease(lease_backend).send(
        _Req("GET", ALLOWED_URL, headers=(("Cookie", "sid=abc"), ("Authorization", "Bearer x"), ("Accept", "application/json")))
    )
    _, _, sent_headers, _ = lease_backend.requests[0]
    assert {name.lower() for name, _ in sent_headers} == {"accept"}


# --- 대여/반납/종료 ---

def test_release_closes_backend() -> None:
    backend = FakeBackend()
    lease = _lease(backend)
    lease.release()
    assert backend.close_calls == 1


def test_executor_lease_creates_backend_and_close_closes_all() -> None:
    created: list[FakeBackend] = []

    def factory(account_id: str) -> FakeBackend:
        backend = FakeBackend()
        created.append(backend)
        return backend

    executor = BrowserSessionExecutor(_config(), factory)
    executor.lease("account:user_a")
    executor.lease("account:user_b")
    assert len(created) == 2
    executor.close()
    assert all(backend.close_calls == 1 for backend in created)
