"""What the two operator CLIs share — `python -m exporter.run_sync` (F10) and
`python -m exporter.run_ops` (Stage J): exit codes, argument types that
refuse an id which would escape its folder, and the JSON report writer.

One copy, so the two tools cannot disagree on what an exit code means or how
a report reaches stdout.
"""

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from exporter.core.ids import validate_segment

__all__ = [
    "EXIT_FAILED",
    "EXIT_OK",
    "EXIT_USAGE",
    "positive_int",
    "segment",
    "write_json_report",
]

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2  # argparse's own code for a usage error, kept for config errors


def segment(name: str) -> Callable[[str], str]:
    """An argparse type that refuses an id which would escape its folder."""

    def parse(value: str) -> str:
        try:
            return validate_segment(name, value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from None

    return parse


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {number}")
    return number


def write_json_report(report: object, destination: str) -> None:
    """`report` (plain JSON values) to `destination`, or stdout for `-`.
    Sorted keys and UTF-8, so two reports diff line for line."""
    text = json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    if destination == "-":
        sys.stdout.write(text)
        sys.stdout.flush()
        return
    Path(destination).write_text(text, encoding="utf-8")
