"""크롤러 설정을 .env에서 읽고, 요청해도 되는 URL인지 판단한다.

특정 앱의 URL·계정·폼 필드명은 전부 설정으로 받는다. 역할 수는 고정하지 않는다.
"""

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit

from dotenv import dotenv_values

logger = logging.getLogger(__name__)

ENV_PREFIX = "CRAWLER_"
TARGET_URL_KEY = "CRAWLER_TARGET_URL"
EXTRA_ALLOWED_ORIGINS_KEY = "CRAWLER_EXTRA_ALLOWED_ORIGINS"
ROLES_KEY = "CRAWLER_ROLES"
LOGIN_PATH_KEY = "CRAWLER_LOGIN_PATH"
LOGIN_USERNAME_FIELD_KEY = "CRAWLER_LOGIN_USERNAME_FIELD"
LOGIN_PASSWORD_FIELD_KEY = "CRAWLER_LOGIN_PASSWORD_FIELD"
LOGIN_SUCCESS_CHECK_KEY = "CRAWLER_LOGIN_SUCCESS_CHECK"
LOGIN_SUCCESS_VALUE_KEY = "CRAWLER_LOGIN_SUCCESS_VALUE"
MAX_DEPTH_KEY = "CRAWLER_MAX_DEPTH"
ALLOW_STATE_CHANGING_KEY = "CRAWLER_ALLOW_STATE_CHANGING"
STATE_CHANGING_KEYWORDS_KEY = "CRAWLER_STATE_CHANGING_KEYWORDS"
LOGIN_KEYS = (
    LOGIN_PATH_KEY,
    LOGIN_USERNAME_FIELD_KEY,
    LOGIN_PASSWORD_FIELD_KEY,
    LOGIN_SUCCESS_CHECK_KEY,
)

GUEST_ROLE = "guest"
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
LIST_SEPARATOR = ","
# 역할 이름이 환경변수 키 일부가 되므로 키에 쓸 수 있는 문자만 허용한다.
ROLE_NAME_PATTERN = re.compile(r"[a-z0-9_]+")
ALLOWED_SCHEMES = {"http": 80, "https": 443}
# 절대 규칙 1번: 요청 대상은 로컬 대상 앱뿐이다. 설정 실수로 외부를 두드리지 않게 로드 단계에서 막는다.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
TRUE_TEXT = "true"
FALSE_TEXT = "false"


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
class RoleAccount:
    role: str
    username: str
    password: str = field(repr=False)


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
    roles: tuple[str, ...]
    accounts: Mapping[str, RoleAccount]
    login: LoginSettings | None
    max_depth: int
    can_change_state: bool
    state_changing_keywords: tuple[str, ...]


def load_config_from_file(env_path: Path = Path(".env")) -> CrawlerConfig:
    """.env 파일을 읽고, 같은 이름의 CRAWLER_* 프로세스 환경변수가 있으면 그 값을 우선한다."""
    if not env_path.is_file():
        logger.warning("설정 파일이 없음: %s", env_path)
    env = {key: value for key, value in dotenv_values(env_path).items() if value is not None}
    env.update({key: value for key, value in os.environ.items() if key.startswith(ENV_PREFIX)})
    return load_config(env)


def load_config(env: Mapping[str, str]) -> CrawlerConfig:
    """환경변수 딕셔너리에서 설정을 만든다. 빠진 키는 한 번에 모아 ConfigError로 알린다."""
    role_names = _parse_role_names(_get(env, ROLES_KEY))
    _check_required_keys(env, _list_required_keys(env, role_names))

    start_url = _get(env, TARGET_URL_KEY) or ""
    allowed_origins = {_parse_allowed_origin(start_url, TARGET_URL_KEY)}
    for extra_url in _split_list(_get(env, EXTRA_ALLOWED_ORIGINS_KEY)):
        allowed_origins.add(_parse_allowed_origin(extra_url, EXTRA_ALLOWED_ORIGINS_KEY))

    accounts = {name: _read_account(env, name) for name in role_names}
    return CrawlerConfig(
        start_url=start_url,
        allowed_origins=frozenset(allowed_origins),
        roles=(GUEST_ROLE, *role_names),
        accounts=MappingProxyType(accounts),
        login=_parse_login(env) if role_names else None,
        max_depth=_parse_max_depth(_get(env, MAX_DEPTH_KEY)),
        can_change_state=_parse_bool(_get(env, ALLOW_STATE_CHANGING_KEY), ALLOW_STATE_CHANGING_KEY),
        state_changing_keywords=_parse_keywords(_get(env, STATE_CHANGING_KEYWORDS_KEY)),
    )


