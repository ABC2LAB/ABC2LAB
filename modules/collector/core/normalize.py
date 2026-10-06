"""URL 경로의 id 조각을 {id}로 바꿔 같은 엔드포인트를 하나로 묶는다.

특정 앱의 URL 모양에 기대지 않고, 세그먼트 전체가 숫자이거나 UUID인 경우만 id로 본다.
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

ID_PLACEHOLDER = "{id}"
PATH_SEPARATOR = "/"
# \d는 아랍 숫자 같은 유니코드 숫자까지 잡으므로 ASCII 숫자로 한정한다.
NUMERIC_SEGMENT_PATTERN = re.compile(r"[0-9]+")
UUID_SEGMENT_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
ID_SEGMENT_PATTERNS = (NUMERIC_SEGMENT_PATTERN, UUID_SEGMENT_PATTERN)


@dataclass(frozen=True)
class NormalizedPath:
    template: str
    path_values: tuple[str, ...]
    # 쿼리 값은 토큰 같은 민감한 값일 수 있어 이름만 남긴다. 값 보관은 capture 단계 몫이다.
    query_param_names: tuple[str, ...]


def normalize_path(url_or_path: str) -> NormalizedPath:
    """경로나 전체 URL을 {id} 템플릿, 뽑은 경로 값, 쿼리 파라미터 이름으로 나눈다."""
    parts = urlsplit(url_or_path)
    template_segments: list[str] = []
    path_values: list[str] = []

    for segment in parts.path.split(PATH_SEPARATOR):
        # 빈 조각은 앞·끝·연속 슬래시에서 생긴다. 버려야 /orders/, /orders//3 이 정리된다.
        if not segment:
            continue
        if _is_id_segment(segment):
            template_segments.append(ID_PLACEHOLDER)
            path_values.append(segment)
        else:
            template_segments.append(segment)

    template = PATH_SEPARATOR + PATH_SEPARATOR.join(template_segments)
    query_param_names = sorted(
        {name for name, _ in parse_qsl(parts.query, keep_blank_values=True)}
    )
    return NormalizedPath(template, tuple(path_values), tuple(query_param_names))


def _is_id_segment(segment: str) -> bool:
    return any(pattern.fullmatch(segment) for pattern in ID_SEGMENT_PATTERNS)
