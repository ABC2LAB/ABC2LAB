"""Web 입력을 Collector 공개 설정으로 준비한다. 모듈 실행·정답 읽기는 하지 않는다."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import secrets
import threading
import time
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

MAX_INPUT_BYTES = 64 * 1024
MAX_ACCOUNTS = 64
MAX_SESSIONS = 16
INPUT_LIFETIME_SECONDS = 15 * 60
NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,127}")
DATASET_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
TOKEN_PATTERN = re.compile(r"[0-9a-f]{64}")
TEMPLATE_KEYS = frozenset({
    "target_url", "extra_allowed_origins", "max_depth", "allow_state_changing",
    "state_changing_keywords", "roles", "accounts", "login",
})
LOGIN_KEYS = frozenset({
    "path", "username_field", "password_field", "success_check", "success_value",
})


class InputError(ValueError):
    """code는 고정 값만 사용한다. 사용자 입력이나 비밀값을 메시지에 넣지 않는다."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class InputSettings:
    collector_template_path: Path | None = None
    datasets_path: Path | None = None

    @classmethod
    def from_environment(cls, values: Mapping[str, str]) -> InputSettings:
        template = values.get("LOCAL_WEB_COLLECTOR_TEMPLATE_PATH", "")
        datasets = values.get("LOCAL_WEB_DATASETS_PATH", "")
        return cls(Path(template) if template else None, Path(datasets) if datasets else None)


@dataclass(frozen=True)
class TestAccount:
    alias: str
    role: str
    login_id: str = field(repr=False)
    password: str = field(repr=False)


@dataclass
class PreparedInput:
    preparation_id: str
    target_url: str
    dataset_id: str
    accounts: tuple[TestAccount, ...] = field(repr=False)
    temporary_directory: TemporaryDirectory[str] = field(repr=False)

    @property
    def collector_config_path(self) -> Path:
        return Path(self.temporary_directory.name) / "collector.toml"

    def make_collector_environment(self, parent: Mapping[str, str]) -> dict[str, str]:
        # 다른 실행의 계정·.env가 섞이지 않도록 자식 프로세스 환경만 구성한다.
        environment = {
            key: value for key, value in parent.items()
            if not key.startswith("COLLECTOR_ACCOUNT_")
            and key not in {"COLLECTOR_CONFIG_PATH", "COLLECTOR_SECRETS_PATH"}
        }
        environment["COLLECTOR_CONFIG_PATH"] = str(self.collector_config_path)
        environment["COLLECTOR_SECRETS_PATH"] = str(Path(self.temporary_directory.name) / "empty.env")
        for account in self.accounts:
            prefix = f"COLLECTOR_ACCOUNT_{account.alias.upper()}"
            environment[f"{prefix}_LOGIN_ID"] = account.login_id
            environment[f"{prefix}_PASSWORD"] = account.password
        return environment

    def close(self) -> None:
        self.accounts = ()
        self.temporary_directory.cleanup()


@dataclass
class InputSession:
    csrf_token: str = field(repr=False)
    expires_at: float
    timer: threading.Timer = field(repr=False)
    prepared: PreparedInput | None = field(default=None, repr=False)


def load_template(settings: InputSettings) -> dict[str, object]:
    path = settings.collector_template_path
    if path is None:
        raise InputError("TEMPLATE_UNAVAILABLE")
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
            raise InputError("TEMPLATE_INVALID")
        with path.open("rb") as source:
            payload = source.read(MAX_INPUT_BYTES + 1)
        if len(payload) > MAX_INPUT_BYTES:
            raise InputError("TEMPLATE_INVALID")
        template = tomllib.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, RecursionError):
        raise InputError("TEMPLATE_INVALID") from None
    if set(template) - TEMPLATE_KEYS:
        raise InputError("TEMPLATE_INVALID")
    login = template.get("login", {})
    if not isinstance(login, dict) or set(login) - LOGIN_KEYS:
        raise InputError("TEMPLATE_INVALID")
    if not all(isinstance(value, str) for value in login.values()):
        raise InputError("TEMPLATE_INVALID")
    template_accounts(template)
    # 비밀값을 TOML에 섞지 않도록 공개 설정 키·직렬화 가능한 타입만 복제한다.
    encode_toml(template)
    return template


