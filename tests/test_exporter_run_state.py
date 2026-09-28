"""A15 — the last-run record and the greppable terminal line.

Pure filesystem and string work: no Docker, no network.

Stage F is the caller; A15 ships the mechanism, so these tests drive it
directly. That is deliberate rather than a gap — the alternative is shipping
an observability feature whose first exercise is the incident it exists to
make visible.
"""

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from exporter.api.models import LastRunSummary
from exporter.services.run_log import (
    DIFF_BUCKETS,
    RUN_LOG_PREFIX,
    format_run_line,
    log_run_outcome,
    reserved_field_names,
)
from exporter.services.run_state import (
    LAST_RUN_FILENAME,
    RunStateStore,
    utc_now_iso,
)

# --------------------------------------------------------------------------
# record / read
# --------------------------------------------------------------------------


def _store(tmp_path: Path) -> RunStateStore:
    return RunStateStore(tmp_path)


def test_no_record_reads_as_none(tmp_path: Path) -> None:
    """Never run is not the same as ran and succeeded."""
    assert _store(tmp_path).read() is None


def test_a_recorded_run_round_trips(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record(
        outcome="success",
        run_id="r-1",
        agency_id="agency-1",
        duration_seconds=12.5,
    )

    run = store.read()
    assert run is not None
    assert run.outcome == "success"
    assert run.run_id == "r-1"
    assert run.agency_id == "agency-1"
    assert run.duration_seconds == 12.5


def test_the_record_lands_on_the_work_dir(tmp_path: Path) -> None:
    """The whole reason it is a file: the forked export child (F9) and the
    run_sync CLI (F10) are different processes from the one serving /health."""
    store = _store(tmp_path)
    store.record(outcome="success", run_id="r-1", agency_id="a-1", duration_seconds=1.0)
    assert (tmp_path / LAST_RUN_FILENAME).is_file()


def test_a_second_process_sees_the_first_process_record(tmp_path: Path) -> None:
    """Two stores over one directory stand in for two processes over one
    volume, which is the shape Stage F actually builds."""
    writer = _store(tmp_path)
    reader = _store(tmp_path)
    assert reader.read() is None

    writer.record(outcome="failed", run_id="r-9", agency_id="a-1", duration_seconds=3.0)

    seen = reader.read()
    assert seen is not None
    assert seen.run_id == "r-9"


def test_recording_overwrites_rather_than_appending(tmp_path: Path) -> None:
    """Exactly one record. History is the deferred Postgres work and this
    must not quietly become a log file on a 1Gi volume."""
    store = _store(tmp_path)
    for index in range(5):
        store.record(
            outcome="success",
            run_id=f"r-{index}",
            agency_id="a-1",
            duration_seconds=1.0,
        )

    payload = json.loads((tmp_path / LAST_RUN_FILENAME).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert payload["run_id"] == "r-4"


def test_no_temp_file_is_left_behind(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record(outcome="success", run_id="r-1", agency_id="a-1", duration_seconds=1.0)
    assert not list(tmp_path.glob("*.tmp"))


def test_an_unknown_outcome_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown run outcome"):
        _store(tmp_path).record(
            outcome="finished",  # pyright: ignore[reportArgumentType]
            run_id="r-1",
            agency_id="a-1",
            duration_seconds=1.0,
        )


def test_finished_at_defaults_to_now_and_is_utc(tmp_path: Path) -> None:
    run = _store(tmp_path).record(
        outcome="success", run_id="r-1", agency_id="a-1", duration_seconds=1.0
    )
    assert run.finished_at.endswith("Z")
    datetime.fromisoformat(run.finished_at.replace("Z", "+00:00"))


def test_utc_now_iso_is_timezone_aware() -> None:
    """A naive local timestamp in a container whose TZ nobody set makes an
    incident timeline unreconstructable."""
    parsed = datetime.fromisoformat(utc_now_iso().replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


# --------------------------------------------------------------------------
# The consecutive counters
# --------------------------------------------------------------------------


def test_consecutive_failures_accumulate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for _ in range(3):
        store.record(
            outcome="failed", run_id="r", agency_id="a-1", duration_seconds=1.0
        )

    run = store.read()
    assert run is not None
    assert run.consecutive_failures == 3


@pytest.mark.parametrize("recovery", ["success", "unchanged"])
def test_a_successful_run_clears_the_failure_streak(
    tmp_path: Path, recovery: str
) -> None:
    """`unchanged` counts as success: Gate 1 matched, so there was nothing to
    publish, which is a run that worked and not one that was skipped."""
    store = _store(tmp_path)
    store.record(outcome="failed", run_id="r", agency_id="a-1", duration_seconds=1.0)
    store.record(
        outcome=recovery,  # pyright: ignore[reportArgumentType]
        run_id="r",
        agency_id="a-1",
        duration_seconds=1.0,
    )

    run = store.read()
    assert run is not None
    assert run.consecutive_failures == 0


def test_busy_does_not_clear_the_failure_streak(tmp_path: Path) -> None:
    """The load-bearing asymmetry. A `busy` run did not happen, so it is no
    evidence that whatever was failing has stopped — and if it cleared the
    counter, a drain colliding with an export would mask a real failure
    streak and the alert would never fire."""
    store = _store(tmp_path)
    store.record(outcome="failed", run_id="r", agency_id="a-1", duration_seconds=1.0)
    store.record(outcome="failed", run_id="r", agency_id="a-1", duration_seconds=1.0)
    store.record(outcome="busy", run_id="r", agency_id="a-1", duration_seconds=0.1)

    run = store.read()
    assert run is not None
    assert run.consecutive_failures == 2
    assert run.consecutive_busy == 1


def test_consecutive_busy_accumulates(tmp_path: Path) -> None:
    """G9: counted rather than discarded, because routinely-busy ticks mean
    the export exceeds the cron interval."""
    store = _store(tmp_path)
    for _ in range(4):
        store.record(outcome="busy", run_id="r", agency_id="a-1", duration_seconds=0.1)

    run = store.read()
    assert run is not None
    assert run.consecutive_busy == 4


def test_a_failure_clears_the_busy_streak(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record(outcome="busy", run_id="r", agency_id="a-1", duration_seconds=0.1)
    store.record(outcome="failed", run_id="r", agency_id="a-1", duration_seconds=1.0)

    run = store.read()
    assert run is not None
    assert run.consecutive_busy == 0


# --------------------------------------------------------------------------
# Corruption tolerance — /health must never 500 over its own bookkeeping
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "contents",
    [
        "",
        "not json",
        "[]",
        '"a string"',
        "null",
        '{"outcome": "success"}',  # missing fields
        '{"outcome": "exploded", "run_id": "r", "agency_id": "a",'
        ' "finished_at": "x", "duration_seconds": 1}',
        '{"outcome": "success", "run_id": "r", "agency_id": "a",'
        ' "finished_at": "x", "duration_seconds": "not-a-float"}',
    ],
)
def test_a_corrupt_record_reads_as_none_without_raising(
    tmp_path: Path, contents: str
) -> None:
    (tmp_path / LAST_RUN_FILENAME).write_text(contents, encoding="utf-8")
    assert _store(tmp_path).read() is None


def test_a_corrupt_record_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / LAST_RUN_FILENAME).write_text("not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="exporter.services.run_state"):
        _store(tmp_path).read()
    assert caplog.records


def test_a_record_written_before_the_counters_existed_still_reads(
    tmp_path: Path,
) -> None:
    """The migration story: default the new fields rather than discard an
    otherwise-valid record."""
    (tmp_path / LAST_RUN_FILENAME).write_text(
        json.dumps(
            {
                "outcome": "success",
                "run_id": "r-old",
                "agency_id": "a-1",
                "finished_at": "2026-09-16T17:00:00Z",
                "duration_seconds": 4.0,
            }
        ),
        encoding="utf-8",
    )

    run = _store(tmp_path).read()
    assert run is not None
    assert run.run_id == "r-old"
    assert run.consecutive_failures == 0


def test_a_record_in_a_nonexistent_directory_reads_as_none() -> None:
    assert RunStateStore(Path("does-not-exist-anywhere")).read() is None


def test_an_unwritable_location_does_not_fail_the_run(tmp_path: Path) -> None:
    """Losing the observability record of a successful export must not turn
    that export into a failure."""
    blocker = tmp_path / "afile"
    blocker.write_text("", encoding="utf-8")

    RunStateStore(blocker).record(
        outcome="success", run_id="r-1", agency_id="a-1", duration_seconds=1.0
    )  # no raise


def test_the_cache_picks_up_an_external_write(tmp_path: Path) -> None:
    """The mtime-keyed cache exists because /health is on a liveness probe. It
    must not turn a write from another process into a stale read."""
    store = _store(tmp_path)
    store.record(outcome="success", run_id="r-1", agency_id="a-1", duration_seconds=1.0)
    assert store.read() is not None

    _store(tmp_path).record(
        outcome="failed", run_id="r-2", agency_id="a-1", duration_seconds=2.0
    )

    run = store.read()
    assert run is not None
    assert run.run_id == "r-2"


def test_the_cache_returns_the_same_object_when_nothing_changed(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.record(outcome="success", run_id="r-1", agency_id="a-1", duration_seconds=1.0)
    assert store.read() is store.read()


# --------------------------------------------------------------------------
# The agreed alert threshold
# --------------------------------------------------------------------------


def test_no_record_is_not_alert_worthy(tmp_path: Path) -> None:
    assert _store(tmp_path).alert_worthy() is None


def test_one_failure_is_not_alert_worthy(tmp_path: Path) -> None:
    """One failure is the design working as intended — the next tick is the
    retry. That is why the threshold is two and not one."""
    store = _store(tmp_path)
    store.record(outcome="failed", run_id="r", agency_id="a-1", duration_seconds=1.0)
    assert store.alert_worthy() is None


def test_two_consecutive_failures_are_alert_worthy(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for _ in range(2):
        store.record(
            outcome="failed", run_id="r", agency_id="a-1", duration_seconds=1.0
        )
    reason = store.alert_worthy()
    assert reason is not None
    assert "consecutive failed" in reason


def test_a_stale_successful_run_is_not_alert_worthy(tmp_path: Path) -> None:
    """A `success` is a success however long ago — the interval, not the
    record, is what would have produced a newer one."""
    store = _store(tmp_path)
    old = (datetime.now(UTC) - timedelta(hours=48)).isoformat().replace("+00:00", "Z")
    store.record(
        outcome="success",
        run_id="r",
        agency_id="a-1",
        duration_seconds=1.0,
        finished_at=old,
    )
    assert store.alert_worthy() is None


def test_a_long_run_of_busy_ticks_is_alert_worthy(tmp_path: Path) -> None:
    """The G9 case: every tick reports `busy`, nothing is an error, and
    nothing has succeeded in hours."""
    store = _store(tmp_path)
    old = (datetime.now(UTC) - timedelta(hours=9)).isoformat().replace("+00:00", "Z")
    store.record(
        outcome="busy",
        run_id="r",
        agency_id="a-1",
        duration_seconds=0.1,
        finished_at=old,
    )
    reason = store.alert_worthy()
    assert reason is not None
    assert "no successful run" in reason


def test_an_unparseable_timestamp_does_not_raise(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record(
        outcome="busy",
        run_id="r",
        agency_id="a-1",
        duration_seconds=0.1,
        finished_at="not-a-timestamp",
    )
    assert store.alert_worthy() is None


# --------------------------------------------------------------------------
# The greppable line
# --------------------------------------------------------------------------


def _line(**overrides: object) -> str:
    kwargs: dict[str, object] = {
        "outcome": "success",
        "run_id": "r-1",
        "agency_id": "agency-1",
        "sink": "object_store",
        "duration_seconds": 12.25,
    }
    kwargs.update(overrides)
    return format_run_line(**kwargs)  # pyright: ignore[reportArgumentType]


def test_the_line_starts_with_the_stable_prefix() -> None:
    """The prefix is the deliverable: an alert rule should be a log query and
    not a code change."""
    assert _line().startswith(RUN_LOG_PREFIX)


def test_the_line_carries_the_outcome_and_the_run_identity() -> None:
    line = _line()
    assert "outcome=success" in line
    assert "run_id=r-1" in line
    assert "agency_id=agency-1" in line
    assert "sink=object_store" in line
    assert "duration_seconds=12.250" in line


def test_every_bucket_appears_even_at_zero() -> None:
    """A field that appears only sometimes is a field no log query can rely
    on, and docs_deleted absent versus docs_deleted=0 is exactly the
    distinction someone investigating a deletion needs."""
    line = _line(counts={"new": 3})
    for bucket in DIFF_BUCKETS:
        assert f"docs_{bucket}=" in line
    assert "docs_new=3" in line
    assert "docs_deleted=0" in line


def test_counts_are_prefixed_so_unchanged_is_unambiguous() -> None:
    """`unchanged` is both an outcome value and a bucket name, and
    `outcome=unchanged unchanged=0` is a line that invites misreading."""
    line = _line(outcome="unchanged", counts={"unchanged": 340})
    assert "outcome=unchanged" in line
    assert "docs_unchanged=340" in line
    assert " unchanged=340" not in line


def test_an_unknown_bucket_is_rejected() -> None:
    """Stage C owns the bucket list; a typo here would silently drop a count
    from every line."""
    with pytest.raises(ValueError, match="unknown diff buckets"):
        _line(counts={"modified": 1})


def test_no_bucket_can_shadow_a_reserved_field() -> None:
    assert not set(DIFF_BUCKETS) & reserved_field_names()


def test_fields_are_sorted_so_two_runs_diff_line_for_line() -> None:
    body = _line().removeprefix(RUN_LOG_PREFIX).strip()
    keys = [field.split("=", 1)[0] for field in body.split(" ")]
    assert keys == sorted(keys)


def test_every_field_is_a_single_key_equals_value_token() -> None:
    """Whitespace in a value would make one run emit something that parses as
    two fields, which is how a log-derived metric silently goes wrong."""
    body = _line(run_id="r 1 with spaces").removeprefix(RUN_LOG_PREFIX).strip()
    for field in body.split(" "):
        assert field.count("=") >= 1
        assert field.split("=", 1)[1] != ""


def test_a_url_style_credential_is_redacted() -> None:
    """F13 and L14: on the llm-module sink the thing that failed was a request
    whose body IS document text, so the line goes through redaction."""
    line = _line(agency_id="https://svc:hunter2@llm.example")
    assert "hunter2" not in line


def test_an_empty_value_becomes_a_placeholder() -> None:
    """An empty value would produce a bare `key=` and break the token
    contract above."""
    assert "agency_id=-" in _line(agency_id="")


def test_deletions_recorded_is_reported() -> None:
    assert "deletions_recorded=7" in _line(deletions_recorded=7)


# --------------------------------------------------------------------------
# log_run_outcome
# --------------------------------------------------------------------------


def test_a_failure_is_logged_once_at_error(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="exporter.services.run_log"):
        log_run_outcome(
            outcome="failed",
            run_id="r-1",
            agency_id="a-1",
            sink="llm_module",
            duration_seconds=1.0,
        )

    records = [r for r in caplog.records if r.name == "exporter.services.run_log"]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].getMessage().startswith(RUN_LOG_PREFIX)


@pytest.mark.parametrize("outcome", ["success", "unchanged", "busy"])
def test_every_other_outcome_is_logged_at_info(
    outcome: str, caplog: pytest.LogCaptureFixture
) -> None:
    """`busy` at INFO deliberately: a concurrent run is by design not an
    error, and an ERROR line for it would train operators to ignore them."""
    with caplog.at_level(logging.INFO, logger="exporter.services.run_log"):
        log_run_outcome(
            outcome=outcome,  # pyright: ignore[reportArgumentType]
            run_id="r-1",
            agency_id="a-1",
            sink="object_store",
            duration_seconds=1.0,
        )

    records = [r for r in caplog.records if r.name == "exporter.services.run_log"]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO


def test_log_run_outcome_returns_the_line_it_emitted() -> None:
    """So a caller can put the same text in a RunReport without formatting it
    twice, and the two can never disagree."""
    line = log_run_outcome(
        outcome="success",
        run_id="r-1",
        agency_id="a-1",
        sink="object_store",
        duration_seconds=1.0,
    )
    assert line.startswith(RUN_LOG_PREFIX)


# --------------------------------------------------------------------------
# The two Literals that have to agree
# --------------------------------------------------------------------------


def test_the_dataclass_and_the_api_model_carry_the_same_outcomes() -> None:
    """RunOutcome is declared in services/ and duplicated in api/models.py,
    because pydantic belongs at the edge and services/ must not import api/.
    Duplication is fine; divergence is not."""
    from typing import get_args

    from exporter.services.run_state import RunOutcome

    model_outcomes = set(get_args(LastRunSummary.model_fields["outcome"].annotation))
    assert model_outcomes == set(get_args(RunOutcome))


def test_the_dataclass_maps_onto_the_api_model_field_for_field(
    tmp_path: Path,
) -> None:
    """app.py builds LastRunSummary(**asdict(last_run)), so a field added to
    one and not the other breaks /health at runtime rather than here."""
    run = _store(tmp_path).record(
        outcome="success", run_id="r-1", agency_id="a-1", duration_seconds=1.0
    )
    summary = LastRunSummary(**asdict(run))
    assert set(asdict(run)) == set(LastRunSummary.model_fields)
    assert summary.run_id == "r-1"
