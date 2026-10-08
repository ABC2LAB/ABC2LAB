"""근거 파일 작성과 비밀값 제거(collector와 같은 기준). 회차 포함 경로·불변성·마스킹을 본다."""

import hashlib
import json
from pathlib import Path

import pytest

from modules.verifier.executor import ReplayRequest, ReplayResponse
from modules.verifier.utils import storage
from modules.verifier.utils.evidence import EvidenceWriter


def _writer(tmp_path: Path, iteration: int = 0, known_secrets=()) -> EvidenceWriter:
    run_root = tmp_path / "run_demo_001"
    run_root.mkdir(parents=True, exist_ok=True)
    return EvidenceWriter(run_root, iteration, known_secrets=tuple(known_secrets))


def test_request_masks_headers_and_body(tmp_path: Path) -> None:
    writer = _writer(tmp_path, known_secrets=("hunter2",))
    request = ReplayRequest(
        "GET", "http://127.0.0.1:8001/orders/7",
        headers=(("Cookie", "sid=x"), ("Authorization", "Bearer t"), ("Accept", "application/json")),
        body={"note": "ok", "password": "hunter2", "token": "abc"},
    )
    ref = writer.write_request("vid-step1", request)
    document = json.loads((_writer_root(writer) / ref["path"]).read_bytes())
    headers = {h["name"]: h["value"] for h in document["headers"]}
    assert headers == {"cookie": "***", "authorization": "***", "accept": "application/json"}
    assert document["body"] == {"note": "ok", "password": "***", "token": "***"}
    assert "hunter2" not in json.dumps(document)  # 알려진 비밀값 스크럽


def test_response_masks_body(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    ref = writer.write_response("vid-step1", ReplayResponse(200, headers=(("Set-Cookie", "sid=x"),), body={"owner_id": 3, "session_token": "z"}))
    document = json.loads((_writer_root(writer) / ref["path"]).read_bytes())
    assert {h["name"]: h["value"] for h in document["headers"]} == {"set-cookie": "***"}
    assert document["body"] == {"owner_id": 3, "session_token": "***"}


def test_evidence_ref_shape_and_iteration_in_path(tmp_path: Path) -> None:
    writer = _writer(tmp_path, iteration=2)
    request = ReplayRequest("GET", "http://127.0.0.1:8001/x")
    ref = writer.write_request("vid-step1", request)
    assert ref["kind"] == "request" and ref["redacted"] is True
    assert ref["path"] == "evidence/verifier/iteration-002/vid-step1-request.json"
    raw = (_writer_root(writer) / ref["path"]).read_bytes()
    assert ref["sha256"] == hashlib.sha256(raw).hexdigest()


def test_evidence_is_immutable(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    request = ReplayRequest("GET", "http://127.0.0.1:8001/x")
    writer.write_request("vid-step1", request)
    with pytest.raises(storage.ArtifactExistsError):
        writer.write_request("vid-step1", request)


def _writer_root(writer: EvidenceWriter) -> Path:
    return writer.run_root
