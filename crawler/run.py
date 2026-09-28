"""설정의 역할마다 로그인 → 탐색 → 캡처를 돌려 data/crawl_result.json으로 남긴다.

    uv run python -m crawler.run [--output 경로]

대상 URL은 CRAWLER_TARGET_URL 환경변수로 덮어쓴다(.env보다 우선). 역할마다 context를 따로 열고,
한 역할이 실패해도 error에 남기고 다음 역할로 넘어간다. evidence ID(page:N, request:N, role:이름)는
모든 역할 결과를 모은 뒤 실행 전체에서 고유하게 붙인다.
"""

import argparse
import logging
import secrets
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import Browser, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from crawler.auth import SECRET_MASK, open_role_context
from crawler.capture import RequestCapture, list_secret_variants, start_capture
from crawler.config import ConfigError, CrawlerConfig, load_config_from_file
from crawler.explorer import crawl
from crawler.schemas import (
    SCHEMA_VERSION,
    CapturedRequest,
    CrawlResult,
    DiscoveredPage,
    PageRecord,
    RequestRecord,
    RoleResult,
)

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_PATH = Path("data") / "crawl_result.json"
DEFAULT_ENV_PATH = Path(".env")
RUN_ID_TIME_FORMAT = "%Y%m%d-%H%M%S"
RUN_ID_RANDOM_BYTES = 2
PAGE_ID_PREFIX = "page"
REQUEST_ID_PREFIX = "request"
ROLE_ID_PREFIX = "role"
JSON_INDENT = 2
TEMP_SUFFIX = ".tmp"
EXIT_OK = 0
# 결과 파일은 남았지만 실패한 역할이 있다.
EXIT_ROLE_FAILED = 1
# 설정·브라우저 문제로 실행 자체를 못 했다. 결과 파일 없음.
EXIT_RUN_FAILED = 2


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    target_base_url: str
    started_at: datetime
    finished_at: datetime


@dataclass(frozen=True)
class RoleCrawl:
    """evidence ID를 붙이기 전의 역할 하나 결과."""

    role: str
    pages: tuple[DiscoveredPage, ...]
    records: tuple[CapturedRequest, ...]
    error: str | None


def make_run_id(now: datetime) -> str:
    """시각 + 짧은 난수. 같은 초에 두 번 돌려도 파일·KG에서 실행이 갈린다."""
    return f"{now.strftime(RUN_ID_TIME_FORMAT)}-{secrets.token_hex(RUN_ID_RANDOM_BYTES)}"


def run_crawl(browser: Browser, config: CrawlerConfig) -> CrawlResult:
    """설정의 역할 순서대로 탐색하고 evidence ID를 붙인 결과를 돌려준다."""
    started_at = datetime.now(UTC)
    run_id = make_run_id(started_at)
    logger.info("실행 시작 %s: 역할 %s", run_id, ", ".join(config.roles))
    role_crawls = [crawl_role(browser, config, role) for role in config.roles]
    run = RunInfo(run_id, config.start_url, started_at, datetime.now(UTC))
    return assign_evidence_ids(run, role_crawls)


def crawl_role(browser: Browser, config: CrawlerConfig, role: str) -> RoleCrawl:
    """역할 하나를 새 context로 탐색한다. 실패는 예외 대신 error로 돌려줘 다음 역할이 계속 돈다."""
    capture: RequestCapture | None = None
    try:
        context = open_role_context(browser, config, role)
        try:
            capture = start_capture(context, config, role)
            pages = crawl(context, config, role, capture)
        finally:
            context.close()
    except Exception as error:
        message = _describe_error(config, error)
        logger.warning("%s 역할 실패, 다음 역할로 진행: %s", role, message)
        # traceback에는 URL·입력값이 섞일 수 있어 평소 로그에는 남기지 않는다.
        logger.debug("%s 역할 실패 상세", role, exc_info=True)
        records = capture.records if capture is not None else ()
        return RoleCrawl(role, (), records, message)
    return RoleCrawl(role, tuple(pages), capture.records, None)


