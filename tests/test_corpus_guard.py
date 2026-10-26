"""F20 — the one gate for anything concluded from "this run saw the whole
corpus". The sweeps that call it are Stage J and L; this pins what it
accepts, so they inherit a guard that is already proved."""

import pytest

from exporter.core.schemas import RunReport
from exporter.services.corpus_guard import (
    IncompleteCorpusError,
    require_observed_full_corpus,
)


def _report(
    outcome: str = "success",
    *,
    full: bool = True,
    limit: int | None = None,
    held_back: int = 0,
) -> RunReport:
    return RunReport(
        run_id="r-1",
        agency_id="agency-1",
        sink_id="object_store",
        outcome=outcome,
        started_at="2026-10-22T10:00:00Z",
        finished_at="2026-10-22T10:05:00Z",
        limit=limit,
        run_observed_full_corpus=full,
        documents_held_back=held_back,
    )


def test_a_successful_full_run_passes() -> None:
    require_observed_full_corpus(_report(), operation="sweep_stale")


@pytest.mark.parametrize(
    ("report", "reason"),
    [
        (_report(full=False, limit=25), "limited to 25"),
        (_report(full=False), "did not classify every row"),
        (_report("failed", full=False), "'failed'"),
        (_report("busy", full=False), "'busy'"),
        (_report("unchanged", full=False), "'unchanged'"),
        (_report(held_back=3), "held 3 document"),
    ],
    ids=["limited", "aborted-or-early", "failed", "busy", "unchanged", "held-back"],
)
def test_anything_less_is_refused_with_its_reason(
    report: RunReport, reason: str
) -> None:
    with pytest.raises(IncompleteCorpusError, match=reason):
        require_observed_full_corpus(report, operation="run_purge")


def test_a_failed_run_is_refused_even_if_its_flag_says_otherwise() -> None:
    """The outcome is checked too: a run that failed after walking every row
    committed nothing, so nothing may be concluded from it."""
    with pytest.raises(IncompleteCorpusError):
        require_observed_full_corpus(_report("failed"), operation="sweep_stale")


def test_a_limit_the_run_never_reached_is_still_a_full_run() -> None:
    """--limit larger than the reads the run needed skipped nothing, so the
    run saw the whole corpus; the flag, not the presence of --limit, is what
    the guard reads."""
    require_observed_full_corpus(_report(limit=500), operation="sweep_stale")


def test_the_refusal_names_the_operation_and_the_run() -> None:
    with pytest.raises(IncompleteCorpusError, match=r"orphan tail sweep after run r-1"):
        require_observed_full_corpus(
            _report(full=False), operation="the orphan tail sweep"
        )
