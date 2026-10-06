"""JSON 값을 값 없이 키 구조+타입만 남긴 모양(ShapeNode)으로 바꾼다.

결과 파일은 LLM API로도 나가므로 응답 본문의 실제 값은 어떤 경우에도 남기지 않는다.
키 자체가 값인 경우(id·이메일·날짜·토큰을 키로 쓰는 맵)도 자리표시자로 바꾼다.
배열은 원소마다 모양을 만들지 않고 전부 합친 모양 하나로 남긴다.
"""

import logging
import re

from modules.collector.core.normalize import ID_PLACEHOLDER, ID_SEGMENT_PATTERNS
from modules.collector.core.models import ShapeNode

logger = logging.getLogger(__name__)

# 이보다 깊은 객체·배열은 안을 보지 않고 "dict"/"list"로 끊는다.
MAX_SHAPE_DEPTH = 6
# 객체 하나에 남길 키 수. 넘으면 나머지를 버리고 TRUNCATED_KEY로 표시한다.
MAX_SHAPE_KEYS = 100
TRUNCATED_KEY = "..."
TRUNCATED_MARK = "truncated"

DICT_TYPE = "dict"
LIST_TYPE = "list"
NULL_TYPE = "null"
TYPE_SEPARATOR = "|"

EMAIL_PLACEHOLDER = "{email}"
DATE_PLACEHOLDER = "{date}"
TOKEN_PLACEHOLDER = "{token}"
EMAIL_KEY_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
DATE_KEY_PATTERN = re.compile(
    r"[0-9]{4}(?:-[0-9]{2}|([-/.])[0-9]{2}\1[0-9]{2}(?:[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?"
    r"(?:Z|[+-][0-9]{2}:?[0-9]{2})?)?)"
)
TOKEN_MIN_LENGTH = 16
HEX_TOKEN_PATTERN = re.compile(rf"(?=.*[0-9])(?=.*[a-f])[0-9a-f]{{{TOKEN_MIN_LENGTH},}}", re.IGNORECASE)
BASE64_TOKEN_PATTERN = re.compile(rf"(?=.*[0-9])[A-Za-z0-9+/_-]{{{TOKEN_MIN_LENGTH},}}={{0,2}}")
# 소문자·대문자·숫자 사이를 오가는 비율. 무작위 문자열은 0.6 안팎, camelCase·snake_case 이름은 0.3 아래라
# 그 사이에 둔다. 숫자 섞인 긴 camelCase 이름 일부가 걸릴 수 있지만 토큰을 키로 남기는 쪽보다 낫다.
TOKEN_MIN_CLASS_CHANGE_RATIO = 0.35


def describe_json_shape(value: object) -> ShapeNode:
    """json.loads 결과를 키 구조+타입만 남긴 모양으로 바꾼다."""
    return _describe(value, 0)


def _describe(value: object, depth: int) -> ShapeNode:
    if isinstance(value, dict):
        if depth >= MAX_SHAPE_DEPTH:
            return DICT_TYPE
        shape: dict[str, ShapeNode] = {}
        for key, item in value.items():
            name = _replace_value_like_key(str(key))
            child = _describe(item, depth + 1)
            shape[name] = _merge_shapes(shape[name], child) if name in shape else child
        return _limit_keys(shape)
    if isinstance(value, list):
        if depth >= MAX_SHAPE_DEPTH:
            return LIST_TYPE
        return _describe_elements(value, depth)
    return _scalar_type(value)


def _describe_elements(items: list[object], depth: int) -> list[ShapeNode]:
    merged: ShapeNode | None = None
    for item in items:
        child = _describe(item, depth + 1)
        merged = child if merged is None else _merge_shapes(merged, child)
    return [] if merged is None else [merged]


def _scalar_type(value: object) -> str:
    # bool은 int의 하위 클래스라 먼저 본다.
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if value is None:
        return NULL_TYPE
    logger.debug("JSON에 없는 타입: %s", type(value).__name__)
    return type(value).__name__


def _merge_shapes(first: ShapeNode, second: ShapeNode) -> ShapeNode:
    if first == second:
        return first
    if isinstance(first, dict) and isinstance(second, dict):
        merged = dict(first)
        for key, child in second.items():
            merged[key] = _merge_shapes(merged[key], child) if key in merged else child
        return _limit_keys(merged)
    if isinstance(first, list) and isinstance(second, list):
        if not first or not second:
            return first or second
        return [_merge_shapes(first[0], second[0])]
    # null이 섞였다고 안쪽 키를 잃지 않게 구조를 남긴다.
    if second == NULL_TYPE and isinstance(first, (dict, list)):
        return first
    if first == NULL_TYPE and isinstance(second, (dict, list)):
        return second
    return TYPE_SEPARATOR.join(sorted(_type_names(first) | _type_names(second)))


def _type_names(shape: ShapeNode) -> set[str]:
    if isinstance(shape, dict):
        return {DICT_TYPE}
    if isinstance(shape, list):
        return {LIST_TYPE}
    return set(shape.split(TYPE_SEPARATOR))


def _limit_keys(shape: dict[str, ShapeNode]) -> dict[str, ShapeNode]:
    names = [name for name in shape if name != TRUNCATED_KEY]
    if len(names) <= MAX_SHAPE_KEYS and TRUNCATED_KEY not in shape:
        return shape
    if len(names) > MAX_SHAPE_KEYS:
        logger.info("응답 객체 키 %d개 중 %d개만 남김", len(names), MAX_SHAPE_KEYS)
    limited = {name: shape[name] for name in names[:MAX_SHAPE_KEYS]}
    limited[TRUNCATED_KEY] = TRUNCATED_MARK
    return limited


def _replace_value_like_key(key: str) -> str:
    if any(pattern.fullmatch(key) for pattern in ID_SEGMENT_PATTERNS):
        return ID_PLACEHOLDER
    if DATE_KEY_PATTERN.fullmatch(key):
        return DATE_PLACEHOLDER
    if EMAIL_KEY_PATTERN.fullmatch(key):
        return EMAIL_PLACEHOLDER
    if _is_token_like(key):
        return TOKEN_PLACEHOLDER
    return key


def _is_token_like(key: str) -> bool:
    if HEX_TOKEN_PATTERN.fullmatch(key):
        return True
    if not BASE64_TOKEN_PATTERN.fullmatch(key):
        return False
    # _·- 같은 구분자는 snake_case에서도 나오므로 영숫자만 보고 문자 종류가 바뀌는 비율을 잰다.
    classes = [_char_class(char) for char in key if char.isascii() and char.isalnum()]
    if len(classes) < TOKEN_MIN_LENGTH:
        return False
    changes = sum(1 for before, after in zip(classes, classes[1:], strict=False) if before != after)
    return changes / (len(classes) - 1) >= TOKEN_MIN_CLASS_CHANGE_RATIO


def _char_class(char: str) -> str:
    if char.isdigit():
        return "digit"
    return "upper" if char.isupper() else "lower"