def is_request_allowed(config: CrawlerConfig, url: str) -> bool:
    """절대 규칙 1번 판단: url의 origin이 허용 목록과 정확히 같을 때만 True."""
    return _parse_origin(url) in config.allowed_origins


def _get(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key, "").strip()
    return value or None


def _split_list(raw_value: str | None) -> list[str]:
    if raw_value is None:
        return []
    return [item.strip() for item in raw_value.split(LIST_SEPARATOR)]


def _account_keys(role: str) -> tuple[str, str]:
    prefix = f"{ENV_PREFIX}ROLE_{role.upper()}"
    return f"{prefix}_USERNAME", f"{prefix}_PASSWORD"


def _parse_role_names(raw_value: str | None) -> tuple[str, ...]:
    role_names = _split_list(raw_value)
    for name in role_names:
        if not ROLE_NAME_PATTERN.fullmatch(name):
            raise ConfigError(f"{ROLES_KEY}: 역할 이름은 소문자·숫자·_만 가능하고 비어 있으면 안 됨 ({name!r})")
        if name == GUEST_ROLE:
            raise ConfigError(f"{ROLES_KEY}: {GUEST_ROLE}는 계정 없는 기본 역할이라 적지 않음")
    if len(set(role_names)) != len(role_names):
        raise ConfigError(f"{ROLES_KEY}: 역할 이름이 중복됨")
    return tuple(role_names)


def _list_required_keys(env: Mapping[str, str], role_names: tuple[str, ...]) -> list[str]:
    required_keys = [TARGET_URL_KEY]
    for name in role_names:
        required_keys.extend(_account_keys(name))
    if role_names:
        required_keys.extend(LOGIN_KEYS)
        check_value = _get(env, LOGIN_SUCCESS_CHECK_KEY)
        is_known_check = check_value in {check.value for check in LoginSuccessCheck}
        if is_known_check and LoginSuccessCheck(check_value).needs_value:
            required_keys.append(LOGIN_SUCCESS_VALUE_KEY)
    return required_keys


def _check_required_keys(env: Mapping[str, str], required_keys: list[str]) -> None:
    missing_keys = [key for key in required_keys if _get(env, key) is None]
    if missing_keys:
        raise ConfigError(f"설정에 빠진 키: {', '.join(missing_keys)}")


def _read_account(env: Mapping[str, str], role: str) -> RoleAccount:
    username_key, password_key = _account_keys(role)
    return RoleAccount(role=role, username=env[username_key].strip(), password=env[password_key].strip())


def _parse_login(env: Mapping[str, str]) -> LoginSettings:
    path = _get(env, LOGIN_PATH_KEY) or ""
    # 절대 URL이나 //host 형태를 받으면 로그인 요청이 대상 밖으로 나갈 수 있다.
    if not path.startswith("/") or path.startswith("//"):
        raise ConfigError(f"{LOGIN_PATH_KEY}: /로 시작하는 경로여야 함")
    check_value = _get(env, LOGIN_SUCCESS_CHECK_KEY)
    try:
        success_check = LoginSuccessCheck(check_value)
    except ValueError as error:
        choices = ", ".join(check.value for check in LoginSuccessCheck)
        raise ConfigError(f"{LOGIN_SUCCESS_CHECK_KEY}: {choices} 중 하나여야 함") from error
    return LoginSettings(
        path=path,
        username_field=_get(env, LOGIN_USERNAME_FIELD_KEY) or "",
        password_field=_get(env, LOGIN_PASSWORD_FIELD_KEY) or "",
        success_check=success_check,
        success_value=_get(env, LOGIN_SUCCESS_VALUE_KEY) if success_check.needs_value else None,
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


def _parse_max_depth(raw_value: str | None) -> int:
    if raw_value is None:
        return DEFAULT_MAX_DEPTH
    if not (raw_value.isascii() and raw_value.isdigit()):
        raise ConfigError(f"{MAX_DEPTH_KEY}: 0 이상의 정수여야 함")
    return int(raw_value)


def _parse_bool(raw_value: str | None, key: str) -> bool:
    # 상태 변경 허용 같은 위험한 스위치라 yes/1 같은 모호한 값은 받지 않는다.
    if raw_value is None:
        return False
    lowered = raw_value.lower()
    if lowered not in (TRUE_TEXT, FALSE_TEXT):
        raise ConfigError(f"{key}: {TRUE_TEXT} 또는 {FALSE_TEXT}여야 함")
    return lowered == TRUE_TEXT


def _parse_keywords(raw_value: str | None) -> tuple[str, ...]:
    if raw_value is None:
        return DEFAULT_STATE_CHANGING_KEYWORDS
    return tuple(keyword.lower() for keyword in _split_list(raw_value) if keyword)
