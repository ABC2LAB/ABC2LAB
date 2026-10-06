"""대상 서버 시계가 크롤 도중 뒤로 갔는지 응답 Date 헤더로 감지한다.

서명된 세션 쿠키(예: itsdangerous 타임스탬프)는 서명 시각이 미래면 거부된다. 테스트 환경의 VM 시계가 뒤로
점프하면 로그인 역할의 요청이 잠깐 비로그인으로 처리되어, 접근통제 판단 재료가 오염된다.
크롤러는 막지 못하므로 감지만 해서 알린다. 앱 종류에 기대지 않도록 표준 Date 헤더만 쓴다.
"""

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

# 최근 이만큼의 응답과만 비교한다. 오래된 값과 비교하면 한 번 센 역행을 계속 다시 센다.
CLOCK_WINDOW_SIZE = 20
# Date는 초 단위라 서버 시각은 [Date, Date+1) 범위로만 안다. 두 범위가 이만큼 이상 떨어져야 확실한 역행으로 본다.
CLOCK_REGRESSION_MARGIN_S = 0.5
DATE_RESOLUTION_S = 1.0


@dataclass(frozen=True)
class ServerClockSample:
    # 호스트가 응답을 받은 시각. 요청 시각을 쓰면 느린 요청이 끼었을 때 역행처럼 보인다.
    received_at: datetime
    # 응답 Date 헤더(초 단위로 버려진 서버 시각)
    server_date: datetime


def count_clock_regressions(samples: Sequence[ServerClockSample]) -> int:
    """응답 받은 순서대로 보며 서버 시계가 확실히 뒤로 간 횟수를 센다.

    각 응답의 (서버 - 호스트) 차이는 [Date - 받은 시각, Date + 1초 - 받은 시각) 범위다.
    최근 응답들의 하한이 지금 응답의 상한보다 여유값 이상 크면 역행이다. 반올림 흔들림이나
    앞으로 가는 드리프트로는 이 조건이 성립하지 않는다.
    """
    count = 0
    recent_lower_bounds: deque[float] = deque(maxlen=CLOCK_WINDOW_SIZE)
    for sample in samples:
        lower_bound = (sample.server_date - sample.received_at).total_seconds()
        upper_bound = lower_bound + DATE_RESOLUTION_S
        if recent_lower_bounds and max(recent_lower_bounds) - upper_bound > CLOCK_REGRESSION_MARGIN_S:
            count += 1
            # 같은 역행을 뒤 응답마다 다시 세지 않게 기준을 새로 잡는다.
            recent_lower_bounds.clear()
        recent_lower_bounds.append(lower_bound)
    return count
