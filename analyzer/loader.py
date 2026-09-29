"""
입력 검증 단계 (명세 3 '입력 검증').

crawl_result.json 원문(raw JSON) → 검증된 CrawlResult.

2단계 검증
  [A] 구조 검증 : CrawlResult.model_validate_json (Pydantic, extra="forbid")
                  필드 누락·타입 오류·미지의 필드 → 즉시 실패(치명적).
  [B] 내용 검증 : 명세 3이 요구하는 항목을 재확인.
                  run_id 형식 / per-file ID 고유성 / source 참조 무결성 /
                  outcome 허용값 / 역할 고유성 등.

심각도 2단계
  · 치명적(critical): 명세가 "검증 실패 → 그 입력을 분석에 쓰지 않는다"로
                     못박은 항목. CrawlValidationError 발생 → 추론 진입 금지.
  · 사소(minor)    : 명세가 강제하지 않는 항목(예: title 없음). warnings로
                     모아서 반환하고 분석은 계속한다.

명세 근거
  · 라인 72  : Analyzer는 model_validate_json으로 파싱하고, 검증 실패 입력은
               분석에 사용하지 않는다(fail-closed).
  · 라인 309 : run ID 형식, per-file ID 고유성, source 참조 무결성 등 확인.
"""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import ValidationError

from analyzer.schemas import CrawlResult

# ─────────────────────────── 규약(정규식·허용값) ───────────────────────────
# run_id: UTC 형식 YYYYMMDD-HHMMSS-xxxx (명세 라인 101)
_RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-z]{4}$")
# page:N / request:N, N은 1부터 (명세 라인 126, 185)
_PAGE_ID = re.compile(r"^page:[1-9]\d*$")
_REQ_ID = re.compile(r"^request:[1-9]\d*$")
# role:{이름} (명세 라인 112) — evidence 전용이라 형식 위반은 '사소'
_ROLE_ID = re.compile(r"^role:.+$")

# outcome 허용값 (명세 라인 164~179)
_OUTCOMES = frozenset({
    "executed",
    "enqueued",
    "already_visited",
    "beyond_max_depth",
    "not_executed_state_changing",
    "blocked_state_changing_request",
    "outside_origin",
    "not_visible",
    "not_found",
    "failed",
})


