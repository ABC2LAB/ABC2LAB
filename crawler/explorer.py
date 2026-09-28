"""로그인된 context로 대상 앱을 BFS로 돌며 페이지마다 링크·폼·버튼을 기록한다.

로그인은 하지 않는다(auth 몫). 방문 기준은 normalize 템플릿이라 /items/1, /items/2 중 처음 만난 것만 연다.
각 역할은 페이지에서 만난 링크·행동만 따라가고 URL을 지어내 요청하지 않는다(규칙 5).

상태를 바꿀 수 있는 동작은 기본적으로 실행하지 않고 기록만 한다(규칙 6).
- 폼: GET이 아니면 상태 변경으로 본다.
- 링크·GET 폼·버튼: 이름(href 경로, 텍스트, id 등)에 설정 키워드가 들어 있으면 상태 변경으로 본다.
- 키워드에 안 걸린 버튼이 POST 같은 요청을 내면 route 가드가 서버에 닿기 전에 끊는다.
설정 can_change_state가 true면 전부 실행한다.

행동마다 capture.set_source(페이지, 행동)를 걸고 그 행동의 요청이 다 끝난 뒤 다음 행동으로 넘어간다.
sync API는 이벤트를 다음 Playwright 호출 때 처리하므로, 기다리지 않으면 요청이 다음 행동에 붙는다.
"""

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import BrowserContext, Locator, Page, Route
from playwright.sync_api import Error as PlaywrightError

from crawler.capture import RequestCapture
from crawler.config import CrawlerConfig, is_request_allowed
from crawler.normalize import normalize_path
from crawler.schemas import DiscoveredPage, FormField, PageAction, PageLink

logger = logging.getLogger(__name__)

# source_action 예약어. 나머지는 "link:0", "form:1", "button:2"처럼 페이지 안 순번이다.
START_ACTION = "start"
LOAD_ACTION = "load"
REVISIT_ACTION = "revisit"
LINK_KIND = "link"
FORM_KIND = "form"
BUTTON_KIND = "button"

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
GET_METHOD = "GET"
ALL_URLS_PATTERN = "**/*"
BLOCKED_ERROR_CODE = "blockedbyclient"
NAVIGATION_TIMEOUT_MS = 10_000
ACTION_TIMEOUT_MS = 5_000
QUIET_POLL_MS = 50
# 이만큼 진행 중 요청이 없으면 행동이 끝났다고 본다. 클릭 뒤 setTimeout으로 조금 늦게 나가는 fetch까지 잡으려는 여유.
QUIET_WINDOW_S = 0.3
QUIET_TIMEOUT_S = 5.0

FORM_SELECTOR = "form"
# form 안 버튼은 폼 제출로 따로 다루므로 form 밖 버튼만 센다.
BUTTON_SELECTOR = ":is(button, input[type=button], input[type=submit], [role=button]):not(form *)"
# 브라우저 검증(required 등)만 끄고 제출한다. 값은 넣지 않는다(규칙 5). submit 이벤트는 그대로 나가므로
# JS가 가로채 fetch를 보내는 폼도 관찰된다.
SUBMIT_FORM_SCRIPT = "form => { form.noValidate = true; HTMLFormElement.prototype.requestSubmit.call(form); }"
READ_LABEL_SCRIPT = "el => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim() || null"
# 요청을 내지 않고 DOM만 읽는다. form.action·form.elements는 name="action" 같은 input에 가려질 수 있어 속성·셀렉터로 읽는다.
# 입력칸은 이름과 타입만 읽고 값은 읽지 않는다. input.type은 브라우저가 소문자·"text" 기본값으로 정규화해 준다.
READ_ELEMENTS_SCRIPT = (
    """buttonSelector => {
  const labelOf = """
    + READ_LABEL_SCRIPT
    + """;
  const hintsOf = el => [el.id, el.getAttribute('name'), el.getAttribute('aria-label'), el.getAttribute('onclick')]
    .filter(Boolean);
  const links = [...document.querySelectorAll('a[href]')]
    .map(a => ({href: a.href, text: (a.innerText || '').trim() || null}));
  const forms = [...document.querySelectorAll('form')].map(form => {
    const method = (form.getAttribute('method') || 'get').toUpperCase();
    const action = new URL(form.getAttribute('action') ?? '', document.baseURI);
    action.hash = '';
    const seen = new Set();
    const fields = [];
    for (const el of form.querySelectorAll('[name]')) {
      const field = {name: el.getAttribute('name'), type: el.tagName === 'INPUT' ? el.type : el.tagName.toLowerCase()};
      // 라디오 그룹처럼 같은 이름·타입이 반복되면 하나로 남긴다.
      const key = JSON.stringify(field);
      if (!seen.has(key)) {
        seen.add(key);
        fields.push(field);
      }
    }
    const submitter = form.querySelector('button, input[type=submit]');
    return {method, action: action.href, fields,
            label: submitter ? labelOf(submitter) : null, hints: hintsOf(form)};
  });
  const buttons = [...document.querySelectorAll(buttonSelector)]
    .map(b => ({label: labelOf(b), hints: hintsOf(b), is_visible: b.getClientRects().length > 0}));
  return {links, forms, buttons};
}"""
)


