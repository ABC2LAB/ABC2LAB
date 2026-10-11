"""공개 설정 매핑·비밀값 분리·임시 입력 수명을 진단 모듈 없이 검증한다."""

import copy
import os
import time
import tomllib
from pathlib import Path

import pytest

from local_web.inputs import (
    MAX_ACCOUNTS, InputError, InputSettings, InputStore,
    list_dataset_ids, load_template, parse_target_url, prepare_input,
)


def test_preparation_preserves_operator_settings_and_source(
    input_settings: InputSettings, input_payload: dict[str, object],
) -> None:
    original = input_settings.collector_template_path.read_bytes()
    prepared = prepare_input(input_settings, input_payload)
    try:
        generated = tomllib.loads(prepared.collector_config_path.read_text(encoding="utf-8"))
        template = tomllib.loads(original.decode("utf-8"))
        assert generated["target_url"] == input_payload["target_url"]
        assert generated["roles"] == ["member"]
        assert generated["accounts"] == [{"alias": "member_a", "role": "member"}, {"alias": "member_b", "role": "member"}]
        for key in {"login", "extra_allowed_origins", "max_depth", "allow_state_changing", "state_changing_keywords"}:
            assert generated[key] == template[key]
        assert input_settings.collector_template_path.read_bytes() == original
        text = prepared.collector_config_path.read_text(encoding="utf-8")
        assert "LOGIN_SENTINEL" not in text and "PASSWORD_SENTINEL" not in text
        assert prepared.collector_config_path.stat().st_mode & 0o777 == 0o600
        assert prepared.collector_config_path.parent.stat().st_mode & 0o777 == 0o700
        assert set(path.name for path in prepared.collector_config_path.parent.iterdir()) == {"collector.toml", "empty.env"}
        assert (prepared.collector_config_path.parent / "empty.env").read_bytes() == b""
    finally:
        root = prepared.collector_config_path.parent
        prepared.close()
        assert prepared.accounts == ()
        assert not root.exists()


def test_child_environment_isolated_without_global_mutation(
    input_settings: InputSettings, input_payload: dict[str, object],
) -> None:
    before = dict(os.environ)
    prepared = prepare_input(input_settings, input_payload)
    try:
        environment = prepared.make_collector_environment({
            "PATH": "/example/bin", "COLLECTOR_ACCOUNT_OLD_PASSWORD": "old",
            "COLLECTOR_CONFIG_PATH": "/old/config", "COLLECTOR_SECRETS_PATH": "/old/secrets",
        })
        assert environment["PATH"] == "/example/bin"
        assert "COLLECTOR_ACCOUNT_OLD_PASSWORD" not in environment
        assert environment["COLLECTOR_ACCOUNT_MEMBER_A_LOGIN_ID"] == "LOGIN_SENTINEL_A"
        assert environment["COLLECTOR_ACCOUNT_MEMBER_B_PASSWORD"] == "PASSWORD_SENTINEL_B"
        assert environment["COLLECTOR_CONFIG_PATH"] == str(prepared.collector_config_path)
        assert environment["COLLECTOR_SECRETS_PATH"].endswith("/empty.env")
        assert os.environ == before
        assert "LOGIN_SENTINEL" not in repr(prepared) and "PASSWORD_SENTINEL" not in repr(prepared)
        assert "LOGIN_SENTINEL" not in repr(prepared.accounts) and "PASSWORD_SENTINEL" not in repr(prepared.accounts)
    finally:
        prepared.close()


