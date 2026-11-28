"""The export timer that replaces CronManager's trigger_content_export.

The contract: while the chatbot's LLM module toggle is ON no export fires, not
even a tick that would only skip. Switched OFF, one export at once, then one
every interval. The chatbot never tells CKB that the toggle changed, so the
timer polls it: a switch is followed within one poll interval. Most tests
drive `step()` with a fake clock, so they assert exactly which calls reach
CKB and when; the thread and the endpoints are covered at the end.
"""

import time
from pathlib import Path
from collections.abc import Callable, Mapping

import pytest
import requests
from fastapi.testclient import TestClient

from exporter.api.app import app
from exporter.api.config import Settings
from exporter.services.export_schedule import (
    CONTENT_EXPORT_PATH,
    CONTENT_EXPORT_STATUS_PATH,
    DEFAULT_POLL_SECONDS,
    CkbCallError,
    ExportSchedule,
    RuuterScheduleClient,
)
from exporter_fakes import FakeCkb, patch_export_schedule

HOUR = 3_600.0
POLL = 300.0
RETRY = 60.0

Answer = Mapping[str, object] | CkbCallError

TOGGLE_OFF = {"status": "allowed", "run": True, "reason": "llm_module_inactive"}
TOGGLE_ON = {"status": "skipped", "run": False, "reason": "llm_module_active"}
UNREADABLE = {"status": "skipped", "run": False, "reason": "toggle_unreadable"}
NO_DOMAIN = {
    "status": "skipped",
    "run": False,
    "reason": "chatbot_domain_not_configured",
}


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def make(ckb: FakeCkb, clock: Clock) -> ExportSchedule:
    return ExportSchedule(
        ckb,
        interval_seconds=HOUR,
        poll_seconds=POLL,
        retry_seconds=RETRY,
        clock=clock,
    )


def settle(schedule: ExportSchedule) -> float | None:
    """Run passes until one asks to sleep, as the thread would."""
    for _ in range(10):
        wait = schedule.step()
        if wait != 0.0:
            return wait
    raise AssertionError("the schedule never settled")


def run_for(schedule: ExportSchedule, clock: Clock, seconds: float) -> None:
    """Advance the clock by `seconds`, running every pass the thread would
    on the way, including one due exactly at the end."""
    end = clock.now + seconds
    while True:
        wait = settle(schedule)
        if wait is None or clock.now + wait > end:
            clock.now = end
            return
        clock.now += wait


# --- startup ---------------------------------------------------------------


def test_startup_with_the_toggle_on_never_ticks() -> None:
    ckb, clock = FakeCkb(sync_answer=TOGGLE_ON), Clock()
    schedule = make(ckb, clock)

    assert settle(schedule) == POLL  # only the poll is scheduled
    run_for(schedule, clock, 24 * HOUR)
    assert schedule.status().state == "inactive"
    assert ckb.export_calls == 0
    # The startup answer, then one poll every POLL seconds.
    assert ckb.sync_calls == 1 + 24 * HOUR / POLL


def test_startup_with_the_toggle_off_exports_at_once_then_hourly() -> None:
    ckb, clock = FakeCkb(sync_answer=TOGGLE_OFF), Clock()
    schedule = make(ckb, clock)

    assert settle(schedule) == POLL
    assert ckb.export_calls == 1
    assert schedule.status().state == "active"
    assert schedule.status().change_reason == "startup:llm_module_inactive"

    run_for(schedule, clock, HOUR - 1)
    assert ckb.export_calls == 1  # polls in between never export
    run_for(schedule, clock, 1)
    assert ckb.export_calls == 2
    assert schedule.status().change_reason == "startup:llm_module_inactive"


@pytest.mark.parametrize(
    "answer",
    [CkbCallError("down"), UNREADABLE, {"status": "started"}],
    ids=["unreachable", "toggle_unreadable", "no_decision"],
)
def test_an_unsettled_startup_retries_and_never_ticks(answer: Answer) -> None:
    ckb, clock = FakeCkb(sync_answer=answer), Clock()
    schedule = make(ckb, clock)

    assert settle(schedule) == RETRY
    assert schedule.status().state == "unknown"
    assert ckb.export_calls == 0

    ckb.sync_answer = TOGGLE_OFF
    clock.now += RETRY
    assert settle(schedule) == POLL
    assert ckb.export_calls == 1


