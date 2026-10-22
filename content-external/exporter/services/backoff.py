"""Jittered backoff for this service's HTTP calls (F8, Key Design #7 point 4).

THE RULE. Retry on connection errors, timeouts and 408/429/5xx only;
everything else is permanent and raises on the first attempt. Three carve-outs
from those classes, because retrying them cannot change the answer: a TLS
failure (requests.SSLError is a ConnectionError subclass, but a bad
certificate does not heal in seconds), 501 Not Implemented and 505 HTTP
Version Not Supported. Full jitter —
each delay is drawn uniformly from [0, min(max_delay, base * 2**attempt)] — so
a fleet of retries does not arrive in lockstep. A `Retry-After` header is a
floor under that draw: the server said when, and arriving earlier is the
self-inflicted throttling this exists to avoid.

A RETRY-AFTER PAST THE CAP STOPS RETRYING. A server asking for an hour is not
asking for a retry inside this run, and sleeping that long would hold the run
lock — and with it every export and drain — on one request. Raising instead
fails the call now; the next tick is the retry.

WHERE IT IS APPLIED, AND WHERE IT IS DELIBERATELY NOT.

- Every `requests` call: the run-start agency check, Stage E's Resql queries
  (services/document_source.py), and Stage L's llm-module push when it
  lands. A timeout is retried, and a timed-out request may have been applied,
  so this is for idempotent calls: GETs, and an upsert keyed by coordinate
  chunk ids, whose replay overwrites rather than adds. A caller with a
  non-idempotent request, or one whose timed-out request keeps running on the
  far side, passes its own `is_retryable`: the Resql queries do, because
  Resql does not cancel a query whose client gave up.
- NOT either S3 client. botocore's `retries={"mode": "standard"}` already
  backs off with jitter on throttles, 5xx and connection errors
  (exporter/stores/s3_store.py for the destination,
  exporter/services/ckb_bucket.py for CKB's bucket), and stacking this on top
  would multiply the attempts into a retry storm against the store it is
  meant to spare.
- NOT the coordinator. A document the destination did not confirm is
  `failed_retryable`: it keeps its previous manifest entry, the watermark is
  withheld, and the next tick retries it. Retrying it again inside the run
  would only lengthen a run the destination is already refusing.

REDACTION (F13). A retry is logged with what was being attempted, the attempt
number, the delay and a status code or exception type. Never a body, never a
URL — the caller's `describe` names the operation, not the address.
"""

import email.utils
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import requests

__all__ = [
    "DEFAULT_POLICY",
    "NO_RETRY",
    "BackoffPolicy",
    "full_jitter_delay",
    "is_retryable_http",
    "retry_after_seconds",
    "retry_with_backoff",
]

logger = logging.getLogger(__name__)

_RETRYABLE_STATUSES = frozenset({408, 429})
_PERMANENT_5XX = frozenset({501, 505})


@dataclass(frozen=True)
class BackoffPolicy:
    """How many attempts, and how long between them.

    `max_attempts` counts the first call: 1 means no retry at all.
    """

    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 30.0
    max_retry_after: float = 120.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {self.max_attempts}")
        if self.base_delay < 0 or self.max_delay < 0 or self.max_retry_after < 0:
            raise ValueError("backoff delays must be >= 0")


DEFAULT_POLICY = BackoffPolicy()

# One attempt. For the startup path, where a slow dependency should delay boot
# by seconds and then warn, not hold the container unable to serve.
NO_RETRY = BackoffPolicy(max_attempts=1)


def _status(exc: BaseException) -> int | None:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code
    return None


def is_retryable_http(exc: BaseException) -> bool:
    """Connection errors, timeouts, and 408/429/5xx, less the carve-outs in
    the module docstring. Nothing else."""
    if isinstance(exc, requests.exceptions.SSLError):
        return False
    if isinstance(exc, requests.ConnectionError | requests.Timeout):
        return True
    status = _status(exc)
    if status is None:
        return False
    if status in _PERMANENT_5XX:
        return False
    return status in _RETRYABLE_STATUSES or status >= 500


def retry_after_seconds(
    exc: BaseException, *, now: Callable[[], datetime] | None = None
) -> float | None:
    """The `Retry-After` an HTTP error carried, in seconds, or None.

    Both forms RFC 9110 allows: delta-seconds, and an HTTP-date. A date in the
    past, or a negative delta, is 0 — retry now. Anything unparseable is None,
    which falls back to the jittered delay rather than guessing.
    """
    if not isinstance(exc, requests.HTTPError) or exc.response is None:
        return None
    raw = exc.response.headers.get("Retry-After")
    if raw is None:
        return None
    raw = raw.strip()
    try:
        return max(0.0, float(int(raw)))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    current = now() if now is not None else datetime.now(UTC)
    return max(0.0, (when - current).total_seconds())


def full_jitter_delay(
    attempt: int, policy: BackoffPolicy, rand: Callable[[], float] = random.random
) -> float:
    """The delay before retry number `attempt` (0 for the first retry)."""
    ceiling = min(policy.max_delay, policy.base_delay * (2**attempt))
    return rand() * ceiling


def _describe_error(exc: BaseException) -> str:
    status = _status(exc)
    return f"HTTP {status}" if status is not None else type(exc).__name__


def retry_with_backoff[T](
    fn: Callable[[], T],
    *,
    describe: str,
    policy: BackoffPolicy = DEFAULT_POLICY,
    is_retryable: Callable[[BaseException], bool] = is_retryable_http,
    retry_after: Callable[[BaseException], float | None] = retry_after_seconds,
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[], float] = random.random,
) -> T:
    """Call `fn` until it returns, a permanent error, or the attempts run out.

    The last error is re-raised unchanged, so a caller's own exception
    handling sees exactly what a single call would have raised.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            if attempt + 1 >= policy.max_attempts or not is_retryable(exc):
                raise
            floor = retry_after(exc)
            if floor is not None and floor > policy.max_retry_after:
                logger.warning(
                    "%s: %s asked to retry after %.0fs, past the %.0fs cap; "
                    "giving up for this run",
                    describe,
                    _describe_error(exc),
                    floor,
                    policy.max_retry_after,
                )
                raise
            delay = full_jitter_delay(attempt, policy, rand)
            if floor is not None:
                delay = max(delay, floor)
            logger.warning(
                "%s: attempt %d of %d failed (%s); retrying in %.2fs",
                describe,
                attempt + 1,
                policy.max_attempts,
                _describe_error(exc),
                delay,
            )
            sleep(delay)
            attempt += 1
