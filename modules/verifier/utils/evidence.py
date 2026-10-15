"""재현 요청·응답 근거 파일 작성과 비밀값 제거.

전송한 단계마다 비밀을 뺀 요청·응답을 evidence/verifier/iteration-<NNN>/에 쓰고 EvidenceRef를 돌려준다(명세 m7).
비밀 판정은 collector와 같은 기준을 자기 폴더에 재구현한다(import하지 않음): 민감 키 이름 조각·cookie/authorization 헤더는
`***`로 가린다. verifier는 계정 비밀번호를 쥐지 않으므로(세션은 collector 창구 안) 알려진 비밀값 목록 스크럽은 선택이다.
collector와 다른 점은 README "비밀값 제거"에 적는다.

파일명은 evidence_id 원문이 아니라 안전하게 파생한다. evidence_id는 scenario_id·candidate_id를 담는데 그 ID들은 계약상
불투명 문자열이라 어떤 문자든(지금은 endpoint_id의 `/`) 들어올 수 있고, 임의 웹앱 대상이라 문자 집합을 가정하지 않는다.
ID는 그대로 두고 파일명만 whitelist + 해시 접미사로 만들어 경로 해석·충돌·파일명 길이 상한을 같이 막는다.
"""

import hashlib
import re
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
# 파일명에 남기는 문자. 그 밖(경로 구분자·공백·유니코드 등)은 _로 바꾼다.
UNSAFE_FILE_NAME_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]")
# 줄기 길이 상한. 접미사(해시 8 + kind + .json)를 더해도 파일시스템 한도(255바이트)에 여유가 있다.
MAX_FILE_STEM_LENGTH = 120
DIGEST_SUFFIX_LENGTH = 8
FALLBACK_FILE_STEM = "evidence"


def safe_file_stem(evidence_id: str) -> str:
    """evidence_id에서 파일명 줄기를 파생한다. 허용 문자만 남기고, 선두 '.'(숨김·상위 경로)을 떼고, 길이를 자른 뒤
    원문 SHA-256 앞 8자를 붙인다. 치환·절단으로 같아진 다른 ID끼리 접미사로 갈린다."""
    digest = hashlib.sha256(evidence_id.encode("utf-8")).hexdigest()[:DIGEST_SUFFIX_LENGTH]
    stem = UNSAFE_FILE_NAME_CHARACTERS.sub("_", evidence_id).lstrip(".")[:MAX_FILE_STEM_LENGTH] or FALLBACK_FILE_STEM
    return f"{stem}-{digest}"


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
        # evidence_id 원문은 EvidenceRef에만 남기고 파일명은 파생값을 쓴다(위 모듈 설명).
        file_name = f"{safe_file_stem(evidence_id)}-{kind}.json"
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