class ActionOutcome(StrEnum):
    EXECUTED = "executed"
    ENQUEUED = "enqueued"
    ALREADY_VISITED = "already_visited"
    BEYOND_MAX_DEPTH = "beyond_max_depth"
    NOT_EXECUTED_STATE_CHANGING = "not_executed_state_changing"
    BLOCKED_STATE_CHANGING_REQUEST = "blocked_state_changing_request"
    OUTSIDE_ORIGIN = "outside_origin"
    NOT_VISIBLE = "not_visible"
    NOT_FOUND = "not_found"
    FAILED = "failed"


@dataclass(frozen=True)
class _QueueEntry:
    url: str
    depth: int
    source_page: str | None
    source_action: str


@dataclass(frozen=True)
class _CurrentPage:
    url: str
    masked_url: str
    endpoint: str
    depth: int


@dataclass(frozen=True)
class _FoundLink:
    url: str
    text: str | None


@dataclass(frozen=True)
class _FoundForm:
    method: str
    action: str
    fields: tuple[FormField, ...]
    label: str | None
    hints: tuple[str, ...]


@dataclass(frozen=True)
class _FoundButton:
    label: str | None
    hints: tuple[str, ...]
    is_visible: bool


@dataclass(frozen=True)
class _FoundElements:
    links: tuple[_FoundLink, ...]
    forms: tuple[_FoundForm, ...]
    buttons: tuple[_FoundButton, ...]


@dataclass(frozen=True)
class _PageFindings:
    title: str | None = None
    links: tuple[PageLink, ...] = ()
    actions: tuple[PageAction, ...] = ()


# 열기 실패·리다이렉트 중복처럼 추출하지 않은 페이지에 쓴다.
EMPTY_FINDINGS = _PageFindings()


@dataclass(frozen=True)
class _ActionStep:
    action_id: str
    selector: str
    index: int
    # 버튼은 추출 때 라벨과 지금 라벨이 같아야 같은 버튼으로 본다. 폼은 확인하지 않는다(None).
    expected_label: str | None
    perform: Callable[[Locator], None]


def crawl(context: BrowserContext, config: CrawlerConfig, role: str, capture: RequestCapture) -> list[DiscoveredPage]:
    """로그인된 context로 config.start_url부터 BFS 탐색해 방문한 페이지를 순서대로 돌려준다. context는 닫지 않는다."""
    page = context.new_page()
    page.set_default_timeout(NAVIGATION_TIMEOUT_MS)
    explorer = _Explorer(config=config, role=role, capture=capture, page=page)
    guard = explorer.guard_state_change
    context.route(ALL_URLS_PATTERN, guard)
    try:
        pages = explorer.run()
    finally:
        capture.set_source(None, None)
        context.unroute(ALL_URLS_PATTERN, guard)
        page.close()
    logger.info("%s 탐색 끝: 페이지 %d개", role, len(pages))
    return pages


