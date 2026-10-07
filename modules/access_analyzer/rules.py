"""분석 규칙. 지금은 Rule A(same_role_other_owner) 하나.

구조로만 판단한다: 역할 이름·경로·필드명이 아니라 소유 관계(OwnershipRow)와 역할 ID 동일성(AccessRow.role_id)으로.
후보는 검증 대상이지 확정 취약점이 아니다. 규칙 입력은 분석 쿼리(resource_ownership·role_resource_access) 결과뿐이다.
structure_snapshot은 평가·감사용이라 규칙 입력으로 쓰지 않는다(명세 m4).
"""

from typing import Any

from modules.access_analyzer.utils.config import RuleConfig
from modules.access_analyzer.utils.envelope import ErrorCode, make_error_item

RULE_ID = "rule_same_role_other_owner"
CATEGORY = "authorization"
VULNERABILITY_TYPE = "horizontal_access"
# 기대 조건의 근거: 소유 관계가 추론이고 교차 접근을 관찰한 적 없는 휴리스틱이라 rule(확정)도 unknown도 아닌 inferred.
EXPECTED_BASIS = "inferred"
HYPOTHESIS = "같은 역할의 다른 계정이 소유자의 자원에 접근할 수 있을 수 있다"
EXPECTED_BEHAVIOR = "소유자가 아닌 계정의 접근은 거절되어야 한다"


def apply_rules(result_data: dict[str, Any], config: RuleConfig) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """완료된 분석 쿼리 결과로 후보와 규칙 오류를 만든다. (candidates, errors)."""
    ownership_rows = _completed_rows(result_data["results"], "resource_ownership")
    access_rows = _completed_rows(result_data["results"], "role_resource_access")
    if not config.same_role_other_owner.enabled:
        return [], []
    return _same_role_other_owner(ownership_rows, access_rows, config.same_role_other_owner.max_candidates)


def _completed_rows(results: list[dict[str, Any]], query_key: str) -> list[dict[str, Any]]:
    """해당 query_key 결과의 rows. 그 질의가 실패(partial 입력)면 빈 목록."""
    for result in results:
        if result["query_key"] == query_key and result["status"] == "completed":
            return result["rows"]
    return []


def _same_role_other_owner(
    ownership_rows: list[dict[str, Any]], access_rows: list[dict[str, Any]], max_candidates: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    role_by_account: dict[str, str] = {}
    accounts_by_role: dict[str, set[str]] = {}
    request_ids_by_owner_resource: dict[tuple[str, str], list[str]] = {}
    evidence_by_owner_resource: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in access_rows:
        account_id = row["account_id"]
        role_id = row["role_id"]
        role_by_account.setdefault(account_id, role_id)
        accounts_by_role.setdefault(role_id, set()).add(account_id)
        resource_id = row["resource_id"]
        if resource_id is None:
            continue
        key = (account_id, resource_id)
        request_bucket = request_ids_by_owner_resource.setdefault(key, [])
        for request_id in row["request_ids"]:
            if request_id not in request_bucket:
                request_bucket.append(request_id)
        _merge_evidence(evidence_by_owner_resource.setdefault(key, []), row["evidence_refs"])

    candidates: list[dict[str, Any]] = []
    incomplete_errors: list[dict[str, Any]] = []
    for ownership in ownership_rows:
        resource_id = ownership["resource_id"]
        owner_account_id = ownership["owner_account_id"]
        owner_role_id = role_by_account.get(owner_account_id)
        if owner_role_id is None:
            # 소유자의 역할을 접근 관찰로 알 수 없다. 소유자 불명으로 보고 후보를 만들지 않는다.
            continue
        peers = sorted(accounts_by_role.get(owner_role_id, set()) - {owner_account_id})
        for actor_account_id in peers:
            candidate_id = f"{RULE_ID}:{actor_account_id}|{owner_account_id}|{resource_id}"
            request_ids = request_ids_by_owner_resource.get((owner_account_id, resource_id))
            if not request_ids:
                # 소유자가 그 자원에 접근한 요청을 못 찾으면 재현할 수집 요청이 없다. 발행하지 않고 오류로 남긴다(확정 답 2-c).
                incomplete_errors.append(
                    make_error_item(
                        ErrorCode.CANDIDATE_INCOMPLETE,
                        "소유자 접근 요청을 찾지 못해 후보를 발행하지 않음",
                        item_ref=candidate_id,
                        is_retryable=True,
                    )
                )
                continue
            evidence_refs = list(ownership["evidence_refs"])
            _merge_evidence(evidence_refs, evidence_by_owner_resource.get((owner_account_id, resource_id), []))
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "category": CATEGORY,
                    "vulnerability_type": VULNERABILITY_TYPE,
                    "rule_id": RULE_ID,
                    "actor_account_id": actor_account_id,
                    "actor_role_id": owner_role_id,
                    "reference_account_id": owner_account_id,
                    "resource_ids": [resource_id],
                    "source_request_ids": list(request_ids),
                    "workflow_id": None,
                    "hypothesis": HYPOTHESIS,
                    "expected_behavior": EXPECTED_BEHAVIOR,
                    "expected_basis": EXPECTED_BASIS,
                    "evidence_refs": evidence_refs,
                }
            )

    # 결정성: 입력 row 순서와 무관하게 candidate_id로 정렬한다. 상한도 정렬 뒤 적용해 어떤 후보가 남는지 고정한다.
    candidates.sort(key=lambda candidate: candidate["candidate_id"])
    incomplete_errors.sort(key=lambda error: error["item_ref"])
    errors = incomplete_errors
    if len(candidates) > max_candidates:
        dropped = len(candidates) - max_candidates
        candidates = candidates[:max_candidates]
        errors = [
            *incomplete_errors,
            make_error_item(
                ErrorCode.CANDIDATES_TRUNCATED,
                f"후보가 상한({max_candidates})을 넘어 {dropped}건 생략",
                is_retryable=False,
            ),
        ]
    return candidates, errors


def _merge_evidence(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> None:
    """evidence_id 기준 중복 없이 이어 붙인다(순서 보존)."""
    seen = {item["evidence_id"] for item in existing}
    for item in incoming:
        if item["evidence_id"] not in seen:
            existing.append(item)
            seen.add(item["evidence_id"])
