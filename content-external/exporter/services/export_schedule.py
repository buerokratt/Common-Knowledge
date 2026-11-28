"""The hourly export timer, run here instead of by CronManager.

The chatbot's "LLM module active" toggle decides whether this service exports
at all (content-external/README.md, "The chatbot's LLM module toggle"). While
it is ON nothing may fire — not an export, and not an hourly job that wakes up
only to skip. CronManager cannot give that: it has no pause or unschedule
endpoint, its /reload adds or replaces jobs but never removes one, and on
Kubernetes its job files are re-cloned from git whenever its pod starts. So
the hourly timer lives in this process, where it can actually be stopped.

CKB only READS the toggle. The chatbot is never changed and never told about
CKB: it calls nothing when its settings are saved, and its database has no
trigger to notify anyone. So this timer polls instead: every
EXPORT_SCHEDULE_POLL_SECONDS it asks CKB's gate for the toggle, which CKB
reads from the chatbot's database through resql-users.

States:

  unknown   From startup until CKB says whether the toggle is OFF. Nothing
            fires. Retried every RESOLVE_RETRY_SECONDS while CKB cannot
            answer, so a container that boots before Ruuter settles once
            Ruuter is up.
  active    The toggle is OFF. One tick at once, then one every
            EXPORT_SCHEDULE_INTERVAL_SECONDS, measured from that first tick.
  inactive  The toggle is ON, or CKB's gate said skip for another settled
            reason (no domain configured, unknown domain). No tick fires;
            only the poll runs.

What moves it:

  Startup, then every poll               CKB's pipeline/content-export-status,
      which returns the gate's decision and calls nothing back. OFF makes it
      active (an export at once), any other settled answer inactive. An
      unsettled answer — the toggle unreadable, CKB not answering — changes
      nothing, so a brief outage never starts or stops exports. A toggle
      switched OFF therefore exports within one poll interval.
  A tick that CKB skips with llm_module_active
      The toggle went ON since the last poll. The timer stops itself.
  POST /schedule/start, /schedule/stop   An operator, or CKB's
      pipeline/content-export-sync, forcing a re-check now rather than at
      the next poll. Nothing calls them on a schedule.

A domain the gate cannot use — BUROKRATT_CHATBOT_WIDGET_DOMAIN_ID empty in
CKB's constants.ini, or not an active chatbot widget domain — is logged as a
WARNING when a toggle read first sees it, and again only if it changes. An
unarmed deployment then never stops exporting silently, and one left unarmed
on purpose during the bootstrap is not warned about at every poll.

A tick is CKB's pipeline/content-export — the flow CronManager used to call.
It reads the toggle again and, only while it is OFF, POSTs
/export_agency_async back to this service. The gate stays the one judge of
whether an export runs; this timer only decides when CKB is asked.

Nothing here holds the lock while calling CKB: a /schedule request can
arrive while a call is in flight, and takes it.
"""

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import requests

from exporter.services.run_state import utc_now_iso

__all__ = [
    "CONTENT_EXPORT_PATH",
    "CONTENT_EXPORT_STATUS_PATH",
    "CkbCallError",
    "CkbScheduleClient",
    "ExportSchedule",
    "RuuterScheduleClient",
    "ScheduleState",
    "ScheduleStatus",
]

logger = logging.getLogger(__name__)

ScheduleState = Literal["unknown", "active", "inactive"]

# CKB's Ruuter-internal flows, under CKB_RUUTER_INTERNAL, which already ends in
# the project layer (/ckb) — as cleaning, scrapper and CronManager append.
CONTENT_EXPORT_PATH = "/pipeline/content-export"
CONTENT_EXPORT_STATUS_PATH = "/pipeline/content-export-status"

# The gate's reasons this module acts on (content-export-gate.yml).
_LLM_MODULE_ACTIVE = "llm_module_active"
_TOGGLE_UNREADABLE = "toggle_unreadable"
_DOMAIN_NOT_CONFIGURED = "chatbot_domain_not_configured"
_DOMAIN_UNKNOWN = "chatbot_domain_unknown"
# Recorded for an answer that is not a gate decision.
_NO_DECISION = "no_decision"

