"""discovered_endpoints 수집(utils/discovery.py). 브라우저 없이 리터럴 탐색 결과로 규칙 하나씩 본다."""

from typing import Any
from urllib.parse import urlsplit

import pytest

from modules.collector.core.config import CrawlerConfig, load_config
from modules.collector.core.models import DiscoveredPage, FormField, PageAction, PageLink
from modules.collector.utils.discovery import DiscoveryCollector, endpoint_key, form_request_method

ORIGIN = "http://127.0.0.1:1"


def _config() -> CrawlerConfig:
    return load_config({"target_url": f"{ORIGIN}/"})


def _form(
    action_id: str, method: str | None, target_url: str | None, fields: list[tuple[str, str]], outcome: str
) -> PageAction:
    return PageAction(
        action_id=action_id, kind="form", label=None, method=method, target_url=target_url,
        fields=[FormField(name=name, type=field_type) for name, field_type in fields],
        is_state_changing=True, outcome=outcome,
    )


def _link(action_id: str, url: str, outcome: str) -> PageLink:
    return PageLink(
        action_id=action_id, url=url, endpoint=urlsplit(url).path, text=None,
        is_state_changing=outcome == "not_executed_state_changing", outcome=outcome,
    )


def _page(account: str, url: str, actions: list[PageAction] = (), links: list[PageLink] = ()) -> DiscoveredPage:
    return DiscoveredPage(
        role="user", account_id=account, url=url, endpoint=urlsplit(url).path, title=None, status=200, depth=0,
        source_page=None, source_action="start", links=list(links), actions=list(actions),
    )


def _dom_ref(page_id: str) -> dict[str, Any]:
    return {"evidence_id": f"evidence:dom:{page_id}", "kind": "dom", "path": f"evidence/collector/dom/{page_id}.json",
            "sha256": "0" * 64, "redacted": True}


def _request(method: str, url: str, account: str = "account:user_b", status: int | None = 200) -> dict[str, Any]:
    return {"method": method, "url": url, "account_id": account, "response": {"status_code": status}}


