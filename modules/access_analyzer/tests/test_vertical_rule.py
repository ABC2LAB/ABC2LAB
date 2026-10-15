"""수직 규칙(rule_vertical_access) 판정. 각 테스트는 '이 판정을 빼면 통과해 버리는' 버그 하나를 겨눈다.

입력은 리터럴이다(계정·역할·엔드포인트 ID는 이 파일의 자리표시자, 앱 값이 아니다). 기대값(candidate_id·resource_ids·
source_request_ids)도 코드 상수가 아니라 여기에 리터럴로 적는다. crawl 관찰은 테스트 도우미로 만들어 입력 Schema를 통과시킨다.
"""

import dataclasses
import logging
from typing import Any

import pytest

from modules.access_analyzer import rules, service
from modules.access_analyzer.observations import build_crawl_observations
from modules.access_analyzer.tests.helpers import make_crawl_artifact, schema_errors
from modules.access_analyzer.utils.config import DEFAULT_RULE_CONFIG, VerticalRuleConfig
from modules.access_analyzer.vertical_rule import apply_vertical_rule

CRAWL_SCHEMA = "input/crawl_result.schema.json"
RESULT_SCHEMA = "input/graph_query_result.schema.json"

# 계정 (account_id, role_id, session_ref). session_ref=None이 비인증.
GUEST = ("acc_guest", "role_guest", None)
USER = ("acc_user", "role_user", "sess_user")
USER_2 = ("acc_user2", "role_user", "sess_user2")
ADMIN = ("acc_admin", "role_admin", "sess_admin")
E_HOME = "endpoint:GET:/home"
E_ORDERS = "endpoint:GET:/orders"
E_ADMIN = "endpoint:GET:/admin"
E_USER_DETAIL = "endpoint:GET:/admin/users/{id}"
OK, DENIED, REDIRECT = 200, 403, 302

# 기본 상황: 공개 E_HOME(셋 다 2xx), user·admin만 2xx인 E_ORDERS, admin만 2xx인 E_ADMIN(자원 type 2개)·E_USER_DETAIL(instance).
BASE_ACCOUNTS = [GUEST, USER, ADMIN]
BASE_REQUESTS = [
    ("req_guest_home", "acc_guest", OK), ("req_user_home", "acc_user", OK), ("req_admin_home", "acc_admin", OK),
    ("req_user_orders", "acc_user", OK), ("req_admin_orders", "acc_admin", OK),
    ("req_admin_admin", "acc_admin", OK), ("req_admin_audit", "acc_admin", OK),
    ("req_admin_user7", "acc_admin", OK),
]


def _row(account: str, role: str, endpoint: str, request_ids: list[str], resource_id: str | None = None, scope: str = "type") -> dict[str, Any]:
    if resource_id is None:
        resource_fields: dict[str, Any] = {"resource_id": None, "resource_key": None, "resource_scope": None, "match_key": None}
    else:
        match_key = {"resource_key": "thing", "identifiers": [{"key": "id", "value": resource_id}]} if scope == "instance" else None
        resource_fields = {"resource_id": resource_id, "resource_key": "thing", "resource_scope": scope, "match_key": match_key}
    evidence = [{"evidence_id": f"ev_{request_id}", "kind": "response", "path": f"evidence/collector/response/{request_id}.json",
                 "sha256": "0" * 64, "redacted": True} for request_id in request_ids]
    return {"account_id": account, "role_id": role, "endpoint_id": endpoint, **resource_fields, "action": "read",
            "request_ids": list(request_ids), "access_observed": True, "evidence_refs": evidence}


def _base_rows() -> list[dict[str, Any]]:
    return [
        _row("acc_guest", "role_guest", E_HOME, ["req_guest_home"]),
        _row("acc_user", "role_user", E_HOME, ["req_user_home"]),
        _row("acc_admin", "role_admin", E_HOME, ["req_admin_home"]),
        _row("acc_user", "role_user", E_ORDERS, ["req_user_orders"], "resource:order"),
        _row("acc_admin", "role_admin", E_ORDERS, ["req_admin_orders"], "resource:order"),
        _row("acc_admin", "role_admin", E_ADMIN, ["req_admin_admin"], "resource:report"),
        _row("acc_admin", "role_admin", E_ADMIN, ["req_admin_audit"], "resource:audit"),
        _row("acc_admin", "role_admin", E_USER_DETAIL, ["req_admin_user7"], "resource:user:7", scope="instance"),
    ]


