"""URL 경로 정규화와 값 타입 판별 (순수 함수, LLM 없음).

특정 앱의 URL 모양에 기대지 않는다. 세그먼트 전체가 숫자이거나 UUID인 경우만 id로 보고
{id}로 묶어 같은 Endpoint를 하나로 모은다. 이름 있는 경로 파라미터는 추측하지 않는다
(관찰된 파라미터 이름은 collector의 ObservedParameter에서 그대로 받는다).
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

ID_PLACEHOLDER = "{id}"
PATH_SEPARATOR = "/"
# \d는 유니코드 숫자까지 잡으므로 ASCII 숫자로 한정한다.
_NUMERIC_SEGMENT = re.compile(r"[0-9]+")
_UUID_SEGMENT = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_ID_SEGMENT_PATTERNS = (_NUMERIC_SEGMENT, _UUID_SEGMENT)

_JSON_TYPE_BY_PYTHON = {
    bool: "boolean",
    int: "number",
    float: "number",
    str: "string",
    list: "array",
    dict: "object",
}


def normalize_method(method: str) -> str:
    """HTTP Method를 대문자로 통일한다."""
    return method.strip().upper()


def normalize_path_template(url_or_path: str) -> str:
    """경로나 전체 URL을 {id} 템플릿으로 바꾼다. 숫자·UUID 세그먼트만 치환한다."""
    path = urlsplit(url_or_path).path
    segments: list[str] = []
    for segment in path.split(PATH_SEPARATOR):
        # 빈 조각은 앞·끝·연속 슬래시에서 생긴다. 버려야 /orders/ 와 /orders 가 같아진다.
        if not segment:
            continue
        segments.append(ID_PLACEHOLDER if _is_id_segment(segment) else segment)
    return PATH_SEPARATOR + PATH_SEPARATOR.join(segments)


# 워크플로 step action용 표준 토큰 규칙. method+path_template만의 순수 함수(LLM 없음)라 특정 앱에 안 박힌다.
# GT ordered_actions도 같은 규칙으로 생성해야 reporter default-v2(이름 제외·subsequence)에서 매칭된다.
# 토큰이 '의미적으로 완벽'할 필요는 없고 GT·analyzer가 같은 규칙을 쓰는 게 목적이다.
_NON_RESOURCE_SEGMENTS = frozenset({"api", "v1", "v2", "v3"})
_AUTH_ACTIONS = {
    ("POST", "/login"): "login",
    ("POST", "/signup"): "signup",
    ("POST", "/logout"): "logout",
}
_DELETE_SUFFIXES = frozenset({"delete", "remove"})
_ADD_SUFFIXES = frozenset({"add"})


def canonical_action(method: str, path_template: str) -> str:
    """관찰된 (method, path_template)에서 결정적 action 토큰 `<verb>_<name>`을 만든다.

    verb: GET/HEAD는 끝이 {id}면 read 아니면 view, POST·PUT·PATCH는 끝이 {id}면 update 아니면 create,
          DELETE는 delete. 끝 세그먼트가 add면 update, delete·remove면 delete로 동사만 바꾸고 이름에서 뺀다.
          POST /login·/signup·/logout은 인증 특례로 login·signup·logout.
    name: {id}와 비자원 접두(api·v1~3)를 뺀 세그먼트를 단수화해 '_'로 잇는다(없으면 root).
    """
    method = normalize_method(method)
    auth = _AUTH_ACTIONS.get((method, path_template))
    if auth is not None:
        return auth
    segments = [segment for segment in path_template.split(PATH_SEPARATOR) if segment]
    ends_with_id = bool(segments) and segments[-1] == ID_PLACEHOLDER
    core = [_singularize(segment) for segment in segments
            if segment != ID_PLACEHOLDER and segment not in _NON_RESOURCE_SEGMENTS]
    override: str | None = None
    if core and core[-1] in _DELETE_SUFFIXES:
        override, core = "delete", core[:-1]
    elif core and core[-1] in _ADD_SUFFIXES:
        override, core = "update", core[:-1]
    name = "_".join(core) or "root"
    if override is not None:
        verb = override
    elif method in ("GET", "HEAD"):
        verb = "read" if ends_with_id else "view"
    elif method == "DELETE":
        verb = "delete"
    elif method in ("POST", "PUT", "PATCH"):
        verb = "update" if ends_with_id else "create"
    else:
        verb = "access"
    return f"{verb}_{name}"


def _singularize(word: str) -> str:
    """아주 단순한 복수→단수(영문 관례). 앱별 사전을 두지 않는다."""
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith("ses") and len(word) > 3:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 1:
        return word[:-1]
    return word


def json_value_type(value: object) -> str:
    """관찰값의 JSON 타입 이름을 돌려준다. 값이 null이면 'null', 모르면 'unknown'."""
    if value is None:
        return "null"
    # bool은 int의 하위형이라 먼저 본다.
    for python_type, json_type in _JSON_TYPE_BY_PYTHON.items():
        if isinstance(value, python_type):
            return json_type
    return "unknown"


def _is_id_segment(segment: str) -> bool:
    return any(pattern.fullmatch(segment) for pattern in _ID_SEGMENT_PATTERNS)
