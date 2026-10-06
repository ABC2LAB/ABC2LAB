"""로그인된 context에서 오가는 요청·응답을 CapturedRequest로 기록한다.

문서 이동과 fetch/XHR만 기록하고, 허용 origin 밖 요청은 auth 가드와 별개로 여기서도 거른다.
쿠키·Authorization·민감한 파라미터 값은 저장 전에 마스킹한다.
응답 본문은 content-type이 JSON일 때만 읽고 값 없이 키 구조+타입(response_shape)만 남긴다.

어떤 페이지의 어떤 행동이 낸 요청인지는 explorer가 set_source로 알려 준다. 값은 요청이 나가는 순간에
찍히므로 응답이 늦게 와도 원래 행동에 붙는다. 다만 sync API는 이벤트를 다음 Playwright 호출 때 처리하므로,
explorer는 행동의 요청이 다 나갈 때까지 기다린 뒤 source를 바꿔야 한다.

응답 본문을 받는 중에 페이지가 새 문서로 바뀌면 Playwright가 그 요청의 끝남 이벤트를 주지 않는다.
그런 요청은 새 문서가 뜰 때(domcontentloaded) 미완료로 기록하고 대기 목록에서 뺀다. 그래도 안 끝나는 요청
(롱 폴링, 문서가 안 바뀌는 이동 등)은 explorer가 대기 시간 초과 때 flush_pending으로 정리한다.
"""

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.errors import MessageError
from email.parser import BytesParser
from email.utils import collapse_rfc2231_value, parsedate_to_datetime
from urllib.parse import parse_qsl, quote, quote_plus, urlencode, urlsplit, urlunsplit

from playwright.sync_api import BrowserContext, Frame, Page, Request, Response
from playwright.sync_api import Error as PlaywrightError

from modules.collector.core.auth import SECRET_MASK
from modules.collector.core.config import CrawlerConfig, is_request_allowed
from modules.collector.core.normalize import normalize_path
from modules.collector.core.response_shape import describe_json_shape
from modules.collector.core.schemas import CapturedRequest, ShapeNode
from modules.collector.core.server_clock import ServerClockSample

logger = logging.getLogger(__name__)

# 허용 목록 방식이라 이미지·CSS·폰트·미디어·스크립트 같은 정적 리소스는 자동으로 빠진다.
RECORDED_RESOURCE_TYPES = frozenset({"document", "fetch", "xhr"})
# 키 이름에 이 조각이 들어 있으면 값을 가린다. 소문자, -는 _로 바꿔 비교한다.
SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "session",
    "sessid",
    "csrf",
    "xsrf",
    "api_key",
    "apikey",
    "credential",
    "jwt",
    "otp",
)
COOKIE_HEADER = "cookie"
SET_COOKIE_HEADER = "set-cookie"
AUTHORIZATION_HEADERS = frozenset({"authorization", "proxy-authorization"})
CONTENT_TYPE_HEADER = "content-type"
CONTENT_LENGTH_HEADER = "content-length"
DATE_HEADER = "date"
JSON_MEDIA_TYPE_SUFFIX = "+json"
# 이보다 큰 응답 본문은 모양을 만들지 않는다. 한 요청이 크롤링 전체 메모리를 먹지 않게 하려는 상한.
MAX_RESPONSE_BODY_BYTES = 1024 * 1024
FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
JSON_CONTENT_TYPE = "application/json"
MULTIPART_FORM_CONTENT_TYPE = "multipart/form-data"
MULTIPART_BOUNDARY_PARAM = "boundary="
# 파일 파트는 내용도 파일 이름도 남기지 않고 이 값으로만 적는다. 개인 파일·파일명일 수 있다.
FILE_PART_VALUE = "<file>"
COOKIE_PAIR_SEPARATOR = ";"
# all_headers()는 여러 Set-Cookie를 줄바꿈으로 이어서 준다.
SET_COOKIE_LINE_SEPARATOR = "\n"
URL_MASK_SAFE_CHARS = "*"


@dataclass(frozen=True)
class RequestSource:
    source_page: str | None
    source_action: str | None
    requested_at: datetime