def _config(**overrides: Any) -> VerticalRuleConfig:
    return dataclasses.replace(DEFAULT_RULE_CONFIG.vertical_access, **overrides)


def _observations(accounts: list, requests: list):
    artifact = make_crawl_artifact(list(accounts), list(requests))
    assert schema_errors(CRAWL_SCHEMA, artifact) == []
    return build_crawl_observations(artifact["data"])


def _apply(rows: list[dict[str, Any]], accounts: list = BASE_ACCOUNTS, requests: list = BASE_REQUESTS, config: VerticalRuleConfig | None = None):
    return apply_vertical_rule(rows, _observations(accounts, requests), config or _config())


def _ids(candidates: list[dict[str, Any]]) -> list[str]:
    return [candidate["candidate_id"] for candidate in candidates]


def _by_id(candidates: list[dict[str, Any]], candidate_id: str) -> dict[str, Any]:
    return next(candidate for candidate in candidates if candidate["candidate_id"] == candidate_id)


# --- 발화 ---

def test_guest_and_user_get_candidates_for_higher_only_endpoints() -> None:
    candidates, errors = _apply(_base_rows())
    assert errors == []
    assert _ids(candidates) == [
        "rule_vertical_access:acc_guest|acc_admin|endpoint:GET:/admin",
        "rule_vertical_access:acc_guest|acc_admin|endpoint:GET:/admin/users/{id}",
        # E_ORDERS는 user·admin 둘 다 2xx. 기준은 가장 가까운 높은 역할(user)이지 admin이 아니다.
        "rule_vertical_access:acc_guest|acc_user|endpoint:GET:/orders",
        "rule_vertical_access:acc_user|acc_admin|endpoint:GET:/admin",
        "rule_vertical_access:acc_user|acc_admin|endpoint:GET:/admin/users/{id}",
    ]
    guest_admin = _by_id(candidates, "rule_vertical_access:acc_guest|acc_admin|endpoint:GET:/admin")
    assert guest_admin["vulnerability_type"] == "forced_browsing"
    assert (guest_admin["actor_account_id"], guest_admin["actor_role_id"], guest_admin["reference_account_id"]) == ("acc_guest", "role_guest", "acc_admin")
    # 기준 계정의 2xx 행이 여럿이면 자원 전부를 정렬·중복 제거해서 담는다. 하나만 고르지 않는다.
    assert guest_admin["resource_ids"] == ["resource:audit", "resource:report"]
    assert guest_admin["source_request_ids"] == ["req_admin_admin", "req_admin_audit"]
    assert [e["evidence_id"] for e in guest_admin["evidence_refs"]] == ["ev_req_admin_admin", "ev_req_admin_audit"]
    user_detail = _by_id(candidates, "rule_vertical_access:acc_user|acc_admin|endpoint:GET:/admin/users/{id}")
    assert user_detail["vulnerability_type"] == "function_level_access"
    assert user_detail["resource_ids"] == ["resource:user:7"]  # instance 자원도 그대로
    assert all(c["rule_id"] == "rule_vertical_access" and c["category"] == "authorization" and c["expected_basis"] == "inferred"
               and c["workflow_id"] is None for c in candidates)
    assert all("확정" not in c["hypothesis"] and "확정" not in c["expected_behavior"] for c in candidates)


