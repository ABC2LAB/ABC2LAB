from pathlib import Path

import pytest

from modules.collector.core.config import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_STATE_CHANGING_KEYWORDS,
    GUEST_ROLE,
    ConfigError,
    LoginSuccessCheck,
    is_request_allowed,
    load_config,
    load_config_from_file,
)

TARGET_URL = "http://localhost:8001"
SECRET_PASSWORD = "s3cret-value-do-not-log"


def make_guest_only_env() -> dict[str, str]:
    return {"CRAWLER_TARGET_URL": TARGET_URL}


def make_full_env() -> dict[str, str]:
    return {
        "CRAWLER_TARGET_URL": TARGET_URL,
        "CRAWLER_ROLES": "user,admin,manager",
        "CRAWLER_ROLE_USER_USERNAME": "alice",
        "CRAWLER_ROLE_USER_PASSWORD": SECRET_PASSWORD,
        "CRAWLER_ROLE_ADMIN_USERNAME": "root",
        "CRAWLER_ROLE_ADMIN_PASSWORD": SECRET_PASSWORD,
        "CRAWLER_ROLE_MANAGER_USERNAME": "bob",
        "CRAWLER_ROLE_MANAGER_PASSWORD": SECRET_PASSWORD,
        "CRAWLER_LOGIN_PATH": "/signin",
        "CRAWLER_LOGIN_USERNAME_FIELD": "login_id",
        "CRAWLER_LOGIN_PASSWORD_FIELD": "login_pw",
        "CRAWLER_LOGIN_SUCCESS_CHECK": "left_login_page",
    }


def test_guest_only_config_needs_only_target_url() -> None:
    config = load_config(make_guest_only_env())

    assert config.start_url == TARGET_URL
    assert config.roles == (GUEST_ROLE,)
    assert config.accounts == {}
    assert config.login is None
    assert config.max_depth == DEFAULT_MAX_DEPTH
    assert config.can_change_state is False


def test_roles_are_not_fixed_in_count() -> None:
    config = load_config(make_full_env())

    assert config.roles == (GUEST_ROLE, "user", "admin", "manager")
    assert set(config.accounts) == {"user", "admin", "manager"}
    assert config.accounts["manager"].username == "bob"
    assert config.accounts["manager"].password == SECRET_PASSWORD


def test_login_settings_come_from_env() -> None:
    login = load_config(make_full_env()).login

    assert login is not None
    assert login.path == "/signin"
    assert login.username_field == "login_id"
    assert login.password_field == "login_pw"
    assert login.success_check is LoginSuccessCheck.LEFT_LOGIN_PAGE
    assert login.success_value is None


def test_all_missing_keys_reported_at_once() -> None:
    env = make_full_env()
    del env["CRAWLER_TARGET_URL"]
    del env["CRAWLER_ROLE_ADMIN_PASSWORD"]
    env["CRAWLER_LOGIN_USERNAME_FIELD"] = ""

    with pytest.raises(ConfigError) as error_info:
        load_config(env)

    message = str(error_info.value)
    assert "CRAWLER_TARGET_URL" in message
    assert "CRAWLER_ROLE_ADMIN_PASSWORD" in message
    assert "CRAWLER_LOGIN_USERNAME_FIELD" in message
    assert SECRET_PASSWORD not in message


def test_empty_env_reports_target_url_missing() -> None:
    with pytest.raises(ConfigError, match="CRAWLER_TARGET_URL"):
        load_config({})


def test_login_keys_required_when_account_role_exists() -> None:
    env = make_guest_only_env()
    env["CRAWLER_ROLES"] = "user"
    env["CRAWLER_ROLE_USER_USERNAME"] = "alice"
    env["CRAWLER_ROLE_USER_PASSWORD"] = SECRET_PASSWORD

    with pytest.raises(ConfigError) as error_info:
        load_config(env)

    message = str(error_info.value)
    for key in (
        "CRAWLER_LOGIN_PATH",
        "CRAWLER_LOGIN_USERNAME_FIELD",
        "CRAWLER_LOGIN_PASSWORD_FIELD",
        "CRAWLER_LOGIN_SUCCESS_CHECK",
    ):
        assert key in message


@pytest.mark.parametrize(
    "roles_value",
    [
        pytest.param("user,guest", id="guest_is_reserved"),
        pytest.param("user,user", id="duplicate_role"),
        pytest.param("user,Admin", id="uppercase_role"),
        pytest.param("user,super-admin", id="hyphen_role"),
        pytest.param("user,,admin", id="empty_role_name"),
    ],
)
def test_invalid_role_names_rejected(roles_value: str) -> None:
    env = make_full_env()
    env["CRAWLER_ROLES"] = roles_value

    with pytest.raises(ConfigError, match="CRAWLER_ROLES"):
        load_config(env)


def test_roles_value_whitespace_trimmed() -> None:
    env = make_full_env()
    env["CRAWLER_ROLES"] = " user , admin , manager "

    assert load_config(env).roles == (GUEST_ROLE, "user", "admin", "manager")


