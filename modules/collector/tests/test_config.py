from pathlib import Path
from typing import Any

import pytest

from modules.collector.core.config import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_STATE_CHANGING_KEYWORDS,
    GUEST_ACCOUNT,
    GUEST_ROLE,
    ConfigError,
    LoginSuccessCheck,
    is_request_allowed,
    load_config,
    load_config_files,
    secret_key_names,
)

TARGET_URL = "http://localhost:8001"
SECRET_PASSWORD = "s3cret-value-do-not-log"
SECRET_LOGIN_ID = "login-id-do-not-log@example.test"


def make_guest_only_settings() -> dict[str, Any]:
    return {"target_url": TARGET_URL}


def make_full_settings() -> dict[str, Any]:
    return {
        "target_url": TARGET_URL,
        "roles": ["user", "admin", "manager"],
        "login": {
            "path": "/signin",
            "username_field": "login_id",
            "password_field": "login_pw",
            "success_check": "left_login_page",
        },
        "accounts": [
            {"alias": "user_a", "role": "user"},
            {"alias": "user_b", "role": "user"},
            {"alias": "admin_a", "role": "admin"},
            {"alias": "manager_a", "role": "manager"},
        ],
    }


def make_secrets(*aliases: str) -> dict[str, str]:
    secrets: dict[str, str] = {}
    for alias in aliases:
        login_id_key, password_key = secret_key_names(alias)
        secrets[login_id_key] = f"{alias}-{SECRET_LOGIN_ID}"
        secrets[password_key] = SECRET_PASSWORD
    return secrets


ALL_SECRETS = make_secrets("user_a", "user_b", "admin_a", "manager_a")


def account_by_alias(settings: dict[str, Any], secrets: dict[str, str]) -> dict[str, Any]:
    return {account.alias: account for account in load_config(settings, secrets).accounts}


# ---- 기본·역할·계정 ----


def test_guest_only_config_needs_only_target_url() -> None:
    config = load_config(make_guest_only_settings())

    assert config.start_url == TARGET_URL
    assert config.roles == (GUEST_ROLE,)
    assert config.accounts == (GUEST_ACCOUNT,)
    assert config.login is None
    assert config.max_depth == DEFAULT_MAX_DEPTH
    assert config.can_change_state is False


def test_roles_and_accounts_are_not_fixed_in_count() -> None:
    config = load_config(make_full_settings(), ALL_SECRETS)

    assert config.roles == (GUEST_ROLE, "user", "admin", "manager")
    assert [(account.alias, account.role) for account in config.accounts] == [
        (GUEST_ROLE, GUEST_ROLE),
        ("user_a", "user"),
        ("user_b", "user"),
        ("admin_a", "admin"),
        ("manager_a", "manager"),
    ]
    assert [account.account_id for account in config.accounts][:3] == ["account:guest", "account:user_a", "account:user_b"]


def test_secrets_read_per_alias() -> None:
    accounts = account_by_alias(make_full_settings(), ALL_SECRETS)

    assert accounts["user_b"].login_id == f"user_b-{SECRET_LOGIN_ID}"
    assert accounts["user_b"].password == SECRET_PASSWORD
    assert accounts[GUEST_ROLE].login_id is None and accounts[GUEST_ROLE].password is None


def test_missing_secrets_leave_that_account_without_credentials() -> None:
    """비밀값이 빠진 계정은 설정 오류가 아니라 그 계정만 로그인에 실패한다(partial)."""
    secrets = make_secrets("user_a", "admin_a", "manager_a")
    login_id_key, _ = secret_key_names("user_b")
    secrets[login_id_key] = "only-login-id"

    accounts = account_by_alias(make_full_settings(), secrets)

    assert (accounts["user_b"].login_id, accounts["user_b"].password) == ("only-login-id", None)
    assert accounts["user_a"].password == SECRET_PASSWORD


def test_known_passwords_skip_missing() -> None:
    secrets = make_secrets("user_a")

    config = load_config(make_full_settings(), secrets)

    assert config.known_passwords == (SECRET_PASSWORD,)


