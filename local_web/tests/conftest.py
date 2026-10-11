"""실제 계정·Ground Truth·진단 모듈을 사용하지 않는 Web 입력 fixture."""

from pathlib import Path

import pytest

from local_web.inputs import InputSettings
from local_web.runner import RunnerSettings

COLLECTOR_TEMPLATE = '''target_url = "http://previous.example:9100"
extra_allowed_origins = ["http://operator-approved.example:9200"]
max_depth = 5
allow_state_changing = false
state_changing_keywords = ["withdraw", "close"]
roles = ["member", "staff"]
[login]
path = "/session/start"
username_field = "identity"
password_field = "passphrase"
success_check = "cookie_present"
success_value = "authenticated"
[[accounts]]
alias = "client_a"
role = "member"
[[accounts]]
alias = "operator_a"
role = "staff"
'''


@pytest.fixture
def input_settings(tmp_path: Path) -> InputSettings:
    template = tmp_path / "collector.toml"
    template.write_text(COLLECTOR_TEMPLATE, encoding="utf-8")
    datasets = tmp_path / "datasets"
    dataset = datasets / "case_v2"
    dataset.mkdir(parents=True)
    # Web은 존재 확인만 한다. 내용 검증과 읽기는 Reporter의 책임이다.
    (dataset / "ground_truth.json").write_text("GT_CONTENT_MUST_NOT_BE_READ", encoding="utf-8")
    return InputSettings(template, datasets)


@pytest.fixture
def input_payload() -> dict[str, object]:
    return {
        "target_url": "http://test-web:8123/start",
        "dataset_id": "case_v2",
        "accounts": [
            {"alias": "member_a", "role": "member", "login_id": "LOGIN_SENTINEL_A", "password": "PASSWORD_SENTINEL_A"},
            {"alias": "member_b", "role": "member", "login_id": "LOGIN_SENTINEL_B", "password": "PASSWORD_SENTINEL_B"},
        ],
    }


@pytest.fixture
def runner_settings(tmp_path: Path) -> RunnerSettings:
    configs = []
    for name in ("semantic", "scenario"):
        path = tmp_path / (name + ".toml")
        path.write_text('[llm]\nprovider = "ollama"\nmodel_id = "TEST_ONLY_MODEL"\nbase_url = "http://ollama:11434"\n', encoding="utf-8")
        configs.append(path)
    policy = tmp_path / "policy.json"
    # Web은 정책 내용을 해석하지 않는다. 실제 검증은 Safety Policy가 담당한다.
    policy.write_text("POLICY_CONTENT_MUST_NOT_BE_PARSED", encoding="utf-8")
    runs = tmp_path / "results"
    runs.mkdir(mode=0o700)
    return RunnerSettings(runs_dir=runs, semantic_config_path=configs[0], scenario_config_path=configs[1],
                          policy_config_path=policy, project_root=Path(__file__).parent / "fixtures" / "runner")
