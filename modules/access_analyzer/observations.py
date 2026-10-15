"""crawl_result에서 규칙이 쓰는 관찰만 뽑은 색인.

KG 결과에는 응답 상태(2xx·거절)와 계정의 세션 유무가 없다(`access_observed`는 늘 true). 수직 규칙은 그 둘이
필요해서 analyze가 crawl_result를 두 번째 입력으로 받고, 여기서 request_id·account_id로 찾을 수 있게 색인한다.
값은 그대로 옮기고 해석하지 않는다(해석은 규칙 몫).
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ObservedAccount:
    account_id: str
    role_id: str
    # null이면 로그인 세션이 없는 계정(비인증). 세션 값 자체가 아니라 collector의 불투명 참조다.
    session_ref: str | None


@dataclass(frozen=True)
class CrawlObservations:
    accounts_by_id: dict[str, ObservedAccount]
    # request_id → 응답 상태 코드(응답을 못 받았으면 None). 여기 없는 request_id는 crawl_result에 없는 요청이다.
    status_by_request_id: dict[str, int | None]


def build_crawl_observations(crawl_data: dict[str, Any]) -> CrawlObservations:
    """Schema·의미 검증(중복 ID 없음)을 통과한 crawl_result의 data로 만든다."""
    accounts_by_id = {
        account["account_id"]: ObservedAccount(account["account_id"], account["role_id"], account["session_ref"])
        for account in crawl_data["accounts"]
    }
    status_by_request_id = {
        request["request_id"]: request["response"]["status_code"] for request in crawl_data["requests"]
    }
    return CrawlObservations(accounts_by_id=accounts_by_id, status_by_request_id=status_by_request_id)
