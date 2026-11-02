"""`python -m exporter.run_ops` — run one deletion operation (Stage J) in the
foreground.

  drain      --agency-id ID [--run-id ID] [--dry-run]
             Retry the whole-document deletes earlier runs journalled and the
             destination did not confirm (J1). --dry-run reports what would
             be retried and touches nothing (J2): run it first, for one full
             cycle, before any real drain.

  reconcile  --agency-id ID [--run-id ID] [--repair]
             Compare the destination with the committed manifest and report
             drift (J4). `clean` only when the whole destination was listed;
             read-backs of the manifest's documents alone give `clean_partial`,
             and a sink that can do neither gives `unverifiable` (J9).
             --repair also removes orphaned tail chunks (J5), on a sink that
             can list.

  purge      --agency-id ID --confirm ID [--run-id ID]
             Delete everything the committed manifest records for the agency
             (J7). --confirm must repeat the agency id, or nothing starts —
             no settings are read, no sink is built, no lock is taken (J8).
             There is no HTTP route for this, and no schedule. Disable the
             export's schedule first: an export after a purge republishes
             every document CKB still holds.

  every operation: --output-json P   the report as JSON to P, or `-` for stdout

Each takes the same whole-run lock as the export, so it reports `busy` while
an export or another operation holds it. Their outcomes are recorded as
`last_drain` / `last_reconcile` / `last_purge.json` on CONTENT_WORK_DIR — never
as the export's last run. The report is JSON on stdout (or the file); logs go
to stderr. Exit codes: 1 for `failed`, and for a purge that did not finish
(`busy`, `incomplete`); 2 for a usage or configuration error that stopped the
operation before it started; 0 otherwise. A purge exits 0 for `purged`,
`purged_unverified` (read `residual` in the report) and `nothing_to_purge`.
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
    segment,
    write_json_report,
)
from exporter.core.redaction import sanitize_sensitive_text
from exporter.services.operations import report_json
from exporter.worker.tasks import (
    OperationTask,
    configure_logging,
    new_run_id,
    operation_task,
)

__all__ = ["exit_code", "main"]

logger = logging.getLogger(__name__)

# The purge outcomes after which nothing the manifest recorded is left to
# delete. `purged_unverified` is one: every delete was acknowledged, and the
# report says residual state could not be checked.
_PURGE_FINISHED = frozenset({"purged", "purged_unverified", "nothing_to_purge"})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m exporter.run_ops",
        description="Run one deletion operation (Stage J) in the foreground.",
    )
    operations = parser.add_subparsers(dest="operation", required=True)

    def common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--agency-id", required=True, type=segment("agency_id"))
        sub.add_argument("--run-id", type=segment("run_id"), default=None)
        sub.add_argument(
            "--output-json",
            default="-",
            help="where to write the report: a path, or - for stdout (default)",
        )

    drain = operations.add_parser(
        "drain", help="retry journalled deletes the destination did not confirm"
    )
    common(drain)
    drain.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be retried; delete nothing (J2)",
    )

    reconcile = operations.add_parser(
        "reconcile", help="compare the destination with the manifest"
    )
    common(reconcile)
    reconcile.add_argument(
        "--repair",
        action="store_true",
        help="also remove orphaned tail chunks, on a sink that can list (J5)",
    )

    purge = operations.add_parser(
        "purge", help="delete everything the manifest records for the agency"
    )
    common(purge)
    purge.add_argument(
        "--confirm",
        required=True,
        type=segment("confirm"),
        help="the agency id again; the purge refuses to start without it (J8)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse, run one operation, write its report, return the exit code."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        # argparse has already written the usage message to stderr.
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    configure_logging(sys.stderr)

    # J8, before anything else: a purge whose confirmation does not repeat
    # the agency id reads no settings and builds nothing.
    if args.operation == "purge" and args.confirm != args.agency_id:
        logger.error("refusing to purge: --confirm must repeat --agency-id exactly")
        return EXIT_USAGE

    try:
        settings = load_settings()
        work_dir = assert_work_dir_usable(settings)
    except ConfigurationError as exc:
        logger.error("refusing to run: %s", sanitize_sensitive_text(str(exc)))
        return EXIT_USAGE

    task = OperationTask(
        operation=args.operation,
        agency_id=args.agency_id,
        run_id=args.run_id or new_run_id(),
        dry_run=getattr(args, "dry_run", False),
        repair=getattr(args, "repair", False),
        confirm=getattr(args, "confirm", None),
    )
    try:
        report = operation_task(task, settings, work_dir)
    except ConfigurationError:
        # Reported already, as the operation's failed outcome.
        return EXIT_USAGE
    except Exception:
        return EXIT_FAILED

    write_json_report(report_json(report), args.output_json)
    return exit_code(args.operation, report.outcome)


def exit_code(operation: str, outcome: str) -> int:
    """`failed` exits 1 for every operation. A purge also exits 1 for any
    outcome that did not finish the purge — `busy` (nothing ran) and
    `incomplete` (deletes left for the drain) — so `purge && next_step` never
    proceeds past a purge that did not happen. Drain and reconcile are read
    from their report: their other outcomes are results, not failures."""
    if outcome == "failed":
        return EXIT_FAILED
    if operation == "purge" and outcome not in _PURGE_FINISHED:
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