def test_no_configured_domain_settles_inactive() -> None:
    ckb, clock = FakeCkb(sync_answer=NO_DOMAIN), Clock()
    schedule = make(ckb, clock)

    assert settle(schedule) == POLL
    assert schedule.status().state == "inactive"
    assert ckb.export_calls == 0


def test_a_start_that_arrived_first_is_not_overridden_by_startup() -> None:
    """A /schedule call that arrived while this service was starting is newer
    than the startup answer, so it wins and startup asks nothing."""
    ckb, clock = FakeCkb(sync_answer=TOGGLE_ON), Clock()
    schedule = make(ckb, clock)
    schedule.activate("ckb_sync")

    # The state is no longer unknown, so the startup answer is never asked.
    assert settle(schedule) == POLL
    assert ckb.sync_calls == 0
    assert schedule.status().state == "active"
    assert ckb.export_calls == 1


# --- following the toggle by polling ---------------------------------------


def test_a_poll_that_reads_off_exports_within_one_poll_interval() -> None:
    """The case nothing else would catch: no tick runs while the toggle is
    ON, so only the poll can see it switched OFF."""
    ckb, clock = FakeCkb(sync_answer=TOGGLE_ON), Clock()
    schedule = make(ckb, clock)
    settle(schedule)

    ckb.sync_answer = TOGGLE_OFF
    run_for(schedule, clock, POLL - 1)
    assert ckb.export_calls == 0
    run_for(schedule, clock, 1)
    assert ckb.export_calls == 1
    status = schedule.status()
    assert status.state == "active"
    assert status.change_reason == "poll:llm_module_inactive"
    assert status.last_poll_result == "llm_module_inactive"
    assert status.last_poll_at is not None

    # Then hourly, measured from that export.
    run_for(schedule, clock, HOUR)
    assert ckb.export_calls == 2


def test_a_poll_that_reads_on_stops_every_further_tick() -> None:
    ckb, clock = FakeCkb(sync_answer=TOGGLE_OFF), Clock()
    schedule = make(ckb, clock)
    settle(schedule)
    assert ckb.export_calls == 1

    ckb.sync_answer = TOGGLE_ON
    run_for(schedule, clock, POLL)
    assert schedule.status().state == "inactive"
    assert schedule.status().change_reason == "poll:llm_module_active"

    run_for(schedule, clock, 48 * HOUR)
    assert ckb.export_calls == 1


def test_a_poll_that_reads_no_domain_stops_the_timer() -> None:
    ckb, clock = FakeCkb(sync_answer=TOGGLE_OFF), Clock()
    schedule = make(ckb, clock)
    settle(schedule)

    ckb.sync_answer = NO_DOMAIN
    run_for(schedule, clock, POLL)
    assert schedule.status().state == "inactive"
    assert schedule.status().change_reason == "poll:chatbot_domain_not_configured"


# --- the unusable-domain warning -------------------------------------------

_SCHEDULE_LOGGER = "exporter.services.export_schedule"


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == _SCHEDULE_LOGGER and r.levelname == "WARNING"
    ]


