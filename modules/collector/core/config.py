"""collector 설정을 읽고, 요청해도 되는 URL인지 판단한다.

특정 앱의 URL·계정·폼 필드명은 전부 설정으로 받는다. 역할·계정 수는 고정하지 않는다.
- 설정 파일(TOML, 비밀 아님): 대상 URL·허용 origin·탐색 제한·로그인 폼·역할 목록·계정 별칭↔역할
- 비밀값(.env 또는 환경변수): 계정 별칭마다 COLLECTOR_ACCOUNT_<별칭 대문자>_LOGIN_ID / _PASSWORD
계정 별칭(alias)은 crawl_result에 그대로 나가므로 로그인 ID와 같으면 거절한다.
익명 탐색(guest)은 설정하지 않아도 늘 첫 번째 계정으로 들어간다.
"""

import logging
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import dotenv_values

logger = logging.getLogger(__name__)

TARGET_URL_KEY = "target_url"
EXTRA_ALLOWED_ORIGINS_KEY = "extra_allowed_origins"
MAX_DEPTH_KEY = "max_depth"
ALLOW_STATE_CHANGING_KEY = "allow_state_changing"
STATE_CHANGING_KEYWORDS_KEY = "state_changing_keywords"
ROLES_KEY = "roles"
ACCOUNTS_KEY = "accounts"
LOGIN_KEY = "login"
TOP_LEVEL_KEYS = frozenset(
    {
        TARGET_URL_KEY,
        EXTRA_ALLOWED_ORIGINS_KEY,
        MAX_DEPTH_KEY,
        ALLOW_STATE_CHANGING_KEY,
        STATE_CHANGING_KEYWORDS_KEY,
        ROLES_KEY,
        ACCOUNTS_KEY,
        LOGIN_KEY,
    }
)
LOGIN_FIELD_KEYS = ("path", "username_field", "password_field", "success_check")
LOGIN_KEYS = frozenset({*LOGIN_FIELD_KEYS, "success_value"})
ACCOUNT_KEYS = frozenset({"alias", "role"})
SECRET_KEY_PREFIX = "COLLECTOR_ACCOUNT_"
LOGIN_ID_SUFFIX = "_LOGIN_ID"
PASSWORD_SUFFIX = "_PASSWORD"

GUEST_ROLE = "guest"
ACCOUNT_ID_FORMAT = "account:{}"
DEFAULT_MAX_DEPTH = 3
# GET 로그아웃 링크처럼 메서드만으론 못 거르는 상태 변경 동작을 이름으로 잡는다. 특정 앱 URL이 아닌 일반 단어.
DEFAULT_STATE_CHANGING_KEYWORDS = (
    "logout",
    "log-out",
    "signout",
    "sign-out",
    "delete",
    "remove",
    "destroy",
    "로그아웃",
    "삭제",
    "탈퇴",
)
# 역할·별칭은 ID와 환경변수 키 일부가 되므로 소문자로 시작하는 소문자·숫자·_만 허용한다.
NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
ALLOWED_SCHEMES = {"http": 80, "https": 443}
# 절대 규칙 1번: 요청 대상은 로컬 대상 앱뿐이다. 설정 실수로 외부를 두드리지 않게 로드 단계에서 막는다.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ConfigError(ValueError):
    """설정이 빠졌거나 형식이 틀렸다. 메시지에는 키 이름만 넣고 값(비밀번호 등)은 넣지 않는다."""


class LoginSuccessCheck(Enum):
    LEFT_LOGIN_PAGE = "left_login_page"
    URL_CONTAINS = "url_contains"
    COOKIE_PRESENT = "cookie_present"
    TEXT_PRESENT = "text_present"

    @property
    def needs_value(self) -> bool:
        return self is not LoginSuccessCheck.LEFT_LOGIN_PAGE


@dataclass(frozen=True)
class Origin:
    scheme: str
    host: str
    port: int


@dataclass(frozen=True)
class AccountSettings:
    """탐색할 계정 하나. guest는 로그인 ID·비밀번호가 없다. 로그인 계정도 비밀값이 빠지면 None(그 계정만 실패)."""

    alias: str
    role: str
    login_id: str | None = field(repr=False)
    password: str | None = field(repr=False)

    @property
    def account_id(self) -> str:
        return ACCOUNT_ID_FORMAT.format(self.alias)

    @property
    def is_guest(self) -> bool:
        return self.role == GUEST_ROLE


GUEST_ACCOUNT = AccountSettings(alias=GUEST_ROLE, role=GUEST_ROLE, login_id=None, password=None)


@dataclass(frozen=True)
class LoginSettings:
    path: str
    username_field: str
    password_field: str
    success_check: LoginSuccessCheck
    success_value: str | None


@dataclass(frozen=True)
class CrawlerConfig:
    start_url: str
    allowed_origins: frozenset[Origin]
    # guest가 먼저, 나머지는 설정 순서
    roles: tuple[str, ...]
    accounts: tuple[AccountSettings, ...]
    login: LoginSettings | None
    max_depth: int
    can_change_state: bool
    state_changing_keywords: tuple[str, ...]

    @property
    def known_passwords(self) -> tuple[str, ...]:
        """출력·로그에서 지울 비밀값."""
        return tuple(account.password for account in self.accounts if account.password)


