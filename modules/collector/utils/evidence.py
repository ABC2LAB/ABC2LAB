"""근거 파일(응답·DOM)을 만들어 원자적으로 쓰고 EvidenceRef를 돌려준다.

형식은 schemas/output/의 response_evidence·dom_evidence(PR #13에서 공개한 그대로).
- 위치: run_root/evidence/collector/<kind>/<대상 ID에서 :를 -로 바꾼 이름>.json (request-4.json, page-2.json)
- 한 번 쓴 근거는 바꾸지 않는다. 같은 이름이 이미 있으면 ArtifactExistsError.
- 공유본이라 redacted=true. 값은 이미 가린 것만 받고, 계정 비밀번호는 쓰기 전에 한 번 더 지운다.
"""

import hashlib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from modules.collector.core.auth import SECRET_MASK
from modules.collector.core.capture import list_secret_variants
from modules.collector.core.explorer import BUTTON_KIND, FORM_KIND, LINK_KIND
from modules.collector.core.models import CapturedRequest, DiscoveredPage, PageAction, PageLink
from modules.collector.utils.storage import prepare_evidence_dir, publish_file, serialize_json
from modules.collector.utils.validation import DOM_KIND, EVIDENCE_ROOT, RESPONSE_KIND

EVIDENCE_SCHEMA_VERSION = "0.1.0"
EVIDENCE_ID_FORMAT = "evidence:{kind}:{owner_id}"
ID_SEPARATOR = ":"
FILE_NAME_SEPARATOR = "-"
FILE_SUFFIX = ".json"


class EvidenceWriter:
    """한 실행(run_root)의 근거 파일을 쓴다. 폴더 확인은 kind마다 한 번."""

    def __init__(self, run_root: Path, known_secrets: Iterable[str]) -> None:
        self._run_root = run_root
        self._secret_variants = list_secret_variants(known_secrets)
        self._directory_by_kind: dict[str, Path] = {}

    def write_response(self, request_id: str, record: CapturedRequest) -> dict[str, Any] | None:
        """JSON 응답이면 shape·식별자 근거를 쓴다. JSON이 아니거나 읽지 못한 응답(shape=null)은 None."""
        if record.response_shape is None:
            return None
        identifiers = [identifier.model_dump() for identifier in record.response_identifiers or []]
        document = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "kind": RESPONSE_KIND,
            "evidence_id": make_evidence_id(RESPONSE_KIND, request_id),
            "request_id": request_id,
            "shape": record.response_shape,
            "identifiers": identifiers,
            "identifiers_truncated": record.is_response_identifiers_truncated,
        }
        return self._write(RESPONSE_KIND, request_id, document)

    def write_dom(
        self, page_id: str, page: DiscoveredPage, action_id_by_local: Mapping[str, str]
    ) -> dict[str, Any] | None:
        """페이지의 링크·폼·버튼 요약을 전역 action_id로 키잉해 쓴다. 요소가 하나도 없으면 None."""
        elements: dict[str, dict[str, Any]] = {}
        for link in page.links:
            elements[action_id_by_local[link.action_id]] = _describe_link(link)
        for action in page.actions:
            elements[action_id_by_local[action.action_id]] = _describe_action(action)
        if not elements:
            return None
        document = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "kind": DOM_KIND,
            "evidence_id": make_evidence_id(DOM_KIND, page_id),
            "page_id": page_id,
            "actions": elements,
        }
        return self._write(DOM_KIND, page_id, document)

    def _write(self, kind: str, owner_id: str, document: Mapping[str, Any]) -> dict[str, Any]:
        directory = self._directory_by_kind.get(kind)
        if directory is None:
            directory = prepare_evidence_dir(self._run_root, kind)
            self._directory_by_kind[kind] = directory
        file_name = f"{owner_id.replace(ID_SEPARATOR, FILE_NAME_SEPARATOR)}{FILE_SUFFIX}"
        raw = serialize_json(self._scrub(document))
        publish_file(directory, file_name, raw)
        return {
            "evidence_id": document["evidence_id"],
            "kind": kind,
            "path": str(EVIDENCE_ROOT / kind / file_name),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "redacted": True,
        }

    def _scrub(self, value: Any) -> Any:
        """라벨·링크 텍스트처럼 키로 못 거른 곳에 계정 비밀번호가 섞여도 남지 않게 지운다."""
        if isinstance(value, str):
            for secret in self._secret_variants:
                value = value.replace(secret, SECRET_MASK)
            return value
        if isinstance(value, dict):
            return {self._scrub(key): self._scrub(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._scrub(item) for item in value]
        return value


def make_evidence_id(kind: str, owner_id: str) -> str:
    return EVIDENCE_ID_FORMAT.format(kind=kind, owner_id=owner_id)


def _describe_link(link: PageLink) -> dict[str, Any]:
    return {
        "element": LINK_KIND,
        "text": link.text,
        "url": link.url,
        "endpoint": link.endpoint,
        "is_state_changing": link.is_state_changing,
        "outcome": link.outcome,
    }


def _describe_action(action: PageAction) -> dict[str, Any]:
    if action.kind == FORM_KIND:
        # 입력칸은 이름·타입만. explorer가 값을 읽지 않으므로 넣을 값도 없다.
        return {
            "element": FORM_KIND,
            "method": action.method,
            "target_url": action.target_url,
            "fields": [{"name": field.name, "type": field.type} for field in action.fields],
            "is_state_changing": action.is_state_changing,
            "outcome": action.outcome,
        }
    return {
        "element": BUTTON_KIND,
        "label": action.label,
        "is_state_changing": action.is_state_changing,
        "outcome": action.outcome,
    }