def test_an_empty_domain_warns_once_at_startup_not_at_every_poll(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The bootstrap leaves the constant empty on purpose: one warning, not
    one every poll."""
    caplog.set_level("INFO", logger=_SCHEDULE_LOGGER)
    ckb, clock = FakeCkb(sync_answer=NO_DOMAIN), Clock()
    schedule = make(ckb, clock)
    settle(schedule)
    run_for(schedule, clock, 10 * POLL)

    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "BUROKRATT_CHATBOT_WIDGET_DOMAIN_ID is empty" in warnings[0]
    assert "no export will run" in warnings[0]


def test_an_unknown_domain_warns_with_the_configured_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger=_SCHEDULE_LOGGER)
    unknown = {
        "status": "skipped",
        "run": False,
        "reason": "chatbot_domain_unknown",
        "domain": "not-a-widget-domain",
    }
    ckb, clock = FakeCkb(sync_answer=unknown), Clock()
    settle(make(ckb, clock))

    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "BUROKRATT_CHATBOT_WIDGET_DOMAIN_ID=not-a-widget-domain" in warnings[0]
    assert "chatbot_domain_unknown" in warnings[0]


def test_a_domain_that_becomes_unusable_warns_and_recovery_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Warned when a poll first sees it, even though the toggle was already ON
    and the state does not change; logged again when it is fixed."""
    caplog.set_level("INFO", logger=_SCHEDULE_LOGGER)
    ckb, clock = FakeCkb(sync_answer=TOGGLE_ON), Clock()
    schedule = make(ckb, clock)
    settle(schedule)
    assert _warnings(caplog) == []

    ckb.sync_answer = NO_DOMAIN
    run_for(schedule, clock, 3 * POLL)
    assert len(_warnings(caplog)) == 1

    ckb.sync_answer = TOGGLE_OFF
    run_for(schedule, clock, POLL)
    assert any(
        "now names an active chatbot widget domain" in r.getMessage()
        for r in caplog.records
        if r.levelname == "INFO"
    )
    assert schedule.status().state == "active"


def test_an_unsettled_read_neither_warns_nor_forgets_the_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An outage says nothing about the domain: no recovery line, and no
    second warning when the same empty domain is read again afterwards."""
    caplog.set_level("INFO", logger=_SCHEDULE_LOGGER)
    ckb, clock = FakeCkb(sync_answer=NO_DOMAIN), Clock()
    schedule = make(ckb, clock)
    settle(schedule)

    ckb.sync_answer = UNREADABLE
    run_for(schedule, clock, POLL)
    ckb.sync_answer = NO_DOMAIN
    run_for(schedule, clock, POLL)

    domain_warnings = [w for w in _warnings(caplog) if "DOMAIN_ID" in w]
    assert len(domain_warnings) == 1
    assert not any("now names" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    ("answer", "result"),
    [(CkbCallError("down"), "unreachable"), (UNREADABLE, "toggle_unreadable")],
)
@pytest.mark.parametrize("toggle", [TOGGLE_OFF, TOGGLE_ON], ids=["active", "inactive"])
def test_an_unsettled_poll_keeps_the_current_state(
    toggle: Mapping[str, object], answer: Answer, result: str
) -> None:
    """A brief outage of CKB or the chatbot database never starts or stops
    exports."""
    ckb, clock = FakeCkb(sync_answer=toggle), Clock()
    schedule = make(ckb, clock)
    settle(schedule)
    state = schedule.status().state
    exports = ckb.export_calls

    ckb.sync_answer = answer
    run_for(schedule, clock, POLL)
    assert schedule.status().state == state
    assert schedule.status().last_poll_result == result
    assert ckb.export_calls == exports


def test_an_active_timer_keeps_ticking_through_unsettled_polls() -> None:
    ckb, clock = FakeCkb(sync_answer=TOGGLE_OFF), Clock()
    schedule = make(ckb, clock)
    settle(schedule)

    ckb.sync_answer = CkbCallError("down")
    run_for(schedule, clock, 3 * HOUR)
    assert ckb.export_calls == 4
    assert schedule.status().state == "active"


def test_a_poll_that_agrees_changes_nothing() -> None:
    ckb, clock = FakeCkb(sync_answer=TOGGLE_OFF), Clock()
    schedule = make(ckb, clock)
    settle(schedule)
    changed_at = schedule.status().changed_at

    run_for(schedule, clock, 5 * POLL)
    assert schedule.status().changed_at == changed_at
    assert schedule.status().change_reason == "startup:llm_module_inactive"
    assert ckb.export_calls == 1


@pytest.mark.parametrize(
    "answer",
    [
        {"status": "allowed", "run": "yes", "reason": "llm_module_inactive"},
        {"status": "started"},
        {"status": "allowed", "run": True},
    ],
    ids=["run_not_a_bool", "no_run", "no_reason"],
)
def test_a_malformed_poll_answer_keeps_the_state(answer: Mapping[str, object]) -> None:
    """Not the gate's answer, so not settled: the timer stays as it is."""
    ckb, clock = FakeCkb(sync_answer=TOGGLE_ON), Clock()
    schedule = make(ckb, clock)
    settle(schedule)

    ckb.sync_answer = answer
    run_for(schedule, clock, POLL)
    assert schedule.status().state == "inactive"
    assert schedule.status().last_poll_result == "no_decision"
    assert ckb.export_calls == 0


# --- unknown, and answers overtaken by a /schedule call --------------------


@pytest.mark.parametrize(
    ("answer", "result"),
    [(CkbCallError("down"), "unreachable"), (UNREADABLE, "toggle_unreadable")],
)
def test_an_unknown_state_shows_why_in_the_status(answer: Answer, result: str) -> None:
    """/health must not be all nulls while CKB cannot answer at startup."""
    ckb, clock = FakeCkb(sync_answer=answer), Clock()
    schedule = make(ckb, clock)

    assert settle(schedule) == RETRY
    status = schedule.status()
    assert status.state == "unknown"
    assert status.last_poll_result == result
    assert status.last_poll_at is not None


class _OvertakenCkb(FakeCkb):
    """A CKB whose status answer arrives after `meanwhile` has run — as a
    /schedule call made while the poll's HTTP request was in flight."""

    def __init__(self, meanwhile: Callable[[], object], **kwargs: object) -> None:
        super().__init__(**kwargs)  # pyright: ignore[reportArgumentType]
        self.meanwhile: Callable[[], object] | None = meanwhile

    def content_export_status(self) -> Mapping[str, object]:
        answer = super().content_export_status()
        if self.meanwhile is not None:
            hook, self.meanwhile = self.meanwhile, None
            hook()
        return answer


def test_a_poll_answer_never_undoes_a_stop_made_while_it_was_asked() -> None:
    clock = Clock()
    schedule: ExportSchedule | None = None

    def stop() -> None:
        assert schedule is not None
        schedule.deactivate("ckb_sync")

    ckb = _OvertakenCkb(meanwhile=lambda: None, sync_answer=TOGGLE_OFF)
    schedule = make(ckb, clock)
    settle(schedule)
    assert schedule.status().state == "active"

    # The next poll reads OFF, but a stop lands while it is in flight.
    ckb.meanwhile = stop
    run_for(schedule, clock, POLL)
    status = schedule.status()
    assert status.state == "inactive"
    assert status.change_reason == "ckb_sync"
    assert ckb.export_calls == 1

    # The poll after that reads the toggle afresh and follows it.
    run_for(schedule, clock, POLL)
    assert schedule.status().state == "active"
    assert schedule.status().change_reason == "poll:llm_module_inactive"
    assert ckb.export_calls == 2


def test_a_startup_answer_never_undoes_a_start_made_while_it_was_asked() -> None:
    clock = Clock()
    schedule: ExportSchedule | None = None

    def start() -> None:
        assert schedule is not None
        schedule.activate("ckb_sync")

    ckb = _OvertakenCkb(meanwhile=start, sync_answer=TOGGLE_ON)
    schedule = make(ckb, clock)

    settle(schedule)
    assert schedule.status().state == "active"
    assert schedule.status().change_reason == "ckb_sync"


# --- the tick and the /schedule endpoints ----------------------------------


def test_a_stop_takes_effect_before_the_next_poll() -> None:
    ckb, clock = FakeCkb(sync_answer=TOGGLE_OFF), Clock()
    schedule = make(ckb, clock)
    settle(schedule)

    ckb.sync_answer = TOGGLE_ON
    schedule.deactivate("ckb_sync")
    run_for(schedule, clock, 48 * HOUR)
    assert ckb.export_calls == 1


def test_a_start_exports_at_once() -> None:
    ckb, clock = FakeCkb(sync_answer=TOGGLE_ON), Clock()
    schedule = make(ckb, clock)
    settle(schedule)

    clock.now += 123
    ckb.sync_answer = TOGGLE_OFF
    schedule.activate("ckb_sync")
    settle(schedule)
    assert ckb.export_calls == 1


def test_a_repeated_start_neither_ticks_nor_resets_the_timer() -> None:
    ckb, clock = FakeCkb(sync_answer=TOGGLE_OFF), Clock()
    schedule = make(ckb, clock)
    settle(schedule)

    run_for(schedule, clock, 600)
    schedule.activate("ckb_sync")
    run_for(schedule, clock, HOUR - 600 - 1)
    assert ckb.export_calls == 1
    run_for(schedule, clock, 1)
    assert ckb.export_calls == 2


def test_a_tick_skipped_as_llm_module_active_stops_the_timer() -> None:
    """The toggle went ON after the last poll: the gate still refuses the
    export, and the timer stops itself at that tick."""
    ckb, clock = FakeCkb(sync_answer=TOGGLE_OFF), Clock()
    schedule = make(ckb, clock)
    settle(schedule)
    run_for(schedule, clock, HOUR - 1)

    ckb.sync_answer = TOGGLE_ON
    ckb.export_answer = TOGGLE_ON
    run_for(schedule, clock, 1)
    assert schedule.status().state == "inactive"
    assert schedule.status().change_reason == "tick:llm_module_active"
    assert schedule.status().last_tick_result == "llm_module_active"

    run_for(schedule, clock, 5 * HOUR)
    assert ckb.export_calls == 2


@pytest.mark.parametrize(
    ("answer", "result"),
    [
        (UNREADABLE, "toggle_unreadable"),
        (CkbCallError("down"), "unreachable"),
        ({"status": "busy"}, "busy"),
    ],
)
def test_other_tick_results_keep_the_timer_running(answer: Answer, result: str) -> None:
    ckb, clock = FakeCkb(sync_answer=TOGGLE_OFF, export_answer=answer), Clock()
    schedule = make(ckb, clock)

    settle(schedule)
    assert schedule.status().state == "active"
    assert schedule.status().last_tick_result == result
    run_for(schedule, clock, HOUR)
    assert ckb.export_calls == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"interval_seconds": 0},
        {"interval_seconds": HOUR, "poll_seconds": 0},
        {"interval_seconds": HOUR, "retry_seconds": 0},
    ],
    ids=["interval", "poll", "retry"],
)
def test_the_interval_poll_and_retry_must_be_positive(
    kwargs: dict[str, float],
) -> None:
    with pytest.raises(ValueError, match="positive"):
        ExportSchedule(FakeCkb(), **kwargs)


