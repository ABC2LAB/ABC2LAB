"""crawl_result 계약(Schema 3종 + 의미 검증)을 손으로 쓴 fixture로 확인한다.

음성 테스트는 fixture를 tmp_path에 복사해 한 곳만 망가뜨리고, 기대한 오류 코드가 나오는지 본다.
근거 파일을 고치는 경우는 참조 해시도 새로 맞춰서, 노린 문제 하나만 남게 한다.
"""

import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from modules.collector.utils.validation import (
    SCHEMA_DIR,
    IssueCode,
    validate_crawl_result_bytes,
    validate_crawl_result_file,
)

FIXTURE_RUN_ROOT = Path(__file__).resolve().parent / "fixtures" / "runs" / "run_demo_001"
ARTIFACT_RELATIVE_PATH = Path("artifacts") / "iteration-000" / "collector" / "crawl_result.json"
# 비밀값 스캔용. fixture 어디에도 없는 값이어야 양성 테스트가 통과한다.
FIXTURE_SECRET = "fixture-only-password"
JSON_INDENT = 2

GUEST_REQUEST = "request:1"
MEMBER_A_API_REQUEST = "request:4"
MEMBER_A_LINK_REQUEST = "request:5"
MEMBER_B_API_REQUEST = "request:7"
MEMBER_B_ACCOUNT = "account:member_b"
RESPONSE_EVIDENCE_PATH = "evidence/collector/response/request-4.json"
DOM_EVIDENCE_PATH = "evidence/collector/dom/page-2.json"

Document = dict[str, Any]
Mutation = Callable[[Document], None]


@dataclass(frozen=True)
class FixtureRun:
    run_root: Path

    @property
    def artifact_path(self) -> Path:
        return self.run_root / ARTIFACT_RELATIVE_PATH

    def load(self) -> Document:
        return json.loads(self.artifact_path.read_text(encoding="utf-8"))

    def save(self, document: Document) -> None:
        _write_json(self.artifact_path, document)

    def load_evidence(self, relative_path: str) -> Document:
        return json.loads((self.run_root / relative_path).read_text(encoding="utf-8"))

    def save_evidence(self, relative_path: str, evidence: Document) -> None:
        """근거 파일을 고치고, 그 파일을 가리키는 모든 참조의 sha256을 새 바이트에 맞춘다."""
        path = self.run_root / relative_path
        _write_json(path, evidence)
        sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        document = self.load()
        for ref in _iter_evidence_refs(document):
            if ref["path"] == relative_path:
                ref["sha256"] = sha256
        self.save(document)

    def validate_codes(self) -> set[IssueCode]:
        issues = validate_crawl_result_file(self.artifact_path, self.run_root, [FIXTURE_SECRET])
        return {issue.code for issue in issues}


@pytest.fixture
def fixture_run(tmp_path: Path) -> FixtureRun:
    run_root = tmp_path / FIXTURE_RUN_ROOT.name
    shutil.copytree(FIXTURE_RUN_ROOT, run_root)
    return FixtureRun(run_root)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=JSON_INDENT) + "\n", encoding="utf-8")


def _iter_evidence_refs(document: Document) -> list[Document]:
    data = document["data"]
    refs = [ref for page in data["pages"] for ref in page["evidence_refs"]]
    refs += [ref for action in data["actions"] for ref in action["evidence_refs"]]
    refs += [request["response"]["body_ref"] for request in data["requests"] if request["response"]["body_ref"]]
    return refs


def _find(document: Document, collection: str, id_key: str, item_id: str) -> Document:
    return next(item for item in document["data"][collection] if item[id_key] == item_id)


def _request(document: Document, request_id: str) -> Document:
    return _find(document, "requests", "request_id", request_id)


def _assign(target: Document, key: str, value: object) -> None:
    target[key] = value


ERROR_ITEM = {"code": "ACCOUNT_CRAWL_FAILED", "message": "로그인 실패", "item_ref": MEMBER_B_ACCOUNT, "retryable": True}


# ---- 양성 ----


