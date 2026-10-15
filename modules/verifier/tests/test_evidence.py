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
    # 파일명은 evidence_id 줄기 + 원문 SHA-256 앞 8자 + kind. evidence_id 자체는 원문이다.
    digest = hashlib.sha256(b"vid-step1").hexdigest()[:8]
    assert ref["path"] == f"evidence/verifier/iteration-002/vid-step1-{digest}-request.json"
    assert ref["evidence_id"] == "vid-step1"
    raw = (_writer_root(writer) / ref["path"]).read_bytes()
    assert ref["sha256"] == hashlib.sha256(raw).hexdigest()


def test_evidence_id_with_path_characters_becomes_one_safe_file(tmp_path: Path) -> None:
    # scenario_id·candidate_id는 불투명 문자열이라 '/'·'|'·':'가 들어온다(수직 규칙 후보의 endpoint_id). 원문을 파일명에 쓰면
    # '/'가 경로로 해석돼 터진다. 파일 하나가 iteration 폴더 바로 아래 생기고 evidence_id는 원문 그대로여야 한다.
    writer = _writer(tmp_path, iteration=0)
    evidence_id = "verification_scenario_rule_vertical_access:acc_guest|acc_admin|endpoint:GET:/admin/users/{id}-s1-hop0"
    ref = writer.write_request(evidence_id, ReplayRequest("GET", "http://127.0.0.1:8001/admin"))
    assert ref["evidence_id"] == evidence_id
    written = _writer_root(writer) / ref["path"]
    assert written.is_file()
    assert written.parent == _writer_root(writer) / "evidence" / "verifier" / "iteration-000"
    assert written.resolve().is_relative_to(_writer_root(writer).resolve())
    assert "/" not in written.name and "|" not in written.name and ":" not in written.name


def test_ids_with_the_same_safe_stem_get_different_files(tmp_path: Path) -> None:
    # 치환으로 줄기가 같아지는 두 ID(a/b ↔ a_b)는 해시 접미사로 갈려야 한다. 아니면 둘째가 ArtifactExistsError거나 덮어쓴다.
    writer = _writer(tmp_path)
    request = ReplayRequest("GET", "http://127.0.0.1:8001/x")
    first = writer.write_request("vid-a/b", request)
    second = writer.write_request("vid-a_b", request)
    assert first["path"] != second["path"]
    assert (_writer_root(writer) / first["path"]).is_file() and (_writer_root(writer) / second["path"]).is_file()


def test_long_evidence_id_stays_under_file_name_limit(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    long_id = "x" * 300
    ref = writer.write_response(long_id, ReplayResponse(200, body={"ok": True}))
    name = Path(ref["path"]).name
    assert len(name.encode("utf-8")) <= 255
    assert (_writer_root(writer) / ref["path"]).is_file()
    assert ref["evidence_id"] == long_id


def test_evidence_is_immutable(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    request = ReplayRequest("GET", "http://127.0.0.1:8001/x")
    writer.write_request("vid-step1", request)
    with pytest.raises(storage.ArtifactExistsError):
        writer.write_request("vid-step1", request)


def _writer_root(writer: EvidenceWriter) -> Path:
    return writer.run_root
