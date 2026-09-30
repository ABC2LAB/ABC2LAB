"""
스키마 검증 + 파싱
=================

크롤러 JSON을 CrawlRun으로 파싱한다. 두 가지를 동시에 한다:
  1) 구조 검증 - 필수 필드 존재/타입, id 참조 무결성. (크롤 산출물 품질 점검 겸함)
  2) 관대한 파싱 - 모르는 필드는 버리고, 없는 선택 필드는 기본값.

검증 실패는 예외를 던지지 않고 errors 리스트로 모아 반환한다. 치명적(파싱 불가)이
아니면 부분 파싱된 CrawlRun과 errors를 함께 돌려줘, 뒤 단계가 판단하게 한다.
"""

from __future__ import annotations

from typing import Any

from .models import (
    CrawlRun, RoleObs, PageObs, RequestObs, LinkObs,
)

SUPPORTED_SCHEMA = {"1.0"}


def _as_int(v: Any):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_crawl(data: dict) -> tuple[CrawlRun, list[str]]:
    errors: list[str] = []

    if not isinstance(data, dict):
        return CrawlRun("", "", ""), ["최상위가 object가 아님"]

    sv = str(data.get("schema_version", ""))
    if sv not in SUPPORTED_SCHEMA:
        errors.append(f"schema_version '{sv}' 미지원 (지원: {sorted(SUPPORTED_SCHEMA)}) - 관대 파싱 계속")

    for k in ("run_id", "target_base_url", "roles"):
        if k not in data:
            errors.append(f"최상위 필수 필드 누락: {k}")

    run = CrawlRun(
        schema_version=sv,
        run_id=str(data.get("run_id", "")),
        target_base_url=str(data.get("target_base_url", "")),
        started_at=data.get("started_at"),
        finished_at=data.get("finished_at"),
    )

    roles_raw = data.get("roles", [])
    if not isinstance(roles_raw, list):
        errors.append("roles가 list가 아님")
        roles_raw = []

    seen_page_ids: set[str] = set()

    for ri, robj in enumerate(roles_raw):
        if not isinstance(robj, dict):
            errors.append(f"roles[{ri}]가 object가 아님")
            continue
        role_name = str(robj.get("role", f"role_{ri}"))
        role = RoleObs(
            role=role_name,
            id=str(robj.get("id", f"role:{role_name}")),
            error=robj.get("error"),
        )

        for pi, pobj in enumerate(robj.get("pages", []) or []):
            if not isinstance(pobj, dict):
                errors.append(f"roles[{ri}].pages[{pi}] object 아님")
                continue
            links = []
            for lobj in pobj.get("links", []) or []:
                if not isinstance(lobj, dict):
                    continue
                links.append(LinkObs(
                    action_id=str(lobj.get("action_id", "")),
                    endpoint=str(lobj.get("endpoint", "")),
                    url=str(lobj.get("url", "")),
                    text=str(lobj.get("text", "")),
                    is_state_changing=bool(lobj.get("is_state_changing", False)),
                    outcome=str(lobj.get("outcome", "")),
                ))
            page = PageObs(
                role=role_name,
                id=str(pobj.get("id", f"page:{ri}:{pi}")),
                url=str(pobj.get("url", "")),
                endpoint=str(pobj.get("endpoint", "")),
                title=str(pobj.get("title", "")),
                status=_as_int(pobj.get("status")),
                depth=_as_int(pobj.get("depth")),
                source_page_id=pobj.get("source_page_id"),
                source_action=pobj.get("source_action"),
                links=links,
                actions=pobj.get("actions", []) or [],
            )
            if not page.endpoint:
                errors.append(f"{page.id}: endpoint 비어있음")
            seen_page_ids.add(page.id)
            role.pages.append(page)

        for qi, qobj in enumerate(robj.get("requests", []) or []):
            if not isinstance(qobj, dict):
                errors.append(f"roles[{ri}].requests[{qi}] object 아님")
                continue
            req = RequestObs(
                role=role_name,
                id=str(qobj.get("id", f"request:{ri}:{qi}")),
                method=str(qobj.get("method", "GET")).upper(),
                endpoint=str(qobj.get("endpoint", "")),
                url=str(qobj.get("url", "")),
                status=_as_int(qobj.get("status")),
                resource_type=str(qobj.get("resource_type", "")),
                query_params=qobj.get("query_params", {}) or {},
                body_params=qobj.get("body_params", {}) or {},
                resource_ids=[str(x) for x in (qobj.get("resource_ids", []) or [])],
                response_shape=qobj.get("response_shape"),
                source_page_id=qobj.get("source_page_id"),
                source_action=qobj.get("source_action"),
            )
            if not req.endpoint:
                errors.append(f"{req.id}: endpoint 비어있음")
            role.requests.append(req)

        run.roles.append(role)

    # 참조 무결성: request.source_page_id가 실제 페이지를 가리키는가
    for req in run.all_requests():
        spid = req.source_page_id
        if spid and spid not in seen_page_ids:
            errors.append(f"{req.id}: source_page_id '{spid}' 가 어떤 페이지에도 매칭 안 됨")

    return run, errors