def test_dataset_discovery_never_reads_ground_truth(
    input_settings: InputSettings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read = Path.open

    def checked_open(path: Path, *args: object, **kwargs: object) -> object:
        assert path.name != "ground_truth.json"
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", checked_open)
    assert list_dataset_ids(input_settings) == ["case_v2"]
    assert load_template(input_settings)["max_depth"] == 5


def test_dataset_paths_and_symlinks_filtered(input_settings: InputSettings, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "ground_truth.json").write_text("outside", encoding="utf-8")
    (input_settings.datasets_path / "linked_dataset").symlink_to(outside, target_is_directory=True)
    linked_truth = input_settings.datasets_path / "linked_truth"
    linked_truth.mkdir()
    (linked_truth / "ground_truth.json").symlink_to(outside / "ground_truth.json")
    (input_settings.datasets_path / "no_truth").mkdir()
    (input_settings.datasets_path / "bad name").mkdir()
    assert list_dataset_ids(input_settings) == ["case_v2"]


@pytest.mark.parametrize("url", [
    "", "ftp://localhost", "javascript:alert(1)", "http:///missing", "http://x:bad",
    "http://login:secret@test-web/", "http://test-web/?password=secret", "http://test-web/#secret",
    " http://test-web/", "http://test-web/\n", "http://test-web\\outside/", "http://test-web:65536/",
])
def test_invalid_target_url(url: str) -> None:
    with pytest.raises(InputError, match="TARGET_URL_INVALID"):
        parse_target_url(url)


@pytest.mark.parametrize("url", ["http://different-container:9123/path", "https://internal.example/", "http://[::1]:8000/"])
def test_valid_url_does_not_duplicate_collector_origin_policy(url: str) -> None:
    assert parse_target_url(url) == url


@pytest.mark.parametrize("field,value", [
    ("alias", "guest"), ("alias", "bad alias"), ("role", "Admin"), ("role", "guest"),
    ("login_id", ""), ("password", "   "), ("password", "bad\x00secret"),
    ("alias", "login_sentinel_a"),
])
def test_invalid_account(
    input_settings: InputSettings, input_payload: dict[str, object], field: str, value: str,
) -> None:
    input_payload["accounts"][0][field] = value
    with pytest.raises(InputError):
        prepare_input(input_settings, input_payload)


def test_duplicate_alias_and_extra_fields_rejected(
    input_settings: InputSettings, input_payload: dict[str, object],
) -> None:
    duplicate = copy.deepcopy(input_payload)
    duplicate["accounts"][1]["alias"] = duplicate["accounts"][0]["alias"]
    with pytest.raises(InputError, match="ACCOUNT_INVALID"):
        prepare_input(input_settings, duplicate)
    input_payload["accounts"][0]["token"] = "SECRET_SENTINEL"
    with pytest.raises(InputError, match="ACCOUNT_INVALID"):
        prepare_input(input_settings, input_payload)


@pytest.mark.parametrize("dataset", ["../case_v2", "/case_v2", "missing", "case_v2/other", "..", "case v2"])
def test_dataset_input_requires_available_id(
    input_settings: InputSettings, input_payload: dict[str, object], dataset: str,
) -> None:
    input_payload["dataset_id"] = dataset
    with pytest.raises(InputError, match="DATASET_INVALID"):
        prepare_input(input_settings, input_payload)


def test_guest_only_and_different_account_count(
    input_settings: InputSettings, input_payload: dict[str, object],
) -> None:
    input_payload["accounts"] = []
    prepared = prepare_input(input_settings, input_payload)
    try:
        config = tomllib.loads(prepared.collector_config_path.read_text(encoding="utf-8"))
        assert config["roles"] == [] and config["accounts"] == []
    finally:
        prepared.close()
    input_payload["accounts"] = [{"alias": f"client_{index}", "role": f"team_{index % 3}", "login_id": f"login-{index}", "password": f"secret-{index}"} for index in range(7)]
    prepared = prepare_input(input_settings, input_payload)
    try:
        config = tomllib.loads(prepared.collector_config_path.read_text(encoding="utf-8"))
        assert len(config["accounts"]) == 7 and config["roles"] == ["team_0", "team_1", "team_2"]
    finally:
        prepared.close()


def test_account_resource_limit(input_settings: InputSettings, input_payload: dict[str, object]) -> None:
    input_payload["accounts"] *= MAX_ACCOUNTS
    with pytest.raises(InputError, match="ACCOUNT_INVALID"):
        prepare_input(input_settings, input_payload)


def test_unsupported_template_and_missing_login(
    input_settings: InputSettings, input_payload: dict[str, object],
) -> None:
    with input_settings.collector_template_path.open("a", encoding="utf-8") as target:
        target.write("\nsecret_password = 'SECRET_SENTINEL'\n")
    with pytest.raises(InputError, match="TEMPLATE_INVALID"):
        prepare_input(input_settings, input_payload)
    input_settings.collector_template_path.write_text("max_depth = 2\n", encoding="utf-8")
    with pytest.raises(InputError, match="TEMPLATE_LOGIN_MISSING"):
        prepare_input(input_settings, input_payload)


def test_configuration_string_escape_roundtrip(
    input_settings: InputSettings, input_payload: dict[str, object],
) -> None:
    with input_settings.collector_template_path.open("a", encoding="utf-8") as target:
        target.write("\n")
    source = input_settings.collector_template_path.read_text(encoding="utf-8")
    source = source.replace('username_field = "identity"', 'username_field = "이름\\\"필드"')
    input_settings.collector_template_path.write_text(source, encoding="utf-8")
    prepared = prepare_input(input_settings, input_payload)
    try:
        config = tomllib.loads(prepared.collector_config_path.read_text(encoding="utf-8"))
        assert config["login"]["username_field"] == '이름"필드'
    finally:
        prepared.close()


def test_store_isolates_browsers_and_consumes_once(
    input_settings: InputSettings, input_payload: dict[str, object],
) -> None:
    store = InputStore(input_settings)
    try:
        first_id, first = store.open_session(None)
        second_id, second = store.open_session(None)
        summary = store.prepare(first_id, first.csrf_token, input_payload)
        assert summary["status"] == "prepared" and summary["account_count"] == 2
        assert "LOGIN_SENTINEL" not in str(summary) and "PASSWORD_SENTINEL" not in str(summary)
        assert store.preparation_summary(second)["status"] == "empty"
        with pytest.raises(InputError, match="INPUT_SESSION_INVALID"):
            store.authenticate(second_id, first.csrf_token)
        prepared = store.consume(first_id, first.csrf_token, summary["preparation_id"])
        try:
            with pytest.raises(InputError, match="PREPARATION_UNAVAILABLE"):
                store.consume(first_id, first.csrf_token, summary["preparation_id"])
        finally:
            prepared.close()
    finally:
        store.close()


def test_store_replacement_clear_and_shutdown_cleanup(
    input_settings: InputSettings, input_payload: dict[str, object],
) -> None:
    store = InputStore(input_settings)
    session_id, session = store.open_session(None)
    store.prepare(session_id, session.csrf_token, input_payload)
    first = session.prepared
    first_path = first.collector_config_path
    store.prepare(session_id, session.csrf_token, input_payload)
    assert not first_path.exists() and first.accounts == ()
    second_path = session.prepared.collector_config_path
    store.clear_preparation(session_id, session.csrf_token)
    assert not second_path.exists() and session.prepared is None
    store.prepare(session_id, session.csrf_token, input_payload)
    third = session.prepared
    third_path = third.collector_config_path
    store.close()
    assert not third_path.exists() and third.accounts == ()


def test_expiration_cleanup(
    input_settings: InputSettings, input_payload: dict[str, object], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("local_web.inputs.INPUT_LIFETIME_SECONDS", 0.1)
    store = InputStore(input_settings)
    try:
        session_id, session = store.open_session(None)
        store.prepare(session_id, session.csrf_token, input_payload)
        prepared = session.prepared
        path = prepared.collector_config_path
        time.sleep(0.2)
        assert not path.exists() and prepared.accounts == ()
        with pytest.raises(InputError, match="INPUT_SESSION_INVALID"):
            store.authenticate(session_id, session.csrf_token)
    finally:
        store.close()


def test_environment_path_configuration() -> None:
    assert InputSettings.from_environment({}) == InputSettings()
    assert InputSettings.from_environment({
        "LOCAL_WEB_COLLECTOR_TEMPLATE_PATH": "/config/collector.toml",
        "LOCAL_WEB_DATASETS_PATH": "/app/datasets",
    }) == InputSettings(Path("/config/collector.toml"), Path("/app/datasets"))
