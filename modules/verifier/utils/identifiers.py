"""응답 JSON에서 자원 식별자(id 꼴 키의 숫자·UUID 값)를 뽑는다. resource_owner(자원 동일성) 평가에 쓴다.

기준은 collector 응답 근거의 identifiers와 같다(collector README "근거 파일"). 다른 모듈 코드는 import하지 않고 여기 다시 구현한다.
- 키: id(대소문자 무관), _id로 끝남(대소문자 무관), 소문자 뒤 Id·ID로 끝남(userId, userID).
- 값: 0 이상의 정수(bool 제외), 숫자로만 된 문자열, UUID 문자열. 원래 JSON 타입을 그대로 둔다("42"는 문자열).
- 민감 키(session_id 등)와 그 아래는 보지 않는다.
- 위치는 RFC 6901 JSON Pointer. 깊이·개수 상한은 collector와 같다(collector `core/identifiers.py`를 바꾸면 같이 바꾼다).
"""

import re
from collections.abc import Callable
from typing import Any

ID_KEY = "id"
ID_SUFFIX = "_id"
# 소문자 바로 뒤의 Id·ID (userId, userID). valid·paid처럼 소문자 id로 끝나는 단어는 걸리지 않는다.
CAMEL_ID_KEY_PATTERN = re.compile(r".*[a-z](Id|ID)")
DIGITS_PATTERN = re.compile(r"[0-9]+")
UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
MAX_IDENTIFIER_DEPTH = 6
MAX_IDENTIFIERS = 1000
POINTER_SEPARATOR = "/"


def is_identifier_key(key: str) -> bool:
    lowered = key.lower()
    return lowered == ID_KEY or lowered.endswith(ID_SUFFIX) or bool(CAMEL_ID_KEY_PATTERN.fullmatch(key))


def to_identifier_value(value: Any) -> int | str | None:
    """식별자로 볼 수 있는 값이면 원래 타입 그대로, 아니면 None."""
    # bool은 int의 하위 클래스라 먼저 걸러낸다.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and (DIGITS_PATTERN.fullmatch(value) or UUID_PATTERN.fullmatch(value)):
        return value
    return None


def extract_identifiers(document: Any, is_sensitive_key: Callable[[str], bool]) -> dict[str, int | str]:
    """JSON 값에서 {pointer: 식별자 값}을 문서 순서대로 뽑는다. 상한에 걸리면 그때까지 찾은 것만 돌려준다."""
    found: dict[str, int | str] = {}
    _walk(document, "", 0, is_sensitive_key, found)
    return found


def _walk(
    value: Any, pointer: str, depth: int, is_sensitive_key: Callable[[str], bool], found: dict[str, int | str]
) -> None:
    if not isinstance(value, dict | list) or depth >= MAX_IDENTIFIER_DEPTH:
        return
    children = value.items() if isinstance(value, dict) else enumerate(value)
    for key, child in children:
        if len(found) >= MAX_IDENTIFIERS:
            return
        child_pointer = f"{pointer}{POINTER_SEPARATOR}{_escape_pointer_token(str(key))}"
        if isinstance(value, dict):
            if is_sensitive_key(str(key)):
                continue
            identifier = to_identifier_value(child) if is_identifier_key(str(key)) else None
            if identifier is not None:
                found[child_pointer] = identifier
        _walk(child, child_pointer, depth + 1, is_sensitive_key, found)


def _escape_pointer_token(token: str) -> str:
    # RFC 6901: ~를 먼저 바꿔야 /를 바꾼 ~1이 다시 바뀌지 않는다.
    return token.replace("~", "~0").replace(POINTER_SEPARATOR, "~1")