def list_dataset_ids(settings: InputSettings) -> list[str]:
    root = settings.datasets_path
    if root is None:
        raise InputError("DATASETS_UNAVAILABLE")
    try:
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise InputError("DATASETS_UNAVAILABLE")
        dataset_ids = []
        for path in root.iterdir():
            if path.is_symlink() or not DATASET_PATTERN.fullmatch(path.name) or not path.is_dir():
                continue
            ground_truth = path / "ground_truth.json"
            if not ground_truth.is_symlink() and ground_truth.is_file():
                dataset_ids.append(path.name)
        return sorted(dataset_ids)
    except OSError:
        raise InputError("DATASETS_UNAVAILABLE") from None


def template_accounts(template: Mapping[str, object]) -> list[dict[str, str]]:
    accounts = template.get("accounts", [])
    if not isinstance(accounts, list) or len(accounts) > MAX_ACCOUNTS:
        raise InputError("TEMPLATE_INVALID")
    public_accounts = []
    for account in accounts:
        if not isinstance(account, dict) or set(account) != {"alias", "role"}:
            raise InputError("TEMPLATE_INVALID")
        alias, role = account["alias"], account["role"]
        if not is_account_name(alias) or not is_account_name(role):
            raise InputError("TEMPLATE_INVALID")
        public_accounts.append({"alias": alias, "role": role})
    return public_accounts


def is_account_name(value: object) -> bool:
    return isinstance(value, str) and bool(NAME_PATTERN.fullmatch(value)) and value != "guest"