def test_schemas_are_valid_draft_2020_12() -> None:
    schema_paths = sorted(SCHEMA_DIR.glob("*.schema.json"))

    assert [path.name for path in schema_paths] == [
        "crawl_result.schema.json",
        "dom_evidence.schema.json",
        "response_evidence.schema.json",
    ]
    for path in schema_paths:
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_fixture_passes() -> None:
    issues = validate_crawl_result_file(FIXTURE_RUN_ROOT / ARTIFACT_RELATIVE_PATH, FIXTURE_RUN_ROOT, [FIXTURE_SECRET])

    assert issues == []


def test_fixture_covers_contract_cases() -> None:
    """fixture가 계약의 주요 경우를 실제로 담고 있어야 양성 테스트가 의미가 있다."""
    data = json.loads((FIXTURE_RUN_ROOT / ARTIFACT_RELATIVE_PATH).read_text(encoding="utf-8"))["data"]
    accounts_per_role: dict[str, int] = {}
    for account in data["accounts"]:
        accounts_per_role[account["role_id"]] = accounts_per_role.get(account["role_id"], 0) + 1
    guest_accounts = [account for account in data["accounts"] if account["session_ref"] is None]
    parameters = [parameter for request in data["requests"] for parameter in request["parameters"]]
    headers = [header for request in data["requests"] for header in request["headers"] + request["response"]["headers"]]
    response_refs = [request["response"]["body_ref"] for request in data["requests"] if request["response"]["body_ref"]]
    identifiers = [
        identifier
        for ref in response_refs
        for identifier in json.loads((FIXTURE_RUN_ROOT / ref["path"]).read_text(encoding="utf-8"))["identifiers"]
    ]

    assert guest_accounts and max(accounts_per_role.values()) >= 2
    assert identifiers
    assert any(parameter["is_sensitive"] and parameter["location"] != "cookie" for parameter in parameters)
    assert any(header["redacted"] for header in headers)
    assert any(page["evidence_refs"] for page in data["pages"])
    # 같은 URL을 두 계정이 본 페이지가 계정별로 따로 있다.
    accounts_by_url: dict[str, set[str]] = {}
    for page in data["pages"]:
        accounts_by_url.setdefault(page["url"], set()).add(page["account_id"])
    assert max(len(accounts) for accounts in accounts_by_url.values()) >= 2
    assert any(action["evidence_refs"] for action in data["actions"])


def test_partial_with_errors_passes(fixture_run: FixtureRun) -> None:
    document = fixture_run.load()
    document["status"] = "partial"
    document["errors"] = [ERROR_ITEM]
    fixture_run.save(document)

    assert fixture_run.validate_codes() == set()


def test_failed_with_null_data_passes(fixture_run: FixtureRun) -> None:
    document = fixture_run.load()
    document["status"] = "failed"
    document["errors"] = [ERROR_ITEM]
    document["data"] = None
    fixture_run.save(document)

    assert fixture_run.validate_codes() == set()


def _add_own_response_to_request_evidence(document: Document) -> None:
    request = _request(document, MEMBER_A_API_REQUEST)
    request["evidence_refs"] = [request["response"]["body_ref"]]


ACCEPTED_DOCUMENT_VARIATIONS: list[tuple[str, Mutation]] = [
    # action↔page 일치는 둘 다 있을 때만 본다.
    ("action_without_page", lambda document: _assign(_request(document, MEMBER_A_LINK_REQUEST), "page_id", None)),
    # 요청 evidence_refs에는 request·response 근거가 올 수 있다.
    ("response_ref_in_request_evidence_refs", _add_own_response_to_request_evidence),
]


@pytest.mark.parametrize(
    "mutate", [pytest.param(mutate, id=case_id) for case_id, mutate in ACCEPTED_DOCUMENT_VARIATIONS]
)
def test_document_variation_passes(fixture_run: FixtureRun, mutate: Mutation) -> None:
    document = fixture_run.load()
    mutate(document)
    fixture_run.save(document)

    assert fixture_run.validate_codes() == set()


