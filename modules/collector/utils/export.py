"""탐색 결과(service.CollectOutcome)를 crawl_result.json의 data 객체로 바꾼다.

공개 계약(schemas/output/crawl_result.schema.json·README)과 내부 모델의 대응은 이 파일에서만 맞춘다.
- 지금은 역할당 계정 하나(account:<role>)다.
- 근거 파일은 아직 만들지 않는다(body_ref=null, evidence_refs=[]).
- ID는 실행 전체에서 1부터 붙인다. 요청은 역할 안에서 나간 순서대로 정렬한 뒤 번호를 매긴다.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from pydantic import JsonValue

from modules.collector.core.auth import SECRET_MASK
from modules.collector.core.capture import (
    AUTHORIZATION_HEADERS,
    CONTENT_TYPE_HEADER,
    COOKIE_HEADER,
    COOKIE_PAIR_SEPARATOR,
    FILE_PART_VALUE,
    SET_COOKIE_HEADER,
)
from modules.collector.core.config import CrawlerConfig
from modules.collector.core.explorer import BUTTON_KIND, FORM_KIND, LOAD_ACTION, REVISIT_ACTION, START_ACTION
from modules.collector.core.models import CapturedRequest, DiscoveredPage
from modules.collector.core.normalize import ID_SEGMENT_PATTERNS, PATH_SEPARATOR
from modules.collector.service import CollectOutcome, RoleCrawl
from modules.collector.utils.envelope import ErrorCode, format_utc, make_error_item

ROLE_ID_FORMAT = "role:{}"
ACCOUNT_ID_FORMAT = "account:{}"
PAGE_ID_FORMAT = "page:{}"
ACTION_ID_FORMAT = "action:{}"
REQUEST_ID_FORMAT = "request:{}"
PATH_PARAMETER_NAME_FORMAT = "path:{}"
REDACTED_VALUE = "[REDACTED]"
NAVIGATE_KIND = "navigate"
ACTION_KIND_BY_ELEMENT = {FORM_KIND: "submit", BUTTON_KIND: "click"}
# 페이지를 열거나 다시 여는 요청이라 사용자 행동이 아니다. action_id를 null로 둔다.
NON_USER_ACTIONS = frozenset({START_ACTION, LOAD_ACTION, REVISIT_ACTION})
# capture가 값을 통째로 가리는 헤더. 남은 값(쿠키 이름·인증 방식)도 공유본에는 넣지 않는다.
SECRET_HEADER_NAMES = frozenset({COOKIE_HEADER, SET_COOKIE_HEADER, *AUTHORIZATION_HEADERS})
COOKIE_NAME_SEPARATOR = "="
QUERY_LOCATION = "query"
BODY_LOCATION = "body"
PATH_LOCATION = "path"
COOKIE_LOCATION = "cookie"


def role_id_of(role: str) -> str:
    return ROLE_ID_FORMAT.format(role)


def account_id_of(role: str) -> str:
    # 역할당 계정 하나인 동안은 역할 이름을 별칭으로 쓴다. 로그인 ID는 넣지 않는다.
    return ACCOUNT_ID_FORMAT.format(role)


def build_data(config: CrawlerConfig, outcome: CollectOutcome) -> dict[str, Any]:
    builder = _DataBuilder()
    for role_crawl in outcome.role_crawls:
        builder.add_role(role_crawl)
    return {
        "target_url": config.start_url,
        "roles": [{"role_id": role_id_of(crawl.role), "name": crawl.role} for crawl in outcome.role_crawls],
        "accounts": builder.accounts,
        "pages": builder.pages,
        "actions": builder.actions,
        "requests": builder.requests,
    }


def list_account_errors(outcome: CollectOutcome) -> list[dict[str, Any]]:
    """실패한 계정마다 ErrorItem 하나. 같은 조건으로 다시 돌리면 될 수 있어 retryable=true."""
    return [
        make_error_item(
            ErrorCode.ACCOUNT_CRAWL_FAILED, role_crawl.error, item_ref=account_id_of(role_crawl.role), is_retryable=True
        )
        for role_crawl in outcome.role_crawls
        if role_crawl.error is not None
    ]


def has_observations(data: Mapping[str, Any]) -> bool:
    return bool(data["pages"] or data["requests"])


@dataclass
class _RoleLinks:
    """한 역할 안에서 요청을 페이지·행동에 잇는 표. 다른 역할의 같은 URL과는 잇지 않는다."""

    page_id_by_url: dict[str, str] = field(default_factory=dict)
    # (page_id, 페이지 안 순번 "link:0" 등) → 전역 action_id
    action_id_by_key: dict[tuple[str, str], str] = field(default_factory=dict)


@dataclass
class _DataBuilder:
    accounts: list[dict[str, Any]] = field(default_factory=list)
    pages: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def add_role(self, role_crawl: RoleCrawl) -> None:
        account = {
            "account_id": account_id_of(role_crawl.role),
            "role_id": role_id_of(role_crawl.role),
            "alias": role_crawl.role,
            "session_ref": role_crawl.session_ref,
        }
        self.accounts.append(account)
        links = _RoleLinks()
        for page in role_crawl.pages:
            self._add_page(page, links)
        # capture는 응답이 끝난 순서로 쌓으므로 요청이 나간 순서로 바꾼다. 같은 시각이면 원래 순서를 지킨다.
        for record in sorted(role_crawl.records, key=lambda record: record.captured_at):
            self.requests.append(self._build_request(record, account, links))

    def _add_page(self, page: DiscoveredPage, links: _RoleLinks) -> None:
        page_id = PAGE_ID_FORMAT.format(len(self.pages) + 1)
        # 리다이렉트로 이미 본 페이지에 다시 오면 같은 URL이 또 생긴다. 뒤의 것은 추출을 건너뛴 페이지라 처음 것에 잇는다.
        links.page_id_by_url.setdefault(page.url, page_id)
        self.pages.append({"page_id": page_id, "url": page.url, "title": page.title, "evidence_refs": []})
        for link in page.links:
            self._add_action(page_id, link.action_id, NAVIGATE_KIND, link.text, links)
        for action in page.actions:
            self._add_action(page_id, action.action_id, ACTION_KIND_BY_ELEMENT[action.kind], action.label, links)

    def _add_action(
        self, page_id: str, local_action_id: str, kind: str, label: str | None, links: _RoleLinks
    ) -> None:
        action_id = ACTION_ID_FORMAT.format(len(self.actions) + 1)
        links.action_id_by_key[(page_id, local_action_id)] = action_id
        self.actions.append(
            {"action_id": action_id, "page_id": page_id, "kind": kind, "label": label, "evidence_refs": []}
        )

    def _build_request(
        self, record: CapturedRequest, account: Mapping[str, Any], links: _RoleLinks
    ) -> dict[str, Any]:
        page_id = links.page_id_by_url.get(record.source_page) if record.source_page is not None else None
        action_id = None
        if page_id is not None and record.source_action not in NON_USER_ACTIONS:
            action_id = links.action_id_by_key.get((page_id, record.source_action))
        return {
            "request_id": REQUEST_ID_FORMAT.format(len(self.requests) + 1),
            "account_id": account["account_id"],
            "role_id": account["role_id"],
            "session_ref": account["session_ref"],
            "page_id": page_id,
            "action_id": action_id,
            "method": record.method,
            "url": record.url,
            "observed_at": format_utc(record.captured_at),
            "parameters": build_parameters(record),
            "headers": build_headers(record.request_headers),
            "body_ref": None,
            "response": {
                "status_code": record.status,
                "content_type": record.response_headers.get(CONTENT_TYPE_HEADER),
                "headers": build_headers(record.response_headers),
                "body_ref": None,
            },
            "evidence_refs": [],
        }


def build_parameters(record: CapturedRequest) -> list[dict[str, Any]]:
    """경로 id → 쿼리 → 바디 → 쿠키 순. 같은 키가 반복되면 값마다 한 건."""
    return [
        *_build_path_parameters(record.url),
        *_build_pair_parameters(record.query_params, QUERY_LOCATION),
        *_build_pair_parameters(record.body_params, BODY_LOCATION),
        *_build_cookie_parameters(record.request_headers.get(COOKIE_HEADER)),
    ]


def build_headers(headers: Mapping[str, str]) -> list[dict[str, Any]]:
    result = []
    for name, value in headers.items():
        lowered = name.lower()
        # capture가 민감 이름 헤더를 통째로 가리면 값이 SECRET_MASK가 된다.
        is_redacted = lowered in SECRET_HEADER_NAMES or value == SECRET_MASK
        result.append({"name": lowered, "value": REDACTED_VALUE if is_redacted else value, "redacted": is_redacted})
    return result


def _build_path_parameters(url: str) -> Iterator[dict[str, Any]]:
    """이름은 path:<index>, index는 빈 조각을 뺀 경로 세그먼트를 0부터 센 위치(/items/7 → path:1)."""
    segments = [segment for segment in urlsplit(url).path.split(PATH_SEPARATOR) if segment]
    for index, segment in enumerate(segments):
        if any(pattern.fullmatch(segment) for pattern in ID_SEGMENT_PATTERNS):
            yield _make_parameter(PATH_PARAMETER_NAME_FORMAT.format(index), PATH_LOCATION, segment, False)


def _build_pair_parameters(params: Mapping[str, list[JsonValue]], location: str) -> Iterator[dict[str, Any]]:
    for name, values in params.items():
        for value in values:
            # capture가 민감 키 값을 SECRET_MASK로 바꿔 둔다. 공유본에서는 값 대신 null로 둔다.
            if value == SECRET_MASK:
                yield _make_parameter(name, location, None, True)
            # 파일 파트는 내용도 파일 이름도 남기지 않는다. 비밀값은 아니지만 값이 없다.
            elif value == FILE_PART_VALUE:
                yield _make_parameter(name, location, None, False)
            else:
                yield _make_parameter(name, location, value, False)


def _build_cookie_parameters(cookie_header: str | None) -> Iterator[dict[str, Any]]:
    """쿠키는 이름만 남기고 값은 늘 가린다(capture가 이미 name=*** 로 바꿔 둠)."""
    if not cookie_header:
        return
    for pair in cookie_header.split(COOKIE_PAIR_SEPARATOR):
        name = pair.split(COOKIE_NAME_SEPARATOR, 1)[0].strip()
        if name:
            yield _make_parameter(name, COOKIE_LOCATION, None, True)


def _make_parameter(name: str, location: str, value: JsonValue, is_sensitive: bool) -> dict[str, Any]:
    return {"name": name, "location": location, "value": value, "is_sensitive": is_sensitive}
