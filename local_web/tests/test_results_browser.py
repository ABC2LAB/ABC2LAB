"""Chromium에서 공개 fixture·실제 Web 서버·CLI 대역을 연결해 결과 화면을 확인한다."""

import os
import threading
from pathlib import Path

import pytest

from local_web.inputs import InputSettings
from local_web.runner import RunnerSettings
from local_web.server import LocalWebServer, WebSettings


@pytest.mark.skipif(os.environ.get("LOCAL_WEB_BROWSER_TESTS") != "1", reason="Chromium 환경에서 명시적으로 실행")
@pytest.mark.parametrize("case", ["completed", "partial", "failed"])
def test_artifact_results_browser(
    input_settings: InputSettings, runner_settings: RunnerSettings, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    from playwright.sync_api import expect, sync_playwright

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
            page.on("response", lambda response: responses.append(response.text()) if "/api/" in response.url else None)
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
            release.touch()
            expect(page.locator("#run-status")).to_contain_text("Runner " + case, timeout=7000)
            page.locator('nav a[href="#results"]').click()
            page.locator("#refresh-results").click()
            expect(page.locator("#results-status")).to_contain_text("· " + case)
            page.reload()
            expect(page.locator("#results-status")).to_contain_text("· " + case)
            for menu in ("results", "approval"):
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
            other.close()
            context.close()
            browser.close()
    finally:
        release.touch()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