def test_each_account_of_the_low_role_gets_its_own_candidate() -> None:
    requests = [*BASE_REQUESTS, ("req_user2_home", "acc_user2", OK)]
    rows = [*_base_rows(), _row("acc_user2", "role_user", E_HOME, ["req_user2_home"])]
    candidates, _ = _apply(rows, [*BASE_ACCOUNTS, USER_2], requests)
    user_ids = [c for c in _ids(candidates) if c.startswith("rule_vertical_access:acc_user")]
    assert user_ids == [
        "rule_vertical_access:acc_user2|acc_admin|endpoint:GET:/admin",
        "rule_vertical_access:acc_user2|acc_admin|endpoint:GET:/admin/users/{id}",
        "rule_vertical_access:acc_user|acc_admin|endpoint:GET:/admin",
        "rule_vertical_access:acc_user|acc_admin|endpoint:GET:/admin/users/{id}",
    ]


# --- 역할 순서 ---

def test_role_order_follows_access_sets_not_role_names() -> None:
    # user가 admin보다 더 많은 엔드포인트를 2xx로 쓰면 admin이 낮은 역할이다. 이름은 보지 않는다.
    rows = [
        _row("acc_user", "role_user", E_HOME, ["req_user_home"]),
        _row("acc_admin", "role_admin", E_HOME, ["req_admin_home"]),
        _row("acc_user", "role_user", E_ORDERS, ["req_user_orders"], "resource:order"),
        _row("acc_admin", "role_admin", E_ORDERS, ["req_admin_orders"], "resource:order"),
        _row("acc_user", "role_user", E_ADMIN, ["req_user_admin"], "resource:report"),
    ]
    requests = [("req_user_home", "acc_user", OK), ("req_admin_home", "acc_admin", OK), ("req_user_orders", "acc_user", OK),
                ("req_admin_orders", "acc_admin", OK), ("req_user_admin", "acc_user", OK)]
    candidates, errors = _apply(rows, [USER, ADMIN], requests)
    assert errors == []
    assert _ids(candidates) == ["rule_vertical_access:acc_admin|acc_user|endpoint:GET:/admin"]
    assert candidates[0]["vulnerability_type"] == "function_level_access"


@pytest.mark.parametrize(
    ("user_endpoints", "admin_endpoints", "reason"),
    [
        pytest.param([E_HOME, E_ORDERS], [E_HOME, E_ADMIN], "포함 관계가 아니다", id="incomparable"),
        pytest.param([E_HOME, E_ORDERS], [E_HOME, E_ORDERS], "같다", id="equal"),
    ],
)
def test_incomparable_authenticated_roles_make_no_candidates_and_warn(
    caplog: pytest.LogCaptureFixture, user_endpoints: list[str], admin_endpoints: list[str], reason: str
) -> None:
    rows, requests = [], []
    for index, endpoint in enumerate(user_endpoints):
        rows.append(_row("acc_user", "role_user", endpoint, [f"req_u{index}"], "resource:x"))
        requests.append((f"req_u{index}", "acc_user", OK))
    for index, endpoint in enumerate(admin_endpoints):
        rows.append(_row("acc_admin", "role_admin", endpoint, [f"req_a{index}"], "resource:x"))
        requests.append((f"req_a{index}", "acc_admin", OK))
    with caplog.at_level(logging.WARNING):
        candidates, errors = _apply(rows, [USER, ADMIN], requests)
    assert candidates == [] and errors == []
    assert any("role_user" in record.message and "role_admin" in record.message and reason in record.message for record in caplog.records)


def test_configured_role_order_resolves_an_incomparable_pair(caplog: pytest.LogCaptureFixture) -> None:
    rows = [
        _row("acc_user", "role_user", E_HOME, ["req_u0"], "resource:x"), _row("acc_user", "role_user", E_ORDERS, ["req_u1"], "resource:x"),
        _row("acc_admin", "role_admin", E_HOME, ["req_a0"], "resource:x"), _row("acc_admin", "role_admin", E_ADMIN, ["req_a1"], "resource:x"),
    ]
    requests = [("req_u0", "acc_user", OK), ("req_u1", "acc_user", OK), ("req_a0", "acc_admin", OK), ("req_a1", "acc_admin", OK)]
    with caplog.at_level(logging.WARNING):
        candidates, _ = _apply(rows, [USER, ADMIN], requests, _config(role_order=("role_user", "role_admin")))
    # 설정이 user < admin으로 정했으니 user 실행 계정의 후보가 나온다. admin이 2xx 받은 E_ADMIN만(E_ORDERS는 user 것).
    assert _ids(candidates) == ["rule_vertical_access:acc_user|acc_admin|endpoint:GET:/admin"]
    assert not any("정할 수 없다" in record.message for record in caplog.records)


