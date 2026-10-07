"""collector 출력(crawl_result.json)과 근거 파일이 계약을 지키는지 검사한다.

형식은 schemas/output/의 JSON Schema로 본다. Schema로 못 잡는 의미는 여기서 따로 본다:
ID 유일·참조 존재·요청↔계정 대응·UTC 시각·근거 파일 위치와 해시·근거 내용과 참조의 대응·비밀값 노출.
문제를 예외로 하나씩 끊지 않고 ValidationIssue 목록으로 모아 돌려준다. 빈 목록이면 통과다.
메시지에는 값 대신 위치·키 이름만 넣는다. 검사 대상에 비밀값이 섞여 있어도 로그로 새지 않게 하려는 것이다.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from modules.collector.core.capture import (
    AUTHORIZATION_HEADERS,
    COOKIE_HEADER,
    SENSITIVE_KEY_PARTS,
    SET_COOKIE_HEADER,
    list_secret_variants,
)

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas" / "output"
CRAWL_RESULT_SCHEMA_NAME = "crawl_result.schema.json"
REQUEST_KIND = "request"
RESPONSE_KIND = "response"
DOM_KIND = "dom"
SCREENSHOT_KIND = "screenshot"
# 근거 자리마다 올 수 있는 kind. 지금 만들지 않는 근거(request·screenshot)도 나중에 붙일 수 있게 열어 둔다.
RESPONSE_BODY_KINDS = frozenset({RESPONSE_KIND})
REQUEST_BODY_KINDS = frozenset({REQUEST_KIND})
PAGE_EVIDENCE_KINDS = frozenset({DOM_KIND, SCREENSHOT_KIND})
ACTION_EVIDENCE_KINDS = frozenset({DOM_KIND})
REQUEST_EVIDENCE_KINDS = frozenset({REQUEST_KIND, RESPONSE_KIND})
# 내용 형식을 정한 근거만 Schema로 본다. 나머지 kind는 위치·해시·비밀값만 본다.
EVIDENCE_SCHEMA_NAME_BY_KIND = {
    RESPONSE_KIND: "response_evidence.schema.json",
    DOM_KIND: "dom_evidence.schema.json",
}
PRODUCER = "collector"
ARTIFACT_FILE_NAME = "crawl_result.json"
ARTIFACTS_DIR_NAME = "artifacts"
ITERATION_DIR_FORMAT = "iteration-{:03d}"
EVIDENCE_ROOT = PurePosixPath("evidence") / PRODUCER
ROOT_LOCATION = "$"
COOKIE_LOCATION = "cookie"
# RFC 3339 date-time 중 UTC만(Z 또는 +00:00). 날짜가 실제로 있는지는 datetime으로 한 번 더 본다.
UTC_TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|\+00:00)"
)
# capture가 값을 통째로 가리는 헤더. 이 헤더가 redacted=false면 비밀값이 나간 것이다.
SECRET_HEADER_NAMES = frozenset({COOKIE_HEADER, SET_COOKIE_HEADER, *AUTHORIZATION_HEADERS})
# jsonschema 오류 중 메시지에 문서의 키 이름만 들어가는 것. 나머지는 값이 섞일 수 있어 규칙 이름만 쓴다.
KEY_ONLY_SCHEMA_RULES = frozenset({"required", "additionalProperties"})


class IssueCode(StrEnum):
    JSON_INVALID = "JSON_INVALID"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    LOCATION_MISMATCH = "LOCATION_MISMATCH"
    DUPLICATE_ID = "DUPLICATE_ID"
    REFERENCE_MISSING = "REFERENCE_MISSING"
    REFERENCE_MISMATCH = "REFERENCE_MISMATCH"
    ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH"
    FORMAT_INVALID = "FORMAT_INVALID"
    TIME_INVALID = "TIME_INVALID"
    SECRET_LEAK = "SECRET_LEAK"
    SECRET_NOT_REDACTED = "SECRET_NOT_REDACTED"
    EVIDENCE_PATH_INVALID = "EVIDENCE_PATH_INVALID"
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    EVIDENCE_HASH_MISMATCH = "EVIDENCE_HASH_MISMATCH"
    EVIDENCE_INVALID = "EVIDENCE_INVALID"
    EVIDENCE_MISMATCH = "EVIDENCE_MISMATCH"


@dataclass(frozen=True)
class ValidationIssue:
    code: IssueCode
    # "$.data.requests[3].page_id"처럼 문서 안 위치
    location: str
    message: str


def validate_crawl_result_file(
    artifact_path: Path, run_root: Path, known_secrets: Iterable[str] = ()
) -> list[ValidationIssue]:
    """공개된 crawl_result.json 하나를 검사한다. 파일이 run_root/artifacts/iteration-NNN/collector/에 있는지도 본다."""
    report = _Report()
    document = _check_document(report, artifact_path.read_bytes(), run_root, known_secrets)
    if document is not None:
        _check_artifact_location(report, artifact_path, run_root, document)
    return report.issues


def validate_crawl_result_bytes(
    raw: bytes, run_root: Path, known_secrets: Iterable[str] = ()
) -> list[ValidationIssue]:
    """저장 전 직렬화 바이트를 검사한다. 참조하는 근거 파일은 run_root 아래에 먼저 써 둬야 한다."""
    report = _Report()
    _check_document(report, raw, run_root, known_secrets)
    return report.issues


@dataclass
class _Report:
    issues: list[ValidationIssue] = field(default_factory=list)

    def add(self, code: IssueCode, location: str, message: str) -> None:
        self.issues.append(ValidationIssue(code, location, message))


@dataclass(frozen=True)
class _EvidenceUse:
    """EvidenceRef 하나가 문서의 어느 자리에서 누구의 근거로 쓰였는지."""

    location: str
    ref: Mapping[str, Any]
    # 이 자리에 올 수 있는 근거 kind
    allowed_kinds: frozenset[str]
    page_id: str | None = None
    action_id: str | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class _LoadedEvidence:
    raw: bytes
    # 읽기·Schema 검사에 실패했으면 None
    document: Mapping[str, Any] | None


class _StrictJsonError(ValueError):
    """표준 json이 조용히 받아 주지만 계약에서 금지한 입력(중복 키, NaN·Infinity)."""


def _check_document(
    report: _Report, raw: bytes, run_root: Path, known_secrets: Iterable[str]
) -> Mapping[str, Any] | None:
    """Schema까지 통과한 문서만 돌려준다. 형식이 틀리면 의미 검사는 건너뛴다(오류가 연쇄로 쏟아지지 않게)."""
    secret_variants = _expand_secret_variants(known_secrets)
    _check_secret_bytes(report, ROOT_LOCATION, raw, secret_variants)
    document = _parse_strict_json(report, ROOT_LOCATION, raw)
    if document is None or not _check_schema(report, ROOT_LOCATION, document, CRAWL_RESULT_SCHEMA_NAME):
        return None
    _check_utc_timestamp(report, f"{ROOT_LOCATION}.created_at", document["created_at"])
    if document["data"] is not None:
        _DataChecker(report, document["data"], run_root.resolve(), secret_variants).check()
    return document


def _check_artifact_location(
    report: _Report, artifact_path: Path, run_root: Path, document: Mapping[str, Any]
) -> None:
    root = run_root.resolve()
    iteration_dir = ITERATION_DIR_FORMAT.format(document["iteration"])
    expected = root / ARTIFACTS_DIR_NAME / iteration_dir / PRODUCER / ARTIFACT_FILE_NAME
    if artifact_path.resolve() != expected:
        report.add(
            IssueCode.LOCATION_MISMATCH,
            ROOT_LOCATION,
            f"iteration·producer에 맞는 위치가 아님 (기대: {expected.relative_to(root)})",
        )
    if document["run_id"] != root.name:
        report.add(IssueCode.LOCATION_MISMATCH, f"{ROOT_LOCATION}.run_id", "run_root 폴더 이름과 run_id가 다름")


class _DataChecker:
    """data 객체의 ID·참조·계정 대응·근거 파일을 본다. Schema를 통과한 data만 받는다."""

    def __init__(
        self, report: _Report, data: Mapping[str, Any], run_root: Path, secret_variants: tuple[str, ...]
    ) -> None:
        self._report = report
        self._data = data
        self._run_root = run_root
        self._secret_variants = secret_variants
        self._roles = _index_by_id(report, "$.data.roles", data["roles"], "role_id")
        self._accounts = _index_by_id(report, "$.data.accounts", data["accounts"], "account_id")
        self._pages = _index_by_id(report, "$.data.pages", data["pages"], "page_id")
        self._actions = _index_by_id(report, "$.data.actions", data["actions"], "action_id")
        _index_by_id(report, "$.data.requests", data["requests"], "request_id")
        self._evidence_by_path: dict[Path, _LoadedEvidence] = {}
        self._evidence_signature_by_id: dict[str, tuple[object, ...]] = {}

    def check(self) -> None:
        for index, account in enumerate(self._data["accounts"]):
            self._check_reference(f"$.data.accounts[{index}].role_id", account["role_id"], self._roles)
        for index, page in enumerate(self._data["pages"]):
            self._check_reference(f"$.data.pages[{index}].account_id", page["account_id"], self._accounts)
        for index, action in enumerate(self._data["actions"]):
            self._check_reference(f"$.data.actions[{index}].page_id", action["page_id"], self._pages)
        for index, request in enumerate(self._data["requests"]):
            self._check_request(f"$.data.requests[{index}]", request)
        for use in self._list_evidence_uses():
            self._check_evidence_use(use)

    def _check_reference(self, location: str, target_id: str, targets: Mapping[str, Any]) -> None:
        if target_id not in targets:
            self._report.add(IssueCode.REFERENCE_MISSING, location, f"없는 ID를 가리킴: {target_id}")

    def _check_request(self, location: str, request: Mapping[str, Any]) -> None:
        self._check_request_account(location, request)
        page_id = request["page_id"]
        if page_id is not None:
            self._check_request_page(location, request, page_id)
        action_id = request["action_id"]
        if action_id is not None:
            action = self._actions.get(action_id)
            if action is None:
                self._check_reference(f"{location}.action_id", action_id, self._actions)
            elif page_id is not None and action["page_id"] != page_id:
                self._report.add(
                    IssueCode.REFERENCE_MISMATCH, f"{location}.action_id", "행동이 일어난 페이지와 요청의 page_id가 다름"
                )
        if request["method"] != request["method"].upper():
            self._report.add(IssueCode.FORMAT_INVALID, f"{location}.method", "Method는 대문자여야 함")
        _check_utc_timestamp(self._report, f"{location}.observed_at", request["observed_at"])
        self._check_parameters(f"{location}.parameters", request["parameters"])
        self._check_headers(f"{location}.headers", request["headers"])
        self._check_headers(f"{location}.response.headers", request["response"]["headers"])

    def _check_request_page(self, location: str, request: Mapping[str, Any], page_id: str) -> None:
        page = self._pages.get(page_id)
        if page is None:
            self._check_reference(f"{location}.page_id", page_id, self._pages)
        elif page["account_id"] != request["account_id"]:
            # 페이지는 계정 하나의 방문이라, 그 페이지에서 난 요청은 같은 계정이어야 한다.
            self._report.add(
                IssueCode.ACCOUNT_MISMATCH, f"{location}.page_id", "page_id가 가리키는 페이지의 계정이 요청 계정과 다름"
            )

    def _check_request_account(self, location: str, request: Mapping[str, Any]) -> None:
        account = self._accounts.get(request["account_id"])
        if account is None:
            self._check_reference(f"{location}.account_id", request["account_id"], self._accounts)
            return
        if request["role_id"] != account["role_id"]:
            self._report.add(IssueCode.ACCOUNT_MISMATCH, f"{location}.role_id", "요청 계정의 role_id와 다름")
        if request["session_ref"] != account["session_ref"]:
            self._report.add(IssueCode.ACCOUNT_MISMATCH, f"{location}.session_ref", "요청 계정의 session_ref와 다름")

    def _check_parameters(self, location: str, parameters: list[Mapping[str, Any]]) -> None:
        for index, parameter in enumerate(parameters):
            is_secret_parameter = parameter["location"] == COOKIE_LOCATION or _is_sensitive_name(parameter["name"])
            if is_secret_parameter and not parameter["is_sensitive"]:
                self._report.add(
                    IssueCode.SECRET_NOT_REDACTED,
                    f"{location}[{index}]",
                    f"쿠키·민감 이름 파라미터는 is_sensitive=true여야 함 ({parameter['name']})",
                )

    def _check_headers(self, location: str, headers: list[Mapping[str, Any]]) -> None:
        for index, header in enumerate(headers):
            name = header["name"]
            if name != name.lower():
                self._report.add(IssueCode.FORMAT_INVALID, f"{location}[{index}].name", "헤더 이름은 소문자여야 함")
            if (name.lower() in SECRET_HEADER_NAMES or _is_sensitive_name(name)) and not header["redacted"]:
                self._report.add(
                    IssueCode.SECRET_NOT_REDACTED, f"{location}[{index}]", f"값을 가려야 하는 헤더 ({name.lower()})"
                )

    def _list_evidence_uses(self) -> list[_EvidenceUse]:
        uses: list[_EvidenceUse] = []
        for index, page in enumerate(self._data["pages"]):
            for ref_index, ref in enumerate(page["evidence_refs"]):
                location = f"$.data.pages[{index}].evidence_refs[{ref_index}]"
                uses.append(_EvidenceUse(location, ref, PAGE_EVIDENCE_KINDS, page_id=page["page_id"]))
        for index, action in enumerate(self._data["actions"]):
            for ref_index, ref in enumerate(action["evidence_refs"]):
                location = f"$.data.actions[{index}].evidence_refs[{ref_index}]"
                uses.append(
                    _EvidenceUse(
                        location, ref, ACTION_EVIDENCE_KINDS, page_id=action["page_id"], action_id=action["action_id"]
                    )
                )
        for index, request in enumerate(self._data["requests"]):
            uses.extend(self._list_request_evidence_uses(f"$.data.requests[{index}]", request))
        return uses

    def _list_request_evidence_uses(self, location: str, request: Mapping[str, Any]) -> list[_EvidenceUse]:
        request_id = request["request_id"]
        uses = [
            _EvidenceUse(f"{location}.evidence_refs[{ref_index}]", ref, REQUEST_EVIDENCE_KINDS, request_id=request_id)
            for ref_index, ref in enumerate(request["evidence_refs"])
        ]
        request_body_ref = request["body_ref"]
        if request_body_ref is not None:
            uses.append(
                _EvidenceUse(f"{location}.body_ref", request_body_ref, REQUEST_BODY_KINDS, request_id=request_id)
            )
        response_body_ref = request["response"]["body_ref"]
        if response_body_ref is not None:
            uses.append(
                _EvidenceUse(
                    f"{location}.response.body_ref", response_body_ref, RESPONSE_BODY_KINDS, request_id=request_id
                )
            )
        return uses

    def _check_evidence_use(self, use: _EvidenceUse) -> None:
        ref = use.ref
        if ref["kind"] not in use.allowed_kinds:
            allowed = ", ".join(sorted(use.allowed_kinds))
            self._report.add(
                IssueCode.EVIDENCE_MISMATCH, use.location, f"이 자리에 올 수 있는 kind는 {allowed} (kind={ref['kind']})"
            )
            return
        if not ref["redacted"]:
            self._report.add(IssueCode.SECRET_NOT_REDACTED, use.location, "collector 근거는 redacted=true여야 함")
        self._check_evidence_signature(use)
        evidence = self._load_evidence(use.location, ref)
        if evidence is not None:
            self._check_evidence_owner(use, evidence)

    def _check_evidence_signature(self, use: _EvidenceUse) -> None:
        """같은 근거 파일을 여러 자리에서 가리킬 수 있다. 그때 evidence_id가 같으면 나머지도 같아야 한다."""
        ref = use.ref
        signature = (ref["kind"], ref["path"], ref["sha256"], ref["redacted"])
        first_signature = self._evidence_signature_by_id.setdefault(ref["evidence_id"], signature)
        if first_signature != signature:
            self._report.add(
                IssueCode.EVIDENCE_MISMATCH, use.location, "같은 evidence_id가 다른 kind·path·sha256·redacted로 쓰임"
            )

    def _load_evidence(self, location: str, ref: Mapping[str, Any]) -> Mapping[str, Any] | None:
        path = _resolve_evidence_path(self._run_root, ref["path"])
        if path is None:
            self._report.add(
                IssueCode.EVIDENCE_PATH_INVALID, f"{location}.path", f"run_root의 {EVIDENCE_ROOT}/ 아래 상대 경로가 아님"
            )
            return None
        loaded = self._evidence_by_path.get(path)
        if loaded is None:
            if not path.is_file():
                self._report.add(IssueCode.EVIDENCE_MISSING, f"{location}.path", "근거 파일이 없음")
                return None
            loaded = self._read_evidence_file(location, path.read_bytes(), ref["kind"])
            self._evidence_by_path[path] = loaded
        if hashlib.sha256(loaded.raw).hexdigest() != ref["sha256"]:
            self._report.add(IssueCode.EVIDENCE_HASH_MISMATCH, f"{location}.sha256", "근거 파일 바이트의 SHA-256과 다름")
        return loaded.document

    def _read_evidence_file(self, location: str, raw: bytes, kind: str) -> _LoadedEvidence:
        """근거 파일 자체의 문제(비밀값·JSON·Schema·DOM 항목 참조)는 처음 읽을 때 한 번만 알린다."""
        _check_secret_bytes(self._report, location, raw, self._secret_variants)
        schema_name = EVIDENCE_SCHEMA_NAME_BY_KIND.get(kind)
        if schema_name is None:
            return _LoadedEvidence(raw, None)
        file_report = _Report()
        document = _parse_strict_json(file_report, ROOT_LOCATION, raw)
        if document is not None and not _check_schema(file_report, ROOT_LOCATION, document, schema_name):
            document = None
        for issue in file_report.issues:
            self._report.add(IssueCode.EVIDENCE_INVALID, f"{location} → 근거 파일 {issue.location}", issue.message)
        if document is not None and kind == DOM_KIND:
            self._check_dom_actions(location, document)
        return _LoadedEvidence(raw, document)

    def _check_dom_actions(self, location: str, evidence: Mapping[str, Any]) -> None:
        for action_id in evidence["actions"]:
            action = self._actions.get(action_id)
            if action is None:
                self._report.add(
                    IssueCode.REFERENCE_MISSING, f"{location} → 근거 파일 actions", f"없는 action_id: {action_id}"
                )
            elif action["page_id"] != evidence["page_id"]:
                self._report.add(
                    IssueCode.EVIDENCE_MISMATCH,
                    f"{location} → 근거 파일 actions",
                    f"다른 페이지의 행동이 들어 있음: {action_id}",
                )

    def _check_evidence_owner(self, use: _EvidenceUse, evidence: Mapping[str, Any]) -> None:
        kind = use.ref["kind"]
        if evidence["evidence_id"] != use.ref["evidence_id"]:
            self._report.add(IssueCode.EVIDENCE_MISMATCH, use.location, "근거 파일의 evidence_id가 참조와 다름")
        if kind == RESPONSE_KIND and evidence["request_id"] != use.request_id:
            self._report.add(IssueCode.EVIDENCE_MISMATCH, use.location, "응답 근거의 request_id가 이 요청이 아님")
        if kind == DOM_KIND:
            if evidence["page_id"] != use.page_id:
                self._report.add(IssueCode.EVIDENCE_MISMATCH, use.location, "DOM 근거의 page_id가 이 페이지가 아님")
            if use.action_id is not None and use.action_id not in evidence["actions"]:
                self._report.add(IssueCode.EVIDENCE_MISMATCH, use.location, "DOM 근거에 이 행동 항목이 없음")


def _index_by_id(
    report: _Report, location: str, items: list[Mapping[str, Any]], id_key: str
) -> dict[str, Mapping[str, Any]]:
    by_id: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(items):
        item_id = item[id_key]
        if item_id in by_id:
            report.add(IssueCode.DUPLICATE_ID, f"{location}[{index}].{id_key}", f"중복 ID: {item_id}")
            continue
        by_id[item_id] = item
    return by_id


def _resolve_evidence_path(run_root: Path, relative_path: str) -> Path | None:
    """run_root 기준 상대 경로를 evidence/collector/ 안의 실제 경로로 푼다. 벗어나면 None."""
    pure_path = PurePosixPath(relative_path)
    root_parts = EVIDENCE_ROOT.parts
    if not relative_path or "\\" in relative_path or pure_path.is_absolute() or ".." in pure_path.parts:
        return None
    if pure_path.parts[: len(root_parts)] != root_parts or len(pure_path.parts) == len(root_parts):
        return None
    # symlink로 루트 밖을 가리키는 경우를 막으려고 실제 경로로 푼 뒤 다시 확인한다.
    evidence_root = (run_root / EVIDENCE_ROOT).resolve()
    resolved = (run_root / pure_path).resolve()
    if not (resolved.is_relative_to(run_root) and resolved.is_relative_to(evidence_root)):
        return None
    return resolved


@cache
def _load_validator(schema_name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _check_schema(report: _Report, location: str, document: object, schema_name: str) -> bool:
    errors = sorted(_load_validator(schema_name).iter_errors(document), key=lambda error: error.json_path)
    for error in errors:
        report.add(IssueCode.SCHEMA_INVALID, f"{location}{error.json_path[1:]}", _describe_schema_error(error))
    return not errors


def _describe_schema_error(error: ValidationError) -> str:
    if error.validator in KEY_ONLY_SCHEMA_RULES:
        return error.message
    return f"{error.validator} 규칙 위반 (Schema 값: {error.validator_value!r})"


def _parse_strict_json(report: _Report, location: str, raw: bytes) -> Any | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        report.add(IssueCode.JSON_INVALID, location, "UTF-8이 아님")
        return None
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_non_finite)
    except json.JSONDecodeError as error:
        report.add(IssueCode.JSON_INVALID, location, f"JSON 형식 오류: {error.msg} ({error.lineno}행 {error.colno}열)")
    except _StrictJsonError as error:
        report.add(IssueCode.JSON_INVALID, location, str(error))
    return None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _StrictJsonError(f"중복 키: {key}")
        result[key] = value
    return result


def _reject_non_finite(constant: str) -> float:
    raise _StrictJsonError(f"허용하지 않는 숫자: {constant}")


def _check_utc_timestamp(report: _Report, location: str, text: str) -> None:
    if not _is_utc_timestamp(text):
        report.add(IssueCode.TIME_INVALID, location, "UTC RFC3339 시각이 아님 (예: 2026-10-06T03:00:00Z)")


def _is_utc_timestamp(text: str) -> bool:
    if not UTC_TIMESTAMP_PATTERN.fullmatch(text):
        return False
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return False
    return True


def _is_sensitive_name(name: str) -> bool:
    lowered = name.lower().replace("-", "_")
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _expand_secret_variants(known_secrets: Iterable[str]) -> tuple[str, ...]:
    """원문·URL 인코딩(capture와 같은 규칙)에 JSON 이스케이프 형태를 더한다. 직렬화된 바이트에서 찾기 때문이다."""
    variants = set(list_secret_variants(known_secrets))
    for variant in tuple(variants):
        for is_ascii_only in (True, False):
            variants.add(json.dumps(variant, ensure_ascii=is_ascii_only)[1:-1])
    return tuple(sorted(variants, key=len, reverse=True))


def _check_secret_bytes(report: _Report, location: str, raw: bytes, secret_variants: tuple[str, ...]) -> None:
    text = raw.decode("utf-8", errors="replace")
    if any(variant in text for variant in secret_variants):
        report.add(IssueCode.SECRET_LEAK, location, "알려진 비밀값(계정 비밀번호 등)이 들어 있음")
