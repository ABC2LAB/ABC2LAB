"""초안 JSON Schema(Ollama 구조화 출력) 테스트. 키 목록은 m5 명세의 초안 경계에서 옮겨 적는다.

Schema는 모양(키·중첩·허용값)만 막는다. ID 존재·순서·참조 같은 의미는 scenario_validator가 계속 본다.
"""
import copy
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from modules.scenario_generator.ollama_drafter import OllamaDrafterConfig, OllamaScenarioDrafter
from modules.scenario_generator.scenario_drafter import build_draft_json_schema

# m5 "구현 주의사항": 초안은 steps만, step은 아래 키만, request는 parameters만 담는다(판정 조건은 프로그램 몫).
SPEC_DRAFT_KEYS = {"steps"}
SPEC_STEP_KEYS = {"step_id", "order", "source_request_id", "account_role", "request", "bindings"}
SPEC_REQUEST_KEYS = {"parameters"}
SPEC_ACCOUNT_ROLES = {"actor", "reference"}
OUTPUT_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "output" / "test_scenarios.schema.json"


def output_defs() -> dict[str, Any]:
    return json.loads(OUTPUT_SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]


def step_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return schema["properties"]["steps"]["items"]


def iter_object_schemas(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from iter_object_schemas(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_object_schemas(item)


def test_schema_requires_exactly_the_spec_keys_at_each_level() -> None:
    schema = build_draft_json_schema()
    step = step_schema(schema)
    request = step["properties"]["request"]
    for node, keys in ((schema, SPEC_DRAFT_KEYS), (step, SPEC_STEP_KEYS), (request, SPEC_REQUEST_KEYS)):
        assert set(node["properties"]) == keys
        assert set(node["required"]) == keys


def test_every_object_in_the_schema_rejects_unknown_keys() -> None:
    objects = list(iter_object_schemas(build_draft_json_schema()))
    # 초안·step·request·parameter·binding 다섯 종류가 모두 객체로 들어 있어야 한다.
    assert len(objects) >= 5
    assert all(node.get("additionalProperties") is False for node in objects)


def test_enums_follow_the_output_contract() -> None:
    defs = output_defs()
    step = step_schema(build_draft_json_schema())
    parameter = step["properties"]["request"]["properties"]["parameters"]["items"]
    binding = step["properties"]["bindings"]["items"]
    assert set(step["properties"]["account_role"]["enum"]) == SPEC_ACCOUNT_ROLES
    assert parameter["properties"]["location"]["enum"] == defs["parameterValue"]["properties"]["location"]["enum"]
    assert binding["properties"]["source_part"]["enum"] == defs["binding"]["properties"]["source_part"]["enum"]


def test_nested_records_have_the_output_contract_keys() -> None:
    # parameter·binding은 초안 값이 출력에 그대로 들어간다. 키가 출력 계약과 다르면 조립 뒤 Schema 위반이 된다.
    defs = output_defs()
    step = step_schema(build_draft_json_schema())
    parameter = step["properties"]["request"]["properties"]["parameters"]["items"]
    binding = step["properties"]["bindings"]["items"]
    for node, definition in ((parameter, "parameterValue"), (binding, "binding")):
        assert set(node["properties"]) == set(defs[definition]["properties"])
        assert set(node["required"]) == set(defs[definition]["required"])


def test_schema_is_valid_and_accepts_the_replay_drafts(drafts: dict[str, Any]) -> None:
    schema = build_draft_json_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for draft in drafts.values():
        assert list(validator.iter_errors(draft)) == []


def _nest_bindings_in_request(draft: dict[str, Any]) -> None:
    step = draft["steps"][0]
    step["request"]["bindings"] = step.pop("bindings")


def _nest_assertions_in_step(draft: dict[str, Any]) -> None:
    draft["steps"][0]["assertions"] = []


def _write_state_change(draft: dict[str, Any]) -> None:
    # 작은 모델이 GET 조회를 possible로 적어 기준 응답을 못 만들었다. state_change는 프로그램이 원본 요청 method로 채운다.
    draft["steps"][0]["state_change"] = "none"


def _write_assertions(draft: dict[str, Any]) -> None:
    # 작은 모델이 판정 기대값을 정상 동작(403)으로 적어 판정이 뒤집혔다. 판정 조건은 프로그램 몫이다.
    draft["assertions"] = [
        {"check_id": "a1", "kind": "response_status", "subject_ref": "s1", "selector": None, "operator": "eq", "expected": 403}
    ]


def _write_url_template(draft: dict[str, Any]) -> None:
    draft["steps"][0]["request"]["url_template"] = "http://target.example.test/items"


def _add_preconditions(draft: dict[str, Any]) -> None:
    draft["preconditions"] = []


def _copy_account_id(draft: dict[str, Any]) -> None:
    draft["steps"][0]["account_id"] = "account_a"


def _unknown_account_role(draft: dict[str, Any]) -> None:
    draft["steps"][0]["account_role"] = "owner"


def _empty_steps(draft: dict[str, Any]) -> None:
    draft["steps"] = []


@pytest.mark.parametrize(
    "break_draft",
    [
        _nest_bindings_in_request,
        _nest_assertions_in_step,
        _write_assertions,
        _write_state_change,
        _write_url_template,
        _add_preconditions,
        _copy_account_id,
        _unknown_account_role,
        _empty_steps,
    ],
    ids=[
        "bindings inside request",
        "assertions inside step",
        "assertions at top",
        "state_change in step",
        "url_template in request",
        "preconditions at top",
        "account_id in step",
        "unknown account_role",
        "empty steps",
    ],
)
def test_schema_rejects_shapes_small_models_produced(
    drafts: dict[str, Any], break_draft: Callable[[dict[str, Any]], None]
) -> None:
    # 6차 입력 qwen2.5:1.5b 초안에서 본 잘못된 중첩(앞의 둘)과 프로그램 몫 키(state_change 포함)·허용값 밖 값.
    draft = copy.deepcopy(next(iter(drafts.values())))
    break_draft(draft)
    assert not Draft202012Validator(build_draft_json_schema()).is_valid(draft)


def test_ollama_payload_constrains_output_with_the_draft_schema() -> None:
    captured: dict[str, Any] = {}

    def transport(url: str, payload: bytes, timeout: float) -> bytes:
        captured["body"] = json.loads(payload)
        return json.dumps({"response": json.dumps({"steps": []})}).encode("utf-8")

    OllamaScenarioDrafter(OllamaDrafterConfig(model_id="test-model"), transport=transport).draft({"candidate": {}})
    # format="json"은 JSON인지만 강제한다. 초안 모양을 디코딩 단계에서 강제하려면 Schema를 넘겨야 한다.
    assert captured["body"]["format"] == build_draft_json_schema()
    # state_change는 프로그램이 채우므로 모델이 낼 수 있는 모양에 없다(1.5b가 GET 조회를 possible로 적었다).
    assert "state_change" not in captured["body"]["format"]["properties"]["steps"]["items"]["properties"]
