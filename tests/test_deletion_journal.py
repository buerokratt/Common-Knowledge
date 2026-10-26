"""F7 — the deferred-deletion journal. Pure filesystem work, no network."""

import json
from pathlib import Path

import pytest

from exporter.services.deletion_journal import (
    JOURNAL_DIRNAME,
    TAIL_REASON,
    DeletionJournal,
    agency_journals,
)
from exporter.stores.base import DeleteOutcome

RUN_ID = "run-1"
AGENCY = "agency-1"


def _journal(work_dir: Path) -> DeletionJournal:
    return DeletionJournal(
        work_dir, run_id=RUN_ID, agency_id=AGENCY, clock=lambda: "2026-10-19T10:00:00Z"
    )


def test_the_journal_lives_under_the_work_dir_by_run_id(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    assert journal.path == tmp_path / JOURNAL_DIRNAME / f"{RUN_ID}.jsonl"


def test_a_run_that_deletes_nothing_writes_no_file(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    assert journal.payload() is None
    assert journal.entries() == []
    assert not (tmp_path / JOURNAL_DIRNAME).exists()


@pytest.mark.parametrize("run_id", ["", "a/b", "..", "."])
def test_a_run_id_that_is_not_a_key_segment_is_refused(
    tmp_path: Path, run_id: str
) -> None:
    """The run id becomes a file name and a mirror key; a "/" in it would put
    the journal somewhere no drain looks."""
    with pytest.raises(ValueError, match="run_id"):
        DeletionJournal(tmp_path, run_id=run_id, agency_id=AGENCY)


def test_document_intents_are_on_disk_before_any_outcome(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.intend_document_deletes({"d1": "is_deleted", "d2": "absent_from_rows"})

    # Read the file directly, as a drain in another process would.
    lines = journal.path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event"] for line in lines] == ["intent", "intent"]
    assert journal.intents_recorded == 2
    first = json.loads(lines[0])
    assert first == {
        "agency_id": AGENCY,
        "document_id": "d1",
        "event": "intent",
        "kind": "document",
        "reason": "is_deleted",
        "recorded_at": "2026-10-19T10:00:00Z",
        "run_id": RUN_ID,
    }


def test_document_outcomes_are_one_line_per_document(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.intend_document_deletes({"d1": "is_deleted", "d2": "is_excluded"})
    journal.document_deletes_done(
        DeleteOutcome(deleted=("d1",), failed={"d2": "AccessDenied"})
    )

    outcomes = [e for e in journal.entries() if e["event"] == "outcome"]
    by_document = {e["document_id"]: e for e in outcomes}
    assert by_document["d1"]["resolved"] == ["d1"]
    assert by_document["d1"]["unresolved"] == []
    assert by_document["d2"]["resolved"] == []
    assert by_document["d2"]["unresolved"] == [{"item": "d2", "reason": "AccessDenied"}]


def test_a_deferred_delete_is_unresolved(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.intend_document_deletes({"d1": "is_deleted"})
    journal.document_deletes_done(DeleteOutcome.deferring(["d1"]))
    (outcome,) = [e for e in journal.entries() if e["event"] == "outcome"]
    assert outcome["unresolved"] == [{"item": "d1", "reason": "deferred"}]


def test_a_tail_delete_records_its_ordinals_and_their_fate(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.intend_chunk_delete("d1", [5, 3, 4])
    journal.chunk_delete_done(
        "d1", DeleteOutcome(deleted=(3,), missing=(4,), failed={5: "SlowDown"})
    )

    intent, outcome = journal.entries()
    assert intent["kind"] == "chunks"
    assert intent["reason"] == TAIL_REASON
    assert intent["ordinals"] == [3, 4, 5]
    assert outcome["resolved"] == [3, 4]
    assert outcome["unresolved"] == [{"item": 5, "reason": "SlowDown"}]
    assert journal.intents_recorded == 1


def test_lines_carry_only_ids_ordinals_reasons_and_timestamps(tmp_path: Path) -> None:
    """F13: a journal line is read by operators and mirrored to a store; no
    text, no URL, no body may reach it."""
    journal = _journal(tmp_path)
    journal.intend_document_deletes({"d1": "is_deleted"})
    journal.document_deletes_done(DeleteOutcome(deleted=("d1",)))
    journal.intend_chunk_delete("d2", [1])
    journal.chunk_delete_done("d2", DeleteOutcome(deleted=(1,)))

    allowed = {
        "agency_id",
        "document_id",
        "event",
        "kind",
        "ordinals",
        "reason",
        "recorded_at",
        "resolved",
        "run_id",
        "unresolved",
    }
    for entry in journal.entries():
        assert set(entry) <= allowed
    assert "http" not in journal.path.read_text(encoding="utf-8")


def test_the_journal_appends_across_instances(tmp_path: Path) -> None:
    """A drain or a re-run with the same id reads what was already written,
    never a truncated file."""
    _journal(tmp_path).intend_document_deletes({"d1": "is_deleted"})
    second = _journal(tmp_path)
    second.intend_document_deletes({"d2": "is_deleted"})
    assert [e["document_id"] for e in second.entries()] == ["d1", "d2"]


def test_an_unwritable_work_dir_raises(tmp_path: Path) -> None:
    """If the record cannot be written the run must not go on to delete:
    the error propagates rather than being logged and swallowed."""
    blocker = tmp_path / "afile"
    blocker.write_text("", encoding="utf-8")
    journal = _journal(blocker)
    with pytest.raises(OSError):  # noqa: PT011 - platform decides the subclass
        journal.intend_document_deletes({"d1": "is_deleted"})


def test_payload_is_the_file_bytes(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.intend_document_deletes({"d1": "is_deleted"})
    assert journal.payload() == journal.path.read_bytes()


# --- torn lines ------------------------------------------------------------


def test_a_torn_last_line_is_skipped(tmp_path: Path) -> None:
    """A crash mid-write leaves a fragment with no newline. It was never
    fsynced, so the delete it describes never started."""
    journal = _journal(tmp_path)
    journal.intend_document_deletes({"d1": "is_deleted"})
    with journal.path.open("ab") as handle:
        handle.write(b'{"event": "intent", "document_id": "d2", "ag')

    assert [e["document_id"] for e in journal.entries()] == ["d1"]


def test_the_next_append_terminates_a_torn_tail(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.intend_document_deletes({"d1": "is_deleted"})
    with journal.path.open("ab") as handle:
        handle.write('{"document_id": "ä'.encode()[:-1])  # torn mid-character

    journal.intend_document_deletes({"d3": "is_deleted"})

    assert [e["document_id"] for e in journal.entries()] == ["d1", "d3"]


# --- closing and mirroring -------------------------------------------------


def test_a_journal_is_open_until_a_committed_run_closes_it(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.intend_document_deletes({"d1": "is_deleted"})
    journal.intend_chunk_delete("d2", [4])
    assert not journal.is_closed()
    assert journal.attempted_documents() == {"d1", "d2"}

    journal.close(closed_by="run-2")

    assert journal.is_closed()
    closed = journal.entries()[-1]
    assert closed["event"] == "closed"
    assert closed["closed_by"] == "run-2"
    assert "document_id" not in closed


def test_closing_is_per_agency(tmp_path: Path) -> None:
    """Two agencies' runs may share an id; closing one closes only its own."""
    _journal(tmp_path).intend_document_deletes({"d1": "is_deleted"})
    other = DeletionJournal(tmp_path, run_id=RUN_ID, agency_id="agency-2")
    other.intend_document_deletes({"x1": "is_deleted"})

    other.close(closed_by="run-2")

    assert other.is_closed()
    assert not _journal(tmp_path).is_closed()
    assert other.attempted_documents() == {"x1"}


def test_closing_a_journal_that_was_never_written_writes_nothing(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    journal.close(closed_by=RUN_ID)
    assert journal.payload() is None


def test_agency_journals_finds_every_run_with_a_line_for_the_agency(
    tmp_path: Path,
) -> None:
    for run_id, agency_id in (("run-a", AGENCY), ("run-b", "agency-2")):
        DeletionJournal(
            tmp_path, run_id=run_id, agency_id=agency_id
        ).intend_document_deletes({"d1": "is_deleted"})
    (tmp_path / JOURNAL_DIRNAME / "notes.txt").write_text("x", encoding="utf-8")

    assert [j.run_id for j in agency_journals(tmp_path, AGENCY)] == ["run-a"]
    assert agency_journals(tmp_path / "nowhere", AGENCY) == []


def test_only_a_closed_journal_needs_a_mirror_until_it_has_one(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    journal.intend_document_deletes({"d1": "is_deleted"})
    assert not journal.needs_mirror()  # open: not committed yet

    journal.close(closed_by=RUN_ID)
    assert journal.needs_mirror()

    payload = journal.payload()
    assert payload is not None
    journal.mark_mirrored(payload)
    assert not journal.needs_mirror()

    # Closed again by a later run: the copy is stale, so mirror again.
    journal.close(closed_by="run-2")
    assert journal.needs_mirror()
