"""근거 파일 작성기(utils/evidence.py)가 공개한 근거 Schema대로, 값 없이, 한 번만 쓰는지 본다."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from modules.collector.core.models import CapturedRequest, DiscoveredPage, FormField, PageAction, PageLink
from modules.collector.utils.evidence import EvidenceWriter
from modules.collector.utils.storage import ArtifactExistsError, OutputPathError
from modules.collector.utils.validation import SCHEMA_DIR

RUN_ID = "run_evidence"
ACCOUNT_PASSWORD = "evidence-pw-do-not-store"
PAGE_URL = "http://127.0.0.1:1/mine"
BASE_TIME = datetime(2026, 10, 1, 5, 12, 3, tzinfo=UTC)
IDENTIFIERS = [{"pointer": "/items/0/id", "value": 7}, {"pointer": "/items/0/owner_id", "value": "2"}]


def load_schema(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8")))


def make_record(**overrides: Any) -> CapturedRequest:
    fields: dict[str, Any] = {
        "role": "user",
        "account_id": "account:user_a",
        "method": "GET",
        "resource_type": "fetch",
        "url": f"{PAGE_URL}/api",
        "endpoint": "/mine/api",
        "status": 200,
        "query_params": {},
        "body_params": {},
        "resource_ids": [],
        "request_headers": {},
        "response_headers": {"content-type": "application/json"},
        "response_shape": {"items": [{"id": "int", "owner_id": "int"}], "next": "null|str"},
        "response_identifiers": IDENTIFIERS,
        "is_response_identifiers_truncated": True,
        "source_page": PAGE_URL,
        "source_action": "load",
        "captured_at": BASE_TIME,
    }
    fields.update(overrides)
    return CapturedRequest.model_validate(fields)


def make_page(links: list[PageLink], actions: list[PageAction]) -> DiscoveredPage:
    return DiscoveredPage(
        role="user",
        account_id="account:user_a",
        url=PAGE_URL,
        endpoint="/mine",
        title="Mine",
        status=200,
        depth=1,
        source_page=None,
        source_action="start",
        links=links,
        actions=actions,
    )


@pytest.fixture
def run_root(tmp_path: Path) -> Path:
    return tmp_path / "runs" / RUN_ID


@pytest.fixture
def writer(run_root: Path) -> EvidenceWriter:
    return EvidenceWriter(run_root, [ACCOUNT_PASSWORD])


def test_response_evidence_follows_schema(run_root: Path, writer: EvidenceWriter) -> None:
    ref = writer.write_response("request:3", make_record())
    assert ref is not None
    document = json.loads((run_root / ref["path"]).read_text(encoding="utf-8"))

    assert ref["path"] == "evidence/collector/response/request-3.json"
    assert list(load_schema("response_evidence.schema.json").iter_errors(document)) == []
    # 원래 타입 그대로: 7은 정수, "2"는 문자열
    assert document["identifiers"] == IDENTIFIERS
    assert document["identifiers_truncated"] is True


def test_non_json_response_writes_nothing(run_root: Path, writer: EvidenceWriter) -> None:
    record = make_record(response_shape=None, response_identifiers=None, is_response_identifiers_truncated=False)

    assert writer.write_response("request:1", record) is None
    assert not (run_root / "evidence").exists()


def test_dom_evidence_has_names_and_types_only(run_root: Path, writer: EvidenceWriter) -> None:
    link = PageLink(
        action_id="link:0",
        url=f"{PAGE_URL}?tab=1",
        endpoint="/mine",
        text=f"hint {ACCOUNT_PASSWORD}",
        is_state_changing=False,
        outcome="already_visited",
    )
    form = PageAction(
        action_id="form:0",
        kind="form",
        label="Delete",
        method="POST",
        target_url="http://127.0.0.1:1/mine/delete",
        fields=[FormField(name="csrf_token", type="hidden"), FormField(name="note", type="textarea")],
        is_state_changing=True,
        outcome="not_executed_state_changing",
    )
    button = PageAction(
        action_id="button:0",
        kind="button",
        label=ACCOUNT_PASSWORD,
        method=None,
        target_url=None,
        fields=[],
        is_state_changing=False,
        outcome="not_visible",
    )
    action_id_by_local = {"link:0": "action:5", "form:0": "action:6", "button:0": "action:7"}

    ref = writer.write_dom("page:2", make_page([link], [form, button]), action_id_by_local)
    assert ref is not None
    text = (run_root / ref["path"]).read_text(encoding="utf-8")
    document = json.loads(text)

    assert ref["path"] == "evidence/collector/dom/page-2.json"
    assert list(load_schema("dom_evidence.schema.json").iter_errors(document)) == []
    assert list(document["actions"]) == ["action:5", "action:6", "action:7"]
    assert document["actions"]["action:6"]["fields"] == [
        {"name": "csrf_token", "type": "hidden"},
        {"name": "note", "type": "textarea"},
    ]
    assert ACCOUNT_PASSWORD not in text
    assert document["actions"]["action:5"]["text"] == "hint ***"


def test_page_without_elements_writes_nothing(run_root: Path, writer: EvidenceWriter) -> None:
    assert writer.write_dom("page:1", make_page([], []), {}) is None
    assert not (run_root / "evidence").exists()


def test_same_evidence_not_written_twice(run_root: Path, writer: EvidenceWriter) -> None:
    first = writer.write_response("request:1", make_record())
    assert first is not None
    original = (run_root / first["path"]).read_bytes()

    with pytest.raises(ArtifactExistsError):
        EvidenceWriter(run_root, []).write_response("request:1", make_record(is_response_identifiers_truncated=False))

    assert (run_root / first["path"]).read_bytes() == original


def test_symlinked_evidence_dir_outside_rejected(run_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside_evidence"
    outside.mkdir()
    run_root.mkdir(parents=True)
    (run_root / "evidence").symlink_to(outside)

    with pytest.raises(OutputPathError):
        EvidenceWriter(run_root, []).write_response("request:1", make_record())

    assert list(outside.iterdir()) == []