def assign_evidence_ids(run: RunInfo, role_crawls: Sequence[RoleCrawl]) -> CrawlResult:
    """모든 역할 결과를 모아 page:N·request:N(1부터, 실행 전체에서 고유)과 source_page_id를 붙인다."""
    page_count = 0
    request_count = 0
    roles: list[RoleResult] = []
    for role_crawl in role_crawls:
        page_ids = [f"{PAGE_ID_PREFIX}:{page_count + offset}" for offset in range(1, len(role_crawl.pages) + 1)]
        page_count += len(role_crawl.pages)
        page_id_by_url: dict[str, str] = {}
        for page, page_id in zip(role_crawl.pages, page_ids, strict=True):
            # 리다이렉트로 이미 본 페이지에 다시 오면 같은 URL이 또 생긴다. 뒤의 것은 추출을 건너뛴 페이지라 처음 것에 잇는다.
            page_id_by_url.setdefault(page.url, page_id)

        pages = [
            PageRecord.model_validate(
                {**page.model_dump(), "id": page_id, "source_page_id": _find_page_id(page_id_by_url, page.source_page)}
            )
            for page, page_id in zip(role_crawl.pages, page_ids, strict=True)
        ]
        # capture는 응답이 끝난 순서로 쌓으므로 요청이 나간 순서로 바꾼다. 같은 시각이면 원래 순서를 지킨다.
        records = sorted(role_crawl.records, key=lambda record: record.captured_at)
        requests: list[RequestRecord] = []
        for record in records:
            request_count += 1
            requests.append(
                RequestRecord.model_validate(
                    {
                        **record.model_dump(),
                        "id": f"{REQUEST_ID_PREFIX}:{request_count}",
                        "source_page_id": _find_page_id(page_id_by_url, record.source_page),
                    }
                )
            )
        unlinked_count = sum(1 for request in requests if request.source_page is not None and not request.source_page_id)
        if unlinked_count:
            logger.debug("%s: 페이지와 잇지 못한 요청 %d개", role_crawl.role, unlinked_count)
        roles.append(
            RoleResult(
                id=f"{ROLE_ID_PREFIX}:{role_crawl.role}",
                role=role_crawl.role,
                error=role_crawl.error,
                pages=pages,
                requests=requests,
            )
        )
    return CrawlResult(
        schema_version=SCHEMA_VERSION,
        run_id=run.run_id,
        target_base_url=run.target_base_url,
        started_at=run.started_at,
        finished_at=run.finished_at,
        roles=roles,
    )


def save_result(result: CrawlResult, path: Path) -> None:
    """결과를 쓰고 다시 읽어 검증한다. 임시 파일에 쓴 뒤 바꿔서 중간에 죽어도 반쯤 쓰인 파일이 남지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}{TEMP_SUFFIX}")
    temp_path.write_text(result.model_dump_json(indent=JSON_INDENT), encoding="utf-8", newline="\n")
    temp_path.replace(path)
    reloaded = CrawlResult.model_validate_json(path.read_text(encoding="utf-8"))
    if reloaded != result:
        raise ValueError(f"저장한 결과를 다시 읽었더니 내용이 다름: {path}")


def log_summary(result: CrawlResult, path: Path) -> None:
    """data/는 직접 열어보지 않으므로 역할별 건수·에러로 결과를 확인한다."""
    for role in result.roles:
        logger.info(
            "role=%s pages=%d requests=%d error=%s", role.role, len(role.pages), len(role.requests), role.error
        )
    logger.info(
        "run_id=%s 저장 %s: 역할 %d개, 페이지 %d개, 요청 %d개, 실패 역할 %d개",
        result.run_id,
        path,
        len(result.roles),
        sum(len(role.pages) for role in result.roles),
        sum(len(role.requests) for role in result.roles),
        sum(1 for role in result.roles if role.error is not None),
    )


def main(argv: Sequence[str] | None = None, env_path: Path = DEFAULT_ENV_PATH) -> int:
    """env_path는 테스트가 레포 루트 .env 대신 자기 설정을 쓰려고 받는다. CLI 옵션은 아니다."""
    parser = argparse.ArgumentParser(prog="python -m crawler.run", description="역할별로 대상 앱을 탐색해 JSON으로 저장")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help=f"결과 파일 (기본 {DEFAULT_OUTPUT_PATH})")
    args = parser.parse_args(argv)

    try:
        config = load_config_from_file(env_path)
    except ConfigError as error:
        logger.error("설정 오류로 실행하지 않음: %s", error)
        return EXIT_RUN_FAILED
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            logger.error("브라우저를 띄우지 못함: %s", _first_line(str(error)))
            return EXIT_RUN_FAILED
        try:
            result = run_crawl(browser, config)
        finally:
            browser.close()
    save_result(result, args.output)
    log_summary(result, args.output)
    return EXIT_OK if all(role.error is None for role in result.roles) else EXIT_ROLE_FAILED


def _find_page_id(page_id_by_url: dict[str, str], url: str | None) -> str | None:
    return page_id_by_url.get(url) if url is not None else None


def _describe_error(config: CrawlerConfig, error: Exception) -> str:
    text = f"{type(error).__name__}: {_first_line(str(error))}"
    # LoginError는 이미 가려져 있지만 Playwright 메시지에 URL·입력값이 섞일 수 있어 계정 비밀번호를 한 번 더 지운다.
    for secret in list_secret_variants(account.password for account in config.accounts.values()):
        text = text.replace(secret, SECRET_MASK)
    return text


def _first_line(text: str) -> str:
    return text.splitlines()[0] if text else ""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(main())