def load_config_files(config_path: Path, secrets_path: Path) -> CrawlerConfig:
    """설정 TOML과 비밀값 .env를 읽는다. 같은 이름의 COLLECTOR_ACCOUNT_* 환경변수가 있으면 그 값이 우선한다."""
    if not config_path.is_file():
        raise ConfigError(f"설정 파일이 없음: {config_path}")
    try:
        settings = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ConfigError(f"설정 파일 형식 오류 ({config_path.name}): {error}") from None
    if not secrets_path.is_file():
        logger.warning("비밀값 파일이 없음: %s (환경변수만 사용)", secrets_path)
    secrets = {key: value for key, value in dotenv_values(secrets_path).items() if value is not None}
    secrets.update({key: value for key, value in os.environ.items() if key.startswith(SECRET_KEY_PREFIX)})
    return load_config(settings, secrets)


def load_config(settings: Mapping[str, Any], secrets: Mapping[str, str] | None = None) -> CrawlerConfig:
    """TOML을 읽은 dict와 비밀값에서 설정을 만든다. 빠진 키는 한 번에 모아 ConfigError로 알린다."""
    _check_keys(settings, TOP_LEVEL_KEYS, "")
    roles = _parse_roles(settings.get(ROLES_KEY, []))
    login_settings = settings.get(LOGIN_KEY)
    accounts = _parse_accounts(settings.get(ACCOUNTS_KEY, []), roles, secrets or {})
    _check_required(settings, login_settings, has_accounts=bool(accounts))

    start_url = settings[TARGET_URL_KEY].strip()
    allowed_origins = {_parse_allowed_origin(start_url, TARGET_URL_KEY)}
    for extra_url in _parse_string_list(settings.get(EXTRA_ALLOWED_ORIGINS_KEY, []), EXTRA_ALLOWED_ORIGINS_KEY):
        allowed_origins.add(_parse_allowed_origin(extra_url, EXTRA_ALLOWED_ORIGINS_KEY))
    return CrawlerConfig(
        start_url=start_url,
        allowed_origins=frozenset(allowed_origins),
        roles=(GUEST_ROLE, *roles),
        accounts=(GUEST_ACCOUNT, *accounts),
        login=_parse_login(login_settings) if accounts else None,
        max_depth=_parse_max_depth(settings.get(MAX_DEPTH_KEY, DEFAULT_MAX_DEPTH)),
        can_change_state=_parse_bool(settings.get(ALLOW_STATE_CHANGING_KEY, False), ALLOW_STATE_CHANGING_KEY),
        state_changing_keywords=_parse_keywords(settings.get(STATE_CHANGING_KEYWORDS_KEY)),
    )


def secret_key_names(alias: str) -> tuple[str, str]:
    """계정 별칭의 로그인 ID·비밀번호 환경변수 이름."""
    prefix = f"{SECRET_KEY_PREFIX}{alias.upper()}"
    return f"{prefix}{LOGIN_ID_SUFFIX}", f"{prefix}{PASSWORD_SUFFIX}"


def is_request_allowed(config: CrawlerConfig, url: str) -> bool:
    """절대 규칙 1번 판단: url의 origin이 허용 목록과 정확히 같을 때만 True."""
    return _parse_origin(url) in config.allowed_origins


def _check_keys(table: Mapping[str, Any], allowed_keys: frozenset[str], prefix: str) -> None:
    unknown_keys = sorted(f"{prefix}{key}" for key in table if key not in allowed_keys)
    if unknown_keys:
        raise ConfigError(f"정의되지 않은 설정 키: {', '.join(unknown_keys)}")


def _check_required(settings: Mapping[str, Any], login_settings: object, has_accounts: bool) -> None:
    missing_keys = [] if _is_filled(settings.get(TARGET_URL_KEY)) else [TARGET_URL_KEY]
    if has_accounts:
        if not isinstance(login_settings, Mapping):
            missing_keys.append(LOGIN_KEY)
        else:
            missing_keys.extend(
                f"{LOGIN_KEY}.{key}" for key in LOGIN_FIELD_KEYS if not _is_filled(login_settings.get(key))
            )
    if missing_keys:
        raise ConfigError(f"설정에 빠진 키: {', '.join(missing_keys)}")


