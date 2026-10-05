from datetime import UTC, datetime, timedelta

from crawler.server_clock import CLOCK_WINDOW_SIZE, ServerClockSample, count_clock_regressions

BASE_TIME = datetime(2026, 10, 1, 5, 12, 3, tzinfo=UTC)
# 서버 시계가 호스트보다 이만큼 앞선다고 둔다. Date 헤더는 초 단위로 버려지므로 오차가 1초 안쪽으로 생긴다.
SERVER_AHEAD_S = 0.4
REQUEST_INTERVAL_S = 0.4


def make_samples(server_offsets: list[float]) -> list[ServerClockSample]:
    """호스트가 응답을 받은 시각과, 그때 서버 시계를 초 단위로 버린 Date 값의 쌍."""
    samples = []
    for index, offset in enumerate(server_offsets):
        received_at = BASE_TIME + timedelta(seconds=index * REQUEST_INTERVAL_S)
        server_time = received_at + timedelta(seconds=offset)
        samples.append(ServerClockSample(received_at, server_time.replace(microsecond=0)))
    return samples


def test_steady_clock_has_no_regression() -> None:
    assert count_clock_regressions(make_samples([SERVER_AHEAD_S] * 40)) == 0


def test_forward_drift_is_not_regression() -> None:
    # VM 시계가 초당 0.15초씩 빨라지는 구간. 앞으로 가는 건 세션을 끊지 않는다.
    drifting = [SERVER_AHEAD_S + 0.06 * index for index in range(40)]

    assert count_clock_regressions(make_samples(drifting)) == 0


def test_backward_step_counted_once() -> None:
    # 실제로 관찰한 톱니 모양: 빨라지다가 한 번에 약 2.1초 뒤로 간다.
    before = [SERVER_AHEAD_S + 0.06 * index for index in range(15)]
    after = [before[-1] - 2.1 + 0.06 * index for index in range(15)]

    assert count_clock_regressions(make_samples(before + after)) == 1


def test_two_steps_counted_separately() -> None:
    first = [0.9] * 10
    second = [-1.2] * 10
    third = [-3.3] * 10

    assert count_clock_regressions(make_samples(first + second + third)) == 2


def test_small_jitter_from_second_rounding_is_ignored() -> None:
    # 서버 시계는 그대로인데 Date가 초 단위라 추정값이 1초 가까이 흔들린다. 불규칙한 간격으로 초 이하 자리를 고루 지나게 한다.
    intervals = [0.13, 0.77, 0.05, 0.91, 0.42, 0.66, 0.29, 0.98, 0.51, 0.08] * 4
    samples = []
    received_at = BASE_TIME
    for interval in intervals:
        received_at += timedelta(seconds=interval)
        server_time = received_at + timedelta(seconds=SERVER_AHEAD_S)
        samples.append(ServerClockSample(received_at, server_time.replace(microsecond=0)))

    assert count_clock_regressions(samples) == 0


def test_small_backward_adjustment_below_margin_is_ignored() -> None:
    # 여유값보다 작게 뒤로 가는 보정은 Date의 초 단위 범위 안이라 확실한 역행으로 보지 않는다.
    adjusted = [0.9] * 10 + [0.6] * 10

    assert count_clock_regressions(make_samples(adjusted)) == 0


def test_regression_compared_only_within_recent_window() -> None:
    # 창 밖으로 밀려난 오래된 값은 기준에서 빠진다. 한 번 센 뒤에는 새 기준으로 다시 본다.
    high = [3.0]
    later = [0.4] * CLOCK_WINDOW_SIZE

    assert count_clock_regressions(make_samples(high + later)) == 1
    assert count_clock_regressions(make_samples(later + later)) == 0


def test_empty_and_single_sample() -> None:
    assert count_clock_regressions([]) == 0
    assert count_clock_regressions(make_samples([0.4])) == 0