@pytest.mark.parametrize("login_id", ["user_a", "USER_A", " user_a "])
def test_alias_equal_to_login_id_rejected(login_id: str) -> None:
    secrets = dict(ALL_SECRETS)
    login_id_key, _ = secret_key_names("user_a")
    secrets[login_id_key] = login_id

    with pytest.raises(ConfigError, match="accounts\\[0\\].alias") as error_info:
        load_config(make_full_settings(), secrets)

    assert SECRET_PASSWORD not in str(error_info.value)


@pytest.mark.parametrize(
    ("accounts", "message"),
    [
        pytest.param([{"alias": "User-A", "role": "user"}], "alias", id="alias_pattern"),
        pytest.param([{"alias": "guest", "role": "user"}], "alias", id="guest_alias_reserved"),
        pytest.param([{"alias": "user_a", "role": "user"}, {"alias": "user_a", "role": "user"}], "중복", id="duplicate_alias"),
        pytest.param([{"alias": "user_a", "role": "owner"}], "role", id="unknown_role"),
        pytest.param([{"alias": "user_a", "role": "user", "password": "x"}], "accounts\\[0\\].password", id="secret_in_toml"),
        pytest.param([{"alias": "user_a"}], "role", id="role_missing"),
    ],
)
def test_invalid_accounts_rejected(accounts: list[dict[str, Any]], message: str) -> None:
    settings = make_full_settings()
    settings["roles"] = ["user"]
    settings["accounts"] = accounts

    with pytest.raises(ConfigError, match=message):
        load_config(settings, ALL_SECRETS)


def test_role_without_account_rejected() -> None:
    settings = make_full_settings()
    settings["accounts"] = [account for account in settings["accounts"] if account["role"] != "manager"]

    with pytest.raises(ConfigError, match="계정이 없는 역할 \\(manager\\)"):
        load_config(settings, ALL_SECRETS)


@pytest.mark.parametrize(
    "roles",
    [
        pytest.param(["user", "guest"], id="guest_is_reserved"),
        pytest.param(["user", "user"], id="duplicate_role"),
        pytest.param(["user", "Admin"], id="uppercase_role"),
        pytest.param(["user", "super-admin"], id="hyphen_role"),
        pytest.param(["user", ""], id="empty_role_name"),
        pytest.param("user,admin", id="not_a_list"),
    ],
)
def test_invalid_role_names_rejected(roles: object) -> None:
    settings = make_full_settings()
    settings["roles"] = roles

    with pytest.raises(ConfigError, match="roles"):
        load_config(settings, ALL_SECRETS)


def test_undefined_keys_rejected() -> None:
    settings = make_full_settings()
    settings["max_dept"] = 2
    settings["login"]["success_chek"] = "left_login_page"

    with pytest.raises(ConfigError, match="max_dept"):
        load_config(settings, ALL_SECRETS)


def test_undefined_login_key_rejected() -> None:
    settings = make_full_settings()
    settings["login"]["success_chek"] = "left_login_page"

    with pytest.raises(ConfigError, match="login.success_chek"):
        load_config(settings, ALL_SECRETS)


# ---- 로그인 ----


def test_login_settings_come_from_config() -> None:
    login = load_config(make_full_settings(), ALL_SECRETS).login

    assert login is not None
    assert login.path == "/signin"
    assert login.username_field == "login_id"
    assert login.password_field == "login_pw"
    assert login.success_check is LoginSuccessCheck.LEFT_LOGIN_PAGE
    assert login.success_value is None


def test_all_missing_keys_reported_at_once() -> None:
    settings = make_full_settings()
    del settings["target_url"]
    settings["login"]["username_field"] = ""
    del settings["login"]["success_check"]

    with pytest.raises(ConfigError) as error_info:
        load_config(settings, ALL_SECRETS)

    message = str(error_info.value)
    assert "target_url" in message
    assert "login.username_field" in message
    assert "login.success_check" in message
    assert SECRET_PASSWORD not in message


def test_empty_settings_report_target_url_missing() -> None:
    with pytest.raises(ConfigError, match="target_url"):
        load_config({})


def test_login_required_when_accounts_exist() -> None:
    settings = make_full_settings()
    del settings["login"]

    with pytest.raises(ConfigError, match="login"):
        load_config(settings, ALL_SECRETS)


