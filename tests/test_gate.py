from dataclasses import replace

import pytest

from exporter.core.gate import can_skip_run
from exporter.core.schemas import AgencyWatermark, Manifest

TIMESTAMP = "2026-10-20T12:00:00.000+00:00"
FINGERPRINT = "a" * 64
PREVIOUS = Manifest(
    schema_version=1,
    manifest_schema_version=1,
    agency_id="agency-1",
    sink_id="test",
    corpus_watermark=TIMESTAMP,
    document_count=3,
    chunker_fingerprint=FINGERPRINT,
    committed_at=TIMESTAMP,
)
WATERMARK = AgencyWatermark(TIMESTAMP, 3)


def test_matching_complete_run_can_stop() -> None:
    assert can_skip_run(PREVIOUS, WATERMARK, FINGERPRINT, force=False)


@pytest.mark.parametrize(
    "previous, watermark, fingerprint, force",
    [
        (PREVIOUS, WATERMARK, FINGERPRINT, True),
        (None, WATERMARK, FINGERPRINT, False),
        (replace(PREVIOUS, corpus_watermark=None), WATERMARK, FINGERPRINT, False),
        (PREVIOUS, AgencyWatermark("changed", 3), FINGERPRINT, False),
        (PREVIOUS, AgencyWatermark(TIMESTAMP, 4), FINGERPRINT, False),
        (PREVIOUS, WATERMARK, "b" * 64, False),
        (
            replace(PREVIOUS, corpus_watermark=None),
            AgencyWatermark(None, 3),
            FINGERPRINT,
            False,
        ),
        (PREVIOUS, AgencyWatermark(None, 3), FINGERPRINT, False),
        (
            PREVIOUS,
            AgencyWatermark("2026-10-20T12:00:00Z", 3),
            FINGERPRINT,
            False,
        ),
    ],
    ids=[
        "forced",
        "first-run",
        "incomplete-publish",
        "timestamp-changed",
        "count-changed",
        "fingerprint-changed",
        "both-watermarks-null",
        "current-watermark-null",
        "timestamp-format-changed",
    ],
)
def test_any_failed_condition_requires_enumeration(
    previous: Manifest | None,
    watermark: AgencyWatermark,
    fingerprint: str,
    force: bool,
) -> None:
    assert not can_skip_run(previous, watermark, fingerprint, force=force)
