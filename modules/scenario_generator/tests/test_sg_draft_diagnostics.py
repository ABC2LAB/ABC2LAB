"""실패 초안 진단 레코드 단위 테스트. 값이 아니라 필드·바인딩·마스킹을 검증한다."""
from typing import Any

from modules.scenario_generator.draft_diagnostics import (
    FAILURE_DRAFT_INVALID,
    FAILURE_DRAFTER_FAILED,
    REDACTED,
    build_draft_invalid_record,
    build_drafter_failed_record,
)

MODEL_INFO = {"model_id": "m", "model_version": "m", "prompt_version": "ollama-scenario-v5", "temperature": 0.0, "seed": None}


def _step(**overrides: Any) -> dict[str, Any]:
    base = {
        "step_id": "s1", "order": 0, "source_request_id": "r1", "account_role": "actor",
        "request": {"parameters": []},
        "bindings": [], "state_change": "none",
    }
    base.update(overrides)
    return base


def _draft(steps: list[dict[str, Any]], assertions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"steps": steps, "assertions": assertions or []}


def test_field_diagnosis_reports_missing_and_extra_step_keys() -> None:
    step = _step()
    del step["account_role"]          # 필수 선택값 누락
    step["account_id"] = "copied_a"   # 프로그램 몫 식별자를 LLM이 복사(금지 키)
    record = build_draft_invalid_record("c1", _draft([step]), ["문제"], MODEL_INFO)
    step_diagnosis = record["field_diagnosis"]["steps"][0]
    assert "account_role" in step_diagnosis["missing"]
    assert "account_id" in step_diagnosis["extra"]
    assert record["failure"] == FAILURE_DRAFT_INVALID
    assert record["model_info"]["prompt_version"] == "ollama-scenario-v5"


def test_field_diagnosis_reports_type_and_enum_problems() -> None:
    step = _step(order="0", account_role="owner")  # order는 문자열, account_role은 허용값 밖
    problems = build_draft_invalid_record("c1", _draft([step]), ["문제"], MODEL_INFO)["field_diagnosis"]["steps"][0]["type_problems"]
    assert any("order" in problem for problem in problems)
    assert any("account_role" in problem for problem in problems)


def test_request_key_diff_is_reported() -> None:
    step = _step(request={"url_template": "http://target.example.test/x", "parameters": [], "method": "GET"})
    request_diff = build_draft_invalid_record("c1", _draft([step]), ["문제"], MODEL_INFO)["field_diagnosis"]["steps"][0]["request"]
    # method·url_template은 프로그램이 원본 요청에서 채우므로 초안에 있으면 안 됨
    assert request_diff["extra"] == ["method", "url_template"]
    assert request_diff["missing"] == []


def test_binding_analysis_flags_undefined_url_binding() -> None:
    step = _step(request={"url_template": "http://target.example.test/items/{item_id}", "parameters": []})
    analysis = build_draft_invalid_record("c1", _draft([step]), ["문제"], MODEL_INFO)["binding_analysis"][0]
    assert analysis["url_referenced"] == ["item_id"]
    assert analysis["declared"] == []
    assert analysis["undefined"] == ["item_id"]


def test_binding_analysis_flags_undefined_parameter_binding() -> None:
    # URL은 프로그램이 채우므로 초안의 바인딩 참조는 parameter의 binding_ref로 온다(path 바인딩 포함).
    step = _step(request={"parameters": [
        {"name": "item_id", "location": "path", "value": None, "binding_ref": "b_item"},
        {"name": "page", "location": "query", "value": "2", "binding_ref": None},
    ]})
    analysis = build_draft_invalid_record("c1", _draft([step]), ["문제"], MODEL_INFO)["binding_analysis"][0]
    assert analysis["parameter_referenced"] == ["b_item"]
    assert analysis["undefined"] == ["b_item"]


def test_binding_analysis_clears_when_declared_in_same_step() -> None:
    step = _step(
        request={"url_template": "http://target.example.test/items/{item_id}", "parameters": []},
        bindings=[{"binding_id": "item_id", "source_step_id": "s0", "source_part": "response_body", "selector": "/id"}],
    )
    assert build_draft_invalid_record("c1", _draft([step]), ["문제"], MODEL_INFO)["binding_analysis"][0]["undefined"] == []


def test_draft_copy_redacts_parameter_values_without_touching_original() -> None:
    step = _step(request={
        "url_template": "http://target.example.test/items/1",
        "parameters": [{"name": "q", "location": "query", "value": "secret-value-123", "binding_ref": None}],
    })
    draft = _draft([step])
    record = build_draft_invalid_record("c1", draft, ["문제"], MODEL_INFO)
    assert record["draft"]["steps"][0]["request"]["parameters"][0]["value"] == REDACTED
    # 구조·url_template은 보존(디버깅 대상), 원본은 깊은 복사라 그대로
    assert record["draft"]["steps"][0]["request"]["url_template"] == "http://target.example.test/items/1"
    assert draft["steps"][0]["request"]["parameters"][0]["value"] == "secret-value-123"


def test_drafter_failed_record_has_no_draft() -> None:
    record = build_drafter_failed_record("c1", "Ollama 호출 실패: URLError", MODEL_INFO)
    assert record["failure"] == FAILURE_DRAFTER_FAILED
    assert record["draft"] is None and record["field_diagnosis"] is None and record["binding_analysis"] is None
    assert record["problems"] == ["Ollama 호출 실패: URLError"]


def test_non_dict_draft_does_not_crash() -> None:
    record = build_draft_invalid_record("c1", "not a dict", ["문제"], MODEL_INFO)
    assert record["field_diagnosis"]["top_level"]["missing"] == ["assertions", "steps"]
    assert record["binding_analysis"] == []
    assert record["draft"] == "not a dict"
