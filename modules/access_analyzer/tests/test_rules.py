"""Rule A(same_role_other_owner) 판정. 각 테스트는 '이 판정을 빼면 통과해 버리는' 버그 하나를 겨눈다.

firing 케이스는 손 fixture(two_owners)로, 변이 케이스는 리터럴 입력으로. 기대값(candidate_id·source_request_ids)은
코드 상수가 아니라 여기에 리터럴로 적는다. 커밋 테스트는 우리 폴더 Schema만 읽는다.
"""

import json
from pathlib import Path

from modules.access_analyzer import entrypoint as ep, service
from modules.access_analyzer.tests.helpers import make_context, output_dir_for, schema_errors
from modules.access_analyzer.utils.config import RuleConfig, SameRoleRuleConfig

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "graph_query_result"
INPUT_REL = "artifacts/iteration-000/knowledge_graph/graph_query_result.json"
CANDIDATES_SCHEMA = "output/vulnerability_candidates.schema.json"


def _config(enabled: bool = True, max_candidates: int = 100) -> RuleConfig:
    return RuleConfig(SameRoleRuleConfig(enabled=enabled, max_candidates=max_candidates))


def _artifact(results, status="completed", errors=None, revision=1):
    return {
        "schema_version": "0.1.0", "artifact_type": "graph_query_result", "artifact_id": "r",
        "run_id": "run_demo_001", "iteration": 0, "producer": "knowledge_graph", "mode": "development",
        "created_at": "2026-10-07T00:03:00Z", "status": status, "input_refs": [], "errors": errors or [],
        "runtime_metrics": None,
        "data": {"graph_id": "graph_demo_001", "graph_revision": revision, "results": results},
    }


def _ownership(rows):
    return {"query_id": "q_o", "query_key": "resource_ownership", "status": "completed", "errors": [], "rows": rows}


def _access(rows):
    return {"query_id": "q_a", "query_key": "role_resource_access", "status": "completed", "errors": [], "rows": rows}


def _own_row(resource_id, owner):
    return {"resource_id": resource_id, "owner_account_id": owner, "basis": "inferred", "evidence_refs": []}


def _acc_row(account, role, resource_id, request_ids):
    return {
        "account_id": account, "role_id": role, "endpoint_id": "endpoint:GET:/x/{id}",
        "resource_id": resource_id, "action": "read", "request_ids": request_ids,
        "access_observed": True, "evidence_refs": [],
    }


def _run(artifact, config=None):
    return service.build_candidates_result(artifact, "graph_demo_001", 1, "run_demo_001", config=config)


# --- firing: 손 fixture end-to-end ---