def test_configured_role_order_wins_over_observation_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    # 관찰은 user ⊊ admin인데 설정은 admin < user. 설정을 따르고 경고를 남긴다.
    with caplog.at_level(logging.WARNING):
        candidates, errors = _apply(_base_rows(), config=_config(role_order=("role_admin", "role_user")))
    assert errors == []
    assert any("어긋난다" in record.message and "role_admin" in record.message and "role_user" in record.message for record in caplog.records)
    # admin이 낮은 역할이 됐지만 user만 2xx 받은 엔드포인트는 없어 admin 후보는 0, user는 더 높은 역할이 없어 0. guest 후보만 남는다.
    assert all(candidate["actor_account_id"] == "acc_guest" for candidate in candidates)
    assert "rule_vertical_access:acc_user|acc_admin|endpoint:GET:/admin" not in _ids(candidates)


def test_unauthenticated_role_is_lowest_even_when_access_sets_are_incomparable(caplog: pytest.LogCaptureFixture) -> None:
    # guest가 guest만 쓰는 엔드포인트(로그인 화면 등)를 2xx로 받으면 관찰 집합은 user와 포함 관계가 아니다.
    # 그래도 비인증은 구조적으로 최하위라 후보가 나오고, 이 쌍에는 "정할 수 없다" 경고가 없다.
    e_login = "endpoint:GET:/login"
    rows = [*_base_rows(), _row("acc_guest", "role_guest", e_login, ["req_guest_login"])]
    requests = [*BASE_REQUESTS, ("req_guest_login", "acc_guest", OK)]
    with caplog.at_level(logging.WARNING):
        candidates, _ = _apply(rows, BASE_ACCOUNTS, requests)
    assert "rule_vertical_access:acc_guest|acc_user|endpoint:GET:/orders" in _ids(candidates)
    assert not any("정할 수 없다" in record.message for record in caplog.records)


def test_unauthenticated_role_is_lowest_even_without_observations() -> None:
    # guest가 아무 요청도 못 했어도(접근 집합 비어 있음) 비인증은 최하위라 후보가 나온다.
    rows = [row for row in _base_rows() if row["account_id"] != "acc_guest"]
    requests = [request for request in BASE_REQUESTS if request[1] != "acc_guest"]
    candidates, errors = _apply(rows, BASE_ACCOUNTS, requests)
    guest_ids = [c for c in _ids(candidates) if "acc_guest" in c]
    assert "rule_vertical_access:acc_guest|acc_admin|endpoint:GET:/admin" in guest_ids
    assert "rule_vertical_access:acc_guest|acc_user|endpoint:GET:/orders" in guest_ids
    # 이제 E_HOME은 공개가 아니라 guest 후보 대상이다. 다만 E_HOME 행은 자원 미연결이라 후보 대신 CANDIDATE_INCOMPLETE로 남는다.
    assert ("CANDIDATE_INCOMPLETE", "rule_vertical_access:acc_guest|acc_user|endpoint:GET:/home") in [(e["code"], e["item_ref"]) for e in errors]


# --- 후보 대상에서 빼는 것 ---

def test_endpoint_reached_by_unauthenticated_role_is_public_for_every_pair() -> None:
    # guest가 E_ADMIN을 2xx로 받았으면 공개 엔드포인트다. guest 후보는 물론 user→admin 후보에서도 뺀다.
    rows = [*_base_rows(), _row("acc_guest", "role_guest", E_ADMIN, ["req_guest_admin"], "resource:report")]
    requests = [*BASE_REQUESTS, ("req_guest_admin", "acc_guest", OK)]
    candidates, _ = _apply(rows, BASE_ACCOUNTS, requests)
    assert E_ADMIN not in {candidate_id.rsplit("|", 1)[1] for candidate_id in _ids(candidates)}
    assert "rule_vertical_access:acc_user|acc_admin|endpoint:GET:/admin/users/{id}" in _ids(candidates)


