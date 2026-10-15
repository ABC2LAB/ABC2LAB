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
READ_PATHS = {"/orders", "/api/users", "/products", "/admin", "/cart", "/checkout", "/mypage"}
APPROVAL_PATHS = {"/orders", "/cart", "/mypage", "/admin"}


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
    assert len(policy["allowed_targets"]) == 1
    target = policy["allowed_targets"][0]
    assert target["origin"] == template["target_url"]
    assert set(target["path_prefixes"]) == READ_PATHS
    accounts_by_id = {account["account_id"]: account for account in policy["test_accounts"]}
    expected_ids = {"account:guest"} | {f"account:{row['alias']}" for row in template["accounts"]}
    assert set(accounts_by_id) == expected_ids
    assert accounts_by_id["account:guest"] == {"account_id": "account:guest", "role_ids": ["role:guest"], "requires_session": False}
    for row in template["accounts"]:
        assert accounts_by_id[f"account:{row['alias']}"] == {
            "account_id": f"account:{row['alias']}", "role_ids": [f"role:{row['role']}"], "requires_session": True,
        }


def test_policy_read_rules_cover_business_paths_and_writes_need_approval() -> None:
    policy = read_policy()
    rules = policy["request_rules"]
    reads = [rule for rule in rules if rule["methods"] == ["GET"]]
    writes = [rule for rule in rules if rule["methods"] == ["POST"]]
    blocked = [rule for rule in rules if rule["state_change"] == "block"]
    assert len(reads) == len(READ_PATHS) and len(writes) == len(APPROVAL_PATHS)
    assert len(blocked) == len(READ_PATHS)
    assert len(rules) == len(reads) + len(writes) + len(blocked)
    assert set(rule["path_prefix"] for rule in reads) == READ_PATHS
    assert set(rule["path_prefix"] for rule in writes) == APPROVAL_PATHS
    assert all((rule["state_change"], rule["data_impact"], rule["service_impact"])
               == ("none", "read_only", "low") for rule in reads)
    assert all((rule["state_change"], rule["data_impact"], rule["service_impact"])
               == ("require_approval", "require_approval", "low") for rule in writes)
    assert set(rule["path_prefix"] for rule in blocked) == READ_PATHS
    for rule in blocked:
        expected_methods = {"PUT", "PATCH", "DELETE"}
        if rule["path_prefix"] not in APPROVAL_PATHS:
            expected_methods.add("POST")
        assert set(rule["methods"]) == expected_methods
        assert all(rule[dimension] == "block" for dimension in ("state_change", "data_impact", "service_impact"))
    assert all(rule["origin"] == "http://testapp-vulnerable:8000" for rule in rules)
    assert len({rule["rule_id"] for rule in rules}) == len(rules)
    assert policy["schema_version"] == "0.1.0"
    assert policy["policy_id"] == "local-web-vulnerable-business"
    assert policy["policy_version"] == "draft-2"
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