# --- the HTTP client -------------------------------------------------------


class _Response:
    def __init__(self, body: object, status: int = 200) -> None:
        self._body = body
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self) -> object:
        return self._body


def test_the_client_unwraps_ruuters_response_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls: list[str] = []

    def get(url: str, timeout: object) -> _Response:
        urls.append(url)
        return _Response({"response": TOGGLE_ON})

    monkeypatch.setattr("exporter.services.export_schedule.requests.get", get)
    # CKB_RUUTER_INTERNAL as constants.ini spells it: the project layer
    # included, so the paths must not add it again.
    client = RuuterScheduleClient("http://ruuter-internal:8089/ckb/")

    assert client.content_export_status() == TOGGLE_ON
    assert client.content_export() == TOGGLE_ON
    assert urls == [
        "http://ruuter-internal:8089/ckb/pipeline/content-export-status",
        "http://ruuter-internal:8089/ckb/pipeline/content-export",
    ]
    assert CONTENT_EXPORT_STATUS_PATH == "/pipeline/content-export-status"
    assert CONTENT_EXPORT_PATH == "/pipeline/content-export"


def test_the_client_accepts_an_unwrapped_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "exporter.services.export_schedule.requests.get",
        lambda url, timeout: _Response({"status": "started"}),
    )
    client = RuuterScheduleClient("http://ruuter-internal:8089")
    assert client.content_export() == {"status": "started"}