@pytest.mark.parametrize("status", [OK, 401, DENIED])
def test_low_role_observation_of_success_or_denial_excludes_the_endpoint(status: int) -> None:
    rows = [*_base_rows(), _row("acc_user", "role_user", E_ADMIN, ["req_user_admin"], "resource:report")]
    requests = [*BASE_REQUESTS, ("req_user_admin", "acc_user", status)]
    candidates, _ = _apply(rows, BASE_ACCOUNTS, requests)
    assert "rule_vertical_access:acc_user|acc_admin|endpoint:GET:/admin" not in _ids(candidates)
    assert "rule_vertical_access:acc_user|acc_admin|endpoint:GET:/admin/users/{id}" in _ids(candidates)


@pytest.mark.parametrize("status", [400, 404, 405, 422, REDIRECT, 500, None])
def test_low_role_observation_of_other_statuses_keeps_the_endpoint(status: int | None) -> None:
    # 3xx·400·404·응답 없음 등은 인가 판단 근거가 아니다. 관찰했다는 이유로 후보에서 빼지 않는다.
    rows = [*_base_rows(), _row("acc_user", "role_user", E_ADMIN, ["req_user_admin"], "resource:report")]
    requests = [*BASE_REQUESTS, ("req_user_admin", "acc_user", status)]
    candidates, _ = _apply(rows, BASE_ACCOUNTS, requests)
    assert "rule_vertical_access:acc_user|acc_admin|endpoint:GET:/admin" in _ids(candidates)


def test_denied_status_codes_come_from_config() -> None:
    # 설정에서 404를 거절로 보면 404 관찰도 후보에서 뺀다. 코드에 박힌 목록이 아니다.
    rows = [*_base_rows(), _row("acc_user", "role_user", E_ADMIN, ["req_user_admin"], "resource:report")]
    requests = [*BASE_REQUESTS, ("req_user_admin", "acc_user", 404)]
    candidates, _ = _apply(rows, BASE_ACCOUNTS, requests, _config(denied_status_codes=frozenset({401, 403, 404})))
    assert "rule_vertical_access:acc_user|acc_admin|endpoint:GET:/admin" not in _ids(candidates)


# --- 기준 계정의 요청·자원 ---

def test_source_request_ids_keep_only_success_requests_of_the_reference() -> None:
    rows = [*_base_rows(), _row("acc_admin", "role_admin", E_ADMIN, ["req_admin_fail", "req_admin_ok2"], "resource:report")]
    requests = [*BASE_REQUESTS, ("req_admin_fail", "acc_admin", 500), ("req_admin_ok2", "acc_admin", OK)]
    candidates, _ = _apply(rows, BASE_ACCOUNTS, requests)
    candidate = _by_id(candidates, "rule_vertical_access:acc_user|acc_admin|endpoint:GET:/admin")
    assert candidate["source_request_ids"] == ["req_admin_admin", "req_admin_audit", "req_admin_ok2"]


def test_reference_rows_without_resource_make_candidate_incomplete() -> None:
    # admin의 E_ADMIN 행이 전부 미연결이면 resource_ids를 못 채운다. 조용히 버리지 않고 실행 계정마다 오류로 남긴다.
    rows = [row for row in _base_rows() if row["endpoint_id"] != E_ADMIN]
    rows.append(_row("acc_admin", "role_admin", E_ADMIN, ["req_admin_admin"]))
    candidates, errors = _apply(rows)
    assert E_ADMIN not in {candidate_id.rsplit("|", 1)[1] for candidate_id in _ids(candidates)}
    assert [(e["code"], e["item_ref"]) for e in errors] == [
        ("CANDIDATE_INCOMPLETE", "rule_vertical_access:acc_guest|acc_admin|endpoint:GET:/admin"),
        ("CANDIDATE_INCOMPLETE", "rule_vertical_access:acc_user|acc_admin|endpoint:GET:/admin"),
    ]