class CrawlValidationError(ValueError):
    """치명적 입력 검증 실패. .errors 에 개별 사유가 담긴다."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(
            "crawl_result 입력 검증 실패(치명적):\n- " + "\n- ".join(errors)
        )


def load_crawl_result(raw_json: str | bytes) -> tuple[CrawlResult, list[str]]:
    """원문 JSON을 검증해 (CrawlResult, 경고목록) 을 돌려준다.

    치명적 오류가 하나라도 있으면 CrawlValidationError 를 던진다(추론 진입 금지).
    사소한 문제는 던지지 않고 warnings 리스트로 돌려준다.
    """
    # [A] 구조 검증 (Pydantic) — 실패는 곧 치명적
    try:
        crawl = CrawlResult.model_validate_json(raw_json)
    except ValidationError as exc:
        raise CrawlValidationError(_fmt_pydantic(exc)) from exc

    # [B] 내용 검증
    critical, minor = _check_content(crawl)
    if critical:
        raise CrawlValidationError(critical)
    return crawl, minor


# ─────────────────────────── 내부 헬퍼 ───────────────────────────
def _fmt_pydantic(exc: ValidationError) -> list[str]:
    """Pydantic 오류를 '[A] 구조: 위치 → 메시지' 문장으로 변환."""
    out: list[str] = []
    for e in exc.errors():
        loc = ".".join(str(p) for p in e["loc"])
        out.append(f"[A] 구조: {loc} → {e['msg']}")
    return out or ["[A] 구조: CrawlResult 파싱 실패"]


def _check_content(crawl: CrawlResult) -> tuple[list[str], list[str]]:
    """[B] 내용 검증. (치명적, 사소) 두 리스트로 나눠 반환."""
    critical: list[str] = []
    minor: list[str] = []

    # run_id 형식 (치명적: crawl_run_id 로 실행 범위를 나누는 키라 형식이 틀리면
    #             Neo4j 병합 키 (crawl_run_id, id) 가 오염됨)
    if not _RUN_ID.match(crawl.run_id):
        critical.append(
            f"[B] run_id 형식 위반: {crawl.run_id!r} "
            "(기대: YYYYMMDD-HHMMSS-xxxx)"
        )

    # 파일 전체 시각 (사소)
    _check_time(crawl.started_at, "started_at", minor)
    if crawl.finished_at is not None:
        _check_time(crawl.finished_at, "finished_at", minor)

    # 역할 고유성 (치명적: 역할은 접근제어 축. 중복되면 접근 비교가 어그러짐)
    seen_roles: set[str] = set()
    for r in crawl.roles:
        if r.role in seen_roles:
            critical.append(f"[B] 역할 중복: role={r.role!r}")
        seen_roles.add(r.role)
        if not _ROLE_ID.match(r.id):
            minor.append(f"[B] RoleResult.id 형식: {r.id!r} (기대: role:이름)")

    # per-file ID 고유성 — page:N / request:N 은 파일 전체에서 고유 (명세 라인 222)
    page_ids: set[str] = set()
    dup_pages: set[str] = set()
    for r in crawl.roles:
        for p in r.pages:
            if p.id in page_ids:
                dup_pages.add(p.id)
            page_ids.add(p.id)

    req_ids: set[str] = set()
    dup_reqs: set[str] = set()
    for r in crawl.roles:
        for q in r.requests:
            if q.id in req_ids:
                dup_reqs.add(q.id)
            req_ids.add(q.id)

    for pid in sorted(dup_pages):
        critical.append(f"[B] page ID 중복: {pid!r} (파일 내 고유해야 함)")
    for qid in sorted(dup_reqs):
        critical.append(f"[B] request ID 중복: {qid!r} (파일 내 고유해야 함)")

    # ID 형식 + source 참조 무결성 + outcome
    for r in crawl.roles:
        for p in r.pages:
            if not _PAGE_ID.match(p.id):
                critical.append(f"[B] page ID 형식 위반: {p.id!r} (기대: page:N)")
            if p.role != r.role:
                minor.append(
                    f"[B] page {p.id} 의 role={p.role!r} 가 "
                    f"소속 역할 {r.role!r} 와 다름"
                )
            if p.title is None:
                minor.append(f"[B] page {p.id} title 없음")
            # source 참조 무결성 (치명적: 끊긴 부모 링크 → 흐름 그래프가 소리 없이 깨짐)
            if p.source_page_id is not None and p.source_page_id not in page_ids:
                critical.append(
                    f"[B] page {p.id} 의 source_page_id={p.source_page_id!r} "
                    "가 존재하지 않는 page 를 가리킴"
                )
            _check_outcomes(p, critical)

        for q in r.requests:
            if not _REQ_ID.match(q.id):
                critical.append(
                    f"[B] request ID 형식 위반: {q.id!r} (기대: request:N)"
                )
            if q.role != r.role:
                minor.append(
                    f"[B] request {q.id} 의 role={q.role!r} 가 "
                    f"소속 역할 {r.role!r} 와 다름"
                )
            _check_time(q.captured_at, f"request {q.id} captured_at", minor)
            if q.source_page_id is not None and q.source_page_id not in page_ids:
                critical.append(
                    f"[B] request {q.id} 의 source_page_id={q.source_page_id!r} "
                    "가 존재하지 않는 page 를 가리킴"
                )

    return critical, minor


def _check_outcomes(page, critical: list[str]) -> None:
    """links[]·actions[] 의 outcome 이 허용값인지 (치명적)."""
    for lk in page.links:
        if lk.outcome not in _OUTCOMES:
            critical.append(
                f"[B] page {page.id} link {lk.action_id} outcome 미허용: "
                f"{lk.outcome!r}"
            )
    for a in page.actions:
        if a.outcome not in _OUTCOMES:
            critical.append(
                f"[B] page {page.id} action {a.action_id} outcome 미허용: "
                f"{a.outcome!r}"
            )


def _check_time(value: str, where: str, minor: list[str]) -> None:
    """ISO-8601(UTC) 파싱 가능 여부 (사소)."""
    try:
        datetime.fromisoformat(value)
    except (ValueError, TypeError):
        minor.append(f"[B] {where} 시각 형식 파싱 불가: {value!r}")
