"""Retry delay math and the retry loop (fake clock: no test really sleeps)."""

from __future__ import annotations

from typing import Any

import pytest

import bypassfast
from bypassfast import APIError, RetryPolicy, is_error_code
from bypassfast._core import full_jitter, retry_delay, solver_busy_delay

from .support import FakeAPI, FakeClock, error, make_client, max_jitter, no_jitter, reply

HOUR = 3600.0
OK = reply(200, {"payload": "ok", "duration_ms": 1})


# -- delay math ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "retry", "retry_after", "remaining", "low", "high"),
    [
        ("no hint uses the one-second floor", 1, 0.0, None, 1.0, 2.0),
        ("window doubles", 2, 1.0, None, 1.0, 4.0),
        ("third retry", 3, 1.0, None, 1.0, 8.0),
        ("window caps at ten seconds", 4, 1.0, None, 1.0, 10.0),
        ("cap holds for long streaks", 60, 1.0, None, 1.0, 10.0),
        ("millisecond hint is the floor", 1, 0.25, None, 0.25, 0.5),
        ("small hint still reaches the cap", 8, 0.25, None, 0.25, 10.0),
        ("hint above the cap keeps a jitter window", 3, 20.0, None, 20.0, 40.0),
        ("safety cap", 1, 2 * HOUR, None, HOUR, HOUR),
        ("window narrows to the remaining budget", 1, 30.0, 40.0, 30.0, 40.0),
        ("floor exactly fills the remaining budget", 2, 5.0, 5.0, 5.0, 5.0),
        ("floor beyond the remaining budget is not undercut", 1, 30.0, 10.0, 30.0, 60.0),
        ("spent budget keeps the full window", 1, 0.0, -1.0, 1.0, 2.0),
    ],
)
def test_solver_busy_delay_vectors(
    name: str, retry: int, retry_after: float, remaining: float | None, low: float, high: float
) -> None:
    budget = HOUR if remaining is None else remaining
    assert solver_busy_delay(retry, retry_after, budget, no_jitter) == pytest.approx(low)
    assert solver_busy_delay(retry, retry_after, budget, max_jitter) == pytest.approx(high)
    for _ in range(200):
        assert low <= solver_busy_delay(retry, retry_after, budget, full_jitter) <= high


@pytest.mark.parametrize(
    ("retry", "expected"),
    [(1, 0.5), (2, 1.0), (3, 2.0), (4, 4.0), (5, 8.0), (6, 8.0), (10, 8.0)],
)
def test_retry_delay_doubles_up_to_max_delay(retry: int, expected: float) -> None:
    policy = RetryPolicy(max_retries=10, base_delay=0.5, max_delay=8.0)
    assert retry_delay(retry, 0.0, policy, max_jitter) == expected
    assert retry_delay(retry, 0.0, policy, no_jitter) == 0.0


def test_retry_delay_snaps_to_max_and_honors_the_hint() -> None:
    policy = RetryPolicy(max_retries=3, base_delay=3.0, max_delay=5.0)
    assert retry_delay(2, 0.0, policy, max_jitter) == 5.0  # 3 > 5/2 snaps to 5
    assert retry_delay(1, 30.0, policy, max_jitter) == 30.0  # hint beyond max_delay wins
    assert retry_delay(1, 1.0, policy, max_jitter) == 3.0
    assert retry_delay(3, 0.0, RetryPolicy(3, 0.0, 0.0), max_jitter) == 0.0


def test_full_jitter_bounds() -> None:
    assert full_jitter(0.0) == 0.0
    assert full_jitter(-1.0) == 0.0
    samples = [full_jitter(0.01) for _ in range(500)]
    assert all(0.0 <= sample <= 0.01 for sample in samples)
    assert len(set(samples)) > 1


# -- the loop --------------------------------------------------------------------


def test_retries_explicit_retryable_responses() -> None:
    api = FakeAPI(error("solver_unavailable", 503), OK)
    clock = FakeClock()
    client = make_client(api, clock=clock, retry_policy=RetryPolicy(max_retries=2))
    result = client.kasada.cd()
    assert result.response.attempts == 2
    assert len(api.calls) == 2
    assert clock.sleeps == []  # full jitter drew 0: retry immediately


def test_backoff_is_exponential_with_full_jitter() -> None:
    api = FakeAPI(error("edge_unavailable", 503))
    clock = FakeClock()
    client = make_client(api, clock=clock, jitter=max_jitter, retry_policy=RetryPolicy(3, 0.5, 8))
    with pytest.raises(APIError) as excinfo:
        client.kasada.cd()
    assert excinfo.value.response.attempts == 4
    assert clock.sleeps == [0.5, 1.0, 2.0]


def test_retry_after_header_sets_the_minimum_wait() -> None:
    api = FakeAPI(error("solver_unavailable", 503), OK)
    api.responses[0] = reply(503, {"error": "solver_unavailable"}, headers={"Retry-After": "3"})
    clock = FakeClock()
    make_client(api, clock=clock, retry_policy=RetryPolicy(max_retries=1)).kasada.cd()
    assert clock.sleeps == [3.0]


