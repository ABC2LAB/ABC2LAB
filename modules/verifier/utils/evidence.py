"""재현 요청·응답 근거 파일 작성과 비밀값 제거.

전송한 단계마다 비밀을 뺀 요청·응답을 evidence/verifier/iteration-<NNN>/에 쓰고 EvidenceRef를 돌려준다(명세 m7).
비밀 판정은 collector와 같은 기준을 자기 폴더에 재구현한다(import하지 않음): 민감 키 이름 조각·cookie/authorization 헤더는
`***`로 가린다. verifier는 계정 비밀번호를 쥐지 않으므로(세션은 collector 창구 안) 알려진 비밀값 목록 스크럽은 선택이다.
collector와 다른 점은 README "비밀값 제거"에 적는다.
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from modules.verifier.executor import ReplayRequest, ReplayResponse
from modules.verifier.utils.storage import prepare_evidence_dir, publish_file, serialize_json

SECRET_MASK = "***"
# 키 이름에 이 조각이 들어 있으면 값을 가린다(collector와 같은 목록). 소문자·-는 _로 바꿔 비교한다.
SENSITIVE_KEY_PARTS = (
    "password", "passwd", "pwd", "token", "secret", "session", "sessid",
    "csrf", "xsrf", "api_key", "apikey", "credential", "jwt", "otp",
)
COOKIE_HEADER = "cookie"
SET_COOKIE_HEADER = "set-cookie"
AUTHORIZATION_HEADERS = frozenset({"authorization", "proxy-authorization"})
REQUEST_KIND = "request"
RESPONSE_KIND = "response"


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact_value(value: Any) -> Any:
    """dict·list를 재귀로 돌며 민감 키의 값을 가린다(collector _mask_json과 같은 규칙)."""
    if isinstance(value, dict):
        return {key: SECRET_MASK if is_sensitive_key(str(key)) else redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def redact_headers(headers: Iterable[tuple[str, str]]) -> list[dict[str, str]]:
    """cookie·authorization·민감 키 헤더 값을 가린다. 이름은 소문자로 남긴다."""
    redacted: list[dict[str, str]] = []
    for name, value in headers:
        lowered = name.lower()
        if lowered in {COOKIE_HEADER, SET_COOKIE_HEADER} or lowered in AUTHORIZATION_HEADERS or is_sensitive_key(lowered):
            redacted.append({"name": lowered, "value": SECRET_MASK})
        else:
            redacted.append({"name": lowered, "value": value})
    return redacted


def _scrub_text(text: str, known_secrets: tuple[str, ...]) -> str:
    for secret in known_secrets:
        if secret:
            text = text.replace(secret, SECRET_MASK)
    return text


@dataclass(frozen=True)
class EvidenceWriter:
    """한 실행(run_root·iteration)의 근거 파일을 쓴다. 파일 이름에 회차를 담은 폴더를 쓴다."""

    run_root: Path
    iteration: int
    # verifier는 보통 비어 있다(세션 비밀은 collector 창구 안). 있으면 문자열 스크럽을 더한다.
    known_secrets: tuple[str, ...] = ()

    def write_request(self, evidence_id: str, request: ReplayRequest) -> dict[str, Any]:
        body = redact_value(request.body)
        document = {
            "method": request.method,
            "url": _scrub_text(request.url, self.known_secrets),
            "headers": redact_headers(request.headers),
            "body": _scrub_text(body, self.known_secrets) if isinstance(body, str) else body,
        }
        return self._publish(evidence_id, REQUEST_KIND, document)

    def write_response(self, evidence_id: str, response: ReplayResponse) -> dict[str, Any]:
        body = redact_value(response.body)
        document = {
            "status_code": response.status_code,
            "headers": redact_headers(response.headers),
            "body": _scrub_text(body, self.known_secrets) if isinstance(body, str) else body,
        }
        return self._publish(evidence_id, RESPONSE_KIND, document)

    def _publish(self, evidence_id: str, kind: str, document: dict[str, Any]) -> dict[str, Any]:
        directory = prepare_evidence_dir(self.run_root, self.iteration)
        file_name = f"{evidence_id}-{kind}.json"
        raw = serialize_json(document)
        path = publish_file(directory, file_name, raw)
        relative = path.resolve().relative_to(self.run_root.resolve()).as_posix()
        return {
            "evidence_id": evidence_id,
            "kind": kind,
            "path": relative,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "redacted": True,
        }