@pytest.mark.parametrize(
    "response",
    [_Response({}, status=500), _Response(["not", "an", "object"])],
    ids=["http_error", "not_an_object"],
)
def test_the_client_raises_ckb_call_error(
    monkeypatch: pytest.MonkeyPatch, response: _Response
) -> None:
    monkeypatch.setattr(
        "exporter.services.export_schedule.requests.get",
        lambda url, timeout: response,
    )
    with pytest.raises(CkbCallError):
        RuuterScheduleClient("http://ruuter-internal:8089").content_export()


def test_the_client_raises_ckb_call_error_when_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(url: str, timeout: object) -> _Response:
        raise requests.ConnectionError("refused")

    monkeypatch.setattr("exporter.services.export_schedule.requests.get", refuse)
    with pytest.raises(CkbCallError):
        RuuterScheduleClient("http://ruuter-internal:8089").content_export_status()


# --- the thread and the app ------------------------------------------------


def test_the_thread_exports_when_started_and_shuts_down() -> None:
    ckb = FakeCkb(sync_answer=TOGGLE_OFF)
    schedule = ExportSchedule(ckb, interval_seconds=HOUR)
    schedule.start()
    try:
        assert ckb.exported.wait(5), "the timer never ticked"
    finally:
        schedule.shutdown()
    assert ckb.export_calls == 1