def test_two_owners_emit_ordered_pair_candidates(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    target = run_root / INPUT_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((FIXTURE_DIR / "two_owners.json").read_bytes())
    result = ep.run("analyze", [INPUT_REL], output_dir_for(run_root), make_context(run_root))
    doc = json.loads((output_dir_for(run_root) / "vulnerability_candidates.json").read_bytes())
    assert result["status"] == "completed"
    candidates = doc["data"]["candidates"]
    assert [c["candidate_id"] for c in candidates] == [
        "rule_same_role_other_owner:acc_alice|acc_bob|resource:order_b",
        "rule_same_role_other_owner:acc_bob|acc_alice|resource:order_a",
    ]
    first, second = candidates
    assert (first["actor_account_id"], first["reference_account_id"], first["resource_ids"]) == (
        "acc_alice", "acc_bob", ["resource:order_b"])
    assert first["source_request_ids"] == ["req_bob_order_b"]
    assert second["source_request_ids"] == ["req_alice_order_a"]
    assert all(c["expected_basis"] == "inferred" and c["vulnerability_type"] == "horizontal_access" for c in candidates)
    assert schema_errors(CANDIDATES_SCHEMA, doc) == []


# --- 변이 케이스(리터럴 입력) ---

def test_different_role_makes_no_candidate() -> None:
    # order_b 소유자 bob이 다른 역할이면 alice와 같은 역할이 아니다 → 후보 0.
    out = _run(_artifact([
        _ownership([_own_row("resource:order_b", "acc_bob")]),
        _access([
            _acc_row("acc_alice", "role_user", "resource:order_a", ["req_a"]),
            _acc_row("acc_bob", "role_admin", "resource:order_b", ["req_b"]),
        ]),
    ]))
    assert out.status.value == "completed"
    assert out.data["candidates"] == []


def test_resource_without_owner_excluded() -> None:
    # 공개 자원은 OwnershipRow가 없다 → 후보 0(소유자 지어내지 않음).
    out = _run(_artifact([
        _ownership([]),
        _access([
            _acc_row("acc_alice", "role_user", "resource:public", ["req_a"]),
            _acc_row("acc_bob", "role_user", "resource:public", ["req_b"]),
        ]),
    ]))
    assert out.data["candidates"] == []


def test_owner_access_missing_is_incomplete_partial() -> None:
    # 소유자 bob이 order_b에 접근한 AccessRow가 없다 → request_ids를 못 채워 미발행 + CANDIDATE_INCOMPLETE.
    out = _run(_artifact([
        _ownership([_own_row("resource:order_b", "acc_bob")]),
        _access([
            _acc_row("acc_alice", "role_user", "resource:order_a", ["req_a"]),
            _acc_row("acc_bob", "role_user", "resource:other", ["req_b"]),
        ]),
    ]))
    assert out.status.value == "partial"
    assert out.data["candidates"] == []
    assert [e["code"] for e in out.errors] == ["CANDIDATE_INCOMPLETE"]
    assert out.errors[0]["item_ref"] == "rule_same_role_other_owner:acc_alice|acc_bob|resource:order_b"


def test_source_request_ids_come_from_owner_access() -> None:
    out = _run(_artifact([
        _ownership([_own_row("resource:order_b", "acc_bob")]),
        _access([
            _acc_row("acc_alice", "role_user", "resource:order_a", ["req_alice"]),
            _acc_row("acc_bob", "role_user", "resource:order_b", ["req_bob_1", "req_bob_2"]),
        ]),
    ]))
    [candidate] = out.data["candidates"]
    # 소유자(bob)가 order_b에 접근한 요청을 쓴다. actor(alice)의 요청이 아니다.
    assert candidate["source_request_ids"] == ["req_bob_1", "req_bob_2"]
    assert candidate["actor_account_id"] == "acc_alice"


def test_truncation_sets_partial_and_error() -> None:
    # 같은 역할 3계정이 각자 자원 소유·접근 → 후보 6개. 상한 2면 4개 잘림.
    accounts = ["acc_a", "acc_b", "acc_c"]
    ownership = _ownership([_own_row(f"resource:{a}", a) for a in accounts])
    access = _access([_acc_row(a, "role_user", f"resource:{a}", [f"req_{a}"]) for a in accounts])
    out = _run(_artifact([ownership, access]), config=_config(max_candidates=2))
    assert out.status.value == "partial"
    assert len(out.data["candidates"]) == 2
    assert any(e["code"] == "CANDIDATES_TRUNCATED" for e in out.errors)


def test_candidate_ids_are_deterministic_regardless_of_row_order() -> None:
    accounts = ["acc_a", "acc_b", "acc_c"]
    forward = _artifact([
        _ownership([_own_row(f"resource:{a}", a) for a in accounts]),
        _access([_acc_row(a, "role_user", f"resource:{a}", [f"req_{a}"]) for a in accounts]),
    ])
    reversed_rows = _artifact([
        _ownership([_own_row(f"resource:{a}", a) for a in reversed(accounts)]),
        _access([_acc_row(a, "role_user", f"resource:{a}", [f"req_{a}"]) for a in reversed(accounts)]),
    ])
    ids_forward = [c["candidate_id"] for c in _run(forward).data["candidates"]]
    ids_reversed = [c["candidate_id"] for c in _run(reversed_rows).data["candidates"]]
    assert ids_forward == ids_reversed
    assert ids_forward == sorted(ids_forward)  # candidate_id로 정렬된 결정적 순서


def test_rule_disabled_makes_no_candidate() -> None:
    out = _run(_artifact([
        _ownership([_own_row("resource:order_b", "acc_bob")]),
        _access([
            _acc_row("acc_alice", "role_user", "resource:order_a", ["req_a"]),
            _acc_row("acc_bob", "role_user", "resource:order_b", ["req_b"]),
        ]),
    ]), config=_config(enabled=False))
    assert out.data["candidates"] == []
