"""Journalled deletes and journal upkeep, shared by every operation that
deletes: the export (Stage F), the drain (J1), reconcile's repair (J5) and
the purge (J7).

One implementation, because the ordering is the guarantee. A delete is
journalled BEFORE the sink is asked (F7), so a crash between the two leaves
an intent the drain can see; and a journal is closed and mirrored only AFTER
whatever made it true has been committed. Four callers each re-deriving that
order is four chances to get it backwards.

Everything here takes its collaborators as arguments and holds no state: the
callers own the run lock, the manifest and the report.
"""

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

from exporter.core.redaction import sanitize_sensitive_text
from exporter.services.deletion_journal import TAIL_REASON, DeletionJournal
from exporter.services.manifest_service import ManifestService
from exporter.sinks.base import ContentSink
from exporter.stores.base import DeleteOutcome

__all__ = [
    "close_and_mirror_journals",
    "delete_chunks_journalled",
    "delete_documents_journalled",
    "mirror_journal",
    "unique_journals",
]

logger = logging.getLogger(__name__)


def delete_documents_journalled(
    sink: ContentSink,
    journal: DeletionJournal,
    reasons: Mapping[str, str],
    *,
    drained_by: str | None = None,
) -> DeleteOutcome[str]:
    """Journal whole-document deletes (document_id -> reason), ask the sink,
    then journal what it did. `drained_by` marks lines a drain appended to
    another run's journal. Nothing requested: nothing written, nothing asked.

    Any OSError from the journal propagates before the sink is called: the
    caller is about to forget these documents, and if this record cannot be
    written nothing may be deleted."""
    if not reasons:
        return DeleteOutcome()
    # Journalled before the sink is asked. The manifest is about to forget
    # these documents, so from here on the journal is their only record.
    journal.intend_document_deletes(reasons, drained_by=drained_by)
    outcome = sink.delete_documents(list(reasons))
    journal.document_deletes_done(outcome, drained_by=drained_by)
    if outcome.unresolved:
        logger.warning(
            "%d of %d document deletes unresolved; journalled for the drain",
            len(outcome.unresolved),
            len(reasons),
        )
    return outcome


def delete_chunks_journalled(
    sink: ContentSink,
    journal: DeletionJournal,
    document_id: str,
    ordinals: Sequence[int],
    *,
    reason: str = TAIL_REASON,
) -> DeleteOutcome[int]:
    """Journal a tail delete, ask the sink, then journal what it did.
    `reason` tells reconcile's orphan tails from the export's own."""
    journal.intend_chunk_delete(document_id, ordinals, reason=reason)
    outcome = sink.delete_chunks(document_id, ordinals)
    journal.chunk_delete_done(document_id, outcome)
    return outcome


def unique_journals(journals: Sequence[DeletionJournal]) -> list[DeletionJournal]:
    """One journal per file, first wins."""
    seen: dict[Path, DeletionJournal] = {}
    for journal in journals:
        seen.setdefault(journal.path, journal)
    return list(seen.values())


def close_and_mirror_journals(
    run_id: str,
    to_close: Sequence[DeletionJournal],
    candidates: Sequence[DeletionJournal],
    manifests: ManifestService,
) -> None:
    """After the commit, so nothing here may fail the run. Close the
    journals this commit reconciled, then mirror every closed journal not
    yet mirrored at its current length — including one an earlier run
    failed to mirror. The local files are the journals of record; the
    mirror is a copy."""
    for journal in unique_journals(to_close):
        try:
            journal.close(closed_by=run_id)
        except OSError as exc:
            # Left open, the next run distrusts its documents again: a
            # needless republish, never a lost one.
            logger.warning(
                "run %s: could not close the journal of run %s (%s: %s)",
                run_id,
                journal.run_id,
                type(exc).__name__,
                sanitize_sensitive_text(str(exc)),
            )
    for journal in unique_journals(candidates):
        if journal.needs_mirror():
            mirror_journal(run_id, journal, manifests)


def mirror_journal(
    run_id: str, journal: DeletionJournal, manifests: ManifestService
) -> None:
    """Copy one journal to the manifest store. Best-effort: a copy must not
    fail a run that has already committed, and the next commit retries."""
    payload = journal.payload()
    if payload is None:
        return
    try:
        manifests.mirror_deletion_journal(journal.run_id, payload)
    except Exception as exc:
        logger.warning(
            "run %s: the deletion journal of run %s was not mirrored "
            "(%s: %s); the local copy at %s is the record",
            run_id,
            journal.run_id,
            type(exc).__name__,
            sanitize_sensitive_text(str(exc)),
            journal.path,
        )
        return
    journal.mark_mirrored(payload)