def test_non_retryable_codes_are_raised_at_once() -> None:
    api = FakeAPI(
        reply(429, {"error": "quota_exceeded", "retry_after_ms": 1}, headers={"Retry-After": "1"})
    )
    client = make_client(api, retry_policy=RetryPolicy(max_retries=2), solver_busy_retry_budget=60)
    with pytest.raises(APIError) as excinfo:
        client.kasada.cd()
    assert is_error_code(excinfo.value, "quota_exceeded")
    assert len(api.calls) == 1


def test_exhausted_retries_report_every_attempt() -> None:
    api = FakeAPI(error("solver_unavailable", 503))
    client = make_client(api, retry_policy=RetryPolicy(max_retries=2))
    with pytest.raises(APIError) as excinfo:
        client.kasada.cd()
    assert len(api.calls) == 3
    assert excinfo.value.response.attempts == 3


@pytest.mark.parametrize("code", ["internal", "internal_error"])
def test_internal_errors_are_retried_once(code: str) -> None:
    api = FakeAPI(error(code, 500))
    client = make_client(api, retry_policy=RetryPolicy(max_retries=5))
    with pytest.raises(APIError) as excinfo:
        client.kasada.cd()
    assert excinfo.value.code == code
    assert len(api.calls) == 2


def test_internal_error_after_solver_busy_is_still_retried_once() -> None:
    api = FakeAPI(error("solver_busy", 429, retry_after_ms=1), error("internal_error", 500))
    client = make_client(api, retry_policy=RetryPolicy(max_retries=2))
    with pytest.raises(APIError) as excinfo:
        client.kasada.cd()
    assert excinfo.value.code == "internal_error"
    assert len(api.calls) == 3


def test_solver_busy_does_not_consume_max_retries() -> None:
    busy = reply(429, {"error": "solver_busy", "retry_after_ms": 1}, headers={"Retry-After": "1"})
    api = FakeAPI(busy, busy, busy, OK)
    clock = FakeClock()
    client = make_client(api, clock=clock, retry_policy=RetryPolicy(max_retries=1))
    result = client.kasada.cd()
    assert result.response.attempts == 4
    # The body's 1 ms hint is authoritative over the 1 s header.
    assert clock.sleeps == [0.001, 0.001, 0.001]


@pytest.mark.parametrize(
    "options",
    [
        {"retry_policy": RetryPolicy(max_retries=2), "solver_busy_retry_budget": 0},
        {"retry_policy": RetryPolicy(max_retries=0)},
    ],
    ids=["zero budget", "zero max_retries"],
)
def test_solver_busy_retries_can_be_disabled(options: dict[str, Any]) -> None:
    api = FakeAPI(error("solver_busy", 429, retry_after_ms=1))
    with pytest.raises(APIError):
        make_client(api, **options).kasada.cd()
    assert len(api.calls) == 1


def test_max_retries_zero_disables_ordinary_retries() -> None:
    api = FakeAPI(error("solver_unavailable", 503), OK)
    with pytest.raises(APIError):
        make_client(api, retry_policy=RetryPolicy(max_retries=0)).kasada.cd()
    assert len(api.calls) == 1


def test_solver_busy_budget_bounds_the_retries() -> None:
    api = FakeAPI(error("solver_busy", 429, retry_after_ms=5))
    clock = FakeClock()
    client = make_client(
        api,
        clock=clock,
        jitter=max_jitter,
        retry_policy=RetryPolicy(max_retries=1),
        solver_busy_retry_budget=0.2,
    )
    with pytest.raises(APIError) as excinfo:
        client.kasada.cd()
    err = excinfo.value
    assert err.code == "solver_busy"
    assert err.retry_after == pytest.approx(0.005)
    assert len(api.calls) >= 3  # more than max_retries would allow
    assert err.response.attempts == len(api.calls)
    assert all(delay >= 0.005 for delay in clock.sleeps)
    assert sum(clock.sleeps) <= 0.2 + 1e-9  # no retry starts after the budget


def test_long_solver_busy_hint_is_waited_out_inside_the_budget() -> None:
    busy = error("solver_busy", 429, retry_after_ms=300)
    for jitter in (no_jitter, max_jitter, full_jitter):
        api = FakeAPI(busy, OK)
        clock = FakeClock()
        client = make_client(
            api,
            clock=clock,
            jitter=jitter,
            retry_policy=RetryPolicy(max_retries=1),
            solver_busy_retry_budget=0.5,
        )
        assert client.kasada.cd().response.attempts == 2
        assert 0.3 <= clock.sleeps[0] <= 0.5


def test_each_call_gets_a_fresh_retry_state() -> None:
    api = FakeAPI(error("solver_unavailable", 503))
    client = make_client(api, retry_policy=RetryPolicy(max_retries=1))
    for expected_calls in (2, 4):
        with pytest.raises(APIError):
            client.kasada.cd()
        assert len(api.calls) == expected_calls


def test_validation_errors_never_reach_the_network() -> None:
    api = FakeAPI()
    client = make_client(api)
    with pytest.raises(bypassfast.ValidationError):
        client.kasada.cd(seed=-1)
    assert api.calls == []