@dataclass
class _Explorer:
    config: CrawlerConfig
    role: str
    capture: RequestCapture
    page: Page
    queue: deque[_QueueEntry] = field(default_factory=deque)
    visited: set[str] = field(default_factory=set)
    blocked_count: int = 0

    def run(self) -> list[DiscoveredPage]:
        self._enqueue(self.config.start_url, 0, None, START_ACTION)
        pages: list[DiscoveredPage] = []
        while self.queue:
            pages.append(self._visit(self.queue.popleft()))
        return pages

    def guard_state_change(self, route: Route) -> None:
        """규칙 6의 안전장치: 허용 설정이 없으면 GET·HEAD·OPTIONS 외 요청은 서버에 닿기 전에 끊는다."""
        method = route.request.method.upper()
        if self.config.can_change_state or method in SAFE_METHODS:
            # auth가 먼저 건 origin 가드로 넘긴다.
            route.fallback()
            return
        self.blocked_count += 1
        logger.info("상태 변경 요청 차단: %s %s", method, normalize_path(route.request.url).template)
        route.abort(BLOCKED_ERROR_CODE)

    def _enqueue(self, url: str, depth: int, source_page: str | None, source_action: str) -> ActionOutcome:
        template = normalize_path(url).template
        if template in self.visited:
            return ActionOutcome.ALREADY_VISITED
        if depth > self.config.max_depth:
            return ActionOutcome.BEYOND_MAX_DEPTH
        # 넣을 때 표시해야 같은 템플릿이 큐에 두 번 쌓이지 않는다.
        self.visited.add(template)
        self.queue.append(_QueueEntry(url, depth, source_page, source_action))
        return ActionOutcome.ENQUEUED

    def _visit(self, entry: _QueueEntry) -> DiscoveredPage:
        try:
            status = self._open(entry)
        except PlaywrightError as error:
            logger.warning("페이지 열기 실패 (%s): %s", normalize_path(entry.url).template, _first_line(error))
            return self._build_page(entry, self._describe(entry.url, entry.depth), None)
        current = self._describe(self.page.url, entry.depth)
        if not self._claim_arrived_page(entry, current):
            return self._build_page(entry, current, status)
        try:
            findings = self._explore_page(current)
        except PlaywrightError as error:
            logger.warning("페이지 탐색 중 오류 (%s): %s", current.endpoint, _first_line(error))
            return self._build_page(entry, current, status)
        return self._build_page(entry, current, status, findings)

    def _open(self, entry: _QueueEntry) -> int | None:
        """문서 요청은 부모 페이지의 행동에, 문서가 뜨며 나가는 요청은 이 페이지의 load에 붙인다."""
        self.capture.set_source(entry.source_page, entry.source_action)
        response = self.page.goto(entry.url, wait_until="commit")
        self.capture.set_source(self.capture.mask_url(self.page.url), LOAD_ACTION)
        self.page.wait_for_load_state("load")
        self._wait_until_quiet()
        return response.status if response is not None else None

    def _claim_arrived_page(self, entry: _QueueEntry, current: _CurrentPage) -> bool:
        """도착한 페이지를 탐색할지. 리다이렉트로 이미 본 템플릿에 왔으면(예: 로그인 페이지로 튕김) 다시 뒤지지 않는다."""
        if not is_request_allowed(self.config, current.url):
            logger.warning("허용 origin 밖에 도착해 추출 생략 (%s)", normalize_path(entry.url).template)
            return False
        requested = normalize_path(entry.url).template
        if current.endpoint == requested:
            return True
        if current.endpoint in self.visited:
            logger.info("%s → 이미 방문한 %s로 리다이렉트, 추출 생략", requested, current.endpoint)
            return False
        self.visited.add(current.endpoint)
        return True

    def _explore_page(self, current: _CurrentPage) -> _PageFindings:
        title = self.page.title() or None
        found = _read_elements(self.page)
        allowed_links = [link for link in found.links if is_request_allowed(self.config, link.url)]
        if len(allowed_links) != len(found.links):
            logger.debug("%s: 허용 밖·비 http 링크 %d개 제외", current.endpoint, len(found.links) - len(allowed_links))
        links = tuple(self._handle_link(current, index, link) for index, link in enumerate(allowed_links))
        forms = [self._handle_form(current, index, form) for index, form in enumerate(found.forms)]
        buttons = [self._handle_button(current, index, button) for index, button in enumerate(found.buttons)]
        return _PageFindings(title, links, (*forms, *buttons))

    def _handle_link(self, current: _CurrentPage, index: int, link: _FoundLink) -> PageLink:
        action_id = f"{LINK_KIND}:{index}"
        is_state_changing = self._has_state_changing_name(_url_hint(link.url), link.text)
        if is_state_changing and not self.config.can_change_state:
            outcome = ActionOutcome.NOT_EXECUTED_STATE_CHANGING
        else:
            outcome = self._enqueue(link.url, current.depth + 1, current.masked_url, action_id)
        return PageLink(
            action_id=action_id,
            url=self.capture.mask_url(link.url),
            endpoint=normalize_path(link.url).template,
            text=link.text,
            is_state_changing=is_state_changing,
            outcome=outcome.value,
        )

    def _handle_form(self, current: _CurrentPage, index: int, form: _FoundForm) -> PageAction:
        action_id = f"{FORM_KIND}:{index}"
        is_get = form.method == GET_METHOD
        is_state_changing = not is_get or self._has_state_changing_name(_url_hint(form.action), form.label, *form.hints)
        if is_state_changing and not self.config.can_change_state:
            outcome = ActionOutcome.NOT_EXECUTED_STATE_CHANGING
        elif not is_request_allowed(self.config, form.action):
            outcome = ActionOutcome.OUTSIDE_ORIGIN
        else:
            # GET 폼도 실제로 제출한다. JS가 submit을 가로채 fetch를 보내는 폼은 URL만 봐서는 요청을 알 수 없다.
            step = _ActionStep(action_id, FORM_SELECTOR, index, None, _submit_form)
            outcome, navigated_url = self._execute(current, step)
            if (
                is_get
                and outcome is ActionOutcome.EXECUTED
                and navigated_url is not None
                and is_request_allowed(self.config, navigated_url)
            ):
                # 이동했으면 링크처럼 BFS 차례에 다시 연다. 이미 본 템플릿이면 already_visited로 남는다.
                outcome = self._enqueue(navigated_url, current.depth + 1, current.masked_url, action_id)
        return PageAction(
            action_id=action_id,
            kind=FORM_KIND,
            label=form.label,
            method=form.method,
            target_url=self.capture.mask_url(form.action),
            fields=list(form.fields),
            is_state_changing=is_state_changing or outcome is ActionOutcome.BLOCKED_STATE_CHANGING_REQUEST,
            outcome=outcome.value,
        )

    def _handle_button(self, current: _CurrentPage, index: int, button: _FoundButton) -> PageAction:
        action_id = f"{BUTTON_KIND}:{index}"
        is_state_changing = self._has_state_changing_name(button.label, *button.hints)
        navigated_url: str | None = None
        if is_state_changing and not self.config.can_change_state:
            outcome = ActionOutcome.NOT_EXECUTED_STATE_CHANGING
        elif not button.is_visible:
            outcome = ActionOutcome.NOT_VISIBLE
        else:
            step = _ActionStep(action_id, BUTTON_SELECTOR, index, button.label, _click)
            outcome, navigated_url = self._execute(current, step)
        if navigated_url is not None and is_request_allowed(self.config, navigated_url):
            self._enqueue(navigated_url, current.depth + 1, current.masked_url, action_id)
        return PageAction(
            action_id=action_id,
            kind=BUTTON_KIND,
            label=button.label,
            method=None,
            target_url=self.capture.mask_url(navigated_url) if navigated_url is not None else None,
            fields=[],
            is_state_changing=is_state_changing or outcome is ActionOutcome.BLOCKED_STATE_CHANGING_REQUEST,
            outcome=outcome.value,
        )

    def _execute(self, current: _CurrentPage, step: _ActionStep) -> tuple[ActionOutcome, str | None]:
        """페이지에서 행동 하나를 실행하고 결과와, 페이지가 바뀌었으면 바뀐 URL을 돌려준다."""
        try:
            self._return_to(current)
            target = self.page.locator(step.selector).nth(step.index)
            if target.count() == 0 or (
                step.expected_label is not None and target.evaluate(READ_LABEL_SCRIPT) != step.expected_label
            ):
                logger.info("%s %s: 추출 때와 같은 요소를 찾지 못함", current.endpoint, step.action_id)
                return ActionOutcome.NOT_FOUND, None
            blocked_before = self.blocked_count
            self.capture.set_source(current.masked_url, step.action_id)
            step.perform(target)
            self._wait_until_quiet()
        except PlaywrightError as error:
            logger.warning("%s %s 실행 실패: %s", current.endpoint, step.action_id, _first_line(error))
            return ActionOutcome.FAILED, None
        navigated_url = self.page.url if self.page.url != current.url else None
        if self.blocked_count > blocked_before:
            return ActionOutcome.BLOCKED_STATE_CHANGING_REQUEST, navigated_url
        return ActionOutcome.EXECUTED, navigated_url

    def _return_to(self, current: _CurrentPage) -> None:
        """앞 행동이 페이지를 옮겼으면 원래 페이지로 돌아온다. 이때 나가는 요청은 revisit으로 표시한다."""
        if self.page.url == current.url:
            return
        self.capture.set_source(current.masked_url, REVISIT_ACTION)
        self.page.goto(current.url)
        self._wait_until_quiet()

    def _wait_until_quiet(self) -> None:
        deadline = time.monotonic() + QUIET_TIMEOUT_S
        quiet_since = time.monotonic()
        while True:
            # 기다리는 동안 sync API가 쌓인 request 이벤트를 처리해 캡처에 원래 source로 들어간다.
            self.page.wait_for_timeout(QUIET_POLL_MS)
            now = time.monotonic()
            if self.capture.has_pending_requests:
                quiet_since = now
            elif now - quiet_since >= QUIET_WINDOW_S:
                return
            if now >= deadline:
                logger.warning("요청이 %.1fs 안에 잦아들지 않아 다음으로 넘어감", QUIET_TIMEOUT_S)
                return

    def _has_state_changing_name(self, *names: str | None) -> bool:
        lowered = [name.lower() for name in names if name]
        return any(keyword in name for name in lowered for keyword in self.config.state_changing_keywords)

    def _describe(self, url: str, depth: int) -> _CurrentPage:
        return _CurrentPage(url, self.capture.mask_url(url), normalize_path(url).template, depth)

    def _build_page(
        self, entry: _QueueEntry, current: _CurrentPage, status: int | None, findings: _PageFindings = EMPTY_FINDINGS
    ) -> DiscoveredPage:
        return DiscoveredPage(
            role=self.role,
            url=current.masked_url,
            endpoint=current.endpoint,
            title=findings.title,
            status=status,
            depth=entry.depth,
            source_page=entry.source_page,
            source_action=entry.source_action,
            links=list(findings.links),
            actions=list(findings.actions),
        )