def test_digit_string_identifier_kept_as_string(fixture_run: FixtureRun) -> None:
    evidence = fixture_run.load_evidence(RESPONSE_EVIDENCE_PATH)
    evidence["identifiers"][0]["value"] = "42"
    fixture_run.save_evidence(RESPONSE_EVIDENCE_PATH, evidence)

    assert fixture_run.validate_codes() == set()
    assert fixture_run.load_evidence(RESPONSE_EVIDENCE_PATH)["identifiers"][0]["value"] == "42"


# ---- 음성: crawl_result.json ----


def _set_failed_with_data(document: Document) -> None:
    document["status"] = "failed"
    document["errors"] = [ERROR_ITEM]


def _set_completed_without_data(document: Document) -> None:
    document["data"] = None


def _duplicate_page_id(document: Document) -> None:
    pages = document["data"]["pages"]
    pages[-1]["page_id"] = pages[0]["page_id"]


DOCUMENT_MUTATIONS: list[tuple[str, Mutation, IssueCode]] = [
    ("unknown_top_level_key", lambda document: _assign(document, "extra", 1), IssueCode.SCHEMA_INVALID),
    (
        "unknown_nested_key",
        lambda document: _assign(_request(document, GUEST_REQUEST), "extra", 1),
        IssueCode.SCHEMA_INVALID,
    ),
    ("completed_with_errors", lambda document: _assign(document, "errors", [ERROR_ITEM]), IssueCode.SCHEMA_INVALID),
    ("partial_without_errors", lambda document: _assign(document, "status", "partial"), IssueCode.SCHEMA_INVALID),
    ("failed_with_data", _set_failed_with_data, IssueCode.SCHEMA_INVALID),
    ("completed_without_data", _set_completed_without_data, IssueCode.SCHEMA_INVALID),
    (
        "sensitive_parameter_with_value",
        lambda document: _assign(_request(document, MEMBER_A_API_REQUEST)["parameters"][1], "value", "abc"),
        IssueCode.SCHEMA_INVALID,
    ),
    (
        "redacted_header_with_value",
        lambda document: _assign(_request(document, MEMBER_A_API_REQUEST)["headers"][1], "value", "sid=abc"),
        IssueCode.SCHEMA_INVALID,
    ),
    (
        "cookie_header_not_redacted",
        lambda document: _assign(_request(document, MEMBER_A_API_REQUEST)["headers"][1], "redacted", False),
        IssueCode.SECRET_NOT_REDACTED,
    ),
    (
        "cookie_parameter_not_sensitive",
        lambda document: _assign(_request(document, MEMBER_A_API_REQUEST)["parameters"][2], "is_sensitive", False),
        IssueCode.SECRET_NOT_REDACTED,
    ),
    ("duplicate_page_id", _duplicate_page_id, IssueCode.DUPLICATE_ID),
    (
        "request_page_missing",
        lambda document: _assign(_request(document, MEMBER_A_API_REQUEST), "page_id", "page:99"),
        IssueCode.REFERENCE_MISSING,
    ),
    (
        "request_action_missing",
        lambda document: _assign(_request(document, MEMBER_A_LINK_REQUEST), "action_id", "action:99"),
        IssueCode.REFERENCE_MISSING,
    ),
    (
        "request_account_missing",
        lambda document: _assign(_request(document, MEMBER_A_API_REQUEST), "account_id", "account:nobody"),
        IssueCode.REFERENCE_MISSING,
    ),
    (
        "action_page_missing",
        lambda document: _assign(_find(document, "actions", "action_id", "action:4"), "page_id", "page:99"),
        IssueCode.REFERENCE_MISSING,
    ),
    (
        "account_role_missing",
        lambda document: _assign(
            _find(document, "accounts", "account_id", MEMBER_B_ACCOUNT), "role_id", "role:nobody"
        ),
        IssueCode.REFERENCE_MISSING,
    ),
    (
        "page_account_missing",
        lambda document: _assign(_find(document, "pages", "page_id", "page:3"), "account_id", "account:nobody"),
        IssueCode.REFERENCE_MISSING,
    ),
    (
        "page_without_account_id",
        lambda document: _find(document, "pages", "page_id", "page:2").pop("account_id"),
        IssueCode.SCHEMA_INVALID,
    ),
    (
        "request_on_page_of_other_account",
        lambda document: _assign(_request(document, MEMBER_B_API_REQUEST), "page_id", "page:2"),
        IssueCode.ACCOUNT_MISMATCH,
    ),
    (
        "request_action_on_other_page",
        lambda document: _assign(_request(document, MEMBER_A_LINK_REQUEST), "page_id", "page:1"),
        IssueCode.REFERENCE_MISMATCH,
    ),
    (
        "request_role_differs_from_account",
        lambda document: _assign(_request(document, MEMBER_A_API_REQUEST), "role_id", "role:guest"),
        IssueCode.ACCOUNT_MISMATCH,
    ),
    (
        "request_session_differs_from_account",
        lambda document: _assign(_request(document, MEMBER_A_API_REQUEST), "session_ref", "session:other"),
        IssueCode.ACCOUNT_MISMATCH,
    ),
    (
        "observed_at_not_utc",
        lambda document: _assign(_request(document, GUEST_REQUEST), "observed_at", "2026-10-06T11:59:00+09:00"),
        IssueCode.TIME_INVALID,
    ),
    (
        "observed_at_without_offset",
        lambda document: _assign(_request(document, GUEST_REQUEST), "observed_at", "2026-10-06T02:59:00"),
        IssueCode.TIME_INVALID,
    ),
    (
        "observed_at_impossible_date",
        lambda document: _assign(_request(document, GUEST_REQUEST), "observed_at", "2026-13-06T02:59:00Z"),
        IssueCode.TIME_INVALID,
    ),
    (
        "created_at_not_utc",
        lambda document: _assign(document, "created_at", "2026-10-06T12:00:05+09:00"),
        IssueCode.TIME_INVALID,
    ),
    (
        "method_lowercase",
        lambda document: _assign(_request(document, GUEST_REQUEST), "method", "get"),
        IssueCode.FORMAT_INVALID,
    ),
    (
        "header_name_uppercase",
        lambda document: _assign(_request(document, GUEST_REQUEST)["headers"][0], "name", "Accept"),
        IssueCode.FORMAT_INVALID,
    ),
    (
        "secret_in_shared_value",
        lambda document: _assign(_request(document, GUEST_REQUEST)["headers"][0], "value", FIXTURE_SECRET),
        IssueCode.SECRET_LEAK,
    ),
    (
        "evidence_parent_path",
        lambda document: _assign(
            _request(document, MEMBER_A_API_REQUEST)["response"]["body_ref"],
            "path",
            "evidence/collector/../../artifacts/iteration-000/collector/crawl_result.json",
        ),
        IssueCode.EVIDENCE_PATH_INVALID,
    ),
    (
        "evidence_absolute_path",
        lambda document: _assign(
            _request(document, MEMBER_A_API_REQUEST)["response"]["body_ref"], "path", "/etc/hosts"
        ),
        IssueCode.EVIDENCE_PATH_INVALID,
    ),
    (
        "evidence_outside_collector_dir",
        lambda document: _assign(
            _request(document, MEMBER_A_API_REQUEST)["response"]["body_ref"],
            "path",
            "artifacts/iteration-000/collector/crawl_result.json",
        ),
        IssueCode.EVIDENCE_PATH_INVALID,
    ),
    (
        "evidence_file_missing",
        lambda document: _assign(
            _request(document, MEMBER_A_API_REQUEST)["response"]["body_ref"],
            "path",
            "evidence/collector/response/request-99.json",
        ),
        IssueCode.EVIDENCE_MISSING,
    ),
    (
        "evidence_hash_in_ref_differs",
        lambda document: _assign(_request(document, MEMBER_A_API_REQUEST)["response"]["body_ref"], "sha256", "0" * 64),
        IssueCode.EVIDENCE_HASH_MISMATCH,
    ),
    (
        "evidence_not_redacted",
        lambda document: _assign(_request(document, MEMBER_A_API_REQUEST)["response"]["body_ref"], "redacted", False),
        IssueCode.SECRET_NOT_REDACTED,
    ),
    (
        "dom_evidence_as_response_body",
        lambda document: _assign(
            _request(document, MEMBER_A_API_REQUEST)["response"],
            "body_ref",
            _find(document, "pages", "page_id", "page:2")["evidence_refs"][0],
        ),
        IssueCode.EVIDENCE_MISMATCH,
    ),
    (
        "response_evidence_on_page",
        lambda document: _assign(
            _find(document, "pages", "page_id", "page:3"),
            "evidence_refs",
            [_request(document, MEMBER_A_API_REQUEST)["response"]["body_ref"]],
        ),
        IssueCode.EVIDENCE_MISMATCH,
    ),
    (
        "response_evidence_as_request_body",
        lambda document: _assign(
            _request(document, MEMBER_A_API_REQUEST),
            "body_ref",
            _request(document, MEMBER_A_API_REQUEST)["response"]["body_ref"],
        ),
        IssueCode.EVIDENCE_MISMATCH,
    ),
    (
        "dom_evidence_in_request_evidence_refs",
        lambda document: _assign(
            _request(document, MEMBER_A_LINK_REQUEST),
            "evidence_refs",
            _find(document, "pages", "page_id", "page:2")["evidence_refs"],
        ),
        IssueCode.EVIDENCE_MISMATCH,
    ),
    (
        "action_points_to_other_page_dom",
        lambda document: _assign(
            _find(document, "actions", "action_id", "action:4"),
            "evidence_refs",
            _find(document, "pages", "page_id", "page:1")["evidence_refs"],
        ),
        IssueCode.EVIDENCE_MISMATCH,
    ),
]


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [pytest.param(mutate, code, id=case_id) for case_id, mutate, code in DOCUMENT_MUTATIONS],
)
def test_document_mutation_rejected(fixture_run: FixtureRun, mutate: Mutation, expected_code: IssueCode) -> None:
    document = fixture_run.load()
    mutate(document)
    fixture_run.save(document)

    assert expected_code in fixture_run.validate_codes()


