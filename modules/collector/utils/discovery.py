"""crawl_result의 discovered_endpoints: 페이지에서 발견했지만 요청을 보내지 않은 엔드포인트를 모은다(0.3.0).

1차 출처는 폼(source=form)과 상태 변경이라 따라가지 않은 링크(source=link)다. 값은 남기지 않고 이름·위치만 남긴다.
- 폼은 실행하지 않은 것만 넣는다(not_executed_state_changing·failed·not_found). blocked_state_changing_request는 뺀다:
  JS가 가로챈 실제 요청이 이미 requests에(status null) 있고, 폼에 적힌 method·action은 그 요청과 다를 수 있다.
- 허용 origin 밖 대상은 넣지 않는다(요청 범위 규칙과 같음).
- (method, origin, 경로 템플릿)으로 묶고, 모든 계정의 requests에서 이미 관찰된 키는 뺀다. 계정 축은 discovered_from(page_id)로만 남는다.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from modules.collector.core.capture import is_sensitive_key, list_sensitive_field_names
from modules.collector.core.config import CrawlerConfig, is_request_allowed
from modules.collector.core.explorer import FORM_KIND, GET_METHOD, ActionOutcome
from modules.collector.core.models import DiscoveredPage, FormField
from modules.collector.core.normalize import ID_PLACEHOLDER, PATH_SEPARATOR, normalize_path

DISCOVERED_ID_FORMAT = "discovered:{}"
PATH_PARAMETER_NAME_FORMAT = "path:{}"
FORM_SOURCE = "form"
LINK_SOURCE = "link"
INFERRED_BASIS = "inferred"
PATH_LOCATION = "path"
QUERY_LOCATION = "query"
BODY_LOCATION = "body"
# 요청을 보내지 않은 폼. 실행했거나(executed·enqueued 등) 범위 밖이거나 실제 요청이 따로 잡힌 폼(blocked_*)은 뺀다.
DISCOVERABLE_FORM_OUTCOMES = frozenset({
    ActionOutcome.NOT_EXECUTED_STATE_CHANGING.value,
    ActionOutcome.FAILED.value,
    ActionOutcome.NOT_FOUND.value,
})
DISCOVERABLE_LINK_OUTCOMES = frozenset({ActionOutcome.NOT_EXECUTED_STATE_CHANGING.value})
# HTML 폼이 보내는 Method. POST가 아니면 브라우저는 GET으로 보내고, dialog는 요청을 보내지 않는다.
POST_METHOD = "POST"
DIALOG_METHOD = "DIALOG"
# 이름이 민감 규칙에 안 걸려도 값이 비밀인 입력칸 타입
PASSWORD_INPUT_TYPE = "password"

type EndpointKey = tuple[str, str, str]


def endpoint_key(method: str, url: str) -> EndpointKey:
    """(대문자 Method, origin, 경로 템플릿). 관찰 요청과 발견 엔드포인트를 같은 기준으로 비교한다."""
    parts = urlsplit(url)
    return method.upper(), f"{parts.scheme}://{parts.netloc}", normalize_path(url).template


def form_request_method(declared_method: str | None) -> str | None:
    """폼이 실제로 보낼 Method. 요청을 보내지 않는 dialog 폼은 None."""
    method = (declared_method or GET_METHOD).upper()
    if method == DIALOG_METHOD:
        return None
    return POST_METHOD if method == POST_METHOD else GET_METHOD


@dataclass
class _Discovery:
    source: str
    template_url: str
    sites: list[dict[str, str]] = field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    # (이름, 위치) → 민감 여부. 여러 페이지의 같은 엔드포인트는 합치고, 한 곳이라도 민감이면 민감이다.
    sensitivity_by_parameter: dict[tuple[str, str], bool] = field(default_factory=dict)

    def add_site(self, page_id: str, action_id: str, dom_ref: Mapping[str, Any] | None) -> None:
        site = {"page_id": page_id, "action_id": action_id}
        if site not in self.sites:
            self.sites.append(site)
        if dom_ref is not None and all(ref["evidence_id"] != dom_ref["evidence_id"] for ref in self.evidence_refs):
            self.evidence_refs.append(dict(dom_ref))

    def add_parameter(self, name: str, location: str, is_sensitive: bool) -> None:
        key = (name, location)
        self.sensitivity_by_parameter[key] = self.sensitivity_by_parameter.get(key, False) or is_sensitive


@dataclass
class DiscoveryCollector:
    """export가 페이지마다 add_page를 부르고, 요청을 다 만든 뒤 build로 discovered_endpoints를 만든다."""

    config: CrawlerConfig
    discoveries: dict[EndpointKey, _Discovery] = field(default_factory=dict)
    sensitive_field_names: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        self.sensitive_field_names = list_sensitive_field_names(self.config)

    def add_page(
        self,
        page: DiscoveredPage,
        page_id: str,
        action_id_by_local: Mapping[str, str],
        dom_ref: Mapping[str, Any] | None,
    ) -> None:
        for link in page.links:
            if link.outcome in DISCOVERABLE_LINK_OUTCOMES:
                self._add(GET_METHOD, link.url, (), LINK_SOURCE, (page_id, action_id_by_local[link.action_id]), dom_ref)
        for action in page.actions:
            if action.kind != FORM_KIND or action.outcome not in DISCOVERABLE_FORM_OUTCOMES or action.target_url is None:
                continue
            method = form_request_method(action.method)
            if method is not None:
                site = (page_id, action_id_by_local[action.action_id])
                self._add(method, action.target_url, action.fields, FORM_SOURCE, site, dom_ref)

    def build(self, requests: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """이미 관찰된 (method, origin, 템플릿)을 빼고 발견 순서대로 ID를 붙인다."""
        observed = {endpoint_key(request["method"], request["url"]) for request in requests}
        records: list[dict[str, Any]] = []
        for key, discovery in self.discoveries.items():
            if key in observed:
                continue
            records.append({
                "discovered_id": DISCOVERED_ID_FORMAT.format(len(records) + 1),
                "method": key[0],
                "url_template": discovery.template_url,
                "parameters": [
                    {"name": name, "location": location, "is_sensitive": is_sensitive}
                    for (name, location), is_sensitive in discovery.sensitivity_by_parameter.items()
                ],
                "source": discovery.source,
                "discovered_from": list(discovery.sites),
                "basis": INFERRED_BASIS,
                "evidence_refs": list(discovery.evidence_refs),
            })
        return records

    def _add(
        self,
        method: str,
        url: str,
        fields: Iterable[FormField],
        source: str,
        site: tuple[str, str],
        dom_ref: Mapping[str, Any] | None,
    ) -> None:
        if not is_request_allowed(self.config, url):
            return
        key = endpoint_key(method, url)
        discovery = self.discoveries.get(key)
        if discovery is None:
            discovery = _Discovery(source, f"{key[1]}{key[2]}")
            self.discoveries[key] = discovery
            for name in path_parameter_names(key[2]):
                discovery.add_parameter(name, PATH_LOCATION, False)
        discovery.add_site(*site, dom_ref)
        for name in normalize_path(url).query_param_names:
            discovery.add_parameter(name, QUERY_LOCATION, is_sensitive_key(name, self.sensitive_field_names))
        field_location = QUERY_LOCATION if method == GET_METHOD else BODY_LOCATION
        for form_field in fields:
            is_sensitive = form_field.type == PASSWORD_INPUT_TYPE or is_sensitive_key(form_field.name, self.sensitive_field_names)
            discovery.add_parameter(form_field.name, field_location, is_sensitive)


def path_parameter_names(template: str) -> list[str]:
    """관찰 요청과 같은 이름 규칙: path:<빈 조각을 뺀 경로 세그먼트 위치>(/items/{id} → path:1)."""
    segments = [segment for segment in template.split(PATH_SEPARATOR) if segment]
    return [PATH_PARAMETER_NAME_FORMAT.format(index) for index, segment in enumerate(segments) if segment == ID_PLACEHOLDER]
