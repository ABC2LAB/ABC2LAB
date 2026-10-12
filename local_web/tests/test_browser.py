"""Chromium이 준비된 환경에서만 실행하는 입력 화면 실제 브라우저 검증."""

import os
import threading
import tomllib
from pathlib import Path

import pytest

from local_web.inputs import InputSettings, list_dataset_ids, prepare_input
from local_web.server import LocalWebServer, WebSettings
from local_web.runner import RunnerSettings


@pytest.mark.skipif(os.environ.get("LOCAL_WEB_BROWSER_TESTS") != "1", reason="Chromium 환경에서 명시적으로 실행")
def test_browser_input_preparation(input_settings: InputSettings) -> None:
    from playwright.sync_api import expect, sync_playwright

    server = LocalWebServer(WebSettings("127.0.0.1", 0, 8080), input_settings)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    original = input_settings.collector_template_path.read_bytes()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            page = context.new_page()
            errors, responses = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("response", lambda response: responses.append(response.text()) if "/api/" in response.url else None)
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            expect(page.locator("#target-url")).to_be_enabled()
            expect(page.locator(".account-row")).to_have_count(2)
            page.locator("#target-url").fill("http://different-test-web:9123/start")
            page.locator("#dataset").select_option("case_v2")
            page.locator("#add-account").click()
            expect(page.locator(".account-row")).to_have_count(3)
            for index in range(3):
                row = page.locator(".account-row").nth(index)
                row.locator('[data-field="alias"]').fill(f"client_{index}")
                row.locator('[data-field="role"]').fill("reviewer")
                login_input = row.locator('[data-field="login_id"]')
                expect(login_input).to_have_attribute("type", "text")
                expect(login_input).to_have_attribute("maxlength", "4096")
                expect(login_input).to_have_attribute("autocomplete", "off")
                assert login_input.get_attribute("pattern") is None
                expect(row.locator('[data-field="password"]')).to_have_attribute("type", "password")
                expect(row.locator('[data-field="role"]')).to_have_attribute("pattern", "[a-z][a-z0-9_]*")
                expect(row.locator('[data-field="alias"]')).to_have_attribute("pattern", "[a-z][a-z0-9_]*")
                login_input.fill(f"LOGIN_SENTINEL_{index}+probe@example.test")
                row.locator('[data-field="password"]').fill(f"PASSWORD_SENTINEL_{index}")
                assert login_input.evaluate("item => item.checkValidity()")
            page.locator("#prepare-input").click()
            expect(page.locator("#input-status")).to_contain_text("입력 준비 완료 · 계정 3개")
            expect(page.locator("#target-url")).to_be_disabled()
            expect(page.locator("#start-diagnosis")).to_be_disabled()
            assert page.locator('[data-field="login_id"], [data-field="password"]').evaluate_all("items => items.every(item => item.value === '')")
            assert page.evaluate("Object.keys(localStorage).length + Object.keys(sessionStorage).length") == 0
            assert page.evaluate("document.cookie") == ""
            assert all("LOGIN_SENTINEL" not in response and "PASSWORD_SENTINEL" not in response for response in responses)
            page.locator("#clear-input").click()
            expect(page.locator("#input-status")).to_contain_text("폐기 완료")
            expect(page.locator("#target-url")).to_be_enabled()
            page.locator("#target-url").fill("http://different-test-web:9123/?password=SECRET_QUERY_SENTINEL")
            for index in range(3):
                row = page.locator(".account-row").nth(index)
                row.locator('[data-field="login_id"]').fill(f"LOGIN_SENTINEL_{index}+probe@example.test")
                row.locator('[data-field="password"]').fill(f"PASSWORD_SENTINEL_{index}")
            page.locator("#prepare-input").click()
            expect(page.locator("#input-status")).to_contain_text("대상 URL 형식을 확인")
            assert page.locator('[data-field="login_id"], [data-field="password"]').evaluate_all("items => items.every(item => item.value === '')")
            assert all("SECRET_QUERY_SENTINEL" not in response for response in responses)
            assert all("LOGIN_SENTINEL" not in response and "PASSWORD_SENTINEL" not in response for response in responses)
            page.locator('.account-row [data-field="login_id"]').first.fill("LOGIN_SENTINEL_EXIT@example.test")
            page.locator('.account-row [data-field="password"]').first.fill("PASSWORD_SENTINEL_EXIT")
            page.evaluate("window.dispatchEvent(new Event('pagehide'))")
            assert page.locator('[data-field="login_id"], [data-field="password"]').evaluate_all("items => items.every(item => item.value === '')")
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            for section in ("progress", "results", "evaluation", "input"):
                page.locator(f'nav a[href="#{section}"]').click()
                expect(page.locator(f"#{section}")).to_be_visible()
            assert errors == []
            assert input_settings.collector_template_path.read_bytes() == original
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.skipif(os.environ.get("LOCAL_WEB_MOUNT_TESTS") != "1", reason="읽기 전용 Docker 마운트 환경에서 실행")
def test_read_only_configured_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = InputSettings.from_environment(os.environ)
    original = settings.collector_template_path.read_bytes()
    original_open = Path.open

    def reject_truth_read(path: Path, *args: object, **kwargs: object) -> object:
        assert path.name != "ground_truth.json"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_truth_read)
    dataset_ids = list_dataset_ids(settings)
    assert dataset_ids
    prepared = prepare_input(settings, {
        "target_url": "http://separate-test-container:9123/",
        "dataset_id": dataset_ids[0],
        "accounts": [{"alias": "probe_a", "role": "reviewer", "login_id": "LOGIN_SENTINEL", "password": "PASSWORD_SENTINEL"}],
    })
    try:
        config = tomllib.loads(prepared.collector_config_path.read_text(encoding="utf-8"))
        assert config["login"] == tomllib.loads(original.decode("utf-8"))["login"]
        assert settings.collector_template_path.read_bytes() == original
        with pytest.raises(OSError):
            descriptor = os.open(settings.collector_template_path, os.O_WRONLY)
            os.close(descriptor)
        assert os.getuid() == 10001
    finally:
        prepared.close()