def _read_elements(page: Page) -> _FoundElements:
    raw = page.evaluate(READ_ELEMENTS_SCRIPT, BUTTON_SELECTOR)
    return _FoundElements(
        links=tuple(_FoundLink(_drop_fragment(item["href"]), item["text"]) for item in raw["links"]),
        forms=tuple(
            _FoundForm(
                method=item["method"],
                action=item["action"],
                fields=tuple(FormField.model_validate(field) for field in item["fields"]),
                label=item["label"],
                hints=tuple(item["hints"]),
            )
            for item in raw["forms"]
        ),
        buttons=tuple(_FoundButton(item["label"], tuple(item["hints"]), item["is_visible"]) for item in raw["buttons"]),
    )


def _submit_form(form: Locator) -> None:
    form.evaluate(SUBMIT_FORM_SCRIPT)


def _click(button: Locator) -> None:
    # confirm 대화상자는 Playwright 기본 동작으로 dismiss(취소)된다. 안전한 쪽이라 그대로 둔다.
    # TODO(minjun): 버튼이 연 팝업(window.open) 처리
    button.click(timeout=ACTION_TIMEOUT_MS)


def _drop_fragment(url: str) -> str:
    return urlunsplit(urlsplit(url)._replace(fragment=""))


def _url_hint(url: str) -> str:
    # 호스트는 키워드 판단과 무관하고 오탐만 늘리므로 경로와 쿼리만 본다.
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}" if parts.query else parts.path


def _first_line(error: PlaywrightError) -> str:
    return error.message.splitlines()[0] if error.message else ""