def _collect(pages: list[tuple[str, DiscoveredPage]], requests: list[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """pages: (page_id, 페이지). 행동의 전역 action_id는 '<page_id>/<페이지 안 ID>'로 둔다."""
    collector = DiscoveryCollector(_config())
    for page_id, page in pages:
        local_ids = [link.action_id for link in page.links] + [action.action_id for action in page.actions]
        collector.add_page(page, page_id, {local: f"{page_id}/{local}" for local in local_ids}, _dom_ref(page_id))
    return collector.build(list(requests))


DELETE_FORM = _form(
    "form:0", "POST", f"{ORIGIN}/items/7/delete", [("csrf_token", "hidden"), ("reason", "textarea")], "not_executed_state_changing"
)


def test_unsent_post_form_becomes_a_discovered_endpoint() -> None:
    records = _collect([("page:1", _page("account:user_a", f"{ORIGIN}/items", actions=[DELETE_FORM]))])
    assert records == [{
        "discovered_id": "discovered:1",
        "method": "POST",
        "url_template": f"{ORIGIN}/items/{{id}}/delete",
        "parameters": [
            {"name": "path:1", "location": "path", "is_sensitive": False},
            {"name": "csrf_token", "location": "body", "is_sensitive": True},
            {"name": "reason", "location": "body", "is_sensitive": False},
        ],
        "source": "form",
        "discovered_from": [{"page_id": "page:1", "action_id": "page:1/form:0"}],
        "basis": "inferred",
        "evidence_refs": [_dom_ref("page:1")],
    }]


def test_blocked_form_is_not_discovered() -> None:
    # JS가 가로챈 실제 요청은 requests에 따로 있다(status null). 폼에 적힌 GET /products/7은 실제 요청과 다르다.
    blocked = _form("form:0", "GET", f"{ORIGIN}/products/7", [("product_id", "hidden")], "blocked_state_changing_request")
    assert _collect([("page:1", _page("account:user_a", f"{ORIGIN}/products/7", actions=[blocked]))]) == []


@pytest.mark.parametrize("status", [200, None], ids=["sent", "blocked_status_null"])
def test_endpoint_observed_by_any_account_is_excluded(status: int | None) -> None:
    # user_a가 폼으로만 본 엔드포인트를 user_b가 (다른 id로) 요청했다 → 발견 목록에서 뺀다. 차단돼 status null인 요청도 관찰이다.
    page = _page("account:user_a", f"{ORIGIN}/items", actions=[DELETE_FORM])
    observed = _request("POST", f"{ORIGIN}/items/9/delete", account="account:user_b", status=status)
    assert _collect([("page:1", page)], [observed]) == []


def test_observation_with_other_method_does_not_exclude() -> None:
    page = _page("account:user_a", f"{ORIGIN}/items", actions=[DELETE_FORM])
    assert len(_collect([("page:1", page)], [_request("GET", f"{ORIGIN}/items/7/delete")])) == 1


def test_same_endpoint_from_several_pages_and_accounts_is_one_record() -> None:
    other_form = _form("form:3", "POST", f"{ORIGIN}/items/8/delete", [("note", "text")], "not_executed_state_changing")
    records = _collect([
        ("page:1", _page("account:user_a", f"{ORIGIN}/items", actions=[DELETE_FORM])),
        ("page:2", _page("account:user_b", f"{ORIGIN}/items", actions=[other_form])),
    ])
    assert len(records) == 1
    record = records[0]
    assert record["discovered_from"] == [
        {"page_id": "page:1", "action_id": "page:1/form:0"}, {"page_id": "page:2", "action_id": "page:2/form:3"}
    ]
    assert record["evidence_refs"] == [_dom_ref("page:1"), _dom_ref("page:2")]
    # 여러 페이지의 입력칸은 합친다(이름·위치 기준, 처음 본 순서).
    assert [p["name"] for p in record["parameters"]] == ["path:1", "csrf_token", "reason", "note"]


def test_get_form_fields_are_query_parameters() -> None:
    search = _form("form:0", "GET", f"{ORIGIN}/search?scope=all", [("q", "text")], "failed")
    records = _collect([("page:1", _page("account:user_a", f"{ORIGIN}/", actions=[search]))])
    assert [(p["name"], p["location"]) for p in records[0]["parameters"]] == [("scope", "query"), ("q", "query")]
    assert records[0]["method"] == "GET"


@pytest.mark.parametrize(
    "declared, expected", [("POST", "POST"), ("post", "POST"), ("GET", "GET"), ("PUT", "GET"), (None, "GET"), ("DIALOG", None)]
)
def test_form_method_follows_what_the_browser_sends(declared: str | None, expected: str | None) -> None:
    # HTML 폼은 POST가 아니면 GET으로 보내고, dialog는 요청을 보내지 않는다.
    assert form_request_method(declared) == expected


def test_dialog_form_is_not_discovered() -> None:
    dialog = _form("form:0", "DIALOG", f"{ORIGIN}/items", [], "not_executed_state_changing")
    assert _collect([("page:1", _page("account:user_a", f"{ORIGIN}/items", actions=[dialog]))]) == []


def test_password_typed_field_is_sensitive_even_with_plain_name() -> None:
    login = _form("form:0", "POST", f"{ORIGIN}/login", [("email", "email"), ("pin", "password")], "not_executed_state_changing")
    records = _collect([("page:1", _page("account:guest", f"{ORIGIN}/login", actions=[login]))])
    assert [(p["name"], p["is_sensitive"]) for p in records[0]["parameters"]] == [("email", False), ("pin", True)]


@pytest.mark.parametrize("outcome", ["not_executed_state_changing", "failed", "not_found"])
def test_unsent_form_outcomes_are_discovered(outcome: str) -> None:
    form = _form("form:0", "POST", f"{ORIGIN}/items", [], outcome)
    assert len(_collect([("page:1", _page("account:user_a", f"{ORIGIN}/items", actions=[form]))])) == 1


@pytest.mark.parametrize("outcome", ["executed", "enqueued", "already_visited", "outside_origin"])
def test_sent_or_out_of_scope_form_outcomes_are_not_discovered(outcome: str) -> None:
    form = _form("form:0", "POST", f"{ORIGIN}/items", [], outcome)
    assert _collect([("page:1", _page("account:user_a", f"{ORIGIN}/items", actions=[form]))]) == []


def test_form_targeting_another_origin_is_not_discovered() -> None:
    # 상태 변경 폼은 대상 origin을 보기 전에 not_executed가 된다. 허용 origin 밖이면 발견 목록에도 넣지 않는다.
    external = _form("form:0", "POST", "http://other.example.test/pay", [], "not_executed_state_changing")
    assert _collect([("page:1", _page("account:user_a", f"{ORIGIN}/", actions=[external]))]) == []


def test_state_changing_link_is_discovered_and_followed_link_is_not() -> None:
    links = [
        _link("link:0", f"{ORIGIN}/cart/remove?item=3&csrf=***", "not_executed_state_changing"),
        _link("link:1", f"{ORIGIN}/items", "enqueued"),
    ]
    records = _collect([("page:1", _page("account:user_a", f"{ORIGIN}/cart", links=links))])
    assert [(r["method"], r["url_template"], r["source"]) for r in records] == [("GET", f"{ORIGIN}/cart/remove", "link")]
    assert [(p["name"], p["location"], p["is_sensitive"]) for p in records[0]["parameters"]] == [
        ("csrf", "query", True), ("item", "query", False)
    ]


def test_ids_are_contiguous_after_excluding_observed() -> None:
    forms = [
        _form("form:0", "POST", f"{ORIGIN}/a", [], "not_executed_state_changing"),
        _form("form:1", "POST", f"{ORIGIN}/b", [], "not_executed_state_changing"),
        _form("form:2", "POST", f"{ORIGIN}/c", [], "not_executed_state_changing"),
    ]
    records = _collect([("page:1", _page("account:user_a", f"{ORIGIN}/", actions=forms))], [_request("POST", f"{ORIGIN}/b")])
    assert [(r["discovered_id"], r["url_template"]) for r in records] == [("discovered:1", f"{ORIGIN}/a"), ("discovered:2", f"{ORIGIN}/c")]


def test_endpoint_key_compares_method_origin_and_template() -> None:
    assert endpoint_key("post", f"{ORIGIN}/items/7/delete?x=1") == ("POST", ORIGIN, "/items/{id}/delete")
