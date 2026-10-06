"""export adapter의 매핑 규칙을 브라우저 없이 만든 탐색 결과로 확인한다."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import JsonValue

from modules.collector.core.config import GUEST_ROLE, CrawlerConfig, load_config
from modules.collector.core.models import CapturedRequest, DiscoveredPage, PageAction, PageLink
from modules.collector.service import CollectOutcome, RoleCrawl
from modules.collector.utils import export
from modules.collector.utils.envelope import Mode, RunContext, Status, WorkResult, build_envelope
from modules.collector.utils.validation import validate_crawl_result_bytes

TARGET_URL = "http://127.0.0.1:1/"
PAGE_A = "http://127.0.0.1:1/a"
PAGE_B = "http://127.0.0.1:1/b"
BASE_TIME = datetime(2026, 10, 1, 5, 12, 3, 250000, tzinfo=UTC)
USER_ROLE = "user"
USER_SESSION_REF = "session:00112233aabbccdd"
UUID_SEGMENT = "0b6f2c1e-8d4a-4c3e-9f1a-2d5e7c8b9a01"
MASK = "***"
REDACTED = "[REDACTED]"


def make_config() -> CrawlerConfig:
    return load_config({"CRAWLER_TARGET_URL": TARGET_URL})


def make_page(
    role: str, url: str, links: list[PageLink] | None = None, actions: list[PageAction] | None = None
) -> DiscoveredPage:
    return DiscoveredPage(
        role=role,
        url=url,
        endpoint=urlsplit(url).path,
        title=None,
        status=200,
        depth=0,
        source_page=None,
        source_action="start",
        links=links or [],
        actions=actions or [],
    )


def make_link(action_id: str, text: str | None) -> PageLink:
    return PageLink(
        action_id=action_id, url=PAGE_B, endpoint="/b", text=text, is_state_changing=False, outcome="enqueued"
    )


def make_action(action_id: str, kind: str, label: str | None) -> PageAction:
    return PageAction(
        action_id=action_id,
        kind=kind,
        label=label,
        method="POST" if kind == "form" else None,
        target_url=None,
        fields=[],
        is_state_changing=False,
        outcome="executed",
    )


def make_record(role: str, url: str, offset_s: float = 0, **overrides: Any) -> CapturedRequest:
    fields: dict[str, Any] = {
        "role": role,
        "method": "GET",
        "resource_type": "fetch",
        "url": url,
        "endpoint": urlsplit(url).path,
        "status": 200,
        "query_params": {},
        "body_params": {},
        "resource_ids": [],
        "request_headers": {},
        "response_headers": {},
        "response_shape": None,
        "response_identifiers": None,
        "is_response_identifiers_truncated": False,
        "source_page": None,
        "source_action": None,
        "captured_at": BASE_TIME + timedelta(seconds=offset_s),
    }
    fields.update(overrides)
    return CapturedRequest.model_validate(fields)


def make_outcome(*role_crawls: RoleCrawl) -> CollectOutcome:
    return CollectOutcome(BASE_TIME, BASE_TIME + timedelta(minutes=1), tuple(role_crawls))


def make_crawl(
    role: str, pages: tuple[DiscoveredPage, ...], records: tuple[CapturedRequest, ...], **overrides: Any
) -> RoleCrawl:
    session_ref = overrides.pop("session_ref", None if role == GUEST_ROLE else USER_SESSION_REF)
    return RoleCrawl(role, pages, records, overrides.pop("error", None), session_ref, **overrides)


def build_single_request(record: CapturedRequest) -> dict[str, Any]:
    data = export.build_data(make_config(), make_outcome(make_crawl(USER_ROLE, (), (record,))))
    return data["requests"][0]


def parameter_tuples(request: dict[str, Any]) -> list[tuple[str, str, JsonValue, bool]]:
    return [(item["name"], item["location"], item["value"], item["is_sensitive"]) for item in request["parameters"]]


# ---- ID·연결 ----


def test_ids_numbered_from_one_across_roles() -> None:
    guest = make_crawl(
        GUEST_ROLE,
        (make_page(GUEST_ROLE, PAGE_A, links=[make_link("link:0", "b")]), make_page(GUEST_ROLE, PAGE_B)),
        (make_record(GUEST_ROLE, PAGE_A, 1), make_record(GUEST_ROLE, PAGE_B, 2)),
    )
    user = make_crawl(
        USER_ROLE,
        (make_page(USER_ROLE, PAGE_A, actions=[make_action("button:0", "button", "go")]),),
        (make_record(USER_ROLE, PAGE_A, 3),),
    )

    data = export.build_data(make_config(), make_outcome(guest, user))

    assert data["target_url"] == TARGET_URL
    assert data["roles"] == [{"role_id": "role:guest", "name": "guest"}, {"role_id": "role:user", "name": "user"}]
    assert [account["account_id"] for account in data["accounts"]] == ["account:guest", "account:user"]
    assert [page["page_id"] for page in data["pages"]] == ["page:1", "page:2", "page:3"]
    assert [action["action_id"] for action in data["actions"]] == ["action:1", "action:2"]
    assert [request["request_id"] for request in data["requests"]] == ["request:1", "request:2", "request:3"]


def test_requests_sorted_by_sent_time_within_role() -> None:
    records = (make_record(USER_ROLE, f"{PAGE_A}/late", 5), make_record(USER_ROLE, f"{PAGE_A}/early", 1))

    data = export.build_data(make_config(), make_outcome(make_crawl(USER_ROLE, (), records)))

    assert [urlsplit(request["url"]).path for request in data["requests"]] == ["/a/early", "/a/late"]


def test_request_links_to_page_and_action_of_same_role() -> None:
    guest_page = make_page(GUEST_ROLE, PAGE_A, links=[make_link("link:0", "b")])
    user_page = make_page(USER_ROLE, PAGE_A, links=[make_link("link:0", "b")])
    guest = make_crawl(
        GUEST_ROLE,
        (guest_page,),
        (
            make_record(GUEST_ROLE, PAGE_A, 1, source_page=None, source_action="start"),
            make_record(GUEST_ROLE, f"{PAGE_A}/api", 2, source_page=PAGE_A, source_action="load"),
            make_record(GUEST_ROLE, PAGE_B, 3, source_page=PAGE_A, source_action="link:0"),
            make_record(GUEST_ROLE, PAGE_A, 4, source_page=PAGE_A, source_action="revisit"),
            make_record(GUEST_ROLE, f"{PAGE_A}/x", 5, source_page="http://127.0.0.1:1/unknown", source_action="link:0"),
        ),
    )
    user = make_crawl(
        USER_ROLE, (user_page,), (make_record(USER_ROLE, PAGE_B, 6, source_page=PAGE_A, source_action="link:0"),)
    )

    requests = export.build_data(make_config(), make_outcome(guest, user))["requests"]

    assert [(request["page_id"], request["action_id"]) for request in requests] == [
        (None, None),
        ("page:1", None),
        ("page:1", "action:1"),
        ("page:1", None),
        (None, None),
        # user의 같은 URL 페이지는 자기 페이지(page:2)·자기 행동(action:2)에 이어진다.
        ("page:2", "action:2"),
    ]


def test_repeated_url_links_to_first_page() -> None:
    first = make_page(USER_ROLE, PAGE_A, links=[make_link("link:0", "b")])
    repeated = make_page(USER_ROLE, PAGE_A)
    record = make_record(USER_ROLE, PAGE_B, 1, source_page=PAGE_A, source_action="link:0")

    data = export.build_data(make_config(), make_outcome(make_crawl(USER_ROLE, (first, repeated), (record,))))

    assert [page["page_id"] for page in data["pages"]] == ["page:1", "page:2"]
    assert (data["requests"][0]["page_id"], data["requests"][0]["action_id"]) == ("page:1", "action:1")


def test_action_kind_and_label_by_element() -> None:
    page = make_page(
        USER_ROLE,
        PAGE_A,
        links=[make_link("link:0", "next")],
        actions=[make_action("form:0", "form", "Save"), make_action("button:0", "button", None)],
    )

    actions = export.build_data(make_config(), make_outcome(make_crawl(USER_ROLE, (page,), ())))["actions"]

    assert [(action["kind"], action["label"], action["page_id"]) for action in actions] == [
        ("navigate", "next", "page:1"),
        ("submit", "Save", "page:1"),
        ("click", None, "page:1"),
    ]
    assert all(action["evidence_refs"] == [] for action in actions)


def test_session_ref_copied_to_account_and_requests() -> None:
    guest = make_crawl(GUEST_ROLE, (), (make_record(GUEST_ROLE, PAGE_A, 1),))
    user = make_crawl(USER_ROLE, (), (make_record(USER_ROLE, PAGE_A, 2),))

    data = export.build_data(make_config(), make_outcome(guest, user))

    assert [account["session_ref"] for account in data["accounts"]] == [None, USER_SESSION_REF]
    assert [(request["account_id"], request["role_id"], request["session_ref"]) for request in data["requests"]] == [
        ("account:guest", "role:guest", None),
        ("account:user", "role:user", USER_SESSION_REF),
    ]


# ---- 파라미터·헤더·응답 ----


def test_path_parameters_named_by_segment_index() -> None:
    request = build_single_request(make_record(USER_ROLE, f"http://127.0.0.1:1/users/5//orders/{UUID_SEGMENT}/7abc"))

    assert parameter_tuples(request) == [("path:1", "path", "5", False), ("path:3", "path", UUID_SEGMENT, False)]


def test_query_and_body_values_one_per_occurrence() -> None:
    record = make_record(
        USER_ROLE,
        f"{PAGE_A}?page=1&page=2&access_token={MASK}",
        query_params={"page": ["1", "2"], "access_token": [MASK]},
        body_params={"count": [3], "nested": [{"api_token": MASK}], "upload": ["<file>"]},
    )

    assert parameter_tuples(build_single_request(record)) == [
        ("page", "query", "1", False),
        ("page", "query", "2", False),
        ("access_token", "query", None, True),
        # JSON 바디 값은 타입 그대로. 안쪽 민감 키는 capture가 이미 가렸다.
        ("count", "body", 3, False),
        ("nested", "body", {"api_token": MASK}, False),
        # 파일 파트는 값이 없지만 비밀값은 아니다.
        ("upload", "body", None, False),
    ]


def test_cookie_names_become_sensitive_parameters() -> None:
    record = make_record(USER_ROLE, PAGE_A, request_headers={"cookie": f"sid={MASK}; theme={MASK}"})

    assert parameter_tuples(build_single_request(record)) == [
        ("sid", "cookie", None, True),
        ("theme", "cookie", None, True),
    ]


def test_secret_headers_redacted() -> None:
    record = make_record(
        USER_ROLE,
        PAGE_A,
        request_headers={
            "accept": "text/html",
            "cookie": f"sid={MASK}",
            "authorization": f"Bearer {MASK}",
            "x-csrf-token": MASK,
        },
        response_headers={"content-type": "application/json", "set-cookie": f"sid={MASK}; Path=/; HttpOnly"},
    )

    request = build_single_request(record)

    assert request["headers"] == [
        {"name": "accept", "value": "text/html", "redacted": False},
        {"name": "cookie", "value": REDACTED, "redacted": True},
        {"name": "authorization", "value": REDACTED, "redacted": True},
        {"name": "x-csrf-token", "value": REDACTED, "redacted": True},
    ]
    assert request["response"]["headers"] == [
        {"name": "content-type", "value": "application/json", "redacted": False},
        {"name": "set-cookie", "value": REDACTED, "redacted": True},
    ]


def test_response_meta_and_time_format() -> None:
    record = make_record(USER_ROLE, PAGE_A, status=None, response_headers={})

    request = build_single_request(record)

    assert request["response"] == {"status_code": None, "content_type": None, "headers": [], "body_ref": None}
    assert request["observed_at"] == "2026-10-01T05:12:03.250000Z"
    assert (request["method"], request["body_ref"], request["evidence_refs"]) == ("GET", None, [])


# ---- 오류·계약 ----


def test_failed_accounts_become_error_items() -> None:
    failed = make_crawl("admin", (), (), error="LoginError: admin 로그인 실패", session_ref=None)
    outcome = make_outcome(make_crawl(GUEST_ROLE, (), ()), failed)

    assert export.list_account_errors(outcome) == [
        {
            "code": "ACCOUNT_CRAWL_FAILED",
            "message": "LoginError: admin 로그인 실패",
            "item_ref": "account:admin",
            "retryable": True,
        }
    ]


def test_exported_data_passes_contract(tmp_path: Path) -> None:
    page = make_page(
        USER_ROLE, PAGE_A, links=[make_link("link:0", "b")], actions=[make_action("form:0", "form", "Save")]
    )
    api_record = make_record(
        USER_ROLE,
        f"{PAGE_A}/7?access_token={MASK}",
        1,
        source_page=PAGE_A,
        source_action="load",
        query_params={"access_token": [MASK]},
        request_headers={"cookie": f"sid={MASK}"},
    )
    link_record = make_record(
        USER_ROLE, PAGE_B, 2, source_page=PAGE_A, source_action="link:0", response_headers={"set-cookie": f"sid={MASK}"}
    )
    outcome = make_outcome(make_crawl(GUEST_ROLE, (), ()), make_crawl(USER_ROLE, (page,), (api_record, link_record)))
    data = export.build_data(make_config(), outcome)
    run_root = tmp_path / "run_export"
    run_context = RunContext("run_export", 0, Mode.DEVELOPMENT, run_root, Path(".env"))
    document = build_envelope(run_context, WorkResult(Status.COMPLETED, [], data, 10))
    raw = json.dumps(document, ensure_ascii=False).encode("utf-8")

    assert validate_crawl_result_bytes(raw, run_root) == []
