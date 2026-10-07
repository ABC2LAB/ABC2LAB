"""세션 공개 창구의 verifier 쪽 계약(Protocol). 플랜 4번.

verifier는 collector 코드·브라우저/세션 객체를 import하지 않는다. 런너가 collector 구현(또는 테스트 대역)을
주입하고, verifier는 이 Protocol로만 쓴다. 실행 모델은 (a) 리스가 요청을 대신 전송 — 세션 쿠키는 collector 안에만
머물고 verifier는 resolve한 요청을 넘겨 응답을 받는다. 비밀값(쿠키·토큰)은 ReplayRequest/Response에 담지 않는다.

이 PR(1)은 게이트만 구현하므로 verify가 send를 호출하지 않는다. 실제 실행은 PR2.
"""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ReplayRequest:
    """바인딩·파라미터를 적용한 실제 전송 요청. 인증 쿠키·토큰은 담지 않는다(창구가 세션으로 붙인다)."""

    method: str
    url: str
    # 비밀이 아닌 헤더만. 인증 헤더·쿠키는 넣지 않는다.
    headers: tuple[tuple[str, str], ...] = ()
    body: Any | None = None


@dataclass(frozen=True)
class ReplayResponse:
    status_code: int
    headers: tuple[tuple[str, str], ...] = ()
    body: Any | None = None


@runtime_checkable
class SessionLease(Protocol):
    """계정 하나의 대여된 세션. 창구가 소유한 세션으로 요청을 대신 보낸다."""

    def is_valid(self) -> bool:
        """session_ref가 있어도 실제 유효성을 다시 확인한다(명세 m7)."""
        ...

    def send(self, request: ReplayRequest) -> ReplayResponse:
        """자동 리다이렉트를 따르지 않는다. verifier가 Location을 effective_origins로 다시 검사한다."""
        ...

    def release(self) -> None:
        """대여한 세션을 반납한다."""
        ...


@runtime_checkable
class SessionExecutor(Protocol):
    """세션 공개 창구. 계정 ID로 세션을 대여한다."""

    def lease(self, account_id: str) -> SessionLease:
        ...
