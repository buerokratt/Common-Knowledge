"""Gate 1 avoids enumeration only after a complete, unchanged publish.

A null previous watermark forces a walk after a failed publish or tail delete.
"""

from exporter.core.schemas import AgencyWatermark, Manifest


def can_skip_run(
    previous: Manifest | None,
    watermark: AgencyWatermark,
    chunker_fingerprint: str,
    *,
    force: bool,
) -> bool:
    return (
        not force
        and previous is not None
        and previous.corpus_watermark is not None
        and previous.corpus_watermark == watermark.max_updated_at
        and previous.document_count == watermark.live_count
        and previous.chunker_fingerprint == chunker_fingerprint
    )