# ---- 음성: 근거 파일 ----


def test_evidence_bytes_changed_without_ref_update(fixture_run: FixtureRun) -> None:
    path = fixture_run.run_root / RESPONSE_EVIDENCE_PATH
    path.write_bytes(path.read_bytes().replace(b'"value": 7', b'"value": 8'))

    assert fixture_run.validate_codes() == {IssueCode.EVIDENCE_HASH_MISMATCH}


EVIDENCE_MUTATIONS: list[tuple[str, str, Mutation, IssueCode]] = [
    (
        "response_unknown_key",
        RESPONSE_EVIDENCE_PATH,
        lambda evidence: _assign(evidence, "raw_body", "{}"),
        IssueCode.EVIDENCE_INVALID,
    ),
    (
        "identifier_unknown_key",
        RESPONSE_EVIDENCE_PATH,
        lambda evidence: _assign(evidence["identifiers"][0], "key", "id"),
        IssueCode.EVIDENCE_INVALID,
    ),
    (
        "shape_leaf_holds_value",
        RESPONSE_EVIDENCE_PATH,
        lambda evidence: _assign(evidence["shape"], "next_cursor", "c2VjcmV0"),
        IssueCode.EVIDENCE_INVALID,
    ),
    (
        "response_for_other_request",
        RESPONSE_EVIDENCE_PATH,
        lambda evidence: _assign(evidence, "request_id", "request:7"),
        IssueCode.EVIDENCE_MISMATCH,
    ),
    (
        "dom_unknown_key",
        DOM_EVIDENCE_PATH,
        lambda evidence: _assign(evidence, "html", "<form></form>"),
        IssueCode.EVIDENCE_INVALID,
    ),
    (
        "dom_item_unknown_key",
        DOM_EVIDENCE_PATH,
        lambda evidence: _assign(evidence["actions"]["action:3"], "values", {"reason": "x"}),
        IssueCode.EVIDENCE_INVALID,
    ),
    (
        "dom_unknown_action",
        DOM_EVIDENCE_PATH,
        lambda evidence: _assign(evidence["actions"], "action:99", evidence["actions"]["action:4"]),
        IssueCode.REFERENCE_MISSING,
    ),
    (
        "dom_evidence_id_differs",
        DOM_EVIDENCE_PATH,
        lambda evidence: _assign(evidence, "evidence_id", "evidence:dom:other"),
        IssueCode.EVIDENCE_MISMATCH,
    ),
    (
        "secret_in_evidence",
        DOM_EVIDENCE_PATH,
        lambda evidence: _assign(evidence["actions"]["action:4"], "label", FIXTURE_SECRET),
        IssueCode.SECRET_LEAK,
    ),
]


