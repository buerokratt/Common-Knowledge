"""One greppable line per terminal run outcome.

A15's other half. The point is that an alert rule should be a log query and
not a code change: a stable prefix and stable `key=value` fields mean ops can
write `content-external run` + `outcome=failed` today and never ask for a
metrics endpoint. `CONFIG_LOG_PREFIX` in exporter/api/config.py follows the
same convention for the startup echo, deliberately — two prefixes, both one
grep away.

WHY THIS IS UNDER services/ AND NOT core/. Formatting a line is pure, so
core/ would be the natural home. But the line carries a `sink=` field, and
Stage C's acceptance check is `grep -rE 'sinks|stores|blob|http'
exporter/core/` returning nothing. That rule is about not naming a
destination anywhere in the pure core, and a field name is naming one. The
rule wins.

REDACTION IS NOT OPTIONAL HERE (F13, L14). This line is emitted on every run,
including failures, and on the llm-module sink the thing that failed was a
request whose body *is* Estonian government document text. So: keys, ids, hex
hash prefixes, counts and durations only. Never chunk text, never a signed
URL, never a request or response body. Every string value goes through
sanitize_sensitive_text, and whitespace is collapsed so one run can never
emit something that parses as two fields.
"""

import logging
from collections.abc import Mapping

from exporter.core.redaction import sanitize_sensitive_text
from exporter.services.run_state import RunOutcome
from exporter.sinks.base import UpsertStatus

logger = logging.getLogger(__name__)

# Grep this. The log level is a convenience on top (failed lands at ERROR so
# level-based alerting works too), never the thing to depend on.
RUN_LOG_PREFIX = "content-external run"

# Stage C's six diff buckets. Prefixed in the output — `docs_unchanged=340`
# rather than `unchanged=340` — because `unchanged` is also an OUTCOME value,
# and `outcome=unchanged unchanged=0` is a line that invites misreading.
DIFF_BUCKETS = (
    "new",
    "content_changed",
    "metadata_changed",
    "unchanged",
    "skipped",
    "deleted",
)

_COUNT_PREFIX = "docs_"

# F18's per-document sink outcomes, emitted as `upsert_published=` and so on.
# Prefixed for the same reason as the buckets, and because `published` alone
# would read as a document count rather than an upsert count.
UPSERT_OUTCOMES = tuple(status.value for status in UpsertStatus)

_UPSERT_PREFIX = "upsert_"

# Fields the caller may not overwrite by passing a same-named count.
_RESERVED = frozenset(
    {
        "outcome",
        "run_id",
        "agency_id",
        "sink",
        "duration_seconds",
        "deletions_recorded",
        "error_type",
        "full_corpus",
        *(f"{_UPSERT_PREFIX}{outcome}" for outcome in UPSERT_OUTCOMES),
    }
)


def scrub_value(value: object) -> str:
    """A value safe to put in a `key=value` line.

    Whitespace is collapsed to `_` rather than quoted: quoting would need
    escaping, escaping needs a parser, and a log line nobody can grep with
    `awk` is not the deliverable A15 asked for. Public because the
    `content-external error` line (exporter/worker/utils.py) follows the
    same convention.
    """
    text = sanitize_sensitive_text(str(value))
    return "_".join(text.split()) or "-"


def format_run_line(
    *,
    outcome: RunOutcome,
    run_id: str,
    agency_id: str,
    sink: str,
    duration_seconds: float,
    counts: Mapping[str, int] | None = None,
    deletions_recorded: int = 0,
    error_type: str | None = None,
    upsert_outcomes: Mapping[str, int] | None = None,
    full_corpus: bool = False,
) -> str:
    """Render the terminal line. Pure — no logging, so it is testable.

    Missing buckets are emitted as 0 rather than omitted. A field that appears
    only sometimes is a field no alert rule or log-parsing query can rely on,
    and `docs_deleted` absent versus `docs_deleted=0` is exactly the
    distinction someone investigating a deletion would need. The same rule
    makes `error_type` always present, as `-` when the run did not fail, and
    every `upsert_*` count present, as 0 when nothing had that outcome.
    """
    counts = counts or {}
    unknown = set(counts) - set(DIFF_BUCKETS)
    if unknown:
        raise ValueError(f"unknown diff buckets: {sorted(unknown)}")
    upsert_outcomes = upsert_outcomes or {}
    unknown = set(upsert_outcomes) - set(UPSERT_OUTCOMES)
    if unknown:
        raise ValueError(f"unknown upsert outcomes: {sorted(unknown)}")

    fields: dict[str, str] = {
        "outcome": scrub_value(outcome),
        "run_id": scrub_value(run_id),
        "agency_id": scrub_value(agency_id),
        "sink": scrub_value(sink),
        "duration_seconds": f"{float(duration_seconds):.3f}",
        "deletions_recorded": str(int(deletions_recorded)),
        "error_type": scrub_value(error_type) if error_type else "-",
        "full_corpus": "true" if full_corpus else "false",
    }
    for bucket in DIFF_BUCKETS:
        fields[f"{_COUNT_PREFIX}{bucket}"] = str(int(counts.get(bucket, 0)))
    for status in UPSERT_OUTCOMES:
        fields[f"{_UPSERT_PREFIX}{status}"] = str(int(upsert_outcomes.get(status, 0)))

    # Sorted so two runs diff line-for-line, as with the startup echo.
    body = " ".join(f"{key}={fields[key]}" for key in sorted(fields))
    return f"{RUN_LOG_PREFIX} {body}"


def log_run_outcome(
    *,
    outcome: RunOutcome,
    run_id: str,
    agency_id: str,
    sink: str,
    duration_seconds: float,
    counts: Mapping[str, int] | None = None,
    deletions_recorded: int = 0,
    error_type: str | None = None,
    upsert_outcomes: Mapping[str, int] | None = None,
    full_corpus: bool = False,
) -> str:
    """Emit the terminal line once, and return it.

    `failed` at ERROR, everything else at INFO — including `busy`, which is by
    design not an error. Returning the line is what lets a caller put the same
    text in a RunReport without formatting it twice.
    """
    line = format_run_line(
        outcome=outcome,
        run_id=run_id,
        agency_id=agency_id,
        sink=sink,
        duration_seconds=duration_seconds,
        counts=counts,
        deletions_recorded=deletions_recorded,
        error_type=error_type,
        upsert_outcomes=upsert_outcomes,
        full_corpus=full_corpus,
    )
    logger.error(line) if outcome == "failed" else logger.info(line)
    return line


def reserved_field_names() -> frozenset[str]:
    """The non-count fields, for tests that assert no count can shadow one."""
    return _RESERVED
