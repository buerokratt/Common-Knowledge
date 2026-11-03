"""F8 — jittered backoff on connection errors and 408/429/5xx only.

No network and no waiting: `sleep` and `rand` are injected throughout, so
every delay is asserted rather than slept.
"""

import logging
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest
import requests

from exporter.services.backoff import (
    NO_RETRY,
    BackoffPolicy,
    full_jitter_delay,
    is_retryable_http,
    retry_after_seconds,
    retry_with_backoff,
)

POLICY = BackoffPolicy(max_attempts=4, base_delay=1.0, max_delay=8.0)


def http_error(status: int, retry_after: str | None = None) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    return requests.HTTPError(f"HTTP {status}", response=response)


class Flaky:
    """Raises each of `errors` in turn, then returns "ok"."""

    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


def run(fn: Flaky, *, policy: BackoffPolicy = POLICY, rand: float = 1.0) -> list[float]:
    """Run `fn` with backoff and return the delays it slept."""
    slept: list[float] = []
    retry_with_backoff(
        fn, describe="test call", policy=policy, sleep=slept.append, rand=lambda: rand
    )
    return slept


# --- what is retryable -------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        requests.ConnectionError("refused"),
        requests.Timeout("slow"),
        http_error(408),
        http_error(429),
        http_error(500),
        http_error(503),
    ],
    ids=["connection", "timeout", "408", "429", "500", "503"],
)
def test_transient_failures_are_retried(error: Exception) -> None:
    fn = Flaky(error)
    run(fn)
    assert fn.calls == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 501, 505])
def test_other_http_errors_are_permanent(status: int) -> None:
    fn = Flaky(http_error(status))
    with pytest.raises(requests.HTTPError):
        run(fn)
    assert fn.calls == 1


def test_a_tls_failure_is_not_retried() -> None:
    """SSLError is a ConnectionError subclass; a bad certificate is not a
    blip, and retrying it only delays the failure."""
    fn = Flaky(requests.exceptions.SSLError("certificate verify failed"))
    with pytest.raises(requests.exceptions.SSLError):
        run(fn)
    assert fn.calls == 1


def test_a_non_http_error_is_never_retried() -> None:
    fn = Flaky(ValueError("a bug, not an outage"))
    with pytest.raises(ValueError):
        run(fn)
    assert fn.calls == 1


def test_an_http_error_without_a_response_is_not_retried() -> None:
    assert not is_retryable_http(requests.HTTPError("no response attached"))


# --- how long it waits -------------------------------------------------------


def test_full_jitter_is_bounded_by_the_exponential_ceiling() -> None:
    assert [full_jitter_delay(n, POLICY, lambda: 1.0) for n in range(5)] == [
        1.0,
        2.0,
        4.0,
        8.0,
        8.0,  # capped at max_delay
    ]
    assert full_jitter_delay(3, POLICY, lambda: 0.0) == 0.0
    assert full_jitter_delay(3, POLICY, lambda: 0.5) == 4.0


def test_the_attempts_run_out_and_the_last_error_is_raised() -> None:
    errors = [requests.ConnectionError(f"attempt {n}") for n in range(5)]
    fn = Flaky(*errors)
    slept: list[float] = []
    with pytest.raises(requests.ConnectionError, match="attempt 3"):
        retry_with_backoff(
            fn, describe="t", policy=POLICY, sleep=slept.append, rand=lambda: 1.0
        )
    assert fn.calls == 4
    assert slept == [1.0, 2.0, 4.0]


def test_no_retry_makes_exactly_one_attempt() -> None:
    fn = Flaky(requests.ConnectionError("down"))
    with pytest.raises(requests.ConnectionError):
        run(fn, policy=NO_RETRY)
    assert fn.calls == 1


# --- Retry-After ---------------------------------------------------------------


def test_retry_after_seconds_floors_the_delay() -> None:
    fn = Flaky(http_error(429, retry_after="7"))
    assert run(fn, rand=0.0) == [7.0]


def test_a_jittered_delay_above_retry_after_is_kept() -> None:
    fn = Flaky(http_error(503, retry_after="0"), http_error(503), http_error(503))
    assert run(fn, rand=1.0) == [1.0, 2.0, 4.0]


def test_retry_after_as_an_http_date() -> None:
    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
    error = http_error(503, retry_after=format_datetime(now + timedelta(seconds=30)))
    assert retry_after_seconds(error, now=lambda: now) == 30.0


def test_a_retry_after_in_the_past_means_now() -> None:
    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
    past = format_datetime(now - timedelta(minutes=5))
    assert retry_after_seconds(http_error(503, past), now=lambda: now) == 0.0
    assert retry_after_seconds(http_error(503, "-3")) == 0.0


def test_a_malformed_retry_after_falls_back_to_jitter() -> None:
    assert retry_after_seconds(http_error(503, retry_after="soon")) is None
    fn = Flaky(http_error(503, retry_after="soon"))
    assert run(fn, rand=0.5) == [0.5]


def test_a_retry_after_past_the_cap_gives_up_without_sleeping(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sleeping an hour would hold the run lock on one request; the next
    tick is the retry instead."""
    fn = Flaky(http_error(503, retry_after="3600"))
    slept: list[float] = []
    with (
        caplog.at_level(logging.WARNING, logger="exporter.services.backoff"),
        pytest.raises(requests.HTTPError),
    ):
        retry_with_backoff(fn, describe="t", policy=POLICY, sleep=slept.append)
    assert slept == []
    assert fn.calls == 1
    assert "past the" in caplog.text


# --- what it logs --------------------------------------------------------------


def test_a_retry_logs_the_status_and_never_the_error_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """F13: a status code or an exception type, never a message or a body."""
    fn = Flaky(
        requests.ConnectionError("https://user:hunter2@resql/agency?x=y"),
        http_error(503),
    )
    with caplog.at_level(logging.WARNING, logger="exporter.services.backoff"):
        run(fn)
    assert "hunter2" not in caplog.text
    assert "ConnectionError" in caplog.text
    assert "HTTP 503" in caplog.text


def test_a_policy_with_no_attempts_is_refused() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        BackoffPolicy(max_attempts=0)
