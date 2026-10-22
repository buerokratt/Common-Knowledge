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
visible to the API process, the spawned child and the CLI alike, and survives
a restart.

WHAT THIS IS NOT. It is not the deferred run-state-in-Postgres work: no
schema, no migration, no Resql query, no GUI, and no history. Exactly one
record, overwritten. If an operator needs history outliving the container,
that is the deferred work and this does not pretend to be it.
"""

import json
import logging
import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from exporter.services.locking import try_exclusive_lock

logger = logging.getLogger(__name__)

LAST_RUN_FILENAME = "last_run.json"

# Serialises record()'s read-modify-write across processes. Its own file, not
# the run lock: a `busy` run records precisely because it could not take that.
RECORD_LOCK_FILENAME = ".last_run.lock"

# A record is one small JSON write, so a lock held this long is wedged, not
# busy; past it record() proceeds unserialised rather than lose the record.
_RECORD_LOCK_TIMEOUT_SECONDS = 5.0
_RECORD_LOCK_POLL_SECONDS = 0.02

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
    # The exception class name behind a `failed` outcome (F12). A type name
    # only: /health serves this unauthenticated, and the sanitised message is
    # in the `content-external error` log line.
    error_type: str | None = None
    # F20. Whether the run classified every row. None when the recorder did
    # not say (a record from before F20, or a failure reported before the
    # coordinator could run), which is not the same as False.
    run_observed_full_corpus: bool | None = None


def _optional_bool(value: object) -> bool | None:
    """A JSON boolean, or None for anything else: absent on an older record,
    and never coerced from a string, where "false" would read as True."""
    return value if isinstance(value, bool) else None


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
        # means a write from the spawned child or the CLI is still picked up.
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
        error_type: str | None = None,
        run_observed_full_corpus: bool | None = None,
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

        The read-modify-write is serialised by a lock of its own. A `busy`
        child records without the run lock, so without this it could read a
        `failed` record, lose the race to the run that then succeeds, and
        write the stale failure streak back over the success.
        """
        if outcome not in _OUTCOMES:
            raise ValueError(f"unknown run outcome {outcome!r}")

        with self._record_lock():
            return self._record_locked(
                outcome=outcome,
                run_id=run_id,
                agency_id=agency_id,
                duration_seconds=duration_seconds,
                finished_at=finished_at,
                error_type=error_type,
                run_observed_full_corpus=run_observed_full_corpus,
            )

    @contextmanager
    def _record_lock(self) -> Iterator[None]:
        """Hold the record lock, or give up after _RECORD_LOCK_TIMEOUT_SECONDS
        and proceed unserialised. Observability only: a wedged or unopenable
        lock file must cost at most the race this exists to close, never the
        record itself."""
        lock_path = self._path.with_name(RECORD_LOCK_FILENAME)
        deadline = time.monotonic() + _RECORD_LOCK_TIMEOUT_SECONDS
        with ExitStack() as held:
            while True:
                attempt = ExitStack()
                try:
                    acquired = attempt.enter_context(try_exclusive_lock(lock_path))
                except OSError:
                    logger.warning(
                        "could not open the last-run record lock at %s; "
                        "recording unserialised",
                        lock_path,
                    )
                    break
                if acquired:
                    held.enter_context(attempt)
                    break
                attempt.close()
                if time.monotonic() >= deadline:
                    logger.warning(
                        "the last-run record lock at %s was held for over %.0fs; "
                        "recording unserialised",
                        lock_path,
                        _RECORD_LOCK_TIMEOUT_SECONDS,
                    )
                    break
                time.sleep(_RECORD_LOCK_POLL_SECONDS)
            yield

    def _record_locked(
        self,
        *,
        outcome: RunOutcome,
        run_id: str,
        agency_id: str,
        duration_seconds: float,
        finished_at: str | None,
        error_type: str | None,
        run_observed_full_corpus: bool | None,
    ) -> LastRun:
        # Parsed afresh, not through read()'s cache: the cache is keyed on
        # (mtime, size), and two records of the same size written within the
        # filesystem's timestamp granularity would look unchanged.
        previous = self._parse() if self._path.exists() else None
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
            # Only a failure has one; a stale type on a later success would
            # read as a current fault.
            error_type=error_type if outcome == "failed" else None,
            run_observed_full_corpus=run_observed_full_corpus,
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
                error_type=(
                    None if raw.get("error_type") is None else str(raw["error_type"])
                ),
                run_observed_full_corpus=_optional_bool(
                    raw.get("run_observed_full_corpus")
                ),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("last-run record at %s is missing fields", self._path)
            return None

    def _write(self, run: LastRun) -> None:
        """Atomic replace, so a concurrent reader sees old or new, never half.

        A failure to write is logged and swallowed: losing the observability
        record of a successful export must not turn that export into a failure.

        The temp file is unique per write. A fixed name shared by two writers
        lets one os.replace the other's file away, which loses a record even
        when record()'s lock had to be given up on.
        """
        tmp: str | None = None
        try:
            # Matches try_exclusive_lock's behaviour in locking.py. In the
            # service the directory always exists — assert_work_dir_usable
            # created it before this store was built — but the CLI and the
            # forked child construct their own stores, and a missing parent
            # must not be the difference between a recorded run and a silent
            # one.
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                prefix=f"{LAST_RUN_FILENAME}.", suffix=".tmp", dir=self._path.parent
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        asdict(run), sort_keys=True, ensure_ascii=False, indent=2
                    )
                )
            os.replace(tmp, self._path)
        except OSError:
            logger.warning(
                "could not write the last-run record to %s; /health will "
                "report a stale or absent last run",
                self._path,
            )
            if tmp is not None:
                with suppress(OSError):
                    Path(tmp).unlink(missing_ok=True)
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