@pytest.mark.parametrize(
    ("relative_path", "mutate", "expected_code"),
    [pytest.param(path, mutate, code, id=case_id) for case_id, path, mutate, code in EVIDENCE_MUTATIONS],
)
def test_evidence_mutation_rejected(
    fixture_run: FixtureRun, relative_path: str, mutate: Mutation, expected_code: IssueCode
) -> None:
    evidence = fixture_run.load_evidence(relative_path)
    mutate(evidence)
    fixture_run.save_evidence(relative_path, evidence)

    codes = fixture_run.validate_codes()

    assert expected_code in codes
    assert IssueCode.EVIDENCE_HASH_MISMATCH not in codes


@pytest.mark.parametrize("value", ["abc", "-1", "4.2", "", " 42"])
def test_non_id_string_identifier_rejected(fixture_run: FixtureRun, value: str) -> None:
    evidence = fixture_run.load_evidence(RESPONSE_EVIDENCE_PATH)
    evidence["identifiers"][0]["value"] = value
    fixture_run.save_evidence(RESPONSE_EVIDENCE_PATH, evidence)

    assert fixture_run.validate_codes() == {IssueCode.EVIDENCE_INVALID}


def test_evidence_symlink_outside_run_root_rejected(fixture_run: FixtureRun, tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    shutil.copyfile(fixture_run.run_root / RESPONSE_EVIDENCE_PATH, outside)
    link_path = "evidence/collector/response/linked.json"
    (fixture_run.run_root / link_path).symlink_to(outside)
    document = fixture_run.load()
    _request(document, MEMBER_A_API_REQUEST)["response"]["body_ref"]["path"] = link_path
    fixture_run.save(document)

    assert IssueCode.EVIDENCE_PATH_INVALID in fixture_run.validate_codes()


# ---- 음성: 파일 위치·JSON 형식 ----


def test_run_id_must_match_run_root(fixture_run: FixtureRun) -> None:
    document = fixture_run.load()
    document["run_id"] = "run_other"
    fixture_run.save(document)

    assert fixture_run.validate_codes() == {IssueCode.LOCATION_MISMATCH}


def test_iteration_must_match_folder(fixture_run: FixtureRun) -> None:
    document = fixture_run.load()
    document["iteration"] = 1
    fixture_run.save(document)

    assert fixture_run.validate_codes() == {IssueCode.LOCATION_MISMATCH}


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b'{"status": "completed", "status": "failed"}', id="duplicate_key"),
        pytest.param(b'{"duration_ms": NaN}', id="nan"),
        pytest.param(b"\xff\xfe", id="not_utf8"),
        pytest.param(b'{"status": ', id="truncated"),
    ],
)
def test_malformed_json_rejected(raw: bytes) -> None:
    issues = validate_crawl_result_bytes(raw, FIXTURE_RUN_ROOT)

    assert [issue.code for issue in issues] == [IssueCode.JSON_INVALID]


def test_issue_messages_do_not_echo_values(fixture_run: FixtureRun) -> None:
    """Schema 오류 메시지에 문서의 값이 그대로 실리면 로그로 비밀값이 샐 수 있다."""
    leaked_value = "value-that-must-not-be-echoed"
    document = fixture_run.load()
    document["mode"] = leaked_value
    _request(document, GUEST_REQUEST)["response"]["status_code"] = leaked_value
    fixture_run.save(document)

    issues = validate_crawl_result_file(fixture_run.artifact_path, fixture_run.run_root)

    assert {issue.code for issue in issues} == {IssueCode.SCHEMA_INVALID}
    assert all(leaked_value not in issue.message for issue in issues)
