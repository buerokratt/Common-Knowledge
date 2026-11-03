"""`python -m exporter.run_sync` — run one export in the foreground (F10).

AN OPERATIONS TOOL, NOT THE PRODUCTION SURFACE. Production runs come from
CronManager through POST /export_agency_async. This is for the cases a human
drives: the first end-to-end run before the cron is enabled, a bootstrap
validated on a slice with --limit, re-exporting by hand, debugging. It runs
the same task the endpoint's child runs (exporter/worker/tasks.py), takes the
same run lock — so it reports `busy` while a scheduled export holds it — and
records its outcome where /health reads it.

  --agency-id ID     the agency to export (required)
  --run-id ID        default: a generated, sortable id
  --force            bypass Gate 1 (E4): walk every row even when the
                     watermark, the live count and the chunker fingerprint
                     all match the committed manifest. A chunker-geometry
                     change never needs it: the fingerprint comparison
                     catches that.
  --limit N          read at most N documents this run. The rest are held,
                     never deleted, and the corpus watermark is withheld, so
                     the next full run still walks every row.
  --accept-existing-content
                     on a first run (no manifest), publish even though the
                     destination already holds this agency's content (F21).
                     Without it such a run fails: a missing manifest in a
                     mature deployment means something deleted it, and a cold
                     start would orphan every document deleted meanwhile. Use
                     it only when that content is known to be safe to
                     overwrite. A bootstrap of this service's own that died
                     before its first checkpoint resumes without it.
  --accept-rechunk   let a pending re-chunk (a CHUNK_PROFILE, CHUNKER_VERSION
                     or NORMALISER_VERSION change) run on a destination that
                     re-embeds every chunk it receives — the llm-module (L18).
                     Without it such a run fails having sent nothing, every
                     tick, until an operator runs it by hand: announced,
                     --limit first, at reduced EXPORT_CONCURRENCY. See the
                     RUNBOOK.
  --output-json P    write the report as JSON to P, or `-` for stdout

The report is JSON on stdout (or the file); logs go to stderr, so stdout
pipes cleanly into jq. Exit codes follow the run outcomes: 0 for success,
unchanged and busy (busy is by design not an error), 1 for failed, and 2 for
a usage or configuration error that stopped the run before it started.
"""

import argparse
import logging
import sys
from collections.abc import Sequence

from exporter.api.config import (
    ConfigurationError,
    assert_work_dir_usable,
    load_settings,
)
from exporter.cli import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    positive_int,
    segment,
    write_json_report,
)
from exporter.core.redaction import sanitize_sensitive_text
from exporter.core.schemas import RunReport, to_json_dict
from exporter.services.run_state import RunStateStore
from exporter.worker.tasks import (
    ExportTask,
    configure_logging,
    export_agency_task,
    new_run_id,
)

__all__ = ["EXIT_FAILED", "EXIT_OK", "EXIT_USAGE", "main"]

logger = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m exporter.run_sync",
        description=(
            "Run one content export in the foreground. An operations tool; "
            "production runs come through POST /export_agency_async."
        ),
    )
    parser.add_argument("--agency-id", required=True, type=segment("agency_id"))
    parser.add_argument("--run-id", type=segment("run_id"), default=None)
    parser.add_argument(
        "--force",
        action="store_true",
        help="bypass Gate 1 (E4): walk every row even when nothing moved",
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=None,
        help="read at most N documents; the rest are held and the watermark withheld",
    )
    parser.add_argument(
        "--accept-existing-content",
        action="store_true",
        help=(
            "on a first run, publish into a destination that already holds "
            "this agency's content (F21); refused without it"
        ),
    )
    parser.add_argument(
        "--accept-rechunk",
        action="store_true",
        help=(
            "let a pending re-chunk run on a destination that re-embeds what "
            "it receives (L18); refused without it"
        ),
    )
    parser.add_argument(
        "--output-json",
        default="-",
        help="where to write the report: a path, or - for stdout (default)",
    )
    return parser


def _write_report(report: RunReport, destination: str) -> None:
    write_json_report(to_json_dict(report), destination)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse, run one export, write its report, and return the exit code."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        # argparse has already written the usage message to stderr.
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    # Before anything can log: the report owns stdout.
    configure_logging(sys.stderr)

    try:
        settings = load_settings()
        work_dir = assert_work_dir_usable(settings)
    except ConfigurationError as exc:
        logger.error("refusing to run: %s", sanitize_sensitive_text(str(exc)))
        return EXIT_USAGE

    task = ExportTask(
        agency_id=args.agency_id,
        run_id=args.run_id or new_run_id(),
        force=args.force,
        limit=args.limit,
        accept_existing_content=args.accept_existing_content,
        accept_rechunk=args.accept_rechunk,
    )
    try:
        report = export_agency_task(
            task, settings, work_dir, run_state=RunStateStore(work_dir)
        )
    except ConfigurationError:
        # Reported already, as a failed run, by catch_error (F12).
        return EXIT_USAGE
    except Exception:
        return EXIT_FAILED

    _write_report(report, args.output_json)
    return EXIT_FAILED if report.outcome == "failed" else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