def test_row_with_request_missing_from_crawl_is_dropped_and_reported() -> None:
    rows = [*_base_rows(), _row("acc_admin", "role_admin", E_ADMIN, ["req_admin_ghost"], "resource:ghost")]
    candidates, errors = _apply(rows)
    assert [(e["code"], e["item_ref"], e["retryable"]) for e in errors] == [("ACCESS_REQUEST_UNRESOLVED", "req_admin_ghost", False)]
    # 그 행만 빠진다. 같은 엔드포인트의 다른 행으로 만든 후보는 그대로다.
    candidate = _by_id(candidates, "rule_vertical_access:acc_user|acc_admin|endpoint:GET:/admin")
    assert candidate["resource_ids"] == ["resource:audit", "resource:report"]


# --- 상한·결정성·공존 ---

def test_truncation_keeps_sorted_prefix_and_reports() -> None:
    candidates, errors = _apply(_base_rows(), config=_config(max_candidates=2))
    assert _ids(candidates) == [
        "rule_vertical_access:acc_guest|acc_admin|endpoint:GET:/admin",
        "rule_vertical_access:acc_guest|acc_admin|endpoint:GET:/admin/users/{id}",
    ]
    assert [e["code"] for e in errors] == ["CANDIDATES_TRUNCATED"]


def test_different_combinations_never_share_a_candidate_id() -> None:
    # 계정 ID에 구분자('|')가 들어 있어도 (실행, 기준, 엔드포인트) 조합이 다르면 ID가 다르다.
    # 두 실행 계정 "acc_u|x"와 "acc_u" 중 하나만 있으면 안 되는 상황을 만들려고 둘 다 user 역할로 둔다.
    tricky_user = ("acc_u|x", "role_user", "sess_ux")
    plain_user = ("acc_u", "role_user", "sess_u")
    rows = [*_base_rows(), _row("acc_u|x", "role_user", E_HOME, ["req_ux_home"]), _row("acc_u", "role_user", E_HOME, ["req_u_home"])]
    requests = [*BASE_REQUESTS, ("req_ux_home", "acc_u|x", OK), ("req_u_home", "acc_u", OK)]
    candidates, _ = _apply(rows, [*BASE_ACCOUNTS, tricky_user, plain_user], requests)
    ids = _ids(candidates)
    assert len(ids) == len(set(ids))
    combos = {(c["actor_account_id"], c["reference_account_id"], c["candidate_id"].rsplit("|", 1)[1]) for c in candidates}
    assert len(combos) == len(candidates)
    assert "rule_vertical_access:acc_u%7Cx|acc_admin|endpoint:GET:/admin" in ids


def test_candidates_are_deterministic_regardless_of_input_order() -> None:
    forward, _ = _apply(_base_rows(), BASE_ACCOUNTS, BASE_REQUESTS)
    backward, _ = _apply(list(reversed(_base_rows())), list(reversed(BASE_ACCOUNTS)), list(reversed(BASE_REQUESTS)))
    assert _ids(forward) == _ids(backward) == sorted(_ids(forward))


def test_disabled_rule_or_missing_observations_make_no_vertical_candidates() -> None:
    result_data = {"results": [
        {"query_id": "q_resource_ownership", "query_key": "resource_ownership", "status": "completed", "rows": [], "errors": []},
        {"query_id": "q_role_resource_access", "query_key": "role_resource_access", "status": "completed", "rows": _base_rows(), "errors": []},
    ]}
    disabled = dataclasses.replace(DEFAULT_RULE_CONFIG, vertical_access=_config(enabled=False))
    assert rules.apply_rules(result_data, disabled, _observations(BASE_ACCOUNTS, BASE_REQUESTS)) == ([], [])
    assert rules.apply_rules(result_data, DEFAULT_RULE_CONFIG, None) == ([], [])