def test_the_thread_follows_a_toggle_switched_off_by_polling() -> None:
    """End to end on a real thread: started with the toggle ON, the poll
    alone notices it switched OFF and exports."""
    ckb = FakeCkb(sync_answer=TOGGLE_ON)
    schedule = ExportSchedule(ckb, interval_seconds=HOUR, poll_seconds=0.05)
    schedule.start()
    try:
        deadline = time.monotonic() + 5
        while schedule.status().state != "inactive":
            assert time.monotonic() < deadline, "the timer never settled"
            time.sleep(0.01)
        ckb.sync_answer = TOGGLE_OFF
        assert ckb.exported.wait(5), "the poll never noticed the toggle"
    finally:
        schedule.shutdown()
    assert schedule.status().change_reason == "poll:llm_module_inactive"


@pytest.fixture
def app_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setattr(
        "exporter.api.app.check_single_agency_at_startup", lambda _settings: 1
    )
    monkeypatch.setenv("CONTENT_WORK_DIR", (tmp_path / "work").as_posix())


def test_the_schedule_endpoints_switch_the_timer(
    app_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    ckb = patch_export_schedule(monkeypatch, FakeCkb(sync_answer=TOGGLE_ON))

    with TestClient(app) as client:
        started = client.post("/schedule/start")
        assert started.status_code == 200
        assert started.json()["state"] == "active"
        assert ckb.exported.wait(5), "start did not export at once"

        stopped = client.post("/schedule/stop")
        assert stopped.json()["state"] == "inactive"
        assert stopped.json()["change_reason"] == "ckb_sync"

        # The fake answers before the timer records the result; wait for it.
        deadline = time.monotonic() + 5
        while True:
            health = client.get("/health").json()["export_schedule"]
            if health["last_tick_result"] or time.monotonic() > deadline:
                break
            time.sleep(0.01)
        assert health["state"] == "inactive"
        assert health["interval_seconds"] == HOUR
        assert health["poll_interval_seconds"] == DEFAULT_POLL_SECONDS
        assert health["last_tick_result"] == "started"
    assert ckb.export_calls == 1


def test_the_interval_and_poll_come_from_settings(
    app_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXPORT_SCHEDULE_INTERVAL_SECONDS", "900")
    monkeypatch.setenv("EXPORT_SCHEDULE_POLL_SECONDS", "120")
    patch_export_schedule(monkeypatch)

    with TestClient(app) as client:
        schedule = client.get("/health").json()["export_schedule"]
        assert schedule["interval_seconds"] == 900
        assert schedule["poll_interval_seconds"] == 120


@pytest.mark.parametrize(
    "variable", ["EXPORT_SCHEDULE_INTERVAL_SECONDS", "EXPORT_SCHEDULE_POLL_SECONDS"]
)
def test_an_interval_under_a_minute_is_refused(
    app_env: None, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.setenv(variable, "30")
    with pytest.raises(Exception, match=variable.lower()):
        Settings(content_work_dir="/var/lib/content-external")  # pyright: ignore[reportCallIssue]


def test_the_default_poll_matches_the_setting() -> None:
    """One default, not two that can drift."""
    assert (
        Settings.model_fields["export_schedule_poll_seconds"].default
        == DEFAULT_POLL_SECONDS
    )
