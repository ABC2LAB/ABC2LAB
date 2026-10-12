"""외부 실행 설정 초안을 검사한다. 실제 모델·대상·진단 모듈은 실행하지 않는다."""

import json
import tomllib
from pathlib import Path

import pytest
from jsonschema.validators import validator_for

from local_web.inputs import InputSettings, load_template, template_accounts
from local_web.runner import actual_model_config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PROJECT_ROOT / "local_web/configs/vulnerable"


def read_policy() -> dict:
    return json.loads((CONFIG_ROOT / "policy.json").read_text(encoding="utf-8"))


def test_collector_template_uses_confirmed_public_settings() -> None:
    settings = InputSettings(collector_template_path=CONFIG_ROOT / "collector.toml")
    template = load_template(settings)
    assert template["target_url"] == "http://testapp-vulnerable:8000"
    assert template["extra_allowed_origins"] == []
    assert template["max_depth"] == 3 and template["allow_state_changing"] is False
    assert template["login"] == {"path": "/login", "username_field": "email",
                                 "password_field": "password", "success_check": "left_login_page"}
    assert template_accounts(template) == template["accounts"]
    assert {account["alias"] for account in template["accounts"]} == {"user_a", "user_b", "admin_a"}


@pytest.mark.parametrize("name", ["semantic_analyzer", "scenario_generator"])
def test_model_settings_remain_public_and_cpu_trial_only(name: str) -> None:
    path = CONFIG_ROOT / (name + ".toml")
    with path.open("rb") as source:
        data = tomllib.load(source)
    assert data == {"llm": {"provider": "ollama", "model_id": "qwen2.5:1.5b",
                            "base_url": "http://ollama:11434", "temperature": 0.0, "timeout_seconds": 300.0}}
    # 준비된 모델의 존재·실제 추론 성공이 아니라 Web의 최소 설정 검사만 의미한다.
    assert actual_model_config(path) is True


def test_policy_draft_matches_owner_schema_without_changing_version() -> None:
    path = PROJECT_ROOT / "modules/safety_policy/schemas/input/policy_config.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    validator_for(schema)(schema).validate(read_policy())


def test_policy_scope_and_accounts_match_collector_public_ids() -> None:
    template = load_template(InputSettings(collector_template_path=CONFIG_ROOT / "collector.toml"))
    policy = read_policy()
    assert policy["allowed_targets"] == [{"origin": template["target_url"], "path_prefixes": ["/orders"]}]
    accounts_by_id = {account["account_id"]: account for account in policy["test_accounts"]}
    expected_ids = {"account:guest"} | {f"account:{row['alias']}" for row in template["accounts"]}
    assert set(accounts_by_id) == expected_ids
    assert accounts_by_id["account:guest"] == {"account_id": "account:guest", "role_ids": ["role:guest"], "requires_session": False}
    for row in template["accounts"]:
        assert accounts_by_id[f"account:{row['alias']}"] == {
            "account_id": f"account:{row['alias']}", "role_ids": [f"role:{row['role']}"], "requires_session": True,
        }


def test_policy_read_rule_is_narrow_and_write_rule_blocks() -> None:
    policy = read_policy()
    rules_by_id = {rule["rule_id"]: rule for rule in policy["request_rules"]}
    assert set(rules_by_id) == {"orders.read-only", "orders.write-blocked"}
    readonly = rules_by_id["orders.read-only"]
    assert readonly["methods"] == ["GET"]
    assert (readonly["state_change"], readonly["data_impact"], readonly["service_impact"]) == ("none", "read_only", "low")
    blocked = rules_by_id["orders.write-blocked"]
    assert set(blocked["methods"]) == {"POST", "PUT", "PATCH", "DELETE"}
    assert all(blocked[key] == "block" for key in ("state_change", "data_impact", "service_impact"))
    assert all(rule["origin"] == "http://testapp-vulnerable:8000" and rule["path_prefix"] == "/orders"
               for rule in policy["request_rules"])
    assert policy["limits"] == {"max_requests": 20, "max_duration_ms": 60000}


def test_configurations_store_no_account_credentials() -> None:
    forbidden = {"login_id", "password", "cookie", "cookies", "token", "session_ref", "authorization"}

    def check_keys(value: object) -> None:
        if isinstance(value, dict):
            assert not set(value) & forbidden
            for child in value.values():
                check_keys(child)
        elif isinstance(value, list):
            for child in value:
                check_keys(child)

    check_keys(read_policy())
    for path in CONFIG_ROOT.glob("*.toml"):
        with path.open("rb") as source:
            check_keys(tomllib.load(source))