def require_secret(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise InputError("ACCOUNT_INVALID")
    if any(ord(character) < 32 or 0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise InputError("ACCOUNT_INVALID")
    return value


def parse_accounts(value: object) -> tuple[TestAccount, ...]:
    if not isinstance(value, list) or len(value) > MAX_ACCOUNTS:
        raise InputError("ACCOUNT_INVALID")
    accounts = []
    aliases = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"alias", "role", "login_id", "password"}:
            raise InputError("ACCOUNT_INVALID")
        alias, role = item["alias"], item["role"]
        if not is_account_name(alias) or not is_account_name(role) or alias in aliases:
            raise InputError("ACCOUNT_INVALID")
        login_id, password = require_secret(item["login_id"]), require_secret(item["password"])
        if alias.lower() == login_id.strip().lower():
            raise InputError("ACCOUNT_ALIAS_INVALID")
        aliases.add(alias)
        accounts.append(TestAccount(alias, role, login_id, password))
    return tuple(accounts)


def parse_target_url(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096 or value != value.strip():
        raise InputError("TARGET_URL_INVALID")
    if any(ord(character) <= 32 or ord(character) == 127 for character in value) or "\\" in value:
        raise InputError("TARGET_URL_INVALID")
    try:
        parts = urlsplit(value)
        parts.port
        if (
            parts.scheme not in {"http", "https"} or not parts.hostname
            or parts.username is not None or parts.password is not None or parts.query or parts.fragment
        ):
            raise InputError("TARGET_URL_INVALID")
        value.encode("utf-8")
    except (ValueError, UnicodeError):
        raise InputError("TARGET_URL_INVALID") from None
    return value


def encode_toml_value(value: object, depth: int = 0) -> str:
    if depth > 8:
        raise InputError("TEMPLATE_INVALID")
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(encode_toml_value(item, depth + 1) for item in value) + "]"
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        pairs = (f"{encode_toml_value(key)} = {encode_toml_value(item, depth + 1)}" for key, item in value.items())
        return "{ " + ", ".join(pairs) + " }"
    raise InputError("TEMPLATE_INVALID")


def encode_toml(settings: Mapping[str, object]) -> str:
    return "\n".join(f"{encode_toml_value(key)} = {encode_toml_value(value)}" for key, value in settings.items()) + "\n"


def write_private_configuration(path: Path, payload: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        target.write(payload)
        target.flush()
        os.fsync(target.fileno())


def prepare_input(settings: InputSettings, payload: object) -> PreparedInput:
    if not isinstance(payload, dict) or set(payload) != {"target_url", "dataset_id", "accounts"}:
        raise InputError("INPUT_INVALID")
    target_url = parse_target_url(payload["target_url"])
    dataset_id = payload["dataset_id"]
    if not isinstance(dataset_id, str) or not DATASET_PATTERN.fullmatch(dataset_id):
        raise InputError("DATASET_INVALID")
    if dataset_id not in list_dataset_ids(settings):
        raise InputError("DATASET_INVALID")
    accounts = parse_accounts(payload["accounts"])
    template = load_template(settings)
    if accounts and not all(isinstance(template.get("login", {}).get(key), str) and template["login"][key].strip()
                            for key in ("path", "username_field", "password_field", "success_check")):
        raise InputError("TEMPLATE_LOGIN_MISSING")
    template.update({
        "target_url": target_url,
        "roles": list(dict.fromkeys(account.role for account in accounts)),
        "accounts": [{"alias": account.alias, "role": account.role} for account in accounts],
    })
    directory = TemporaryDirectory(prefix="abc2lab-web-input-")
    try:
        write_private_configuration(Path(directory.name) / "collector.toml", encode_toml(template))
        write_private_configuration(Path(directory.name) / "empty.env", "")
    except (OSError, UnicodeError, InputError):
        directory.cleanup()
        raise InputError("INPUT_PREPARATION_FAILED") from None
    return PreparedInput(secrets.token_hex(32), target_url, dataset_id, accounts, directory)


class InputStore:
    """브라우저별 입력 준비. 만료·취소·종료 시 참조와 임시 설정을 폐기한다."""

    def __init__(self, settings: InputSettings) -> None:
        self.settings = settings
        self._sessions: dict[str, InputSession] = {}
        self._lock = threading.RLock()

    def open_session(self, session_id: str | None) -> tuple[str, InputSession]:
        with self._lock:
            if session_id in self._sessions and self._sessions[session_id].expires_at > time.monotonic():
                return session_id, self._sessions[session_id]
            if len(self._sessions) >= MAX_SESSIONS:
                raise InputError("INPUT_SESSION_LIMIT")
            session_id = secrets.token_hex(32)
            timer = threading.Timer(INPUT_LIFETIME_SECONDS, self.clear_session, args=(session_id,))
            timer.daemon = True
            session = InputSession(secrets.token_hex(32), time.monotonic() + INPUT_LIFETIME_SECONDS, timer)
            self._sessions[session_id] = session
            timer.start()
            return session_id, session

    def authenticate(self, session_id: str | None, csrf_token: str) -> InputSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if (
                session is None or session.expires_at <= time.monotonic()
                or not TOKEN_PATTERN.fullmatch(csrf_token)
                or not secrets.compare_digest(session.csrf_token, csrf_token)
            ):
                raise InputError("INPUT_SESSION_INVALID")
            return session

    def prepare(self, session_id: str, csrf_token: str, payload: object) -> dict[str, object]:
        self.authenticate(session_id, csrf_token)
        prepared = prepare_input(self.settings, payload)
        try:
            with self._lock:
                session = self.authenticate(session_id, csrf_token)
                if session.prepared is not None:
                    session.prepared.close()
                session.prepared = prepared
                return self.preparation_summary(session)
        except (InputError, OSError):
            prepared.close()
            raise

    def preparation_summary(self, session: InputSession) -> dict[str, object]:
        with self._lock:
            prepared = session.prepared
            return {
                "status": "prepared" if prepared else "empty",
                "preparation_id": prepared.preparation_id if prepared else None,
                "account_count": len(prepared.accounts) if prepared else 0,
                "expires_in_seconds": max(0, int(session.expires_at - time.monotonic())),
                "diagnosis_enabled": False,
            }

    def clear_preparation(self, session_id: str, csrf_token: str) -> None:
        with self._lock:
            session = self.authenticate(session_id, csrf_token)
            if session.prepared is not None:
                session.prepared.close()
                session.prepared = None

    def consume(self, session_id: str, csrf_token: str, preparation_id: str) -> PreparedInput:
        """4단계 연결용. 호출자가 사용 종료 후 close해야 한다. 여기서는 실행하지 않는다."""
        with self._lock:
            session = self.authenticate(session_id, csrf_token)
            if session.prepared is None or session.prepared.preparation_id != preparation_id:
                raise InputError("PREPARATION_UNAVAILABLE")
            prepared, session.prepared = session.prepared, None
            return prepared

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is not None:
                session.timer.cancel()
                if session.prepared is not None:
                    try:
                        session.prepared.close()
                    except OSError:
                        logger.error("input_cleanup_failed")
                    session.prepared = None

    def close(self) -> None:
        with self._lock:
            for session_id in list(self._sessions):
                self.clear_session(session_id)
