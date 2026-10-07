"""후보와 크롤 결과 대조: ID가 존재하는 것만이 아니라 계정·역할·세션 관계까지 맞아야 통과한다.

LLM을 쓰기 전에 코드로 한다. 대조에 실패한 후보는 시나리오를 만들지 않고 오류로 추적한다
(명세 m5: ID가 존재하더라도 계정·역할 관계가 다르면 거절한다).
"""

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from modules.scenario_generator.input_adapter import InputError, InputErrorCode


class MatchErrorCode(StrEnum):
    CANDIDATE_ID_DUPLICATE = "CANDIDATE_ID_DUPLICATE"
    CANDIDATE_ACCOUNT_INVALID = "CANDIDATE_ACCOUNT_INVALID"
    CANDIDATE_REQUEST_INVALID = "CANDIDATE_REQUEST_INVALID"


@dataclass(frozen=True)
class CrawlIndex:
    roles_by_id: dict[str, dict[str, Any]]
    accounts_by_id: dict[str, dict[str, Any]]
    requests_by_id: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class MatchedCandidate:
    candidate: dict[str, Any]
    actor_account: dict[str, Any]
    reference_account: dict[str, Any] | None
    source_requests: list[dict[str, Any]]


@dataclass(frozen=True)
class RejectedCandidate:
    candidate_id: str
    code: MatchErrorCode
    message: str

    def to_error_item(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "item_ref": self.candidate_id, "retryable": False}


@dataclass(frozen=True)
class MatchResult:
    matched: list[MatchedCandidate]
    rejected: list[RejectedCandidate]


def _index_by_unique_id(records: list[dict[str, Any]], id_field: str, label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        record_id = record[id_field]
        if record_id in indexed:
            # 같은 ID가 둘이면 어느 쪽이 맞는지 알 수 없어서 추측하지 않고 입력 오류로 돌려보낸다.
            raise InputError(InputErrorCode.DUPLICATE_ID, f"crawl_result의 {label} ID가 중복이다: {record_id}")
        indexed[record_id] = record
    return indexed


def build_crawl_index(crawl_data: dict[str, Any]) -> CrawlIndex:
    return CrawlIndex(
        roles_by_id=_index_by_unique_id(crawl_data["roles"], "role_id", "role"),
        accounts_by_id=_index_by_unique_id(crawl_data["accounts"], "account_id", "account"),
        requests_by_id=_index_by_unique_id(crawl_data["requests"], "request_id", "request"),
    )


def _find_account_problem(account_id: str, role_id: str | None, index: CrawlIndex, label: str) -> str | None:
    account = index.accounts_by_id.get(account_id)
    if account is None:
        return f"{label} 계정 {account_id}이(가) crawl_result에 없다"
    if account["role_id"] not in index.roles_by_id:
        return f"{label} 계정 {account_id}의 역할 {account['role_id']}이(가) roles에 없다"
    if role_id is not None and account["role_id"] != role_id:
        return (
            f"{label} 계정 {account_id}의 역할이 후보({role_id})와 "
            f"crawl_result({account['role_id']})에서 다르다"
        )
    return None


def _find_request_problem(request_id: str, index: CrawlIndex) -> str | None:
    request = index.requests_by_id.get(request_id)
    if request is None:
        return f"요청 {request_id}이(가) crawl_result에 없다"
    account = index.accounts_by_id.get(request["account_id"])
    if account is None:
        return f"요청 {request_id}의 계정 {request['account_id']}이(가) crawl_result에 없다"
    if account["role_id"] != request["role_id"]:
        return f"요청 {request_id}의 역할이 계정 {account['account_id']}의 역할과 다르다"
    if account["session_ref"] != request["session_ref"]:
        return f"요청 {request_id}의 session_ref가 계정 {account['account_id']}의 것과 다르다"
    return None


def _match_one(candidate: dict[str, Any], index: CrawlIndex) -> MatchedCandidate | RejectedCandidate:
    candidate_id = candidate["candidate_id"]

    problem = _find_account_problem(candidate["actor_account_id"], candidate["actor_role_id"], index, "실행")
    if problem:
        return RejectedCandidate(candidate_id, MatchErrorCode.CANDIDATE_ACCOUNT_INVALID, problem)

    reference_account_id = candidate["reference_account_id"]
    if reference_account_id is not None:
        problem = _find_account_problem(reference_account_id, None, index, "기준")
        if problem:
            return RejectedCandidate(candidate_id, MatchErrorCode.CANDIDATE_ACCOUNT_INVALID, problem)

    request_ids = candidate["source_request_ids"]
    if not request_ids:
        # steps의 모든 단계는 존재하는 수집 요청을 근거로 해야 하므로 근거 요청이 없으면 만들 수 없다.
        return RejectedCandidate(
            candidate_id, MatchErrorCode.CANDIDATE_REQUEST_INVALID, "source_request_ids가 비어 있다"
        )
    for request_id in request_ids:
        problem = _find_request_problem(request_id, index)
        if problem:
            return RejectedCandidate(candidate_id, MatchErrorCode.CANDIDATE_REQUEST_INVALID, problem)

    return MatchedCandidate(
        candidate=candidate,
        actor_account=index.accounts_by_id[candidate["actor_account_id"]],
        reference_account=index.accounts_by_id[reference_account_id] if reference_account_id is not None else None,
        source_requests=[index.requests_by_id[request_id] for request_id in request_ids],
    )


def match_candidates(candidates: list[dict[str, Any]], index: CrawlIndex) -> MatchResult:
    id_counts = Counter(candidate["candidate_id"] for candidate in candidates)
    duplicated_ids = {candidate_id for candidate_id, count in id_counts.items() if count > 1}
    already_reported: set[str] = set()

    matched: list[MatchedCandidate] = []
    rejected: list[RejectedCandidate] = []
    for candidate in candidates:
        candidate_id = candidate["candidate_id"]
        if candidate_id in duplicated_ids:
            # 같은 ID의 후보가 둘이면 이후 시나리오·결과·리포트의 연결 키가 모호해지므로 모두 제외한다.
            if candidate_id not in already_reported:
                already_reported.add(candidate_id)
                rejected.append(
                    RejectedCandidate(
                        candidate_id, MatchErrorCode.CANDIDATE_ID_DUPLICATE, f"candidate_id가 중복이다: {candidate_id}"
                    )
                )
            continue
        result = _match_one(candidate, index)
        if isinstance(result, MatchedCandidate):
            matched.append(result)
        else:
            rejected.append(result)
    return MatchResult(matched=matched, rejected=rejected)
