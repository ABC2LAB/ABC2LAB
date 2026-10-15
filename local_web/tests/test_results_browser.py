"""Chromium에서 공개 fixture·실제 Web 서버·CLI 대역을 연결해 결과 화면을 확인한다."""

from __future__ import annotations

import copy
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from local_web.inputs import InputSettings
from local_web.runner import RunnerSettings
from local_web.server import LocalWebServer, WebSettings

if TYPE_CHECKING:
    from playwright.sync_api import Page


@pytest.mark.skipif(os.environ.get("LOCAL_WEB_BROWSER_TESTS") != "1", reason="Chromium 환경에서 명시적으로 실행")
@pytest.mark.parametrize("case", ["completed", "partial", "failed"])
def test_artifact_results_browser(
    input_settings: InputSettings, runner_settings: RunnerSettings, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    from playwright.sync_api import Request, expect, sync_playwright

    release = tmp_path / "results-release"
    monkeypatch.setenv("LOCAL_WEB_TEST_RUNNER_RELEASE", str(release))
    monkeypatch.setenv("LOCAL_WEB_TEST_RUNNER_CASE", case)
    monkeypatch.setenv("LOCAL_WEB_TEST_PUBLIC_ARTIFACTS", "1")
    server = LocalWebServer(WebSettings("127.0.0.1", 0, 8080), input_settings, runner_settings)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            errors, responses = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            def capture_response(request: Request) -> None:
                response = request.response()
                if response is not None and "/api/" in response.url:
                    responses.append(response.text())

            page.on("requestfinished", capture_response)
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            expect(page.locator("#target-url")).to_be_enabled()
            page.locator("#target-url").fill("http://fixture-web:9345/start")
            page.locator("#dataset").select_option("case_v2")
            for index in range(2):
                row = page.locator(".account-row").nth(index)
                row.locator('[data-field="login_id"]').fill(f"LOGIN_SENTINEL_{index}")
                row.locator('[data-field="password"]').fill(f"PASSWORD_SENTINEL_{index}")
            page.locator("#prepare-input").click()
            expect(page.locator("#start-diagnosis")).to_be_enabled()
            page.locator("#start-diagnosis").click()
            expect(page.locator("#run-status")).to_contain_text("Runner 실행 중")
            page.locator('nav a[href="#results"]').click()
            expect(page.locator("#results")).to_be_visible()
            if case == "failed":
                expect(page.locator("#crawl-state")).to_contain_text("failed")
                expect(page.locator("#report-state")).to_contain_text("미발행")
                assert page.locator("#report-view table").count() == 0
                assert page.locator("#graph-view svg").count() == 0
            else:
                expect(page.locator("#graph-view svg circle")).to_have_count(8)
                expect(page.locator("#graph-view")).to_contain_text("query revision 2")
                expect(page.locator("#graph-view")).to_contain_text("instance")
                expect(page.locator("#semantic-state")).to_contain_text("partial" if case == "partial" else "completed")
                expect(page.locator("#process-view")).to_contain_text("not_executed")
                candidate = page.get_by_role("table", name="검증 후보(확정 취약점 아님)").locator("tbody tr").filter(has_text="candidate-alpha")
                expect(candidate).to_contain_text("scenario-alpha")
                expect(candidate).not_to_contain_text("scenario-omega")
                expect(page.locator("#report-view tbody tr")).to_have_count(1)
                expect(page.locator("#report-view")).to_contain_text("finding-alpha")
                expect(page.locator("#report-view")).not_to_contain_text("finding-delta")
                expect(page.locator("#report-view")).not_to_contain_text("finding-omega")
                expect(page.locator("#report-view")).to_contain_text("<img src=x")
                assert page.locator("#report-view img").count() == 0
                assert page.evaluate("typeof window.artifactXss") == "undefined"
                expect(page.locator("#report-view")).to_contain_text("SHA-256")
                page.locator('nav a[href="#approval"]').click()
                expect(page.locator("#approval")).to_be_visible()
                expect(page.locator("#approval-view tbody tr")).to_have_count(1)
                expect(page.locator("#approval-view")).to_contain_text("scenario-delta")
                expect(page.locator("#approval-view")).to_contain_text("candidate-delta")
                expect(page.locator("#approval-view")).not_to_contain_text("scenario-alpha")
                assert page.locator("#approval button").count() == 0
            page.locator('nav a[href="#evaluation"]').click()
            expect(page.locator("#evaluation")).to_be_visible()
            if case == "failed":
                expect(page.locator("#evaluation-state")).to_contain_text("미발행")
                assert page.locator("#evaluation-view table").count() == 0
            else:
                expect(page.locator("#evaluation-state")).to_contain_text("partial" if case == "partial" else "completed")
                expect(page.locator("#evaluation-view")).to_contain_text("fixture-v3")
                metric = page.get_by_role("table", name="Reporter 성능 지표").locator("tbody tr")
                expect(metric.filter(has_text="page_recall")).to_contain_text("0.75 (75.00%)")
                expect(metric.filter(has_text="endpoint_recall")).to_contain_text("미측정 / 계산 불가")
                expect(page.get_by_role("table", name="후보·검증 확정 TP/FP/FN").locator("tbody tr")).to_have_count(2)
                expect(page.locator("#evaluation-view")).to_contain_text("fixture-semantic")
                expect(page.locator("#evaluation-view")).to_contain_text("<img src=x")
                assert page.locator("#evaluation-view img").count() == 0
                assert page.evaluate("typeof window.evaluationXss") == "undefined"
            release.touch()
            expect(page.locator("#run-status")).to_contain_text("Runner " + case, timeout=7000)
            page.locator('nav a[href="#results"]').click()
            page.locator("#refresh-results").click()
            expect(page.locator("#results-status")).to_contain_text("· " + case)
            page.reload()
            expect(page.locator("#results-status")).to_contain_text("· " + case)
            if case != "failed":
                page.locator('nav a[href="#evaluation"]').click()
                expect(page.locator("#evaluation")).to_be_visible()
                expect(page.locator("#evaluation-state")).to_contain_text("partial" if case == "partial" else "completed")
                page.locator("#refresh-evaluation").click()
                expect(page.locator("#evaluation-view")).to_contain_text("page_recall")
                page.route("**/api/run-results", lambda route: route.abort())
                page.locator("#refresh-evaluation").click()
                expect(page.locator("#evaluation-state")).to_contain_text("결과 조회 실패")
                assert page.locator("#evaluation-view table").count() == 0
                page.unroute("**/api/run-results")
                page.locator("#refresh-evaluation").click()
                expect(page.locator("#evaluation-view")).to_contain_text("page_recall")
            for menu in ("results", "approval", "evaluation"):
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator(f'nav a[href="#{menu}"]').click()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert errors == []
            assert all("PASSWORD_SENTINEL" not in body and "LOGIN_SENTINEL" not in body and "PRIVATE_SESSION_SENTINEL" not in body for body in responses)
            assert page.evaluate("Object.keys(localStorage).length + Object.keys(sessionStorage).length") == 0
            other = browser.new_context()
            other_page = other.new_page()
            other_page.goto(f"http://127.0.0.1:{server.server_port}/#results")
            expect(other_page.locator("#results-status")).to_contain_text("소유 진단 없음")
            assert other_page.locator("#graph-view svg").count() == 0
            other_page.locator('nav a[href="#evaluation"]').click()
            expect(other_page.locator("#evaluation-state")).to_contain_text("진단 시작 전")
            assert other_page.locator("#evaluation-view table").count() == 0
            other.close()
            context.close()
            browser.close()
    finally:
        release.touch()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def build_graph_payload() -> dict[str, object]:
    """앞 100개가 Action이고 관계가 240개를 넘는 공개 화면 모델 대역."""
    nodes = []
    for node_type, count in {"Action": 145, "Endpoint": 5, "Page": 5, "Parameter": 5,
                             "Resource": 6, "Role": 3, "User": 4}.items():
        for index in range(count):
            nodes.append({"node_id": f"{node_type.lower()}:opaque-{index:03d}", "node_type": node_type,
                          "basis": ("observed", "inferred", "verified")[index % 3],
                          "resource_scope": ("type" if index % 2 else "instance") if node_type == "Resource" else None})
    edges = []
    for index, source in enumerate(nodes):
        for offset in (1, 2):
            target = nodes[(index + offset) % len(nodes)]
            edges.append({"relationship_id": f"relationship-{index}-{offset}", "source_id": source["node_id"],
                          "target_id": target["node_id"], "relation_type": "REFERENCE", "basis": source["basis"]})
    # 자기 루프도 다른 관계와 동일하게 원본 식별자를 유지한다.
    edges.append({"relationship_id": "self-loop", "source_id": nodes[0]["node_id"],
                  "target_id": nodes[0]["node_id"], "relation_type": "PRECEDES", "basis": "observed"})
    nodes.append({"node_id": "action:isolated-<img src=x onerror=window.graph_xss=1>",
                  "node_type": "Action", "basis": "observed", "resource_scope": None})
    nodes.sort(key=lambda node: node["node_id"])
    return {"run_id": "web-graph-fixture", "state": "completed", "artifacts": [{
        "artifact_type": "graph_query_result", "state": "completed", "error_count": 0,
        "view": {"graph_id": "graph-explorer-fixture", "graph_revision": 7, "snapshot_state": "completed",
                 "nodes": nodes, "relationships": edges, "workflows": []},
    }]}


@pytest.fixture
def graph_explorer_page(
    input_settings: InputSettings, runner_settings: RunnerSettings,
) -> Iterator[tuple[Page, dict[str, object]]]:
    """실제 정적 화면을 띄우되 공개 API 응답만 대역으로 준다. 진단은 시작하지 않는다."""
    from playwright.sync_api import sync_playwright

    server = LocalWebServer(WebSettings("127.0.0.1", 0, 8080), input_settings, runner_settings)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    payloads = {"value": build_graph_payload()}
    errors = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/run-results", lambda route: route.fulfill(json=payloads["value"]))
            page.route("**/api/run-status", lambda route: route.fulfill(json={
                "run_id": payloads["value"]["run_id"], "state": "completed", "is_busy": False,
                "elapsed_seconds": 1, "exit_code": 0, "artifacts": [], "summary": None,
            }))
            page.goto(f"http://127.0.0.1:{server.server_port}/#results")
            yield page, payloads
            assert errors == []
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.skipif(os.environ.get("LOCAL_WEB_BROWSER_TESTS") != "1", reason="Chromium 환경에서 명시적으로 실행")
@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("state", ["completed", "partial"])
def test_verification_success_is_not_a_confirmed_finding_in_browser(
    graph_explorer_page: tuple[Page, dict[str, object]], width: int, state: str,
) -> None:
    from playwright.sync_api import expect

    page, payloads = graph_explorer_page
    payloads["value"] = build_diagnosis_payload(state)
    page.set_viewport_size({"width": width, "height": 900})
    page.locator("#refresh-results").click()
    overview = page.locator("#diagnosis-overview")
    for label, count in {"후보": 1, "검증 조건 통과": 1, "검증 미재현": 0,
                         "검증 판단불가": 0, "검증 미실행·차단": 0, "Reporter 확정": 0}.items():
        expect(overview.locator("dl > div").filter(has=page.get_by_text(label, exact=True)).locator("dd")).to_have_text(str(count))
    expect(overview).to_contain_text("서로 합산하거나 고유 취약점 수로 해석하지 않는다")
    expect(overview).to_contain_text("확정 취약점이 아니다")
    if state == "partial":
        expect(overview).to_contain_text("부분 결과")
    report = page.get_by_role("table", name="확정 외 리포트 상태(확인된 취약점 목록에서 제외)")
    expect(report).to_contain_text("suspected")
    expect(report).to_contain_text("success")
    expect(report).to_contain_text("기대 동작이 추론에 기반함")
    expect(report).to_contain_text("<img src=x")
    expect(report.locator("img")).to_have_count(0)
    assert page.evaluate("typeof window.reason_xss") == "undefined"
    expect(page.locator("#report-view tbody tr")).to_have_count(0)
    expect(page.locator("#report-view")).not_to_contain_text("finding-ui")


def build_diagnosis_payload(state: str = "completed") -> dict[str, object]:
    """이미 검증된 공개 화면 모델 대역. Web이 판정이나 GT를 생성하지 않는다."""
    candidate = {"candidate_id": "candidate-ui", "vulnerability_type": "horizontal_access",
                 "actor_account_id": "account:actor", "actor_role_id": "role:member", "resource_ids": [],
                 "hypothesis": "Public hypothesis", "expected_behavior": "Owner only", "expected_basis": "inferred"}
    verification = {"verification_id": "verification-ui", "candidate_id": "candidate-ui", "scenario_id": "scenario-ui",
                    "decision_id": "decision-ui", "policy_decision": "allow", "execution_status": "completed",
                    "result": "success", "reason": "Verification public reason", "steps": [], "error_count": 0}
    report = {"summary": {"candidate_count": 1, "confirmed_count": 0, "not_confirmed_count": 0,
                          "suspected_count": 1, "indeterminate_count": 0, "policy_blocked_count": 0, "approval_pending_count": 0},
              "confirmed_findings": [], "other_findings": [{"finding_id": "finding-ui", "candidate_id": "candidate-ui",
                  "scenario_id": "scenario-ui", "verification_id": "verification-ui", "status": "suspected",
                  "description": "위반 정황은 재현됐으나 기대 동작이 추론에 기반함 <img src=x onerror=window.reason_xss=1>"}],
              "limitations": []}
    return {"run_id": "web-graph-fixture", "state": state, "artifacts": [
        {"artifact_type": artifact_type, "state": state, "error_count": int(state == "partial"), "view": view}
        for artifact_type, view in (("vulnerability_candidates", {"candidates": [candidate]}),
                                    ("verification_results", {"results": [verification]}), ("diagnosis_report", report))
    ]}


@pytest.mark.skipif(os.environ.get("LOCAL_WEB_BROWSER_TESTS") != "1", reason="Chromium 환경에서 명시적으로 실행")
@pytest.mark.parametrize("artifact_type,label", [("verification_results", "검증 조건 통과"), ("diagnosis_report", "Reporter 확정")])
@pytest.mark.parametrize("state", ["missing", "failed", "invalid"])
def test_unavailable_diagnosis_counts_are_not_zero_in_browser(
    graph_explorer_page: tuple[Page, dict[str, object]], artifact_type: str, label: str, state: str,
) -> None:
    from playwright.sync_api import expect

    page, payloads = graph_explorer_page
    payload = build_diagnosis_payload()
    unavailable = next(row for row in payload["artifacts"] if row["artifact_type"] == artifact_type)
    unavailable.update(state=state, view=None, error_count=1, issue="ARTIFACT_INVALID")
    payloads["value"] = payload
    page.locator("#refresh-results").click()
    overview = page.locator("#diagnosis-overview")
    expect(overview.locator("dl > div").filter(has=page.get_by_text(label, exact=True)).locator("dd")).to_have_text("미확인")
    available_label = "Reporter 확정" if artifact_type == "verification_results" else "검증 조건 통과"
    available_count = "0" if artifact_type == "verification_results" else "1"
    expect(overview.locator("dl > div").filter(has=page.get_by_text(available_label, exact=True)).locator("dd")).to_have_text(available_count)
    payloads["value"] = build_diagnosis_payload()
    page.locator("#refresh-results").click()
    expect(overview.locator("dl > div").filter(has=page.get_by_text(label, exact=True)).locator("dd")).to_have_text("1" if artifact_type == "verification_results" else "0")
    page.route("**/api/run-results", lambda route: route.abort())
    page.locator("#refresh-results").click()
    expect(page.locator("#results-status")).to_contain_text("결과 조회 실패")
    expect(overview.locator("dd")).to_have_text(["미확인"] * 6)


@pytest.mark.skipif(os.environ.get("LOCAL_WEB_BROWSER_TESTS") != "1", reason="Chromium 환경에서 명시적으로 실행")
@pytest.mark.parametrize("result,label", [("failure", "검증 미재현"), ("indeterminate", "검증 판단불가"), ("blocked", "검증 미실행·차단")])
def test_verification_outcome_counters_in_browser(
    graph_explorer_page: tuple[Page, dict[str, object]], result: str, label: str,
) -> None:
    from playwright.sync_api import expect

    page, payloads = graph_explorer_page
    payload = build_diagnosis_payload()
    verification = next(row for row in payload["artifacts"] if row["artifact_type"] == "verification_results")
    verification["view"]["results"][0].update(result=result,
        execution_status="not_executed" if result == "blocked" else "completed",
        policy_decision="require_approval" if result == "blocked" else "allow")
    report = next(row for row in payload["artifacts"] if row["artifact_type"] == "diagnosis_report")["view"]
    status = {"failure": "not_confirmed", "indeterminate": "indeterminate", "blocked": "approval_pending"}[result]
    report["summary"].update(suspected_count=0)
    report["summary"][status + "_count"] = 1
    report["other_findings"][0].update(status=status, description="Reporter outcome description")
    payloads["value"] = payload
    page.locator("#refresh-results").click()
    overview = page.locator("#diagnosis-overview")
    expect(overview.locator("dl > div").filter(has=page.get_by_text(label, exact=True)).locator("dd")).to_have_text("1")
    expect(overview.locator("dl > div").filter(has=page.get_by_text("검증 조건 통과", exact=True)).locator("dd")).to_have_text("0")
    expect(overview.locator("dl > div").filter(has=page.get_by_text("Reporter 확정", exact=True)).locator("dd")).to_have_text("0")


@pytest.mark.skipif(os.environ.get("LOCAL_WEB_BROWSER_TESTS") != "1", reason="Chromium 환경에서 명시적으로 실행")
@pytest.mark.parametrize("width", [1280, 390])
def test_large_graph_explorer_browser(graph_explorer_page: tuple[Page, dict[str, object]], width: int) -> None:
    from playwright.sync_api import expect

    page, payloads = graph_explorer_page
    view = payloads["value"]["artifacts"][0]["view"]
    nodes, edges = view["nodes"], view["relationships"]
    page.set_viewport_size({"width": width, "height": 900})
    expect(page.locator("#graph-view svg .graph-node")).to_have_count(len(nodes))
    expect(page.locator("#graph-view svg .graph-edge")).to_have_count(len(edges))
    expect(page.locator("#graph-view svg .graph-heading")).to_have_count(len({node["node_type"] for node in nodes}))
    expect(page.locator("#kg-display-counts")).to_contain_text(f"노드 {len(nodes)}/{len(nodes)}개")
    expect(page.locator("#kg-display-counts")).to_contain_text("연결 없는 노드 1개")
    node_table = page.get_by_role("table", name="KG 노드", exact=True)
    edge_table = page.get_by_role("table", name="KG 관계", exact=True)
    expect(node_table.locator("tbody tr")).to_have_count(50)
    expect(edge_table.locator("tbody tr")).to_have_count(50)
    node_pager = page.get_by_role("group", name="KG 노드 페이지 이동", exact=True)
    expect(node_pager.get_by_role("button", name="이전", exact=True)).to_be_disabled()
    node_pager.get_by_role("button", name="다음", exact=True).click()
    expect(node_pager).to_contain_text("2 / 4 페이지")
    node_pager.get_by_role("button", name="마지막", exact=True).click()
    expect(node_table).to_contain_text(next(node["node_id"] for node in reversed(nodes) if node["node_type"] == "User"))
    expect(node_table.locator("tbody tr")).to_have_count(len(nodes) % 50)
    expect(node_pager.get_by_role("button", name="다음", exact=True)).to_be_disabled()
    edge_pager = page.get_by_role("group", name="KG 관계 페이지 이동", exact=True)
    edge_pager.get_by_role("button", name="마지막", exact=True).click()
    expect(edge_table).to_contain_text("self-loop")

    page.get_by_label("노드 종류", exact=True).select_option("Resource")
    resources = [node for node in nodes if node["node_type"] == "Resource"]
    expect(page.locator("#graph-view svg .graph-node")).to_have_count(len(resources))
    page.get_by_label("Resource 범위", exact=True).select_option("instance")
    instances = [node for node in resources if node["resource_scope"] == "instance"]
    expect(page.locator("#graph-view svg .graph-node")).to_have_count(len(instances))
    page.get_by_label("노드 basis", exact=True).select_option("observed")
    expect(page.locator("#graph-view svg .graph-node")).to_have_count(sum(node["basis"] == "observed" for node in instances))
    page.get_by_role("button", name="전체 보기", exact=True).click()

    selected = next(node for node in reversed(nodes) if node["node_type"] == "User")
    search = page.get_by_label("node_id 검색", exact=True)
    search.fill(selected["node_id"].upper())
    expect(page.locator("#graph-view svg .graph-node")).to_have_count(1)
    page.get_by_role("button", name=selected["node_type"] + " " + selected["node_id"], exact=True).press("Enter")
    expect(page.locator("#kg-detail")).to_contain_text(selected["node_id"])
    related = [edge for edge in edges if selected["node_id"] in (edge["source_id"], edge["target_id"])]
    neighbors = {selected["node_id"]} | {edge["source_id"] for edge in related} | {edge["target_id"] for edge in related}
    page.get_by_role("button", name="주변 보기", exact=True).click()
    expect(search).to_have_value("")
    expect(page.locator("#graph-view svg .graph-node")).to_have_count(len(neighbors))
    expect(page.locator("#kg-display-counts")).to_contain_text("1-hop")
    expect(page.get_by_role("table", name="선택 노드의 원본 관계", exact=True).locator("tbody tr")).to_have_count(len(related))
    next_id = next(value for value in neighbors if value != selected["node_id"])
    page.get_by_label("연결 노드로 이동", exact=True).select_option(next_id)
    expect(page.locator("#kg-detail")).to_contain_text(next_id)

    page.get_by_role("button", name="전체 보기", exact=True).click()
    isolated = next(node for node in nodes if "isolated" in node["node_id"])
    search.fill(isolated["node_id"])
    page.get_by_role("button", name="노드 " + isolated["node_id"] + " 선택", exact=True).click()
    expect(page.locator("#kg-detail")).to_contain_text("연결 관계가 없는 노드")
    expect(page.get_by_label("연결 노드로 이동", exact=True)).to_be_disabled()
    assert page.locator("#graph-view img").count() == 0
    assert page.evaluate("typeof window.graph_xss") == "undefined"
    page.get_by_role("button", name="전체 보기", exact=True).click()
    page.get_by_label("연결된 노드만 보기", exact=True).check()
    expect(page.locator("#graph-view svg .graph-node")).to_have_count(len(nodes) - 1)
    page.get_by_role("button", name="전체 보기", exact=True).click()
    search.fill("NO_MATCHING_NODE")
    expect(page.locator("#graph-view svg")).to_have_count(0)
    expect(page.locator("#kg-display-counts")).to_contain_text(f"노드 0/{len(nodes)}개")
    expect(page.get_by_role("button", name="확대", exact=True)).to_be_disabled()
    page.get_by_role("button", name="전체 보기", exact=True).click()
    expect(page.locator("#graph-view svg .graph-node")).to_have_count(len(nodes))
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.evaluate("Object.keys(localStorage).length + Object.keys(sessionStorage).length") == 0


@pytest.mark.skipif(os.environ.get("LOCAL_WEB_BROWSER_TESTS") != "1", reason="Chromium 환경에서 명시적으로 실행")
def test_graph_camera_and_refresh_browser(graph_explorer_page: tuple[Page, dict[str, object]]) -> None:
    from playwright.sync_api import expect

    page, payloads = graph_explorer_page
    nodes = payloads["value"]["artifacts"][0]["view"]["nodes"]
    svg = page.locator("#graph-view svg")
    expect(svg.locator(".graph-node")).to_have_count(len(nodes))
    initial = svg.get_attribute("viewBox")
    page.get_by_role("button", name="확대", exact=True).click()
    zoomed = svg.get_attribute("viewBox")
    assert float(zoomed.split()[2]) < float(initial.split()[2])
    svg.focus()
    svg.press("ArrowRight")
    moved = svg.get_attribute("viewBox")
    assert float(moved.split()[0]) > float(zoomed.split()[0])
    bounds = svg.bounding_box()
    page.mouse.move(bounds["x"] + 15, bounds["y"] + 15)
    page.mouse.down()
    page.mouse.move(bounds["x"] + 65, bounds["y"] + 35, steps=5)
    page.mouse.up()
    assert svg.get_attribute("viewBox") != moved
    page.get_by_role("button", name="화면 맞춤", exact=True).click()
    expect(svg).to_have_attribute("viewBox", initial)
    page.get_by_role("button", name="축소", exact=True).click()
    assert float(svg.get_attribute("viewBox").split()[2]) > float(initial.split()[2])
    previous_width = float(svg.get_attribute("viewBox").split()[2])
    svg.dispatch_event("wheel", {"deltaY": -100, "clientX": bounds["x"] + 50, "clientY": bounds["y"] + 50})
    assert float(svg.get_attribute("viewBox").split()[2]) < previous_width

    pager = page.get_by_role("group", name="KG 노드 페이지 이동", exact=True)
    pager.get_by_role("button", name="다음", exact=True).click()
    page.get_by_role("button", name="확대", exact=True).click()
    camera = svg.get_attribute("viewBox")
    page.evaluate("window.abc2labResults.refresh()")
    expect(pager).to_contain_text("2 / 4 페이지")
    expect(svg).to_have_attribute("viewBox", camera)
    search = page.get_by_label("node_id 검색", exact=True)
    search.fill("resource:")
    search.focus()
    page.evaluate("window.abc2labResults.refresh()")
    expect(search).to_have_value("resource:")
    expect(search).to_be_focused()
    selected_id = next(node["node_id"] for node in nodes if node["node_type"] == "Resource")
    page.get_by_role("button", name="노드 " + selected_id + " 선택", exact=True).click()
    expect(page.locator("#kg-detail")).to_be_focused()
    camera_before_center = svg.get_attribute("viewBox")
    page.get_by_role("button", name="화면 맞춤", exact=True).click()
    page.get_by_role("button", name="선택 노드로 이동", exact=True).click()
    assert svg.get_attribute("viewBox") == camera_before_center
    page.get_by_role("button", name="주변 보기", exact=True).click()
    count = svg.locator(".graph-node").count()
    camera = svg.get_attribute("viewBox")
    page.evaluate("window.abc2labResults.refresh()")
    expect(svg.locator(".graph-node")).to_have_count(count)
    expect(svg).to_have_attribute("viewBox", camera)
    expect(page.locator("#kg-detail")).to_contain_text(selected_id)

    payloads["value"] = copy.deepcopy(payloads["value"])
    payloads["value"]["artifacts"][0]["view"]["graph_revision"] += 1
    page.evaluate("window.abc2labResults.refresh()")
    expect(svg.locator(".graph-node")).to_have_count(len(nodes))
    expect(page.locator("#kg-detail")).to_contain_text("노드를 선택하면")
    expect(page.get_by_role("button", name="주변 보기", exact=True)).to_be_disabled()

    page.get_by_label("node_id 검색", exact=True).fill(selected_id)
    page.get_by_role("button", name="노드 " + selected_id + " 선택", exact=True).click()
    page.get_by_label("노드 종류", exact=True).select_option("User")
    expect(page.locator("#kg-detail")).to_contain_text("현재 필터에서 제외됨")
    expect(page.get_by_role("button", name="선택 노드로 이동", exact=True)).to_be_disabled()
    page.get_by_role("button", name="주변 보기", exact=True).click()
    expect(page.get_by_label("노드 종류", exact=True)).to_have_value("all")
    expect(page.locator("#kg-detail")).to_contain_text("표시 중")


@pytest.mark.skipif(os.environ.get("LOCAL_WEB_BROWSER_TESTS") != "1", reason="Chromium 환경에서 명시적으로 실행")
@pytest.mark.parametrize("case", ["empty", "missing_snapshot", "failed_snapshot", "failed_artifact", "invalid_artifact", "missing_artifact"])
def test_graph_unavailable_and_empty_browser(graph_explorer_page: tuple[Page, dict[str, object]], case: str) -> None:
    from playwright.sync_api import expect

    page, payloads = graph_explorer_page
    original = build_graph_payload()
    expect(page.locator("#graph-view svg .graph-node")).to_have_count(len(original["artifacts"][0]["view"]["nodes"]))
    artifact = payloads["value"]["artifacts"][0]
    if case == "empty":
        artifact["view"].update({"nodes": [], "relationships": []})
    elif case == "missing_artifact":
        payloads["value"]["artifacts"] = []
    elif case in {"missing_snapshot", "failed_snapshot"}:
        artifact["view"].update({"snapshot_state": "missing" if case == "missing_snapshot" else "failed",
                                 "nodes": None, "relationships": None, "workflows": None})
    else:
        artifact.update({"state": "failed" if case == "failed_artifact" else "invalid", "view": None,
                         "error_count": 1, "issue": "GRAPH_REFERENCE_INVALID"})
    page.evaluate("window.abc2labResults.refresh()")
    expect(page.locator("#graph-view svg")).to_have_count(0)
    if case == "empty":
        expect(page.locator("#kg-display-counts")).to_contain_text("노드 0/0개")
    else:
        expect(page.locator("#kg-display-counts")).to_have_count(0)
        expect(page.locator("#graph-view table")).to_have_count(0)
        expect(page.locator("#graph-view")).not_to_contain_text("노드 0/0개")
    payloads["value"] = original
    page.evaluate("window.abc2labResults.refresh()")
    expect(page.locator("#graph-view svg .graph-node")).to_have_count(len(original["artifacts"][0]["view"]["nodes"]))


@pytest.mark.skipif(os.environ.get("LOCAL_WEB_BROWSER_TESTS") != "1", reason="Chromium 환경에서 명시적으로 실행")
def test_graph_new_run_resets_selection_browser(graph_explorer_page: tuple[Page, dict[str, object]]) -> None:
    from playwright.sync_api import expect

    page, payloads = graph_explorer_page
    nodes = payloads["value"]["artifacts"][0]["view"]["nodes"]
    expect(page.locator("#graph-view svg .graph-node")).to_have_count(len(nodes))
    selected_id = next(node["node_id"] for node in nodes if node["node_type"] == "User")
    page.get_by_label("node_id 검색", exact=True).fill(selected_id)
    page.get_by_role("button", name="노드 " + selected_id + " 선택", exact=True).click()
    page.get_by_role("button", name="주변 보기", exact=True).click()
    payloads["value"] = copy.deepcopy(payloads["value"])
    # graph_id/revision이 같아도 다른 실행의 화면 상태는 재사용하지 않는다.
    payloads["value"]["run_id"] = "web-another-graph-fixture"
    page.evaluate("window.abc2labResults.setRun({run_id: 'web-another-graph-fixture'})")
    page.evaluate("window.abc2labResults.refresh()")
    expect(page.locator("#graph-view svg .graph-node")).to_have_count(len(nodes))
    expect(page.locator("#kg-detail")).to_contain_text("노드를 선택하면")
    expect(page.get_by_role("button", name="주변 보기", exact=True)).to_be_disabled()