def test_unknown_success_check_rejected() -> None:
    settings = make_full_settings()
    settings["login"]["success_check"] = "magic"

    with pytest.raises(ConfigError, match="login.success_check"):
        load_config(settings, ALL_SECRETS)


@pytest.mark.parametrize("check", ["url_contains", "cookie_present", "text_present"])
def test_success_value_required_for_checks_that_compare(check: str) -> None:
    settings = make_full_settings()
    settings["login"]["success_check"] = check

    with pytest.raises(ConfigError, match="login.success_value"):
        load_config(settings, ALL_SECRETS)


def test_success_value_kept_for_cookie_check() -> None:
    settings = make_full_settings()
    settings["login"]["success_check"] = "cookie_present"
    settings["login"]["success_value"] = "sessionid"

    login = load_config(settings, ALL_SECRETS).login

    assert login is not None
    assert login.success_check is LoginSuccessCheck.COOKIE_PRESENT
    assert login.success_value == "sessionid"


@pytest.mark.parametrize(
    "login_path",
    [
        pytest.param("login", id="no_leading_slash"),
        pytest.param("http://localhost:8001/login", id="absolute_url"),
        pytest.param("//evil.example/login", id="scheme_relative_url"),
    ],
)
def test_login_path_must_be_path(login_path: str) -> None:
    settings = make_full_settings()
    settings["login"]["path"] = login_path

    with pytest.raises(ConfigError, match="login.path"):
        load_config(settings, ALL_SECRETS)


# ---- 탐색 제한 ----


def test_max_depth_from_config() -> None:
    settings = make_guest_only_settings()
    settings["max_depth"] = 0

    assert load_config(settings).max_depth == 0


@pytest.mark.parametrize("depth_value", [-1, "2", 1.5, True])
def test_invalid_max_depth_rejected(depth_value: object) -> None:
    settings = make_guest_only_settings()
    settings["max_depth"] = depth_value

    with pytest.raises(ConfigError, match="max_depth"):
        load_config(settings)


@pytest.mark.parametrize("raw_value", [True, False])
def test_allow_state_changing_parsed(raw_value: bool) -> None:
    settings = make_guest_only_settings()
    settings["allow_state_changing"] = raw_value

    assert load_config(settings).can_change_state is raw_value


@pytest.mark.parametrize("raw_value", ["true", "yes", 1, "on"])
def test_allow_state_changing_rejects_ambiguous_values(raw_value: object) -> None:
    settings = make_guest_only_settings()
    settings["allow_state_changing"] = raw_value

    with pytest.raises(ConfigError, match="allow_state_changing"):
        load_config(settings)


def test_state_changing_keywords_default() -> None:
    config = load_config(make_guest_only_settings())

    assert config.state_changing_keywords == DEFAULT_STATE_CHANGING_KEYWORDS
    assert "logout" in config.state_changing_keywords


def test_state_changing_keywords_overridden_and_lowered() -> None:
    settings = make_guest_only_settings()
    settings["state_changing_keywords"] = [" Purge ", "취소", "", "wipe"]

    assert load_config(settings).state_changing_keywords == ("purge", "취소", "wipe")


# ---- 허용 origin (절대 규칙 1) ----


@pytest.mark.parametrize(
    "target_url",
    [
        pytest.param("http://example.com", id="public_host"),
        pytest.param("http://192.168.0.10:8000", id="private_lan_host"),
        pytest.param("localhost:8001", id="no_scheme"),
        pytest.param("ftp://localhost", id="non_http_scheme"),
        pytest.param("http://localhost:abc", id="invalid_port"),
    ],
)
def test_target_url_must_be_loopback_http(target_url: str) -> None:
    settings = make_guest_only_settings()
    settings["target_url"] = target_url

    with pytest.raises(ConfigError, match="target_url"):
        load_config(settings)


