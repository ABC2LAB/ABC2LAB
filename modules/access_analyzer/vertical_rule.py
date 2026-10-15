"""수직 접근 규칙(rule_vertical_access): 낮은 역할 계정이 높은 역할만 2xx로 쓴 엔드포인트에 접근할 수 있는지 후보를 만든다.

기능 수준 인가(인증 역할 사이, function_level_access)와 강제 브라우징(비인증 → 인증 역할, forced_browsing)을 한 규칙으로
다룬다. 구조로만 판단한다: 역할 이름·URL이 아니라 crawl 계정의 세션 유무, 관찰한 응답 상태, KG 접근 행의
endpoint_id·resource_id로. 후보는 가설이지 확정이 아니다(expected_basis=inferred, 검증은 verifier).

- 2xx(성공 범위)는 crawl_result 응답 상태에서 본다. KG `access_observed`는 늘 true라 쓸 수 없다. request_id를 crawl에서
  못 찾은 행은 빼고 ACCESS_REQUEST_UNRESOLVED로 남긴다.
- 비인증 역할 = crawl 계정이 전부 session_ref=null. 비인증은 모든 인증 역할보다 낮다(구조적 사실, 설정과 무관).
- 인증 역할끼리는 2xx 엔드포인트 집합이 진부분집합(⊊)일 때만 낮다. 같거나 비교 불가면 그 쌍은 후보를 내지 않고 경고를
  남긴다. 설정 role_order(낮은 → 높은)에 둘 다 있으면 설정이 우선이고, 관찰이 정반대면 경고를 남긴다.
- 후보 엔드포인트 = 높은 역할이 2xx 받은 것 − 비인증 역할이 2xx 받은 것(공개) − 낮은 역할이 2xx 또는 거절(denied_status_codes)로
  관찰한 것. 그 밖의 상태(3xx·400·404·405·422 등)로만 관찰한 엔드포인트는 인가 판단 근거가 아니라 후보로 남긴다.
- 후보 1건 = (낮은 역할의 crawl 계정, 엔드포인트). 기준 계정은 그 엔드포인트에 2xx 행이 있는 가장 가까운 높은 역할의 계정이다.
  resource_ids는 기준 계정 2xx 행의 resource_id 전부(정렬·중복 제거, type·instance 모두), 하나도 없으면 CANDIDATE_INCOMPLETE.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from modules.access_analyzer.candidate_ids import build_candidate_id
from modules.access_analyzer.evidence import merge_evidence
from modules.access_analyzer.observations import CrawlObservations
from modules.access_analyzer.utils.config import VerticalRuleConfig
from modules.access_analyzer.utils.envelope import ErrorCode, make_error_item

logger = logging.getLogger(__name__)

RULE_ID = "rule_vertical_access"
CATEGORY = "authorization"
# 기대 조건의 근거: 역할 순서가 관찰 추정(또는 운영자 설정)이고 교차 접근을 관찰한 적 없는 휴리스틱이라 inferred.
EXPECTED_BASIS = "inferred"
# vulnerability_type은 GT 어휘와 같은 값이다. 어휘를 새로 만들지 않는다(vertical_access 같은 이름은 쓰지 않는다).
FUNCTION_LEVEL_ACCESS = "function_level_access"
FORCED_BROWSING = "forced_browsing"
HYPOTHESIS_BY_TYPE = {
    FUNCTION_LEVEL_ACCESS: "낮은 역할 계정이 상위 역할만 사용한 기능에 접근할 수 있을 수 있다",
    FORCED_BROWSING: "비인증 계정이 인증 역할만 사용한 경로에 직접 접근할 수 있을 수 있다",
}
EXPECTED_BEHAVIOR_BY_TYPE = {
    FUNCTION_LEVEL_ACCESS: "상위 역할이 아닌 계정의 접근은 거절되어야 한다",
    FORCED_BROWSING: "인증되지 않은 접근은 거절되어야 한다",
}
# crawl에 없는 request_id 표시. 응답 없음(None)과 구분한다.
_MISSING = object()


@dataclass(frozen=True)
class _ObservedRow:
    """요청 상태를 crawl에서 전부 찾은 KG 접근 행. statuses는 row["request_ids"] 순서다."""

    row: dict[str, Any]
    statuses: tuple[int | None, ...]


@dataclass
class _RoleAccess:
    """역할 하나의 관찰: 2xx 받은 엔드포인트 집합과 엔드포인트별로 관찰한 상태 전부."""

    success_endpoints: set[str] = field(default_factory=set)
    statuses_by_endpoint: dict[str, set[int | None]] = field(default_factory=dict)


def apply_vertical_rule(
    access_rows: list[dict[str, Any]], observations: CrawlObservations, config: VerticalRuleConfig
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """role_resource_access 행과 crawl 관찰로 수직 후보와 규칙 오류를 만든다. (candidates, errors)."""
    resolved_rows, unresolved_errors = _resolve_statuses(access_rows, observations)
    accounts_by_role = _accounts_by_role(observations)
    if not accounts_by_role:
        return [], unresolved_errors
    access_by_role = _collect_role_access(resolved_rows, config)
    unauthenticated_roles = {
        role_id for role_id, account_ids in accounts_by_role.items()
        if all(observations.accounts_by_id[account_id].session_ref is None for account_id in account_ids)
    }
    higher_roles_by_role = _order_roles(sorted(accounts_by_role), unauthenticated_roles, access_by_role, config.role_order)
    # 비인증 역할이 2xx 받은 엔드포인트는 공개로 보고 모든 쌍에서 뺀다.
    public_endpoints = set().union(*(_success_endpoints(access_by_role, role_id) for role_id in unauthenticated_roles))
    success_rows = _success_rows_by_account_endpoint(resolved_rows, config)

    candidates: list[dict[str, Any]] = []
    incomplete_errors: list[dict[str, Any]] = []
    for low_role in sorted(accounts_by_role):
        higher_roles = higher_roles_by_role[low_role]
        if not higher_roles:
            continue
        low_access = access_by_role.get(low_role, _RoleAccess())
        reachable = set().union(*(_success_endpoints(access_by_role, role_id) for role_id in higher_roles))
        vulnerability_type = FORCED_BROWSING if low_role in unauthenticated_roles else FUNCTION_LEVEL_ACCESS
        for endpoint_id in sorted(reachable - public_endpoints):
            if _observed_decisively(low_access, endpoint_id, config):
                continue
            reference = _choose_reference(endpoint_id, higher_roles, higher_roles_by_role, accounts_by_role, success_rows)
            if reference is None:
                continue
            reference_rows = success_rows[(reference, endpoint_id)]
            resource_ids = sorted({row.row["resource_id"] for row in reference_rows if row.row["resource_id"] is not None})
            for actor in accounts_by_role[low_role]:
                candidate_id = build_candidate_id(RULE_ID, actor, reference, endpoint_id)
                if not resource_ids:
                    # 기준 계정의 2xx 행이 자원과 연결되지 않았다(미연결 행뿐). resource_ids를 못 채우므로 발행하지 않고 남긴다.
                    incomplete_errors.append(
                        make_error_item(
                            ErrorCode.CANDIDATE_INCOMPLETE,
                            "기준 계정의 2xx 접근 행이 자원과 연결되지 않아 후보를 발행하지 않음",
                            item_ref=candidate_id,
                            is_retryable=True,
                        )
                    )
                    continue
                candidates.append(
                    _build_candidate(candidate_id, actor, low_role, reference, endpoint_id, vulnerability_type, resource_ids, reference_rows, config)
                )
    return _finalize(candidates, unresolved_errors + sorted(incomplete_errors, key=lambda error: error["item_ref"]), config.max_candidates)


def _resolve_statuses(
    access_rows: list[dict[str, Any]], observations: CrawlObservations
) -> tuple[list[_ObservedRow], list[dict[str, Any]]]:
    """행의 request_id마다 crawl 응답 상태를 찾는다. 하나라도 없으면 그 행은 빼고 request_id별 오류 하나."""
    resolved: list[_ObservedRow] = []
    unresolved_ids: list[str] = []
    for row in access_rows:
        statuses = [observations.status_by_request_id.get(request_id, _MISSING) for request_id in row["request_ids"]]
        missing = [request_id for request_id, status in zip(row["request_ids"], statuses) if status is _MISSING]
        if missing:
            unresolved_ids += [request_id for request_id in missing if request_id not in unresolved_ids]
            continue
        resolved.append(_ObservedRow(row, tuple(statuses)))
    errors = [
        make_error_item(
            ErrorCode.ACCESS_REQUEST_UNRESOLVED,
            "KG 접근 행의 request_id가 crawl_result에 없어 그 행을 수직 규칙에서 제외함",
            item_ref=request_id,
            is_retryable=False,
        )
        for request_id in sorted(unresolved_ids)
    ]
    return resolved, errors


def _accounts_by_role(observations: CrawlObservations) -> dict[str, list[str]]:
    """실행 계정 후보는 crawl 계정이다(검증은 crawl 계정으로만 돈다). 역할마다 account_id 정렬."""
    accounts_by_role: dict[str, list[str]] = {}
    for account in observations.accounts_by_id.values():
        accounts_by_role.setdefault(account.role_id, []).append(account.account_id)
    return {role_id: sorted(account_ids) for role_id, account_ids in accounts_by_role.items()}


def _collect_role_access(rows: list[_ObservedRow], config: VerticalRuleConfig) -> dict[str, _RoleAccess]:
    access_by_role: dict[str, _RoleAccess] = {}
    for observed in rows:
        access = access_by_role.setdefault(observed.row["role_id"], _RoleAccess())
        endpoint_id = observed.row["endpoint_id"]
        access.statuses_by_endpoint.setdefault(endpoint_id, set()).update(observed.statuses)
        if any(_is_success(status, config) for status in observed.statuses):
            access.success_endpoints.add(endpoint_id)
    return access_by_role


def _success_endpoints(access_by_role: dict[str, _RoleAccess], role_id: str) -> set[str]:
    access = access_by_role.get(role_id)
    return set(access.success_endpoints) if access is not None else set()


def _success_rows_by_account_endpoint(
    rows: list[_ObservedRow], config: VerticalRuleConfig
) -> dict[tuple[str, str], list[_ObservedRow]]:
    """(계정, 엔드포인트)별로 2xx 요청이 하나라도 있는 행. 기준 계정의 자원·요청·근거를 여기서 꺼낸다."""
    success_rows: dict[tuple[str, str], list[_ObservedRow]] = {}
    for observed in rows:
        if any(_is_success(status, config) for status in observed.statuses):
            success_rows.setdefault((observed.row["account_id"], observed.row["endpoint_id"]), []).append(observed)
    return success_rows


def _order_roles(
    roles: list[str], unauthenticated_roles: set[str], access_by_role: dict[str, _RoleAccess], role_order: tuple[str, ...]
) -> dict[str, frozenset[str]]:
    """역할마다 자기보다 높은 역할 집합. 비인증 < 인증은 구조, 인증끼리는 ⊊ 관찰, 설정 role_order가 둘 다 담으면 설정."""
    higher_by_role: dict[str, set[str]] = {role_id: set() for role_id in roles}
    configured_index = {role_id: index for index, role_id in enumerate(role_order)}
    for position, first in enumerate(roles):
        for second in roles[position + 1:]:
            lower, higher = _compare_roles(first, second, unauthenticated_roles, access_by_role, configured_index)
            if lower is not None and higher is not None:
                higher_by_role[lower].add(higher)
    return {role_id: frozenset(higher) for role_id, higher in higher_by_role.items()}


def _compare_roles(
    first: str,
    second: str,
    unauthenticated_roles: set[str],
    access_by_role: dict[str, _RoleAccess],
    configured_index: dict[str, int],
) -> tuple[str | None, str | None]:
    """두 역할의 (낮은, 높은). 정할 수 없으면 (None, None)."""
    first_unauthenticated, second_unauthenticated = first in unauthenticated_roles, second in unauthenticated_roles
    if first_unauthenticated and second_unauthenticated:
        return None, None  # 둘 다 최하위. 비인증끼리는 비교하지 않는다.
    if first_unauthenticated or second_unauthenticated:
        lower, higher = (first, second) if first_unauthenticated else (second, first)
        if first in configured_index and second in configured_index and configured_index[lower] > configured_index[higher]:
            logger.warning("설정 role_order가 비인증 역할 %s를 인증 역할 %s보다 높게 두었다. 비인증은 항상 최하위라 설정을 따르지 않는다", lower, higher)
        return lower, higher
    observed = _observed_relation(first, second, access_by_role)
    if first in configured_index and second in configured_index:
        configured = (first, second) if configured_index[first] < configured_index[second] else (second, first)
        if observed is not None and observed != configured:
            logger.warning("설정 role_order(%s < %s)와 관찰(2xx 접근 집합 %s ⊊ %s)이 어긋난다. 설정을 따른다", *configured, *observed)
        return configured
    if observed is None:
        first_set, second_set = _success_endpoints(access_by_role, first), _success_endpoints(access_by_role, second)
        reason = "접근 집합이 같다" if first_set == second_set else "접근 집합이 포함 관계가 아니다"
        logger.warning("역할 쌍 %s·%s의 높낮이를 정할 수 없다(%s). 이 쌍은 수직 후보를 만들지 않는다. role_order 설정으로 정할 수 있다", first, second, reason)
        return None, None
    return observed


def _observed_relation(first: str, second: str, access_by_role: dict[str, _RoleAccess]) -> tuple[str, str] | None:
    """2xx 엔드포인트 집합의 진부분집합 관계로 (낮은, 높은). 같거나 비교 불가면 None."""
    first_set, second_set = _success_endpoints(access_by_role, first), _success_endpoints(access_by_role, second)
    if first_set < second_set:
        return first, second
    if second_set < first_set:
        return second, first
    return None


def _observed_decisively(low_access: _RoleAccess, endpoint_id: str, config: VerticalRuleConfig) -> bool:
    """낮은 역할이 이 엔드포인트를 2xx(공유) 또는 거절 상태로 관찰했는가. 그 밖의 상태는 인가 판단 근거가 아니다."""
    statuses = low_access.statuses_by_endpoint.get(endpoint_id, set())
    return any(_is_success(status, config) or status in config.denied_status_codes for status in statuses)


def _choose_reference(
    endpoint_id: str,
    higher_roles: frozenset[str],
    higher_roles_by_role: dict[str, frozenset[str]],
    accounts_by_role: dict[str, list[str]],
    success_rows: dict[tuple[str, str], list[_ObservedRow]],
) -> str | None:
    """엔드포인트에 2xx 행이 있는 가장 가까운 높은 역할(사이에 다른 제공 역할이 없는 것, 여럿이면 role_id 순)의 첫 계정."""
    providing_roles = sorted(
        role_id for role_id in higher_roles
        if any((account_id, endpoint_id) in success_rows for account_id in accounts_by_role.get(role_id, []))
    )
    nearest_roles = [
        role_id for role_id in providing_roles
        if not any(role_id in higher_roles_by_role.get(other, frozenset()) for other in providing_roles if other != role_id)
    ]
    if not nearest_roles:
        return None
    reference_accounts = [a for a in accounts_by_role[nearest_roles[0]] if (a, endpoint_id) in success_rows]
    return reference_accounts[0] if reference_accounts else None


def _build_candidate(
    candidate_id: str,
    actor: str,
    actor_role: str,
    reference: str,
    endpoint_id: str,
    vulnerability_type: str,
    resource_ids: list[str],
    reference_rows: list[_ObservedRow],
    config: VerticalRuleConfig,
) -> dict[str, Any]:
    # 재현 요청은 기준 계정이 실제로 2xx 받은 요청만(같은 행에 섞인 비2xx 요청은 기준 응답이 될 수 없다).
    source_request_ids: list[str] = []
    evidence_refs: list[dict[str, Any]] = []
    for observed in reference_rows:
        for request_id, status in zip(observed.row["request_ids"], observed.statuses):
            if _is_success(status, config) and request_id not in source_request_ids:
                source_request_ids.append(request_id)
        merge_evidence(evidence_refs, observed.row["evidence_refs"])
    return {
        "candidate_id": candidate_id,
        "category": CATEGORY,
        "vulnerability_type": vulnerability_type,
        "rule_id": RULE_ID,
        "actor_account_id": actor,
        "actor_role_id": actor_role,
        "reference_account_id": reference,
        "resource_ids": resource_ids,
        "source_request_ids": source_request_ids,
        "workflow_id": None,
        "hypothesis": HYPOTHESIS_BY_TYPE[vulnerability_type],
        "expected_behavior": EXPECTED_BEHAVIOR_BY_TYPE[vulnerability_type],
        "expected_basis": EXPECTED_BASIS,
        "evidence_refs": evidence_refs,
    }


def _finalize(
    candidates: list[dict[str, Any]], errors: list[dict[str, Any]], max_candidates: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """결정성: 입력 순서와 무관하게 candidate_id로 정렬하고, 상한도 정렬 뒤 적용해 어떤 후보가 남는지 고정한다."""
    candidates.sort(key=lambda candidate: candidate["candidate_id"])
    if len(candidates) > max_candidates:
        dropped = len(candidates) - max_candidates
        candidates = candidates[:max_candidates]
        errors = [
            *errors,
            make_error_item(ErrorCode.CANDIDATES_TRUNCATED, f"수직 후보가 상한({max_candidates})을 넘어 {dropped}건 생략", is_retryable=False),
        ]
    return candidates, errors


def _is_success(status: int | None, config: VerticalRuleConfig) -> bool:
    low, high = config.success_status_range
    return status is not None and low <= status <= high