# How long to wait before asking CKB again while the state is unknown.
RESOLVE_RETRY_SECONDS = 60.0

# How often a settled timer asks CKB for the toggle again.
DEFAULT_POLL_SECONDS = 300.0

# Connect, read. The status flow is one gate query and the export flow only
# starts a child here, so both answer in well under a second; the read
# timeout only bounds a hung Ruuter.
_TIMEOUT = (5.0, 60.0)


class CkbCallError(RuntimeError):
    """CKB could not be asked, or did not answer with a JSON object."""


class CkbScheduleClient(Protocol):
    def content_export(self) -> Mapping[str, Any]:
        """Run one tick: CKB's pipeline/content-export."""
        ...

    def content_export_status(self) -> Mapping[str, Any]:
        """Ask CKB for the gate's decision: pipeline/content-export-status."""
        ...


class RuuterScheduleClient:
    """CKB's two flows over HTTP, at CKB_RUUTER_INTERNAL."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def content_export(self) -> Mapping[str, Any]:
        return self._get(CONTENT_EXPORT_PATH)

    def content_export_status(self) -> Mapping[str, Any]:
        return self._get(CONTENT_EXPORT_STATUS_PATH)

    def _get(self, path: str) -> Mapping[str, Any]:
        try:
            response = requests.get(self._base_url + path, timeout=_TIMEOUT)
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            # The exception type only: a message can carry the URL.
            raise CkbCallError(f"{path}: {type(exc).__name__}") from exc
        # Ruuter wraps a flow's return value as {"response": value} unless the
        # flow opts out, so accept both shapes.
        if isinstance(body, dict) and set(body) == {"response"}:
            body = body["response"]
        if not isinstance(body, dict):
            raise CkbCallError(f"{path}: the answer is not a JSON object")
        return body


@dataclass(frozen=True)
class ScheduleStatus:
    """The timer as /health and the /schedule endpoints report it."""

    state: ScheduleState
    interval_seconds: float
    poll_interval_seconds: float
    # When and why the state last changed; null while still unknown.
    changed_at: str | None = None
    change_reason: str | None = None
    # The last tick: CKB's `status` (started / busy), or the gate's skip
    # reason, or `unreachable`. Null until a tick has fired.
    last_tick_at: str | None = None
    last_tick_result: str | None = None
    # The last read of the toggle, at startup or by a poll: the gate's reason,
    # or `unreachable`. While `unknown` it says why the state is not settled.
    last_poll_at: str | None = None
    last_poll_result: str | None = None


class ExportSchedule:
    """The timer. `start()` runs it on a daemon thread; `step()` is one pass
    of that thread's loop, separate so tests can drive it without one."""

    def __init__(
        self,
        client: CkbScheduleClient,
        *,
        interval_seconds: float,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        retry_seconds: float = RESOLVE_RETRY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if interval_seconds <= 0 or poll_seconds <= 0 or retry_seconds <= 0:
            raise ValueError(
                "the interval, the poll and the retry delay must be positive"
            )
        self._client = client
        self._interval = interval_seconds
        self._poll_interval = poll_seconds
        self._retry = retry_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: ScheduleState = "unknown"
        # Bumped on every state change. A toggle read captures it before
        # calling CKB and applies its answer only if it is unchanged, so an
        # answer that was in flight never overrides a newer /schedule call.
        self._generation = 0
        # Monotonic time of the next tick; None unless active.
        self._next_tick: float | None = None
        # Monotonic time of the next toggle poll; None while unknown.
        self._next_poll: float | None = None
        self._changed_at: str | None = None
        self._change_reason: str | None = None
        self._last_tick_at: str | None = None
        self._last_tick_result: str | None = None
        self._last_poll_at: str | None = None
        self._last_poll_result: str | None = None
        # The (reason, domain) last warned about as an unusable domain; None
        # once a read finds the domain usable.
        self._domain_warning: tuple[str, str] | None = None

    # --- control -------------------------------------------------------------

    def activate(self, reason: str) -> ScheduleStatus:
        """Toggle OFF: tick at once, then every interval. A no-op when already
        active, so a repeated start never adds a tick or resets the timer."""
        self._set_state("active", reason)
        return self.status()

    def deactivate(self, reason: str) -> ScheduleStatus:
        """Toggle ON: nothing fires until the next activate. A tick already
        under way finishes; an export it started is not cancelled."""
        self._set_state("inactive", reason)
        return self.status()

    def status(self) -> ScheduleStatus:
        with self._lock:
            return ScheduleStatus(
                state=self._state,
                interval_seconds=self._interval,
                poll_interval_seconds=self._poll_interval,
                changed_at=self._changed_at,
                change_reason=self._change_reason,
                last_tick_at=self._last_tick_at,
                last_tick_result=self._last_tick_result,
                last_poll_at=self._last_poll_at,
                last_poll_result=self._last_poll_result,
            )

    def _set_state(
        self, state: ScheduleState, reason: str, *, if_generation: int | None = None
    ) -> None:
        """Move to `state`. With `if_generation`, only if no other change
        happened since that generation was read: the caller's answer is then
        older than the current state and is dropped."""
        with self._lock:
            previous = self._state
            if previous == state:
                return
            stale = if_generation is not None and if_generation != self._generation
            if not stale:
                self._state = state
                self._generation += 1
                now = self._clock()
                self._next_tick = now if state == "active" else None
                self._next_poll = now + self._poll_interval
                self._changed_at = utc_now_iso()
                self._change_reason = reason
        # Logged outside the lock; every decision above was made inside it.
        if stale:
            logger.info(
                "content-external schedule ignored %s: the state changed to %s "
                "while CKB was answering",
                reason,
                previous,
            )
            return
        logger.info(
            "content-external schedule %s -> %s reason=%s", previous, state, reason
        )
        self._wake.set()

    # --- the loop ------------------------------------------------------------

    def step(self) -> float | None:
        """One pass: settle an unknown state, poll the toggle when a poll is
        due, or fire a tick that is due. Returns how long to sleep before the
        next pass; None means until woken by a state change."""
        with self._lock:
            state = self._state
            tick_due = self._next_tick if state == "active" else None
            poll_due = self._next_poll
        if state == "unknown":
            return self._resolve()
        now = self._clock()
        # The tick before the poll when both are due: an activation sets both,
        # and its export must not wait behind a poll.
        if tick_due is not None and now >= tick_due:
            with self._lock:
                if self._state != "active":
                    return 0.0  # stopped meanwhile; the next pass sees it
                self._next_tick = now + self._interval
            self._tick()
            return 0.0
        if poll_due is not None and now >= poll_due:
            with self._lock:
                # Before the call, so a failing poll waits a full interval
                # rather than spinning.
                self._next_poll = now + self._poll_interval
            self._poll()
            return 0.0
        waits = [due - now for due in (tick_due, poll_due) if due is not None]
        return min(waits) if waits else None

    def _ask(self, situation: str, retry_note: str) -> tuple[bool | None, str, str]:
        """CKB's gate decision as (run, reason, domain). `run` is None when
        the answer is not settled — CKB did not answer (reason `unreachable`)
        or could not read the toggle — and the caller then keeps its state."""
        try:
            answer = self._client.content_export_status()
        except CkbCallError as exc:
            logger.warning(
                "content-external schedule %s: CKB did not answer (%s); %s",
                situation,
                exc,
                retry_note,
            )
            return None, "unreachable", ""
        run = answer.get("run")
        domain = str(answer.get("domain") or "")
        # The gate always answers a boolean `run` and a reason. Anything else
        # is not its decision, and /health says so rather than echo it.
        if isinstance(run, bool) and answer.get("reason"):
            reason = str(answer["reason"])
        else:
            reason = _NO_DECISION
        if not isinstance(run, bool) or reason in (_TOGGLE_UNREADABLE, _NO_DECISION):
            logger.warning(
                "content-external schedule %s: CKB could not read the toggle "
                "(reason=%s); %s",
                situation,
                reason,
                retry_note,
            )
            return None, reason, domain
        return run, reason, domain

    def _read_toggle(
        self, situation: str, retry_note: str
    ) -> tuple[bool | None, str, int]:
        """`_ask`, recorded for /health, plus the generation it was asked in.
        The startup read is recorded too, so an `unknown` state shows why."""
        with self._lock:
            generation = self._generation
        run, reason, domain = self._ask(situation, retry_note)
        with self._lock:
            self._last_poll_at = utc_now_iso()
            self._last_poll_result = reason
            previous_warning = self._domain_warning
            if run is not None:
                unusable = reason in (_DOMAIN_NOT_CONFIGURED, _DOMAIN_UNKNOWN)
                self._domain_warning = (reason, domain) if unusable else None
            current_warning = self._domain_warning
        if current_warning != previous_warning:
            self._log_domain_warning(current_warning)
        return run, reason, generation

    @staticmethod
    def _log_domain_warning(warning: tuple[str, str] | None) -> None:
        if warning is None:
            logger.info(
                "content-external schedule: BUROKRATT_CHATBOT_WIDGET_DOMAIN_ID "
                "now names an active chatbot widget domain"
            )
        elif warning[0] == _DOMAIN_NOT_CONFIGURED:
            logger.warning(
                "content-external schedule: no export will run: "
                "BUROKRATT_CHATBOT_WIDGET_DOMAIN_ID is empty in CKB's "
                "constants.ini (reason=%s). Set it to the chatbot widget "
                "domain UUID and restart ruuter-internal",
                _DOMAIN_NOT_CONFIGURED,
            )
        else:
            logger.warning(
                "content-external schedule: no export will run: "
                "BUROKRATT_CHATBOT_WIDGET_DOMAIN_ID=%s is not an active chatbot "
                "widget domain (reason=%s). Check it against the chatbot's "
                "public.widget_domains",
                warning[1] or "[not reported]",
                _DOMAIN_UNKNOWN,
            )

    def _resolve(self) -> float:
        run, reason, generation = self._read_toggle(
            "unknown", f"retrying in {self._retry}s"
        )
        if run is None:
            return self._retry
        # A /schedule call that arrived meanwhile has already settled it, and
        # is newer than this answer.
        self._set_state(
            "active" if run else "inactive",
            f"startup:{reason}",
            if_generation=generation,
        )
        return 0.0

    def _poll(self) -> None:
        """Follow the toggle: OFF makes the timer active, any other settled
        answer inactive. An unsettled one keeps the current state, and so does
        any answer overtaken by a /schedule call made while it was asked."""
        run, reason, generation = self._read_toggle(
            "poll", f"keeping the current state, next poll in {self._poll_interval}s"
        )
        if run is not None:
            self._set_state(
                "active" if run else "inactive",
                f"poll:{reason}",
                if_generation=generation,
            )

    def _tick(self) -> None:
        try:
            answer = self._client.content_export()
        except CkbCallError as exc:
            self._record_tick("unreachable")
            logger.warning(
                "content-external schedule tick: CKB did not answer (%s); next "
                "tick in %ss",
                exc,
                self._interval,
            )
            return
        status = str(answer.get("status"))
        reason = answer.get("reason")
        result = str(reason) if status == "skipped" else status
        self._record_tick(result)
        logger.info("content-external schedule tick: %s", result)
        if status == "skipped" and reason == _LLM_MODULE_ACTIVE:
            # The toggle went ON since the last poll: stop now.
            self._set_state("inactive", f"tick:{_LLM_MODULE_ACTIVE}")

    def _record_tick(self, result: str) -> None:
        with self._lock:
            self._last_tick_at = utc_now_iso()
            self._last_tick_result = result

    # --- the thread ----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("the export schedule is already running")
        self._thread = threading.Thread(
            target=self._run, name="content-export-schedule", daemon=True
        )
        self._thread.start()

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop the thread. A call to CKB in flight is not interrupted; the
        thread is a daemon, so it never holds the process open."""
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                wait = self.step()
            except Exception:
                # Never let the timer die silently: log, back off, carry on.
                logger.exception("content-external schedule pass failed")
                wait = self._retry
            if self._stopping.is_set():
                return
            self._wake.wait(wait)
            self._wake.clear()