def test_extra_origin_must_be_loopback() -> None:
    settings = make_guest_only_settings()
    settings["extra_allowed_origins"] = ["http://127.0.0.1:8001", "https://example.com"]

    with pytest.raises(ConfigError, match="extra_allowed_origins"):
        load_config(settings)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("http://localhost:8001/orders/3?tab=items", True, id="same_origin"),
        pytest.param("http://LOCALHOST:8001/", True, id="host_case_insensitive"),
        pytest.param("http://localhost:8001", True, id="no_path"),
        pytest.param("http://localhost:8002/", False, id="other_port"),
        pytest.param("https://localhost:8001/", False, id="other_scheme"),
        pytest.param("http://127.0.0.1:8001/", False, id="other_loopback_name"),
        pytest.param("http://example.com/", False, id="external_host"),
        pytest.param("http://localhost:8001@evil.example/", False, id="userinfo_trick"),
        pytest.param("http://localhost.evil.example:8001/", False, id="subdomain_trick"),
        pytest.param("data:text/html,hi", False, id="data_scheme"),
        pytest.param("about:blank", False, id="about_scheme"),
        pytest.param("/orders/3", False, id="relative_path"),
        pytest.param("http://localhost:abc/", False, id="invalid_port"),
    ],
)
def test_is_request_allowed(url: str, expected: bool) -> None:
    config = load_config(make_guest_only_settings())

    assert is_request_allowed(config, url) is expected


def test_default_port_treated_same_as_explicit() -> None:
    settings = make_guest_only_settings()
    settings["target_url"] = "http://localhost/"
    config = load_config(settings)

    assert is_request_allowed(config, "http://localhost:80/page") is True


def test_extra_origin_allowed() -> None:
    settings = make_guest_only_settings()
    settings["extra_allowed_origins"] = ["http://127.0.0.1:8001"]
    config = load_config(settings)

    assert is_request_allowed(config, "http://127.0.0.1:8001/api") is True


def test_secrets_not_in_repr() -> None:
    config = load_config(make_full_settings(), ALL_SECRETS)

    assert SECRET_PASSWORD not in repr(config)
    assert SECRET_LOGIN_ID not in repr(config)


# ---- 파일 읽기 ----

CONFIG_TOML = f"""target_url = "{TARGET_URL}"
max_depth = 2
roles = ["user"]

[login]
path = "/signin"
username_field = "login_id"
password_field = "login_pw"
success_check = "left_login_page"

[[accounts]]
alias = "user_a"
role = "user"
"""


@pytest.fixture
def clean_secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in secret_key_names("user_a"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.usefixtures("clean_secret_env")
def test_load_config_files(tmp_path: Path) -> None:
    config_path = tmp_path / "collector.toml"
    config_path.write_text(CONFIG_TOML, encoding="utf-8")
    secrets_path = tmp_path / ".env"
    login_id_key, password_key = secret_key_names("user_a")
    secrets_path.write_text(f"{login_id_key}=alice\n{password_key}={SECRET_PASSWORD}\n", encoding="utf-8")

    config = load_config_files(config_path, secrets_path)

    assert (config.start_url, config.max_depth) == (TARGET_URL, 2)
    user = config.accounts[1]
    assert (user.alias, user.login_id, user.password) == ("user_a", "alice", SECRET_PASSWORD)


@pytest.mark.usefixtures("clean_secret_env")
def test_process_env_overrides_secrets_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "collector.toml"
    config_path.write_text(CONFIG_TOML, encoding="utf-8")
    login_id_key, password_key = secret_key_names("user_a")
    (tmp_path / ".env").write_text(f"{login_id_key}=alice\n{password_key}=from-file\n", encoding="utf-8")
    monkeypatch.setenv(password_key, "from-process")

    assert load_config_files(config_path, tmp_path / ".env").accounts[1].password == "from-process"


@pytest.mark.usefixtures("clean_secret_env")
def test_missing_secrets_file_is_not_config_error(tmp_path: Path) -> None:
    config_path = tmp_path / "collector.toml"
    config_path.write_text(CONFIG_TOML, encoding="utf-8")

    config = load_config_files(config_path, tmp_path / "missing.env")

    assert (config.accounts[1].login_id, config.accounts[1].password) == (None, None)


def test_missing_config_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="설정 파일이 없음"):
        load_config_files(tmp_path / "missing.toml", tmp_path / ".env")


def test_broken_toml_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "collector.toml"
    config_path.write_text('target_url = "http://localhost:8001\nroles = [', encoding="utf-8")

    with pytest.raises(ConfigError, match="설정 파일 형식 오류"):
        load_config_files(config_path, tmp_path / ".env")
