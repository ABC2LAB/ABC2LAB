"""민감 키 판정 규칙(capture 모듈 함수). 리터럴 이름으로 규칙 하나씩 본다."""

import pytest

from modules.collector.core.capture import is_sensitive_key, list_sensitive_field_names, normalize_key_name
from modules.collector.core.config import CrawlerConfig, load_config, secret_key_names

ALIAS = "user_a"


def _config(password_field: str) -> CrawlerConfig:
    login_id_key, password_key = secret_key_names(ALIAS)
    settings = {
        "target_url": "http://127.0.0.1:8001",
        "roles": ["user"],
        "login": {
            "path": "/signin", "username_field": "login_id", "password_field": password_field, "success_check": "left_login_page",
        },
        "accounts": [{"alias": ALIAS, "role": "user"}],
    }
    return load_config(settings, {login_id_key: "alice", password_key: "pw-value"})


@pytest.mark.parametrize("name", ["password", "user_password", "X-CSRF-Token", "session_id", "api-key", "OTP"])
def test_name_with_sensitive_part_is_sensitive(name: str) -> None:
    assert is_sensitive_key(name)


@pytest.mark.parametrize("name", ["name", "price", "product_id", "quantity", "email"])
def test_plain_name_is_not_sensitive(name: str) -> None:
    assert not is_sensitive_key(name)


def test_configured_password_field_is_sensitive_even_without_sensitive_part() -> None:
    names = list_sensitive_field_names(_config("login_pw"))
    assert is_sensitive_key("login_pw", names)
    assert not is_sensitive_key("login_pw")  # 설정을 모르면 조각 규칙만 본다


def test_configured_name_matches_in_the_same_normalized_form() -> None:
    # 설정 이름에 -·대문자가 있어도 키와 같은 형태로 바꿔 비교한다(예전에는 설정 쪽만 -를 그대로 둬서 놓쳤다).
    names = list_sensitive_field_names(_config("User-Pin"))
    assert names == frozenset({normalize_key_name("User-Pin")})
    assert is_sensitive_key("user-pin", names) and is_sensitive_key("USER_PIN", names)


def test_no_login_means_no_configured_names() -> None:
    # 로그인 계정 없이 비로그인만 도는 설정
    assert list_sensitive_field_names(load_config({"target_url": "http://127.0.0.1:8001", "roles": []})) == frozenset()
