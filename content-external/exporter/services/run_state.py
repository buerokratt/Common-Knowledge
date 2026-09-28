"""The last terminal run, so an hourly job can say when it last succeeded.

A15. *"The next tick is the retry"* is correct for transient failure and
**symmetric**: a wrong base URL, a rotated-away credential or a query past
its timeout also produce `failed` every hour, indefinitely, with the evidence
only in stdout. This module is half of the cheap fix — `/health` reporting
the last run — and exporter/services/run_log.py is the other half.

WHY A FILE AND NOT A DICT IN MEMORY. A15 says "held in memory", and in
memory is the wrong mechanism for the shape Stage F actually builds:
`POST /export_agency_async` runs the export in a `multiprocessing.Process`
(F9) and `python -m exporter.run_sync` is a separate process entirely (F10).
A dict in the API process would therefore report `null` forever in
production while passing every test that drove a run in-process. One small
JSON file on CONTENT_WORK_DIR — already durable, already asserted by A14 — is
visible to the API process, the forked child and the CLI alike, and survives
a restart.

WHAT THIS IS NOT. It is not the deferred run-state-in-Postgres work: no
schema, no migration, no Resql query, no GUI, and no history. Exactly one
record, overwritten. If an operator needs history outliving the container,
that is the deferred work and this does not pretend to be it.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

LAST_RUN_FILENAME = "last_run.json"

# Must stay in step with exporter/api/models.py's LastRunSummary.outcome. A
# test asserts the two agree rather than trusting that they do.
RunOutcome = Literal["success", "unchanged", "busy", "failed"]

_OUTCOMES: frozenset[str] = frozenset({"success", "unchanged", "busy", "failed"})

# Outcomes that mean the export actually ran to completion. `unchanged` counts:
# Gate 1 matched, so there was nothing to publish, which is a successful run
# and not a skipped one.
_SUCCESSFUL: frozenset[str] = frozenset({"success", "unchanged"})


@dataclass(frozen=True, slots=True)
class LastRun:
    """A terminal run outcome.

    A plain dataclass, not a pydantic model: the house rule is pydantic at the
    API boundary only (B2), and exporter/api/app.py maps this onto
    LastRunSummary for the response. That also keeps services/ from importing
    api/, which would invert the layering.
    """

    outcome: RunOutcome
    run_id: str
    agency_id: str
    finished_at: str
    duration_seconds: float
    consecutive_failures: int = 0
    consecutive_busy: int = 0


def utc_now_iso() -> str:
    """An ISO-8601 UTC timestamp, seconds resolution, always suffixed Z.

    Explicit rather than datetime.now(): a naive local timestamp in a
    container whose TZ nobody set is the kind of detail that makes an incident
    timeline unreconstructable.
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class RunStateStore:
    """Reads and writes the single last-run record under CONTENT_WORK_DIR."""

    def __init__(self, work_dir: Path) -> None:
        self._path = work_dir / LAST_RUN_FILENAME
        # (mtime_ns, size) of the parse currently cached. /health is on a
        # liveness probe, so it is called far more often than a run completes;
        # one stat() per call beats one parse per call, and keying on mtime
        # means a write from the forked child or the CLI is still picked up.
        self._cache_key: tuple[int, int] | None = None
        self._cached: LastRun | None = None

    @property
    def path(self) -> Path:
        return self._path

    def record(
        self,
        *,
        outcome: RunOutcome,
        run_id: str,
        agency_id: str,
        duration_seconds: float,
        finished_at: str | None = None,
    ) -> LastRun:
        """Persist a terminal outcome, maintaining the consecutive counters.

        The counters are here rather than computed by a caller because they
        need the previous record, and the previous record is this module's
        business. G9 requires consecutive `busy` outcomes to be counted rather
        than discarded, and the agreed alert rule needs consecutive failures.

        Written atomically (temp file + os.replace) because /health may read
        this file at any moment, including from another process, and a
        half-written JSON document read as "corrupt" would look like a lost
        run rather than a concurrent write.
        """
        if outcome not in _OUTCOMES:
            raise ValueError(f"unknown run outcome {outcome!r}")

        previous = self.read()
        failures = previous.consecutive_failures if previous else 0
        busy = previous.consecutive_busy if previous else 0

        if outcome == "failed":
            failures += 1
            busy = 0
        elif outcome == "busy":
            # Deliberately does NOT reset the failure streak. A `busy` run did
            # not happen, so it is no evidence that whatever was failing has
            # stopped failing — and if it cleared the counter, a drain
            # colliding with an export would mask a real failure streak.
            busy += 1
        else:
            failures = 0
            busy = 0

        current = LastRun(
            outcome=outcome,
            run_id=run_id,
            agency_id=agency_id,
            finished_at=finished_at or utc_now_iso(),
            duration_seconds=round(float(duration_seconds), 3),
            consecutive_failures=failures,
            consecutive_busy=busy,
        )
        self._write(current)
        return current

    def read(self) -> LastRun | None:
        """The last recorded run, or None.

        None covers three cases that are all honestly "nothing to report":
        never run, file missing, file unreadable or malformed. A malformed
        file additionally warns — but it must never raise, because /health
        returning 500 over its own bookkeeping would take a working service
        out of a load balancer.
        """
        try:
            stat = self._path.stat()
        except OSError:
            self._cache_key, self._cached = None, None
            return None

        key = (stat.st_mtime_ns, stat.st_size)
        if key == self._cache_key:
            return self._cached

        parsed = self._parse()
        self._cache_key, self._cached = key, parsed
        return parsed

    def _parse(self) -> LastRun | None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning(
                "could not read the last-run record at %s; reporting no last "
                "run. This affects observability only, never an export.",
                self._path,
            )
            return None

        if not isinstance(raw, dict):
            logger.warning("last-run record at %s is not an object", self._path)
            return None

        outcome = raw.get("outcome")
        if outcome not in _OUTCOMES:
            logger.warning(
                "last-run record at %s carries an unknown outcome", self._path
            )
            return None

        try:
            return LastRun(
                outcome=outcome,
                run_id=str(raw["run_id"]),
                agency_id=str(raw["agency_id"]),
                finished_at=str(raw["finished_at"]),
                duration_seconds=float(raw["duration_seconds"]),
                # Absent on a record written before these were added, which is
                # the migration story: default rather than discard the record.
                consecutive_failures=int(raw.get("consecutive_failures", 0)),
                consecutive_busy=int(raw.get("consecutive_busy", 0)),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("last-run record at %s is missing fields", self._path)
            return None

    def _write(self, run: LastRun) -> None:
        """Atomic replace, so a concurrent reader sees old or new, never half.

        A failure to write is logged and swallowed: losing the observability
        record of a successful export must not turn that export into a failure.
        """
        tmp = self._path.with_suffix(".json.tmp")
        try:
            # Matches try_exclusive_lock's behaviour in locking.py. In the
            # service the directory always exists — assert_work_dir_usable
            # created it before this store was built — but the CLI and the
            # forked child construct their own stores, and a missing parent
            # must not be the difference between a recorded run and a silent
            # one.
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(asdict(run), sort_keys=True, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self._path)
        except OSError:
            logger.warning(
                "could not write the last-run record to %s; /health will "
                "report a stale or absent last run",
                self._path,
            )
            return

        self._cache_key, self._cached = None, None

    def alert_worthy(
        self, *, max_consecutive_failures: int = 2, max_age_hours: int = 6
    ) -> str | None:
        """The agreed threshold, as a function rather than only a README line.

        Returns a reason, or None. **Two consecutive `failed`, or no `success`
        in 6 hours** — one failure is the design working as intended, which is
        why the default is two and not one.

        Nothing calls this yet. It exists so the threshold has one definition
        that a later monitoring surface can reuse, instead of being re-derived
        from prose in an alert rule.
        """
        run = self.read()
        if run is None:
            return None

        if run.consecutive_failures >= max_consecutive_failures:
            return (
                f"{run.consecutive_failures} consecutive failed runs "
                f"(threshold {max_consecutive_failures})"
            )

        try:
            finished = datetime.fromisoformat(run.finished_at.replace("Z", "+00:00"))
        except ValueError:
            return None

        age_hours = (datetime.now(UTC) - finished).total_seconds() / 3600
        if run.outcome in _SUCCESSFUL or age_hours <= max_age_hours:
            return None
        return f"no successful run in {age_hours:.1f} hours (threshold {max_age_hours})"
