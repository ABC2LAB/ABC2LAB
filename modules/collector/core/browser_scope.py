"""브라우저가 허용 origin 밖으로 나가지 못하게 하는 두 번째·세 번째 겹(절대 규칙 1번).

context.route 가드(auth.py)는 리다이렉트면 첫 URL에만 걸려서, 302로 다른 곳을 가리키면 다음 hop은 가드를 거치지 않는다.
- 이름 해석 규칙: 브라우저 launch 인자로 허용 origin의 호스트 말고는 전부 해석 실패(~NOTFOUND)로 만든다.
  IP 리터럴도 이 규칙을 탄다. 같은 호스트의 다른 포트·스킴은 호스트 단위 규칙이라 막지 못한다.
- 리다이렉트 hop 감시: 그 남는 경우(같은 호스트, 허용 밖 origin)를 request 이벤트로 탐지해 기록한다. 막지는 못한다.
- 프록시 끔: 환경변수(HTTP_PROXY 등) 프록시를 타면 대상 이름 해석을 프록시가 해서 이름 해석 규칙이 안 먹는다.
  브라우저가 늘 직접 연결하게 한다. 프록시 경유 실행은 지원하지 않는다.
"""

import logging
from dataclasses import dataclass, field

from playwright.sync_api import Request

from modules.collector.core.config import CrawlerConfig, describe_origin, is_request_allowed, parse_origin

logger = logging.getLogger(__name__)

HOST_RESOLVER_RULES_SWITCH = "--host-resolver-rules"
BLOCK_ALL_HOSTS_RULE = "MAP * ~NOTFOUND"
EXCLUDE_RULE_PREFIX = "EXCLUDE "
RULE_SEPARATOR = ", "
# Playwright가 띄우는 헤드리스 chromium은 --no-proxy-server로는 환경변수 프록시를 끄지 못하고(실측),
# --proxy-server=direct://는 끈다. 창구 send(APIRequestContext)는 Playwright proxy 옵션만 따르고 우리는 주지 않는다.
DIRECT_CONNECTION_SWITCH = "--proxy-server=direct://"


def build_host_resolver_rules(config: CrawlerConfig) -> str:
    """허용 origin의 호스트만 이름 해석을 남기는 규칙 문자열.

    호스트는 config가 로드 때 검증한 값만 쓴다(규칙 구분자인 쉼표·공백·*가 없다). IPv6 리터럴은 대괄호 없이 적어야
    Chromium이 EXCLUDE로 맞춘다(실측). 규칙은 설정 키로 받지 않는다.
    """
    hosts = sorted({origin.host for origin in config.allowed_origins})
    return RULE_SEPARATOR.join([BLOCK_ALL_HOSTS_RULE, *(f"{EXCLUDE_RULE_PREFIX}{host}" for host in hosts)])


def build_launch_args(config: CrawlerConfig) -> list[str]:
    """크롤 브라우저와 세션 창구 브라우저가 같이 쓰는 chromium launch 인자."""
    return [f"{HOST_RESOLVER_RULES_SWITCH}={build_host_resolver_rules(config)}", DIRECT_CONNECTION_SWITCH]


@dataclass
class RedirectHopMonitor:
    """리다이렉트로 이어진 요청 중 허용 origin 밖으로 간 것을 본다.

    다른 호스트로 가는 hop은 이름 해석 규칙이 막으므로 경고만 남긴다. 같은 호스트의 다른 포트·스킴은 막지 못하고
    실제로 나갔을 수 있어 unblocked_origins에 scheme://host:port로 남긴다(경로·쿼리는 남기지 않는다).
    """

    config: CrawlerConfig
    unblocked_origins: list[str] = field(default_factory=list)

    def observe(self, request: Request) -> None:
        if request.redirected_from is None or is_request_allowed(self.config, request.url):
            return
        origin = parse_origin(request.url)
        if origin is None:
            return
        described = describe_origin(origin)
        if origin.host in {allowed.host for allowed in self.config.allowed_origins}:
            logger.warning("허용 밖 리다이렉트 hop 탐지(같은 호스트라 막지 못함): %s", described)
            self.unblocked_origins.append(described)
        else:
            logger.warning("허용 밖 리다이렉트 hop 차단(이름 해석 규칙): %s", described)