@pytest.mark.skipif(os.environ.get("LOCAL_WEB_BROWSER_TESTS") != "1", reason="Chromium 환경에서 명시적으로 실행")
@pytest.mark.parametrize("case, state", [("completed", "completed"), ("partial", "partial"), ("failed", "failed")])
def test_browser_runner_flow(
    input_settings: InputSettings, runner_settings: RunnerSettings, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, case: str, state: str,
) -> None:
    from playwright.sync_api import expect, sync_playwright

    release = tmp_path / "release"
    monkeypatch.setenv("LOCAL_WEB_TEST_RUNNER_RELEASE", str(release))
    monkeypatch.setenv("LOCAL_WEB_TEST_RUNNER_CASE", case)
    server = LocalWebServer(WebSettings("127.0.0.1", 0, 8080), input_settings, runner_settings)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            page = context.new_page()
            errors, responses = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("response", lambda response: responses.append(response.text()) if "/api/" in response.url else None)
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            expect(page.locator("#target-url")).to_be_enabled()
            page.locator("#target-url").fill("http://separate-test-web:9222/start")
            page.locator("#dataset").select_option("case_v2")
            for index in range(2):
                row = page.locator(".account-row").nth(index)
                row.locator('[data-field="login_id"]').fill(f"LOGIN_SENTINEL_{index}")
                row.locator('[data-field="password"]').fill(f"PASSWORD_SENTINEL_{index}")
            page.locator("#prepare-input").click()
            expect(page.locator("#start-diagnosis")).to_be_enabled()
            page.locator("#start-diagnosis").click()
            expect(page.locator("#progress")).to_be_visible()
            expect(page.locator("#run-status")).to_contain_text("Runner 실행 중")
            expect(page.locator("#artifact-status li")).to_have_count(10)
            page.reload()
            expect(page.locator("#run-status")).to_contain_text("Runner 실행 중")
            assert page.evaluate("document.cookie") == ""
            assert page.evaluate("Object.keys(localStorage).length + Object.keys(sessionStorage).length") == 0
            # 입력 세션 만료는 이미 시작한 진단과 소유 run cookie를 만료시키지 않는다.
            server.inputs.close()
            page.locator("#refresh-run").click()
            expect(page.locator("#run-status")).to_contain_text("Runner 실행 중")
            release.touch()
            expect(page.locator("#run-status")).to_contain_text("Runner " + state, timeout=7000)
            expect(page.locator("#step-status li")).to_have_count(12)
            if case == "failed":
                expect(page.locator("#step-status")).to_contain_text("skipped")
                assert page.locator("#artifact-status li").filter(has_text="파일 존재").count() == 1
            else:
                assert page.locator("#artifact-status li").filter(has_text="파일 존재").count() == 10
            if case == "partial":
                expect(page.locator("#step-status")).to_contain_text("partial · 오류 1건")
            assert all("LOGIN_SENTINEL" not in response and "PASSWORD_SENTINEL" not in response for response in responses)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert errors == []
            assert context.request.get(f"http://127.0.0.1:{server.server_port}/runs/private/").status == 404
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