def _is_filled(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_string_list(value: object, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{key}: 문자열 목록이어야 함")
    return [item.strip() for item in value]


def _parse_roles(value: object) -> tuple[str, ...]:
    role_names = _parse_string_list(value, ROLES_KEY)
    for name in role_names:
        if not NAME_PATTERN.fullmatch(name):
            raise ConfigError(f"{ROLES_KEY}: 역할 이름은 소문자로 시작하는 소문자·숫자·_만 가능 ({name!r})")
        if name == GUEST_ROLE:
            raise ConfigError(f"{ROLES_KEY}: {GUEST_ROLE}는 계정 없는 기본 역할이라 적지 않음")
    if len(set(role_names)) != len(role_names):
        raise ConfigError(f"{ROLES_KEY}: 역할 이름이 중복됨")
    return tuple(role_names)


def _parse_accounts(value: object, roles: tuple[str, ...], secrets: Mapping[str, str]) -> tuple[AccountSettings, ...]:
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ConfigError(f"{ACCOUNTS_KEY}: [[accounts]] 표 목록이어야 함")
    accounts = tuple(_parse_account(index, item, roles, secrets) for index, item in enumerate(value))
    aliases = [account.alias for account in accounts]
    if len(set(aliases)) != len(aliases):
        raise ConfigError(f"{ACCOUNTS_KEY}: 계정 별칭이 중복됨")
    roles_without_account = [role for role in roles if role not in {account.role for account in accounts}]
    if roles_without_account:
        raise ConfigError(f"{ROLES_KEY}: 계정이 없는 역할 ({', '.join(roles_without_account)})")
    return accounts


def _parse_account(
    index: int, item: Mapping[str, Any], roles: tuple[str, ...], secrets: Mapping[str, str]
) -> AccountSettings:
    location = f"{ACCOUNTS_KEY}[{index}]"
    _check_keys(item, ACCOUNT_KEYS, f"{location}.")
    alias, role = item.get("alias"), item.get("role")
    if not isinstance(alias, str) or not NAME_PATTERN.fullmatch(alias) or alias == GUEST_ROLE:
        raise ConfigError(f"{location}.alias: 소문자로 시작하는 소문자·숫자·_이고 {GUEST_ROLE}가 아니어야 함")
    if role not in roles:
        raise ConfigError(f"{location}.role: {ROLES_KEY}에 있는 역할이어야 함")
    login_id_key, password_key = secret_key_names(alias)
    login_id = _read_secret(secrets, login_id_key)
    if login_id is not None and login_id.lower() == alias.lower():
        # 별칭은 공개 JSON에 그대로 나간다. 같으면 로그인 ID가 새어 나간다.
        raise ConfigError(f"{location}.alias: 로그인 ID와 같으면 안 됨 ({login_id_key})")
    return AccountSettings(alias=alias, role=role, login_id=login_id, password=_read_secret(secrets, password_key))


def _read_secret(secrets: Mapping[str, str], key: str) -> str | None:
    value = secrets.get(key, "").strip()
    return value or None


def _parse_login(login_settings: Mapping[str, Any]) -> LoginSettings:
    _check_keys(login_settings, LOGIN_KEYS, f"{LOGIN_KEY}.")
    path = login_settings["path"]
    # 절대 URL이나 //host 형태를 받으면 로그인 요청이 대상 밖으로 나갈 수 있다.
    if not path.startswith("/") or path.startswith("//"):
        raise ConfigError(f"{LOGIN_KEY}.path: /로 시작하는 경로여야 함")
    try:
        success_check = LoginSuccessCheck(login_settings["success_check"])
    except ValueError as error:
        choices = ", ".join(check.value for check in LoginSuccessCheck)
        raise ConfigError(f"{LOGIN_KEY}.success_check: {choices} 중 하나여야 함") from error
    success_value = login_settings.get("success_value")
    if success_check.needs_value and not _is_filled(success_value):
        raise ConfigError(f"설정에 빠진 키: {LOGIN_KEY}.success_value")
    return LoginSettings(
        path=path,
        username_field=login_settings["username_field"].strip(),
        password_field=login_settings["password_field"].strip(),
        success_check=success_check,
        success_value=success_value.strip() if success_check.needs_value else None,
    )


def _parse_origin(url: str) -> Origin | None:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    return Origin(scheme, parts.hostname.lower(), port or ALLOWED_SCHEMES[scheme])


def _parse_allowed_origin(url: str, key: str) -> Origin:
    origin = _parse_origin(url)
    if origin is None:
        raise ConfigError(f"{key}: http(s)://호스트[:포트] 형식이어야 함 ({url!r})")
    if origin.host not in LOOPBACK_HOSTS:
        raise ConfigError(f"{key}: 로컬 호스트({', '.join(sorted(LOOPBACK_HOSTS))})만 허용 ({url!r})")
    return origin


def _parse_max_depth(value: object) -> int:
    # bool은 int의 하위 클래스라 따로 막는다.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"{MAX_DEPTH_KEY}: 0 이상의 정수여야 함")
    return value


def _parse_bool(value: object, key: str) -> bool:
    # 상태 변경 허용 같은 위험한 스위치라 "yes"·1 같은 모호한 값은 받지 않는다.
    if not isinstance(value, bool):
        raise ConfigError(f"{key}: true 또는 false여야 함")
    return value


def _parse_keywords(value: object) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_STATE_CHANGING_KEYWORDS
    return tuple(keyword.lower() for keyword in _parse_string_list(value, STATE_CHANGING_KEYWORDS_KEY) if keyword)
