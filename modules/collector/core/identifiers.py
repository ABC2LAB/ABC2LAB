"""응답 JSON에서 자원 식별자(id 꼴 키의 숫자·UUID 값)만 골라낸다. 소유 관계를 관찰하는 근거다.

허용 목록 방식이라 처음 보는 앱에서도 이름·이메일 같은 값은 남지 않는다.
- 키: id(대소문자 무관), _id로 끝남(대소문자 무관), 소문자 뒤 Id·ID로 끝남(userId, userID). valid·paid는 아니다.
- 값: 0 이상의 정수, 숫자로만 된 문자열, UUID 문자열. 원래 JSON 타입을 그대로 둔다("42"는 문자열).
- 민감 키 규칙이 먼저다. 민감 키(session_id 등)와 그 아래는 보지 않는다. 계정 비밀번호가 섞인 값·위치는 버린다.
- 위치는 RFC 6901 JSON Pointer. 깊이·개수 상한을 넘으면 거기서 멈추고 is_truncated로 알린다.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from modules.collector.core.normalize import UUID_SEGMENT_PATTERN
from modules.collector.core.response_shape import MAX_SHAPE_DEPTH

# shape와 같은 깊이까지만 본다. shape가 "dict"/"list"로 끊은 곳 안쪽 id는 위치를 설명할 수 없다.
MAX_IDENTIFIER_DEPTH = MAX_SHAPE_DEPTH
# 근거 파일 하나가 너무 커지지 않게 하는 상한
MAX_IDENTIFIERS = 1000
ID_KEY = "id"
ID_SUFFIX = "_id"
# 소문자 바로 뒤의 Id·ID (userId, userID). valid·paid처럼 소문자 id로 끝나는 단어는 걸리지 않는다.
CAMEL_ID_KEY_PATTERN = re.compile(r".*[a-z](Id|ID)")
DIGITS_PATTERN = re.compile(r"[0-9]+")
POINTER_SEPARATOR = "/"

type IdentifierValue = int | str


@dataclass(frozen=True)
class Identifier:
    pointer: str
    value: IdentifierValue


@dataclass(frozen=True)
class IdentifierScan:
    identifiers: tuple[Identifier, ...]
    # 깊이·개수 상한 때문에 다 보지 못했으면 True
    is_truncated: bool


def extract_identifiers(
    document: object, is_sensitive_key: Callable[[str], bool], secret_variants: tuple[str, ...]
) -> IdentifierScan:
    """json.loads 결과에서 식별자를 문서 순서대로 뽑는다."""
    scanner = _Scanner(is_sensitive_key, secret_variants)
    scanner.scan(document)
    return IdentifierScan(tuple(scanner.identifiers), scanner.is_truncated)


def is_identifier_key(key: str) -> bool:
    lowered = key.lower()
    return lowered == ID_KEY or lowered.endswith(ID_SUFFIX) or bool(CAMEL_ID_KEY_PATTERN.fullmatch(key))


def to_identifier_value(value: object) -> IdentifierValue | None:
    """식별자로 남길 수 있는 값이면 원래 타입 그대로, 아니면 None."""
    # bool은 int의 하위 클래스라 먼저 걸러낸다.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and (DIGITS_PATTERN.fullmatch(value) or UUID_SEGMENT_PATTERN.fullmatch(value)):
        return value
    return None


def escape_pointer_token(token: str) -> str:
    # RFC 6901: ~를 먼저 바꿔야 /를 바꾼 ~1이 다시 바뀌지 않는다.
    return token.replace("~", "~0").replace(POINTER_SEPARATOR, "~1")


@dataclass
class _Scanner:
    is_sensitive_key: Callable[[str], bool]
    secret_variants: tuple[str, ...]
    identifiers: list[Identifier] = field(default_factory=list)
    is_truncated: bool = False

    def scan(self, document: object) -> None:
        self._walk(document, "", 0)

    def _walk(self, value: object, pointer: str, depth: int) -> bool:
        """문서 순서대로 내려간다. 개수 상한에 걸려 멈췄으면 False."""
        if not isinstance(value, dict | list):
            return True
        if depth >= MAX_IDENTIFIER_DEPTH:
            # 비어 있지 않은 안쪽을 못 봤으니 빠진 식별자가 있을 수 있다.
            self.is_truncated = self.is_truncated or bool(value)
            return True
        children = value.items() if isinstance(value, dict) else enumerate(value)
        for key, child in children:
            child_pointer = f"{pointer}{POINTER_SEPARATOR}{escape_pointer_token(str(key))}"
            if isinstance(value, dict):
                if self.is_sensitive_key(str(key)):
                    continue
                if is_identifier_key(str(key)) and not self._add(child_pointer, child):
                    return False
            if not self._walk(child, child_pointer, depth + 1):
                return False
        return True

    def _add(self, pointer: str, raw_value: object) -> bool:
        """식별자 꼴이면 담는다. 담을 자리가 없어 멈춰야 하면 False."""
        value = to_identifier_value(raw_value)
        # 키로 못 거른 곳에 계정 비밀번호가 섞여 있으면 값을 가리는 대신 통째로 버린다(식별자 형식을 지키려고).
        if value is None or any(secret in pointer or secret in str(value) for secret in self.secret_variants):
            return True
        if len(self.identifiers) >= MAX_IDENTIFIERS:
            self.is_truncated = True
            return False
        self.identifiers.append(Identifier(pointer, value))
        return True
