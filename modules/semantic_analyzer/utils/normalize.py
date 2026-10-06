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