class RequestCapture:
    """한 역할의 context에 붙어 요청을 모은다. start_capture로 만든다."""

    def __init__(self, config: CrawlerConfig, role: str) -> None:
        if role not in config.roles:
            raise ValueError(f"설정에 없는 역할: {role}")
        self._config = config
        self._role = role
        self._source_page: str | None = None
        self._source_action: str | None = None
        self._pending_sources: dict[Request, RequestSource] = {}
        # 대기 중 요청의 응답 헤더. 끝남 이벤트 없이 정리할 때 request.response()는 응답이 없으면 계속 기다리므로 이벤트로 받아 둔다.
        self._responses: dict[Request, Response] = {}
        # 메인 프레임 문서가 바뀌는 순간 대기 중이던 옛 문서의 요청. 새 문서가 뜨면 정리한다.
        self._stale_candidates: dict[Page, list[Request]] = {}
        # 응답 받은 시각과 서버 Date. run이 대상 서버 시계 역행을 감지하는 데 쓴다. 결과 파일에는 넣지 않는다.
        self._clock_samples: list[ServerClockSample] = []
        self._records: list[CapturedRequest] = []
        # 목록에 안 걸리는 이름이어도 설정의 로그인 비밀번호 필드는 가린다.
        self._sensitive_field_names = frozenset({config.login.password_field.lower()} if config.login else ())
        self._known_secrets = list_secret_variants(account.password for account in config.accounts.values())

    @property
    def records(self) -> tuple[CapturedRequest, ...]:
        return tuple(self._records)

    @property
    def has_pending_requests(self) -> bool:
        """기록 대상 요청 중 아직 끝나지 않은 게 있는지. explorer가 행동 뒤 잦아듦을 판단할 때 쓴다."""
        return bool(self._pending_sources)

    @property
    def clock_samples(self) -> tuple[ServerClockSample, ...]:
        return tuple(self._clock_samples)

    def set_source(self, source_page: str | None, source_action: str | None) -> None:
        """이후 나가는 요청에 붙일 페이지와 행동. 둘 다 None이면 해제."""
        self._source_page = source_page
        self._source_action = source_action

    def attach(self, context: BrowserContext) -> None:
        context.on("request", self._on_request)
        context.on("response", self._on_response)
        context.on("requestfinished", self._on_request_done)
        context.on("requestfailed", self._on_request_done)
        context.on("page", self._watch_page)
        for page in context.pages:
            self._watch_page(page)

    def flush_pending(self) -> list[str]:
        """아직 안 끝난 요청을 전부 미완료로 기록하고 대기 목록에서 뺀다. 정리한 요청의 endpoint를 돌려준다."""
        requests = list(self._pending_sources)
        for request in requests:
            self._record_unfinished(request)
        return [normalize_path(request.url).template for request in requests]

    def _watch_page(self, page: Page) -> None:
        page.on("framenavigated", lambda frame: self._on_frame_navigated(page, frame))
        page.on("domcontentloaded", lambda _: self._on_document_loaded(page))

    def _on_frame_navigated(self, page: Page, frame: Frame) -> None:
        # pushState·해시 이동에도 불리므로 여기서 바로 끊지 않고 후보만 적어 둔다. 새 문서면 domcontentloaded가 이어진다.
        if frame != page.main_frame:
            return
        self._stale_candidates[page] = select_stale_requests(self._pending_sources, frame)

    def _on_document_loaded(self, page: Page) -> None:
        candidates = self._stale_candidates.pop(page, [])
        stale = [request for request in candidates if request in self._pending_sources]
        for request in stale:
            self._record_unfinished(request)
        if stale:
            logger.debug("새 문서로 바뀌며 끝나지 않은 요청 %d개를 미완료로 기록", len(stale))

    def _on_response(self, response: Response) -> None:
        if response.request not in self._pending_sources:
            return
        self._responses[response.request] = response
        sample = _read_clock_sample(response)
        if sample is not None:
            self._clock_samples.append(sample)

    def _on_request(self, request: Request) -> None:
        if request.resource_type not in RECORDED_RESOURCE_TYPES:
            return
        if not is_request_allowed(self._config, request.url):
            return
        self._pending_sources[request] = RequestSource(self._source_page, self._source_action, datetime.now(UTC))

    def _on_request_done(self, request: Request) -> None:
        source = self._pending_sources.pop(request, None)
        self._responses.pop(request, None)
        if source is None:
            return
        if request.failure is not None:
            logger.debug("요청 실패로 응답 없이 기록: %s", request.failure)
        self._records.append(self._build_record(request, source, _read_response(request), is_body_readable=True))

    def _record_unfinished(self, request: Request) -> None:
        """본문을 다 받지 못한 요청. 응답 헤더가 왔으면 그 status를 남기고 본문 모양은 null."""
        source = self._pending_sources.pop(request)
        response = self._responses.pop(request, None)
        self._records.append(self._build_record(request, source, response, is_body_readable=False))

    def _build_record(
        self, request: Request, source: RequestSource, response: Response | None, is_body_readable: bool
    ) -> CapturedRequest:
        request_headers = _read_headers(request)
        response_headers = _read_headers(response) if response is not None else {}
        normalized = normalize_path(request.url)
        fields = {
            "role": self._role,
            "method": request.method.upper(),
            "resource_type": request.resource_type,
            "url": self._mask_url_query(request.url),
            "endpoint": normalized.template,
            "status": response.status if response is not None else None,
            "query_params": self._mask_params(parse_qsl(urlsplit(request.url).query, keep_blank_values=True)),
            "body_params": self._parse_body(request, request_headers.get(CONTENT_TYPE_HEADER, "")),
            "resource_ids": list(normalized.path_values),
            "request_headers": self._mask_headers(request_headers),
            "response_headers": self._mask_headers(response_headers),
            "response_shape": _read_response_shape(response, response_headers) if is_body_readable else None,
            "source_page": source.source_page,
            "source_action": source.source_action,
            "captured_at": source.requested_at,
        }
        # 키로 못 잡은 곳(경로, 엉뚱한 키의 값 등)에 계정 비밀번호가 섞여 나가도 남지 않게 마지막에 한 번 더 훑는다.
        return CapturedRequest.model_validate(self._scrub_known_secrets(fields))

    def _is_sensitive_key(self, key: str) -> bool:
        lowered = key.lower().replace("-", "_")
        return lowered in self._sensitive_field_names or any(part in lowered for part in SENSITIVE_KEY_PARTS)

    def _mask_params(self, pairs: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
        params: dict[str, list[str]] = {}
        for key, value in pairs:
            params.setdefault(key, []).append(SECRET_MASK if self._is_sensitive_key(key) else value)
        return params

    def mask_url(self, url: str) -> str:
        """기록과 같은 규칙으로 URL을 가린다. explorer의 페이지 URL이 기록의 source_page와 그대로 맞아야 한다."""
        return self._scrub_text(self._mask_url_query(url))

    def _mask_url_query(self, url: str) -> str:
        parts = urlsplit(url)
        # user:pw@host 형태의 자격 증명은 통째로 뺀다.
        netloc = parts.netloc.rpartition("@")[2]
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        masked_pairs = [(key, SECRET_MASK if self._is_sensitive_key(key) else value) for key, value in pairs]
        query = urlencode(masked_pairs, safe=URL_MASK_SAFE_CHARS) if pairs else parts.query
        return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))

    def _parse_body(self, request: Request, content_type: str) -> dict[str, list[str]]:
        body = _read_body(request)
        if not body:
            return {}
        media_type = _media_type(content_type)
        if media_type == FORM_CONTENT_TYPE:
            return self._mask_params(parse_qsl(_decode_text(body), keep_blank_values=True))
        if media_type == JSON_CONTENT_TYPE:
            return self._parse_json_body(_decode_text(body))
        if media_type == MULTIPART_FORM_CONTENT_TYPE:
            # 이름·값 쌍으로 풀면 마스킹 규칙은 urlencoded와 같다.
            return self._mask_params(_parse_multipart(content_type, body))
        logger.debug("바디 파라미터를 풀지 않는 형식: %s", media_type or "(없음)")
        return {}

    def _parse_json_body(self, body: str) -> dict[str, list[str]]:
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as error:
            logger.warning("JSON 바디 해석 실패, 파라미터 없이 기록: %s", error.msg)
            return {}
        if not isinstance(parsed, dict):
            logger.debug("최상위가 객체가 아닌 JSON 바디라 파라미터 없이 기록")
            return {}
        masked = self._mask_json(parsed)
        return {key: [_to_param_text(value)] for key, value in masked.items()}

    def _mask_json(self, value: object) -> object:
        if isinstance(value, dict):
            return {
                key: SECRET_MASK if self._is_sensitive_key(key) else self._mask_json(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._mask_json(item) for item in value]
        return value

    def _mask_headers(self, headers: dict[str, str]) -> dict[str, str]:
        masked: dict[str, str] = {}
        for name, value in headers.items():
            lowered = name.lower()
            if lowered == COOKIE_HEADER:
                masked[lowered] = _mask_cookie_header(value)
            elif lowered == SET_COOKIE_HEADER:
                masked[lowered] = SET_COOKIE_LINE_SEPARATOR.join(
                    _mask_set_cookie_line(line) for line in value.split(SET_COOKIE_LINE_SEPARATOR)
                )
            elif lowered in AUTHORIZATION_HEADERS:
                masked[lowered] = _mask_authorization(value)
            elif self._is_sensitive_key(lowered):
                masked[lowered] = SECRET_MASK
            else:
                masked[lowered] = value
        return masked

    def _scrub_known_secrets(self, value: object) -> object:
        if isinstance(value, str):
            return self._scrub_text(value)
        if isinstance(value, dict):
            return {self._scrub_known_secrets(key): self._scrub_known_secrets(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._scrub_known_secrets(item) for item in value]
        return value

    def _scrub_text(self, text: str) -> str:
        for secret in self._known_secrets:
            text = text.replace(secret, SECRET_MASK)
        return text


def start_capture(context: BrowserContext, config: CrawlerConfig, role: str) -> RequestCapture:
    """context에 캡처를 붙여 돌려준다. 붙인 뒤에 나가는 요청부터 기록된다."""
    capture = RequestCapture(config, role)
    capture.attach(context)
    return capture


def list_secret_variants(secrets: Iterable[str]) -> tuple[str, ...]:
    """비밀값의 원문·URL 인코딩 형태를 긴 것부터. run이 에러 메시지를 가릴 때도 같은 규칙을 쓴다."""
    variants = {
        variant for secret in secrets if secret for variant in (secret, quote(secret, safe=""), quote_plus(secret))
    }
    # 긴 것부터 바꿔야 인코딩된 형태가 원문 치환에 쪼개지지 않는다.
    return tuple(sorted(variants, key=len, reverse=True))


def _to_param_text(value: object) -> str:
    # 중첩 객체·숫자는 JSON 문자열로 남겨 원래 모양을 잃지 않게 한다.
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _read_response(request: Request) -> Response | None:
    if request.failure is not None:
        return None
    try:
        return request.response()
    except PlaywrightError as error:
        logger.warning("응답을 읽지 못해 상태 없이 기록: %s", error.message.splitlines()[0] if error.message else "")
        return None


def select_stale_requests(pending_requests: Iterable[Request], frame: Frame) -> list[Request]:
    """frame의 문서가 바뀔 때 옛 문서 소속으로 볼 대기 요청. 새 문서가 뜨면 이들을 미완료로 정리한다.

    새 문서 자신(navigation 요청)은 보통 domcontentloaded 전에 끝나지만 순서가 보장되지 않아 뺀다.
    """
    return [
        request
        for request in pending_requests
        if not request.is_navigation_request() and _frame_of(request) == frame
    ]


def _read_clock_sample(response: Response) -> ServerClockSample | None:
    # headers는 IPC 없이 읽히는 속성이라 이벤트 처리 중에도 안전하다.
    date_text = response.headers.get(DATE_HEADER)
    if not date_text:
        return None
    try:
        server_date = parsedate_to_datetime(date_text)
    except (TypeError, ValueError):
        logger.debug("해석할 수 없는 Date 헤더는 시계 비교에서 뺌")
        return None
    if server_date.tzinfo is None:
        return None
    return ServerClockSample(datetime.now(UTC), server_date)


def _frame_of(request: Request) -> Frame | None:
    try:
        return request.frame
    except PlaywrightError:
        # 서비스워커 요청은 프레임이 없다(auth가 서비스워커를 막지만 방어).
        return None


def _read_headers(message: Request | Response) -> dict[str, str]:
    try:
        return message.all_headers()
    except PlaywrightError as error:
        logger.warning("헤더를 읽지 못해 빈 값으로 기록: %s", error.message.splitlines()[0] if error.message else "")
        return {}


def _media_type(content_type: str) -> str:
    return content_type.split(";")[0].strip().lower()


def _is_json_media_type(media_type: str) -> bool:
    return media_type == JSON_CONTENT_TYPE or media_type.endswith(JSON_MEDIA_TYPE_SUFFIX)


def _read_response_shape(response: Response | None, headers: dict[str, str]) -> ShapeNode | None:
    """JSON 응답의 키 구조+타입. 본문 원문은 이 함수 밖으로 나가지 않는다."""
    if response is None:
        return None
    # resource_type이 아니라 content-type으로 본다. 링크로 바로 연 JSON API도 문서 요청이라서다.
    if not _is_json_media_type(_media_type(headers.get(CONTENT_TYPE_HEADER, ""))):
        return None
    declared_length = headers.get(CONTENT_LENGTH_HEADER, "")
    if declared_length.isdigit() and int(declared_length) > MAX_RESPONSE_BODY_BYTES:
        logger.info("응답 본문이 커서 모양 없이 기록: %s바이트", declared_length)
        return None
    try:
        body = response.body()
    except PlaywrightError as error:
        logger.warning("응답 본문을 읽지 못해 모양 없이 기록: %s", error.message.splitlines()[0] if error.message else "")
        return None
    if len(body) > MAX_RESPONSE_BODY_BYTES:
        logger.info("응답 본문이 커서 모양 없이 기록: %d바이트", len(body))
        return None
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        # 예외 메시지에 본문 조각이 섞이지 않게 종류만 남긴다.
        logger.debug("JSON 응답 해석 실패, 모양 없이 기록: %s", type(error).__name__)
        return None
    return describe_json_shape(parsed)


def _read_body(request: Request) -> bytes | None:
    # 바이트로 둔다. multipart의 파일 바이트를 문자열로 먼저 풀면 경계가 깨질 수 있다.
    return request.post_data_buffer


def _decode_text(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")


def _parse_multipart(content_type: str, body: bytes) -> list[tuple[str, str]]:
    """multipart/form-data 본문을 (이름, 값) 쌍으로. 파일 파트는 값을 FILE_PART_VALUE로만 남긴다."""
    if MULTIPART_BOUNDARY_PARAM not in content_type.lower():
        logger.warning("boundary 없는 multipart 바디라 파라미터 없이 기록")
        return []
    try:
        # 표준 라이브러리 email 파서는 헤더부터 읽으므로 Content-Type 줄을 앞에 붙여 한 메시지로 만든다.
        message = BytesParser(policy=policy.HTTP).parsebytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
        if not message.is_multipart():
            logger.warning("multipart 바디를 파트로 나누지 못해 파라미터 없이 기록")
            return []
        pairs: list[tuple[str, str]] = []
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if isinstance(name, tuple):
                name = collapse_rfc2231_value(name)
            if not name:
                logger.debug("이름 없는 multipart 파트는 건너뜀")
                continue
            if part.get_filename() is not None:
                pairs.append((name, FILE_PART_VALUE))
            else:
                pairs.append((name, _decode_text(part.get_payload(decode=True) or b"")))
        return pairs
    except (MessageError, ValueError, LookupError) as error:
        # 예외 메시지에 본문 조각이 섞이지 않게 종류만 남긴다.
        logger.warning("multipart 바디 해석 실패, 파라미터 없이 기록: %s", type(error).__name__)
        return []


def _mask_cookie_header(value: str) -> str:
    names = [pair.split("=", 1)[0].strip() for pair in value.split(COOKIE_PAIR_SEPARATOR) if pair.strip()]
    return f"{COOKIE_PAIR_SEPARATOR} ".join(f"{name}={SECRET_MASK}" for name in names)


def _mask_set_cookie_line(line: str) -> str:
    # 첫 조각만 이름=값이고 나머지(Path, HttpOnly 등)는 속성이라 남긴다.
    first, *attributes = line.split(COOKIE_PAIR_SEPARATOR)
    name = first.split("=", 1)[0].strip()
    return COOKIE_PAIR_SEPARATOR.join([f"{name}={SECRET_MASK}", *attributes])


def _mask_authorization(value: str) -> str:
    scheme, _, credentials = value.strip().partition(" ")
    return f"{scheme} {SECRET_MASK}" if credentials else SECRET_MASK