def test_vertical_candidates_follow_rule_a_candidates_without_id_overlap() -> None:
    # 같은 입력에서 수평(같은 역할 user 둘)과 수직(guest·user → admin)이 같이 나온다. 순서는 Rule A → 수직.
    ownership_rows = [
        {"resource_id": "resource:order:1", "resource_key": "order", "resource_scope": "instance",
         "match_key": {"resource_key": "order", "identifiers": [{"key": "id", "value": "1"}]}, "owner_account_id": "acc_user", "basis": "inferred", "evidence_refs": []},
        {"resource_id": "resource:order:2", "resource_key": "order", "resource_scope": "instance",
         "match_key": {"resource_key": "order", "identifiers": [{"key": "id", "value": "2"}]}, "owner_account_id": "acc_user2", "basis": "inferred", "evidence_refs": []},
    ]
    e_order_detail = "endpoint:GET:/orders/{id}"
    access_rows = [
        *_base_rows(),
        _row("acc_user", "role_user", e_order_detail, ["req_user_order1"], "resource:order:1", scope="instance"),
        _row("acc_user2", "role_user", e_order_detail, ["req_user2_order2"], "resource:order:2", scope="instance"),
        _row("acc_user2", "role_user", E_HOME, ["req_user2_home"]),
        _row("acc_user2", "role_user", E_ORDERS, ["req_user2_orders"], "resource:order"),
        _row("acc_admin", "role_admin", e_order_detail, ["req_admin_order1"], "resource:order:1", scope="instance"),
    ]
    requests = [*BASE_REQUESTS, ("req_user_order1", "acc_user", OK), ("req_user2_order2", "acc_user2", OK),
                ("req_user2_home", "acc_user2", OK), ("req_user2_orders", "acc_user2", OK), ("req_admin_order1", "acc_admin", OK)]
    artifact = {
        "schema_version": "0.3.0", "artifact_type": "graph_query_result", "artifact_id": "r", "run_id": "run_demo_001",
        "iteration": 0, "producer": "knowledge_graph", "mode": "development", "created_at": "2026-10-07T00:03:00Z",
        "status": "completed", "errors": [], "runtime_metrics": None,
        "input_refs": [{"artifact_id": "gq", "artifact_type": "graph_query", "iteration": 0,
                        "path": "artifacts/iteration-000/access_analyzer/graph_query.json", "sha256": "0" * 64}],
        "data": {"graph_id": "graph_demo_001", "graph_revision": 1, "results": [
            {"query_id": "q_resource_ownership", "query_key": "resource_ownership", "status": "completed", "rows": ownership_rows, "errors": []},
            {"query_id": "q_role_resource_access", "query_key": "role_resource_access", "status": "completed", "rows": access_rows, "errors": []},
            {"query_id": "q_workflow_dependencies", "query_key": "workflow_dependencies", "status": "completed", "rows": [], "errors": []},
            {"query_id": "q_structure_snapshot", "query_key": "structure_snapshot", "status": "completed",
             "rows": [{"nodes": [], "relationships": [], "workflows": []}], "errors": []},
        ]},
    }
    assert schema_errors(RESULT_SCHEMA, artifact) == []
    crawl = make_crawl_artifact([*BASE_ACCOUNTS, USER_2], requests)
    out = service.build_candidates_result(artifact, "graph_demo_001", 1, "run_demo_001", config=DEFAULT_RULE_CONFIG, crawl_artifact=crawl)
    assert out.status.value == "completed"
    rule_ids = [candidate["rule_id"] for candidate in out.data["candidates"]]
    assert rule_ids[:2] == ["rule_same_role_other_owner"] * 2 and set(rule_ids[2:]) == {"rule_vertical_access"}
    ids = _ids(out.data["candidates"])
    assert len(ids) == len(set(ids))
    # 같은 (실행, 기준, 대상) 조합이 두 규칙에 동시에 나올 수 없다: Rule A는 같은 역할, 수직은 낮은 역할이 실행 계정이다.
    assert all(c["actor_role_id"] == "role_user" and c["reference_account_id"] in {"acc_user", "acc_user2"} for c in out.data["candidates"][:2])
    assert all(c["actor_role_id"] != "role_admin" for c in out.data["candidates"][2:])