def test_unknown_success_check_rejected() -> None:
    env = make_full_env()
    env["CRAWLER_LOGIN_SUCCESS_CHECK"] = "magic"

    with pytest.raises(ConfigError, match="CRAWLER_LOGIN_SUCCESS_CHECK"):
        load_config(env)


@pytest.mark.parametrize(
    "check",
    ["url_contains", "cookie_present", "text_present"],
)
def test_success_value_required_for_checks_that_compare(check: str) -> None:
    env = make_full_env()
    env["CRAWLER_LOGIN_SUCCESS_CHECK"] = check

    with pytest.raises(ConfigError, match="CRAWLER_LOGIN_SUCCESS_VALUE"):
        load_config(env)


def test_success_value_kept_for_cookie_check() -> None:
    env = make_full_env()
    env["CRAWLER_LOGIN_SUCCESS_CHECK"] = "cookie_present"
    env["CRAWLER_LOGIN_SUCCESS_VALUE"] = "sessionid"

    login = load_config(env).login

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
    env = make_full_env()
    env["CRAWLER_LOGIN_PATH"] = login_path

    with pytest.raises(ConfigError, match="CRAWLER_LOGIN_PATH"):
        load_config(env)


def test_max_depth_from_env() -> None:
    env = make_guest_only_env()
    env["CRAWLER_MAX_DEPTH"] = "0"

    assert load_config(env).max_depth == 0


@pytest.mark.parametrize("depth_value", ["-1", "abc", "1.5"])
def test_invalid_max_depth_rejected(depth_value: str) -> None:
    env = make_guest_only_env()
    env["CRAWLER_MAX_DEPTH"] = depth_value

    with pytest.raises(ConfigError, match="CRAWLER_MAX_DEPTH"):
        load_config(env)


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [("true", True), ("TRUE", True), ("false", False), ("False", False)],
)
def test_allow_state_changing_parsed(raw_value: str, expected: bool) -> None:
    env = make_guest_only_env()
    env["CRAWLER_ALLOW_STATE_CHANGING"] = raw_value

    assert load_config(env).can_change_state is expected


def test_state_changing_keywords_default() -> None:
    config = load_config(make_guest_only_env())

    assert config.state_changing_keywords == DEFAULT_STATE_CHANGING_KEYWORDS
    assert "logout" in config.state_changing_keywords


def test_state_changing_keywords_overridden_and_lowered() -> None:
    env = make_guest_only_env()
    env["CRAWLER_STATE_CHANGING_KEYWORDS"] = " Purge , 취소,,wipe "

    assert load_config(env).state_changing_keywords == ("purge", "취소", "wipe")


@pytest.mark.parametrize("raw_value", ["yes", "1", "on", "tru"])
def test_allow_state_changing_rejects_ambiguous_values(raw_value: str) -> None:
    env = make_guest_only_env()
    env["CRAWLER_ALLOW_STATE_CHANGING"] = raw_value

    with pytest.raises(ConfigError, match="CRAWLER_ALLOW_STATE_CHANGING"):
        load_config(env)


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
    env = make_guest_only_env()
    env["CRAWLER_TARGET_URL"] = target_url

    with pytest.raises(ConfigError, match="CRAWLER_TARGET_URL"):
        load_config(env)


def test_extra_origin_must_be_loopback() -> None:
    env = make_guest_only_env()
    env["CRAWLER_EXTRA_ALLOWED_ORIGINS"] = "http://127.0.0.1:8001,https://example.com"

    with pytest.raises(ConfigError, match="CRAWLER_EXTRA_ALLOWED_ORIGINS"):
        load_config(env)


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
    config = load_config(make_guest_only_env())

    assert is_request_allowed(config, url) is expected


def test_default_port_treated_same_as_explicit() -> None:
    env = make_guest_only_env()
    env["CRAWLER_TARGET_URL"] = "http://localhost/"
    config = load_config(env)

    assert is_request_allowed(config, "http://localhost:80/page") is True


def test_extra_origin_allowed() -> None:
    env = make_guest_only_env()
    env["CRAWLER_EXTRA_ALLOWED_ORIGINS"] = "http://127.0.0.1:8001"
    config = load_config(env)

    assert is_request_allowed(config, "http://127.0.0.1:8001/api") is True


def test_password_not_in_repr() -> None:
    config = load_config(make_full_env())

    assert SECRET_PASSWORD not in repr(config)


def test_load_config_from_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRAWLER_TARGET_URL", raising=False)
    monkeypatch.delenv("CRAWLER_MAX_DEPTH", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"CRAWLER_TARGET_URL={TARGET_URL}\nCRAWLER_MAX_DEPTH=2\n", encoding="utf-8"
    )

    config = load_config_from_file(env_path)

    assert config.start_url == TARGET_URL
    assert config.max_depth == 2


def test_process_env_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(f"CRAWLER_TARGET_URL={TARGET_URL}\n", encoding="utf-8")
    monkeypatch.setenv("CRAWLER_MAX_DEPTH", "5")

    assert load_config_from_file(env_path).max_depth == 5


def test_missing_env_file_reports_missing_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CRAWLER_TARGET_URL", raising=False)

    with pytest.raises(ConfigError, match="CRAWLER_TARGET_URL"):
        load_config_from_file(tmp_path / "does-not-exist.env")
