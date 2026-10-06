"""탐색 core의 내부 모델. 공개 계약(crawl_result.json)은 utils/export.py가 이 모델에서 만든다.

여기 필드를 바꿔도 공개 계약은 export adapter와 schemas/output/에서 따로 지킨다.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, JsonValue, StrictInt, StrictStr

# JSON 응답의 키 구조+타입. 잎은 "int", "int|null" 같은 타입 이름이고 값은 담지 않는다.
# type 문으로 선언해야 pydantic이 중첩된 곳까지 재귀로 검증한다.
type ShapeNode = str | dict[str, ShapeNode] | list[ShapeNode]
# 바디 파라미터. 폼·multipart 값은 문자열, JSON 바디는 최상위 키마다 가린 뒤의 JSON 값 그대로.
type BodyParams = dict[str, list[JsonValue]]



class ResponseIdentifier(BaseModel):
    """응답 JSON의 식별자 하나. 위치(RFC 6901 JSON Pointer)와 원래 JSON 타입의 값."""

    model_config = ConfigDict(frozen=True)

    pointer: str
    # 0 이상 정수, 숫자로만 된 문자열, UUID 문자열. Strict라 "42"가 42로 바뀌지 않는다.
    value: StrictInt | StrictStr


class CapturedRequest(BaseModel):
    """브라우저에서 난 요청 하나와 그 응답. 민감한 값은 마스킹된 채로만 들어온다.

    기본값을 두지 않아서 없는 값도 JSON에 null로 남는다.
    """

    model_config = ConfigDict(frozen=True)

    role: str
    method: str
    # "document"(문서 이동) | "fetch" | "xhr"
    resource_type: str
    url: str
    endpoint: str
    status: int | None
    # ?a=1&a=2 처럼 같은 키가 반복될 수 있어 값은 list로 둔다.
    query_params: dict[str, list[str]]
    body_params: BodyParams
    # 경로에서 뽑은 id만. 쿼리 값 중 무엇이 id인지는 KG 단계가 판단한다.
    resource_ids: list[str]
    request_headers: dict[str, str]
    response_headers: dict[str, str]
    # content-type이 JSON인 응답만. HTML·JSON 아닌 응답·읽기 실패는 null
    response_shape: ShapeNode | None
    # response_shape와 같은 응답에서 뽑은 식별자. response_shape가 null이면 null
    response_identifiers: list[ResponseIdentifier] | None
    # 깊이·개수 상한 때문에 식별자를 다 보지 못했으면 true
    is_response_identifiers_truncated: bool
    source_page: str | None
    source_action: str | None
    captured_at: datetime


class PageLink(BaseModel):
    """페이지에서 찾은 허용 origin 안의 링크. 따라갈지는 outcome에 남는다."""

    model_config = ConfigDict(frozen=True)

    # capture 기록의 source_action과 같은 값이라 (페이지 url, action_id)로 요청과 잇는다.
    action_id: str
    url: str
    endpoint: str
    text: str | None
    is_state_changing: bool
    # "enqueued" | "already_visited" | "beyond_max_depth" | "not_executed_state_changing"
    outcome: str


class FormField(BaseModel):
    """폼 입력칸 하나. 값은 CSRF 토큰·기본 개인정보가 섞일 수 있어 남기지 않는다."""

    model_config = ConfigDict(frozen=True)

    name: str
    # input은 브라우저가 정규화한 type(없거나 모르는 값이면 "text"), 그 밖은 태그명("select", "textarea" 등)
    type: str


class PageAction(BaseModel):
    """페이지의 폼·버튼 하나와 크롤러가 그걸 어떻게 다뤘는지."""

    model_config = ConfigDict(frozen=True)

    action_id: str
    # "form" | "button"
    kind: str
    label: str | None
    # 폼만. 버튼은 null
    method: str | None
    # 폼은 action, 버튼은 클릭 뒤 이동한 URL. 없으면 null
    target_url: str | None
    # 폼만. 버튼은 []
    fields: list[FormField]
    is_state_changing: bool
    # "executed" | "enqueued" | "already_visited" | "beyond_max_depth" | "not_executed_state_changing"
    # | "blocked_state_changing_request" | "outside_origin" | "not_visible" | "not_found" | "failed"
    outcome: str


class DiscoveredPage(BaseModel):
    """한 역할이 BFS로 방문한 페이지. 그 페이지에 오게 한 부모 페이지·행동도 같이 남긴다.

    페이지가 열리며 나간 요청은 capture 기록에 source_action "load"로 붙는다.
    """

    model_config = ConfigDict(frozen=True)

    role: str
    # 리다이렉트까지 따라간 최종 URL, capture와 같은 규칙으로 마스킹
    url: str
    endpoint: str
    title: str | None
    status: int | None
    depth: int
    source_page: str | None
    source_action: str | None
    links: list[PageLink]
    actions: list[PageAction]

