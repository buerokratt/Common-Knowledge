"""Tests for content-external's diff (C7-C16).

Two tests the plan once listed are absent on purpose: "byte-identical
documents stay distinct" and "a moved document yields new + deleted". Both
covered the ancestor's keying of documents by content hash. Here document_id
is source_file.base_id, so two identical documents are two ids by
construction and a document cannot move, which leaves those tests nothing to
check (C12). Neither test existed in the ancestor either.
"""

import ast
import dataclasses
import inspect
import random
import re
import typing
from pathlib import Path
from typing import Any

import pytest

from exporter.core import diff
from exporter.core.constants import CHUNK_PROFILES
from exporter.core.diff import (
    Bucket,
    ChunkCoordinate,
    DeletionReason,
    DiffResult,
    DocumentDiff,
    SkipReason,
    classify,
    classify_document,
    needs_read,
    record,
    tail_coordinates,
)
from exporter.core.ids import chunker_fingerprint
from exporter.core.schemas import DocumentRef, Manifest, ManifestDocumentEntry
from exporter.services.run_log import DIFF_BUCKETS

AGENCY = "7d2e4c1a-3b5f-4e6d-9a8b-0c1d2e3f4a5b"
SOURCE = "5c1d3e2f-7b64-4a9d-8e21-6f0a9b8c7d31"
FP = chunker_fingerprint(CHUNK_PROFILES["azure_native"])
OTHER_FP = chunker_fingerprint(CHUNK_PROFILES["compact"])

RAW = "a" * 64
CONTENT = "b" * 64
META = "c" * 64
PUBLISHED_AT = "2026-10-01T09:00:00Z"
MOVED_AT = "2026-10-08T09:00:00Z"
LATER = "2026-10-09T10:00:00Z"  # when this run's publishes land

DIFF_SOURCE = Path(diff.__file__).read_text(encoding="utf-8")
FULL_HEX = re.compile(r"[0-9a-f]{64}")

DIFF_CLASSES = (ChunkCoordinate, DocumentDiff, DiffResult)


def doc(n: int) -> str:
    return f"0b9f2b4e-6a52-4d8e-9a43-{n:012d}"


def ref(n: int, **overrides: object) -> DocumentRef:
    """A finished, read and hashed row that matches entry(n)."""
    values: dict[str, Any] = {
        "document_id": doc(n),
        "source_base_id": SOURCE,
        "agency_id": AGENCY,
        "url": "https://www.sotsiaalkindlustusamet.ee/et/toetused/peretoetused",
        "page_title": "Peretoetused",
        "subsector": "",
        "status": "finished",
        "is_excluded": False,
        "is_deleted": False,
        "updated_at": MOVED_AT,
        "content_key": "uploads/scrapped-data/x/cleaned.txt",
        "metadata_key": "uploads/scrapped-data/x/cleaned.meta.json",
        "content_origin": "cleaned",
        "file_size": None,
        "raw_sha256": RAW,
        "content_sha256": CONTENT,
        "metadata_sha256": META,
    }
    return DocumentRef(**(values | overrides))


def unread(n: int, **overrides: object) -> DocumentRef:
    """A row Gate 2 let through without reading it."""
    values: dict[str, Any] = {
        "raw_sha256": None,
        "content_sha256": None,
        "metadata_sha256": None,
        "updated_at": PUBLISHED_AT,
    }
    return ref(n, **(values | overrides))


def entry(**overrides: object) -> ManifestDocumentEntry:
    values: dict[str, Any] = {
        "source_base_id": SOURCE,
        "content_origin": "cleaned",
        "source_updated_at": PUBLISHED_AT,
        "source_status": "finished",
        "raw_sha256": RAW,
        "content_sha256": CONTENT,
        "metadata_sha256": META,
        "file_size": 48213,
        "chunk_count": 5,
        "chunker_fingerprint": FP,
        "state": "published",
        "processed_at": PUBLISHED_AT,
    }
    return ManifestDocumentEntry(**(values | overrides))


def manifest(
    documents: dict[str, ManifestDocumentEntry],
    *,
    fingerprint: str = FP,
    sink_id: str = "object_store",
    stamp: bool = True,
) -> Manifest:
    """A committed manifest. With `stamp`, every entry carries the
    manifest's fingerprint, which is what a manifest written in one run
    looks like; pass stamp=False to keep each entry's own."""
    if stamp:
        documents = {
            key: dataclasses.replace(value, chunker_fingerprint=fingerprint)
            for key, value in documents.items()
        }
    return Manifest(
        schema_version=1,
        manifest_schema_version=2,
        agency_id=AGENCY,
        sink_id=sink_id,
        corpus_watermark=PUBLISHED_AT,
        document_count=len(documents),
        chunker_fingerprint=fingerprint,
        committed_at=PUBLISHED_AT,
        documents=documents,
    )


def published(*ns: int, **overrides: object) -> Manifest:
    return manifest({doc(n): entry(**overrides) for n in ns})


def only(result: DiffResult) -> DocumentDiff:
    """The single document in the result, whichever bucket it is in."""
    everything = [e for bucket in Bucket for e in result.documents(bucket)]
    assert len(everything) == 1, result
    return everything[0]


def bucket_of(result: DiffResult, n: int) -> Bucket:
    for bucket in Bucket:
        if any(e.document_id == doc(n) for e in result.documents(bucket)):
            return bucket
    raise AssertionError(f"document {n} is in no bucket")


# --- C7: models -----------------------------------------------------------


@pytest.mark.parametrize("cls", DIFF_CLASSES, ids=lambda cls: cls.__name__)
def test_every_diff_model_is_frozen_with_no_mutable_field_type(cls: type) -> None:
    assert cls.__dataclass_params__.frozen  # type: ignore[attr-defined]
    hints = typing.get_type_hints(cls)
    for f in dataclasses.fields(cls):
        origin = typing.get_origin(hints[f.name]) or hints[f.name]
        assert origin not in (dict, list, set), f"{cls.__name__}.{f.name}"


def test_bucket_names_are_the_run_log_buckets() -> None:
    assert {bucket.value for bucket in Bucket} == set(DIFF_BUCKETS)
    assert set(classify(None, [], FP).counts()) == set(DIFF_BUCKETS)


def test_bucket_names_are_the_result_field_names() -> None:
    names = {f.name for f in dataclasses.fields(DiffResult)}
    assert {bucket.value for bucket in Bucket} <= names


def test_a_result_cannot_be_rebound_and_buckets_are_tuples() -> None:
    result = classify(None, [ref(1)], FP)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.new = ()  # type: ignore[misc]
    assert isinstance(result.new, tuple)


def test_lists_passed_in_are_frozen_into_tuples() -> None:
    change = classify_document(None, ref(1), chunker_fingerprint=FP)
    assert change is not None
    result = DiffResult(
        first_run=True,
        fingerprint_changed=False,
        chunker_fingerprint=FP,
        new=[change],  # type: ignore[arg-type]
    )
    assert result.new == (change,)


def test_an_entry_in_the_wrong_bucket_is_refused() -> None:
    change = classify_document(None, ref(1), chunker_fingerprint=FP)
    with pytest.raises(ValueError, match="sits in"):
        DiffResult(
            first_run=True,
            fingerprint_changed=False,
            chunker_fingerprint=FP,
            skipped=(change,),  # type: ignore[arg-type]
        )


def test_a_document_in_two_buckets_is_refused() -> None:
    previous = entry()
    changed = DocumentDiff(doc(1), Bucket.CONTENT_CHANGED, None, ref(1), previous)
    meta = DocumentDiff(doc(1), Bucket.METADATA_CHANGED, None, ref(1), previous)
    with pytest.raises(ValueError, match="more than one bucket"):
        DiffResult(
            first_run=False,
            fingerprint_changed=False,
            chunker_fingerprint=FP,
            content_changed=(changed,),
            metadata_changed=(meta,),
        )


def test_a_first_run_can_only_hold_new_and_unpublished_skipped() -> None:
    held = DocumentDiff(doc(1), Bucket.SKIPPED, "not_ready", ref(1), entry())
    with pytest.raises(ValueError, match="first run"):
        DiffResult(
            first_run=True,
            fingerprint_changed=False,
            chunker_fingerprint=FP,
            skipped=(held,),
        )
    with pytest.raises(ValueError, match="first run"):
        DiffResult(first_run=True, fingerprint_changed=True, chunker_fingerprint=FP)


@pytest.mark.parametrize(
    ("bucket", "reason", "current", "previous", "message"),
    [
        (Bucket.NEW, None, ref(1), entry(), "cannot have a previous"),
        (Bucket.CONTENT_CHANGED, None, ref(1), None, "needs a previous"),
        (Bucket.UNCHANGED, None, ref(1), None, "needs a previous"),
        (Bucket.DELETED, "gone", ref(1), entry(), "DeletionReason"),
        (Bucket.DELETED, DeletionReason.IS_DELETED, None, entry(), "no current row"),
        (Bucket.SKIPPED, None, ref(1), None, "needs a reason"),
        (Bucket.SKIPPED, "absent_from_rows", None, None, "no current row"),
        (Bucket.UNCHANGED, "why", ref(1), entry(), "takes no reason"),
        (Bucket.NEW, None, ref(2), None, "another one"),
    ],
)
def test_document_diff_refuses_an_impossible_verdict(
    bucket: Bucket,
    reason: str | None,
    current: DocumentRef | None,
    previous: ManifestDocumentEntry | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        DocumentDiff(doc(1), bucket, reason, current, previous)


def test_counts_and_skipped_reasons() -> None:
    result = classify(
        published(1, 2),
        [ref(1), ref(2, status="in_review"), ref(3, content_key=None)],
        FP,
    )
    assert result.counts() == {
        "new": 0,
        "content_changed": 0,
        "metadata_changed": 0,
        "unchanged": 1,
        "deleted": 0,
        "skipped": 2,
    }
    assert result.skipped_reasons() == {doc(2): "not_ready", doc(3): "no_content"}


def test_repr_shows_counts_only() -> None:
    result = classify(published(1), [ref(1, content_sha256="d" * 64), ref(2)], FP)
    text = repr(result)
    assert text.startswith("DiffResult(first_run=False")
    assert "content_changed=1" in text
    assert "new=1" in text
    assert not FULL_HEX.search(text)
    assert "http" not in text
    assert doc(1) not in text


def test_document_diff_repr_leaves_out_the_row_and_the_entry() -> None:
    change = classify_document(entry(), ref(1), chunker_fingerprint=FP)
    text = repr(change)
    assert doc(1) in text
    assert not FULL_HEX.search(text)
    assert "http" not in text


# --- C8: classify ------------------------------------------------------------


def test_classify_signature_is_the_designed_one() -> None:
    assert list(inspect.signature(classify).parameters) == [
        "previous",
        "current",
        "chunker_fingerprint",
    ]


def test_first_run_makes_every_ready_document_new() -> None:
    result = classify(None, [ref(1), ref(2)], FP)
    assert result.first_run is True
    assert result.fingerprint_changed is False
    assert [e.document_id for e in result.new] == [doc(1), doc(2)]
    assert result.deleted == ()


def test_identical_hashes_are_unchanged() -> None:
    assert only(classify(published(1), [ref(1)], FP)).bucket is Bucket.UNCHANGED


def test_the_result_carries_the_current_fingerprint() -> None:
    assert classify(published(1), [ref(1)], OTHER_FP).chunker_fingerprint == OTHER_FP


def test_classify_and_classify_document_agree() -> None:
    previous = published(1, 2, 3, 4)
    rows = [
        ref(1),
        ref(2, content_sha256="d" * 64),
        ref(3, metadata_sha256="e" * 64),
        ref(4, is_excluded=True),
        ref(5),
        ref(6, status="cleaning"),
    ]
    result = classify(previous, rows, FP)
    for row in rows:
        change = classify_document(
            previous.documents.get(row.document_id), row, chunker_fingerprint=FP
        )
        assert change is not None
        assert bucket_of(result, int(row.document_id[-12:])) is change.bucket


def test_the_same_rows_in_any_order_give_an_equal_result() -> None:
    previous = published(1, 2, 3)
    rows = [ref(n) for n in range(1, 8)] + [ref(9, status="scraping")]
    shuffled = rows[:]
    random.Random(4).shuffle(shuffled)
    assert classify(previous, rows, FP) == classify(previous, shuffled, FP)


def test_sink_id_has_no_effect_on_the_diff() -> None:
    rows = [ref(1, metadata_sha256="e" * 64), ref(2)]
    assert classify(manifest({doc(1): entry()}, sink_id="object_store"), rows, FP) == (
        classify(manifest({doc(1): entry()}, sink_id="llm_module"), rows, FP)
    )


# --- C13: content_changed and metadata_changed are disjoint -----------------


def test_a_fingerprint_change_makes_everything_content_changed() -> None:
    previous = manifest({doc(n): entry() for n in (1, 2, 3)}, fingerprint=OTHER_FP)
    rows = [ref(1), ref(2, metadata_sha256="e" * 64), ref(3, content_sha256="d" * 64)]
    result = classify(previous, rows, FP)
    assert result.fingerprint_changed is True
    assert len(result.content_changed) == 3
    assert result.metadata_changed == ()
    assert result.unchanged == ()


def test_content_and_metadata_both_moving_is_content_changed_only() -> None:
    result = classify(
        published(1), [ref(1, content_sha256="d" * 64, metadata_sha256="e" * 64)], FP
    )
    assert only(result).bucket is Bucket.CONTENT_CHANGED


def test_only_the_metadata_moving_is_metadata_changed() -> None:
    result = classify(published(1), [ref(1, metadata_sha256="e" * 64)], FP)
    assert only(result).bucket is Bucket.METADATA_CHANGED


@pytest.mark.parametrize(
    "overrides",
    [{"content_origin": "edited"}, {"source_base_id": "another-source"}],
    ids=["content_origin", "source_base_id"],
)
def test_a_moved_record_field_is_metadata_changed(overrides: dict[str, str]) -> None:
    result = classify(published(1), [ref(1, **overrides)], FP)
    assert only(result).bucket is Bucket.METADATA_CHANGED


def test_raw_bytes_alone_moving_is_unchanged() -> None:
    """A BOM or CRLF change moves raw_sha256 only. That is encoding churn,
    not a change to publish."""
    result = classify(published(1), [ref(1, raw_sha256="f" * 64)], FP)
    assert only(result).bucket is Bucket.UNCHANGED


# --- Gate 2 ------------------------------------------------------------------


def test_an_unread_row_that_never_moved_is_unchanged() -> None:
    assert only(classify(published(1), [unread(1)], FP)).bucket is Bucket.UNCHANGED


def test_an_unread_row_that_moved_is_a_caller_bug() -> None:
    with pytest.raises(ValueError, match="row moved"):
        classify(published(1), [unread(1, updated_at=MOVED_AT)], FP)


def test_an_unread_row_after_a_fingerprint_change_is_a_caller_bug() -> None:
    previous = manifest({doc(1): entry()}, fingerprint=OTHER_FP)
    with pytest.raises(ValueError, match="fingerprint changed"):
        classify(previous, [unread(1)], FP)


def test_an_unread_new_document_is_a_caller_bug() -> None:
    with pytest.raises(ValueError, match="it is new"):
        classify(None, [unread(1)], FP)


def test_a_half_read_row_is_a_caller_bug_even_if_it_never_moved() -> None:
    with pytest.raises(ValueError, match="not been read"):
        classify(published(1), [unread(1, content_sha256=CONTENT)], FP)


def test_a_read_row_is_compared_even_if_it_never_moved() -> None:
    row = ref(1, updated_at=PUBLISHED_AT, content_sha256="d" * 64)
    assert only(classify(published(1), [row], FP)).bucket is Bucket.CONTENT_CHANGED


@pytest.mark.parametrize(
    "overrides",
    [{"content_origin": "edited"}, {"source_base_id": "another-source"}],
    ids=["content_origin", "source_base_id"],
)
def test_gate_2_does_not_spare_a_row_whose_published_fields_moved(
    overrides: dict[str, str],
) -> None:
    """updated_at alone is not enough: a row whose content_origin or
    source_base_id moved must be read, even if updated_at did not."""
    row = unread(1, **overrides)
    assert needs_read(entry(), row, chunker_fingerprint=FP)
    with pytest.raises(ValueError, match="row moved"):
        classify(published(1), [row], FP)
    read = ref(1, updated_at=PUBLISHED_AT, **overrides)
    assert only(classify(published(1), [read], FP)).bucket is Bucket.METADATA_CHANGED


NEEDS_READ_CASES = [
    pytest.param(None, unread(1), False, True, id="new"),
    pytest.param(entry(), unread(1), True, True, id="fingerprint-changed"),
    pytest.param(entry(), unread(1), False, False, id="gate-2"),
    pytest.param(entry(), unread(1, updated_at=MOVED_AT), False, True, id="moved"),
    pytest.param(entry(), unread(1, is_deleted=True), True, False, id="deleted"),
    pytest.param(
        None, unread(1, is_excluded=True), False, False, id="unpublished-gone"
    ),
    pytest.param(entry(), unread(1, status="in_review"), True, False, id="not-ready"),
    pytest.param(entry(), unread(1, content_key=None), False, False, id="no-content"),
    pytest.param(
        entry(), unread(1, skip_reason="oversize"), True, False, id="reader-skip"
    ),
]


@pytest.mark.parametrize(
    ("previous", "row", "fp_changed", "expected"), NEEDS_READ_CASES
)
def test_needs_read_is_exactly_what_classify_document_requires(
    previous: ManifestDocumentEntry | None,
    row: DocumentRef,
    fp_changed: bool,
    expected: bool,
) -> None:
    """needs_read() False means the unread row classifies; True means the
    unread row is refused. So a caller that asks it can never drift."""
    current = OTHER_FP if fp_changed else FP  # entry() carries FP
    assert needs_read(previous, row, chunker_fingerprint=current) is expected
    if expected:
        with pytest.raises(ValueError, match="not been read"):
            classify_document(previous, row, chunker_fingerprint=current)
    else:
        classify_document(previous, row, chunker_fingerprint=current)


@pytest.mark.parametrize("state", ["pending", "failed", ""])
def test_a_manifest_entry_that_is_not_published_is_refused(state: str) -> None:
    with pytest.raises(ValueError, match="not 'published'"):
        classify(published(1, state=state), [ref(1)], FP)
    with pytest.raises(ValueError, match="not 'published'"):
        classify(published(1, state=state), [], FP)


# --- C14: the four deletion signals -----------------------------------------


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"is_deleted": True}, DeletionReason.IS_DELETED),
        ({"is_excluded": True}, DeletionReason.IS_EXCLUDED),
        ({"status": "not_found"}, DeletionReason.NOT_FOUND),
    ],
    ids=["is_deleted", "is_excluded", "not_found"],
)
def test_each_row_signal_deletes_a_published_document(
    overrides: dict[str, object], reason: DeletionReason
) -> None:
    change = only(classify(published(1), [ref(1, **overrides)], FP))
    assert change.bucket is Bucket.DELETED
    assert change.reason == reason
    assert change.previous == entry()


def test_not_found_with_its_content_url_intact_is_still_deleted() -> None:
    """What update_source_file_scraped_stop.sql leaves behind: live, not
    excluded, content URL populated. Only status says the page is gone."""
    row = unread(1, status="not_found")
    assert row.content_key is not None
    assert only(classify(published(1), [row], FP)).bucket is Bucket.DELETED


def test_a_row_missing_from_the_rows_is_deleted() -> None:
    result = classify(published(1, 2), [ref(1)], FP)
    (gone,) = result.deleted
    assert gone.document_id == doc(2)
    assert gone.reason == DeletionReason.ABSENT_FROM_ROWS
    assert gone.current is None
    assert gone.previous == entry()


def test_a_signal_on_a_never_published_row_is_in_no_bucket() -> None:
    result = classify(
        published(1),
        [ref(1), ref(2, is_deleted=True), ref(3, status="not_found")],
        FP,
    )
    assert result.deleted == ()
    assert result.deleted_unpublished == 2
    assert sum(result.counts().values()) == 1
    assert (
        classify_document(None, ref(2, is_deleted=True), chunker_fingerprint=FP) is None
    )


def test_deletion_wins_over_skipping() -> None:
    row = unread(1, is_deleted=True, status="scraping", content_key=None)
    assert only(classify(published(1), [row], FP)).bucket is Bucket.DELETED


# --- C15: skipped documents are held -----------------------------------------


@pytest.mark.parametrize("status", ["scraping", "cleaning", "in_review", "failed"])
def test_a_row_not_finished_is_held_despite_its_content_url(status: str) -> None:
    row = unread(1, status=status, updated_at=MOVED_AT)
    assert row.content_key is not None
    change = only(classify(published(1), [row], FP))
    assert change.bucket is Bucket.SKIPPED
    assert change.reason == SkipReason.NOT_READY
    assert change.held
    assert change.previous == entry()


def test_a_document_with_no_content_object_is_held_and_never_deleted() -> None:
    row = unread(1, content_key=None, content_origin=None, updated_at=MOVED_AT)
    result = classify(published(1), [row], FP)
    change = only(result)
    assert change.bucket is Bucket.SKIPPED
    assert change.reason == SkipReason.NO_CONTENT
    assert result.held == (change,)
    assert result.deleted == ()
    assert result.chunks_to_delete == ()


def test_a_skip_reason_from_the_reader_passes_through() -> None:
    row = unread(1, skip_reason="sidecar_missing", updated_at=MOVED_AT)
    change = only(classify(published(1), [row], FP))
    assert (change.bucket, change.reason, change.held) == (
        Bucket.SKIPPED,
        "sidecar_missing",
        True,
    )


def test_an_empty_skip_reason_is_refused() -> None:
    with pytest.raises(ValueError, match="skip_reason"):
        classify(published(1), [ref(1, skip_reason="")], FP)


def test_a_never_published_skipped_document_is_not_held() -> None:
    change = only(classify(None, [unread(1, status="in_review")], FP))
    assert change.bucket is Bucket.SKIPPED
    assert not change.held


def test_skipping_wins_over_a_fingerprint_change() -> None:
    previous = manifest({doc(1): entry()}, fingerprint=OTHER_FP)
    change = only(classify(previous, [unread(1, status="cleaning")], FP))
    assert change.held


# --- C9: fail closed ----------------------------------------------------------


def test_everything_deleted_and_everything_new_cannot_be_confused() -> None:
    purge = classify(published(1, 2), [], FP)
    cold = classify(None, [], FP)
    assert purge.first_run is False
    assert len(purge.deleted) == 2
    assert all(e.reason == DeletionReason.ABSENT_FROM_ROWS for e in purge.deleted)
    assert cold.first_run is True
    assert sum(cold.counts().values()) == 0
    assert purge != cold


def test_the_diff_has_no_fallback_path() -> None:
    """The ancestor caught every error and treated everything as new. There
    is no try statement here at all, so nothing can be swallowed."""
    tree = ast.parse(DIFF_SOURCE)
    assert not any(isinstance(node, ast.Try | ast.TryStar) for node in ast.walk(tree))


def test_a_duplicate_document_is_refused() -> None:
    with pytest.raises(ValueError, match="appears twice"):
        classify(None, [ref(1), ref(1)], FP)


def test_rows_from_another_agency_are_refused() -> None:
    with pytest.raises(ValueError, match="different agency"):
        classify(published(1), [ref(1, agency_id="another-agency")], FP)
    with pytest.raises(ValueError, match="different agency"):
        classify(None, [ref(1), ref(2, agency_id="another-agency")], FP)


@pytest.mark.parametrize("fingerprint", ["", "f", FP.upper(), FP + "0", None])
def test_a_malformed_fingerprint_is_refused(fingerprint: object) -> None:
    with pytest.raises(ValueError, match="chunker_fingerprint"):
        classify(None, [], fingerprint)  # type: ignore[arg-type]


# --- C16: chunks_to_delete holds coordinates ---------------------------------


def test_tail_coordinates_are_the_ordinals_the_document_lost() -> None:
    assert tail_coordinates(doc(1), 5, 3) == (
        ChunkCoordinate(doc(1), 3),
        ChunkCoordinate(doc(1), 4),
    )
    assert tail_coordinates(doc(1), 3, 3) == ()
    assert tail_coordinates(doc(1), 3, 8) == ()


@pytest.mark.parametrize(("previous", "new"), [(-1, 0), (0, -1), (True, 0)])
def test_tail_coordinates_refuse_a_bad_count(previous: int, new: int) -> None:
    with pytest.raises(ValueError):
        tail_coordinates(doc(1), previous, new)


def test_classify_leaves_chunks_to_delete_empty() -> None:
    result = classify(published(1), [ref(1, content_sha256="d" * 64)], FP)
    assert result.chunks_to_delete == ()


def test_with_chunk_tails_fills_in_content_changed_tails() -> None:
    result = classify(
        published(1, 2),
        [ref(1, content_sha256="d" * 64), ref(2, content_sha256="d" * 64), ref(3)],
        FP,
    )
    tailed = result.with_chunk_tails({doc(1): 3, doc(2): 9, doc(3): 4})
    assert tailed.chunks_to_delete == (
        ChunkCoordinate(doc(1), 3),
        ChunkCoordinate(doc(1), 4),
    )
    assert result.chunks_to_delete == ()  # the original is untouched
    assert tailed.counts() == result.counts()
    # A second call for the same document replaces its tail and keeps the
    # others' (doc(2) has none, being longer than before).
    again = tailed.with_chunk_tails({doc(1): 4}, require_complete=False)
    assert again.chunks_to_delete == (ChunkCoordinate(doc(1), 4),)
    # A count for a new document is accepted and produces no tail.
    assert (
        result.with_chunk_tails({doc(1): 5, doc(2): 5, doc(3): 1}).chunks_to_delete
        == ()
    )


def test_with_chunk_tails_refuses_an_incomplete_count_map() -> None:
    result = classify(
        published(1, 2),
        [ref(1, content_sha256="d" * 64), ref(2, content_sha256="d" * 64)],
        FP,
    )
    with pytest.raises(ValueError, match="1 content_changed document"):
        result.with_chunk_tails({doc(1): 3})
    partial = result.with_chunk_tails({doc(1): 3}, require_complete=False)
    assert partial.chunks_to_delete == (
        ChunkCoordinate(doc(1), 3),
        ChunkCoordinate(doc(1), 4),
    )
    # The supported way to leave one out: demote it first.
    demoted = result.demote_to_skipped(doc(2), "too_many_chunks")
    assert demoted.with_chunk_tails({doc(1): 3}).chunks_to_delete == (
        partial.chunks_to_delete
    )


@pytest.mark.parametrize(
    "rows",
    [
        [ref(1)],  # unchanged
        [ref(1, metadata_sha256="e" * 64)],  # metadata_changed
        [unread(1, status="in_review")],  # skipped and held
    ],
    ids=["unchanged", "metadata_changed", "held"],
)
def test_with_chunk_tails_refuses_a_document_that_was_not_chunked(
    rows: list[DocumentRef],
) -> None:
    result = classify(published(1), rows, FP)
    with pytest.raises(ValueError, match="not chunked"):
        result.with_chunk_tails({doc(1): 2})


def test_with_chunk_tails_refuses_a_negative_count() -> None:
    result = classify(published(1), [ref(1, content_sha256="d" * 64)], FP)
    with pytest.raises(ValueError):
        result.with_chunk_tails({doc(1): -1})


def test_chunks_to_delete_must_name_published_content_changed_chunks() -> None:
    result = classify(published(1), [ref(1, content_sha256="d" * 64)], FP)
    with pytest.raises(ValueError, match="never published"):
        dataclasses.replace(result, chunks_to_delete=(ChunkCoordinate(doc(1), 5),))
    with pytest.raises(ValueError, match="not content_changed"):
        dataclasses.replace(result, chunks_to_delete=(ChunkCoordinate(doc(9), 0),))
    with pytest.raises(ValueError, match="twice"):
        dataclasses.replace(
            result,
            chunks_to_delete=(ChunkCoordinate(doc(1), 4), ChunkCoordinate(doc(1), 4)),
        )


def test_demoting_a_content_changed_document_holds_it_and_drops_its_tail() -> None:
    result = classify(
        published(1), [ref(1, content_sha256="d" * 64), ref(2)], FP
    ).with_chunk_tails({doc(1): 3})
    assert result.chunks_to_delete
    demoted = result.demote_to_skipped(doc(1), "too_many_chunks")
    (held,) = demoted.skipped
    assert (held.document_id, held.reason, held.held) == (
        doc(1),
        "too_many_chunks",
        True,
    )
    assert held.previous == entry()
    assert demoted.content_changed == ()
    assert demoted.chunks_to_delete == ()
    assert demoted.new == result.new
    assert demoted.skipped_reasons() == {doc(1): "too_many_chunks"}


def test_demoting_a_new_document_skips_it_without_holding() -> None:
    demoted = classify(None, [ref(1), ref(2)], FP).demote_to_skipped(doc(2), "x")
    assert [e.document_id for e in demoted.new] == [doc(1)]
    (skipped,) = demoted.skipped
    assert not skipped.held


def test_demoted_skips_stay_sorted() -> None:
    result = classify(None, [ref(1), ref(2), ref(3, status="in_review")], FP)
    demoted = result.demote_to_skipped(doc(1), "x")
    assert [e.document_id for e in demoted.skipped] == [doc(1), doc(3)]


@pytest.mark.parametrize(
    "rows",
    [
        [ref(1)],  # unchanged
        [ref(1, metadata_sha256="e" * 64)],  # metadata_changed
        [ref(1, is_deleted=True)],  # deleted
        [unread(1, status="in_review")],  # already skipped
        [],  # absent
    ],
    ids=["unchanged", "metadata_changed", "deleted", "skipped", "absent"],
)
def test_only_a_chunked_document_can_be_demoted(rows: list[DocumentRef]) -> None:
    with pytest.raises(ValueError, match="can be demoted"):
        classify(published(1), rows, FP).demote_to_skipped(doc(1), "x")


def test_a_demotion_needs_a_reason() -> None:
    with pytest.raises(ValueError, match="needs a reason"):
        classify(None, [ref(1)], FP).demote_to_skipped(doc(1), "")


def test_is_purge_only_when_everything_published_goes() -> None:
    assert classify(published(1, 2), [], FP).is_purge
    assert classify(published(1, 2), [ref(1, is_deleted=True)], FP).is_purge
    assert not classify(published(1, 2), [ref(1)], FP).is_purge
    assert not classify(published(1), [unread(1, status="in_review")], FP).is_purge
    assert not classify(None, [], FP).is_purge
    assert not classify(published(), [], FP).is_purge


def test_from_documents_counts_unpublished_deletions_itself() -> None:
    rows = [ref(1), ref(2, is_deleted=True), ref(3, status="not_found")]
    streamed = DiffResult.from_documents(
        (classify_document(None, row, chunker_fingerprint=FP) for row in rows),
        first_run=True,
        fingerprint_changed=False,
        chunker_fingerprint=FP,
    )
    assert streamed.deleted_unpublished == 2
    assert streamed == classify(None, rows, FP)


@pytest.mark.parametrize(
    ("bucket", "reason", "row", "message"),
    [
        (
            Bucket.DELETED,
            DeletionReason.IS_EXCLUDED,
            ref(1, is_deleted=True),
            "carries",
        ),
        (Bucket.DELETED, DeletionReason.ABSENT_FROM_ROWS, ref(1), "carries None"),
        (Bucket.UNCHANGED, None, ref(1, is_deleted=True), "can only be deleted"),
        (Bucket.SKIPPED, "x", ref(1, status="not_found"), "can only be deleted"),
        (Bucket.UNCHANGED, None, ref(1, status="in_review"), "finished row"),
        (Bucket.METADATA_CHANGED, None, ref(1, content_key=None), "finished row"),
        (Bucket.CONTENT_CHANGED, None, ref(1, skip_reason="x"), "finished row"),
    ],
)
def test_a_verdict_must_agree_with_its_row(
    bucket: Bucket, reason: str | None, row: DocumentRef, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        DocumentDiff(doc(1), bucket, reason, row, entry())


@pytest.mark.parametrize(("document_id", "ordinal"), [("", 0), (doc(1), -1)])
def test_a_coordinate_must_be_well_formed(document_id: str, ordinal: int) -> None:
    with pytest.raises(ValueError):
        ChunkCoordinate(document_id, ordinal)


# --- chunk_counts: the counts the tails are derived from ---------------------


def test_with_chunk_tails_keeps_every_count_and_demotion_drops_one() -> None:
    result = classify(
        published(1), [ref(1, content_sha256="d" * 64), ref(2)], FP
    ).with_chunk_tails({doc(1): 3, doc(2): 4})
    assert dict(result.chunk_counts) == {doc(1): 3, doc(2): 4}
    demoted = result.demote_to_skipped(doc(1), "too_many_chunks")
    assert dict(demoted.chunk_counts) == {doc(2): 4}
    assert doc(2) not in repr(result)


def test_counts_can_be_added_in_steps_and_completeness_counts_them_all() -> None:
    result = classify(
        published(1, 2),
        [ref(1, content_sha256="d" * 64), ref(2, content_sha256="d" * 64)],
        FP,
    )
    step = result.with_chunk_tails({doc(1): 3}, require_complete=False)
    done = step.with_chunk_tails({doc(2): 9})
    assert dict(done.chunk_counts) == {doc(1): 3, doc(2): 9}
    assert done.chunks_to_delete == (
        ChunkCoordinate(doc(1), 3),
        ChunkCoordinate(doc(1), 4),
    )


def test_counts_for_a_document_that_was_not_chunked_are_refused() -> None:
    result = classify(published(1), [ref(1), ref(2)], FP)
    with pytest.raises(ValueError, match="not new or content_changed"):
        dataclasses.replace(result, chunk_counts={doc(1): 2})  # unchanged
    with pytest.raises(ValueError, match="not new or content_changed"):
        dataclasses.replace(result, chunk_counts={doc(9): 2})  # unknown
    with pytest.raises(ValueError):
        dataclasses.replace(result, chunk_counts={doc(2): -1})


def test_tails_must_be_exactly_what_the_counts_imply() -> None:
    result = classify(published(1), [ref(1, content_sha256="d" * 64)], FP)
    with pytest.raises(ValueError, match="has no chunk count"):
        dataclasses.replace(result, chunks_to_delete=(ChunkCoordinate(doc(1), 4),))
    tailed = result.with_chunk_tails({doc(1): 3})
    with pytest.raises(ValueError, match="does not match its chunk count"):
        dataclasses.replace(tailed, chunk_counts={doc(1): 4})
    with pytest.raises(ValueError, match="does not match its chunk count"):
        dataclasses.replace(tailed, chunks_to_delete=(ChunkCoordinate(doc(1), 4),))


# --- per-entry chunker fingerprint --------------------------------------------


def test_an_entry_cut_with_an_old_fingerprint_is_rechunked() -> None:
    """The manifest's fingerprint is current, but one document's chunks
    predate it. It is read and re-chunked even though its row never moved;
    the rest are left alone."""
    previous = manifest(
        {doc(1): entry(chunker_fingerprint=OTHER_FP), doc(2): entry()}, stamp=False
    )
    assert needs_read(previous.documents[doc(1)], unread(1), chunker_fingerprint=FP)
    assert not needs_read(previous.documents[doc(2)], unread(2), chunker_fingerprint=FP)
    result = classify(previous, [ref(1, updated_at=PUBLISHED_AT), unread(2)], FP)
    assert result.fingerprint_changed is False
    assert bucket_of(result, 1) is Bucket.CONTENT_CHANGED
    assert bucket_of(result, 2) is Bucket.UNCHANGED


def test_a_document_held_through_a_fingerprint_change_is_rechunked_once() -> None:
    """The case a manifest-level fingerprint alone gets wrong: the document
    is in review while the geometry changes, keeps its old chunks, and its
    content never changes afterwards."""
    finished_at = "2026-10-09T08:00:00Z"
    previous = manifest({doc(1): entry(), doc(2): entry()}, fingerprint=OTHER_FP)

    # The geometry changes while document 2 is in review.
    run = classify(
        previous, [ref(1), unread(2, status="in_review", updated_at=MOVED_AT)], FP
    ).with_chunk_tails({doc(1): 5})
    assert run.held[0].document_id == doc(2)
    entries = record(run, {doc(1)}, tails_deleted=set(), processed_at=LATER)
    assert entries[doc(2)].chunker_fingerprint == OTHER_FP

    # Document 2 finishes with the same content: re-chunked, not unchanged.
    rows = [unread(1, updated_at=MOVED_AT), ref(2, updated_at=finished_at)]
    run = classify(manifest(entries, stamp=False), rows, FP)
    assert bucket_of(run, 1) is Bucket.UNCHANGED
    assert bucket_of(run, 2) is Bucket.CONTENT_CHANGED
    entries = record(
        run.with_chunk_tails({doc(2): 5}),
        {doc(2)},
        tails_deleted=set(),
        processed_at=LATER,
    )

    # And never again.
    rows = [unread(1, updated_at=MOVED_AT), unread(2, updated_at=finished_at)]
    run = classify(manifest(entries, stamp=False), rows, FP)
    assert run.counts()["unchanged"] == 2


@pytest.mark.parametrize("bad", ["", "f", FP.upper()])
def test_a_malformed_entry_fingerprint_is_refused(bad: str) -> None:
    previous = manifest({doc(1): entry(chunker_fingerprint=bad)}, stamp=False)
    with pytest.raises(ValueError, match="malformed chunker_fingerprint"):
        classify(previous, [ref(1)], FP)


def test_classify_document_and_needs_read_refuse_a_malformed_fingerprint() -> None:
    with pytest.raises(ValueError, match="chunker_fingerprint"):
        classify_document(entry(), ref(1), chunker_fingerprint="f")
    with pytest.raises(ValueError, match="chunker_fingerprint"):
        needs_read(entry(), ref(1), chunker_fingerprint="f")


# --- C17: record ---------------------------------------------------------------


def test_record_signature_is_the_designed_one() -> None:
    assert list(inspect.signature(record).parameters) == [
        "diff",
        "published",
        "tails_deleted",
        "processed_at",
    ]
    # Required, not defaulted: a forgotten tails_deleted must not compile
    # into "no tail was ever confirmed" and re-publish shrunk documents
    # every run.
    assert inspect.signature(record).parameters["tails_deleted"].default is (
        inspect.Parameter.empty
    )


def test_a_published_new_document_gets_a_fresh_entry() -> None:
    result = classify(None, [ref(1, file_size=48213)], FP).with_chunk_tails({doc(1): 3})
    assert record(result, {doc(1)}, tails_deleted=set(), processed_at=LATER) == {
        doc(1): entry(source_updated_at=MOVED_AT, chunk_count=3, processed_at=LATER)
    }


def test_the_entry_takes_file_size_as_it_is_even_when_absent() -> None:
    result = classify(None, [ref(1)], FP).with_chunk_tails({doc(1): 3})
    assert (
        record(result, {doc(1)}, tails_deleted=set(), processed_at=LATER)[
            doc(1)
        ].file_size
        is None
    )


def test_a_new_document_that_did_not_publish_is_absent() -> None:
    result = classify(None, [ref(1)], FP).with_chunk_tails({doc(1): 3})
    assert record(result, set(), tails_deleted=set(), processed_at=LATER) == {}


def test_a_published_content_changed_document_records_its_new_count() -> None:
    row = ref(1, content_sha256="d" * 64, file_size=48213)
    result = classify(published(1), [row], FP).with_chunk_tails({doc(1): 3})
    assert record(result, {doc(1)}, tails_deleted={doc(1)}, processed_at=LATER) == {
        doc(1): entry(
            source_updated_at=MOVED_AT,
            content_sha256="d" * 64,
            chunk_count=3,
            processed_at=LATER,
        )
    }


def test_a_grown_document_has_no_tail_to_confirm() -> None:
    row = ref(1, content_sha256="d" * 64)
    result = classify(published(1), [row], FP).with_chunk_tails({doc(1): 8})
    entries = record(result, {doc(1)}, tails_deleted=set(), processed_at=LATER)
    assert entries[doc(1)].chunk_count == 8


def test_an_upserted_document_whose_tail_delete_failed_keeps_the_old_count() -> None:
    """Review finding: recording the new, smaller count would leave nothing
    that ever names the old tail again. Carried, the next run re-reads the
    row, repeats the upsert and recomputes the same tail from the live
    count, so the delete is retried."""
    rows = [ref(1, content_sha256="d" * 64)]
    result = classify(published(1), rows, FP).with_chunk_tails({doc(1): 3})
    entries = record(result, {doc(1)}, tails_deleted=set(), processed_at=LATER)
    assert entries == {doc(1): entry()}
    again = classify(manifest(entries), rows, FP).with_chunk_tails({doc(1): 3})
    assert again.chunks_to_delete == result.chunks_to_delete
    # Once the delete is confirmed, the new count is recorded.
    done = record(again, {doc(1)}, tails_deleted={doc(1)}, processed_at=LATER)
    assert done[doc(1)].chunk_count == 3


@pytest.mark.parametrize(
    "rows",
    [
        [ref(1, content_sha256="d" * 64)],  # grown: no tail
        [ref(1)],  # unchanged
        [ref(1, metadata_sha256="e" * 64)],  # metadata_changed
    ],
    ids=["no-tail", "unchanged", "metadata_changed"],
)
def test_tails_deleted_may_only_name_a_document_with_a_tail(
    rows: list[DocumentRef],
) -> None:
    result = classify(published(1), rows, FP)
    if result.content_changed:
        result = result.with_chunk_tails({doc(1): 9})
    with pytest.raises(ValueError, match="had no tail"):
        record(result, set(), tails_deleted={doc(1)}, processed_at=LATER)


def test_a_content_changed_document_that_failed_keeps_what_is_live() -> None:
    """Its previous entry is carried, so the next run re-reads it and works
    out the tail from the five chunks still at the destination."""
    rows = [ref(1, content_sha256="d" * 64)]
    result = classify(published(1), rows, FP).with_chunk_tails({doc(1): 3})
    entries = record(result, set(), tails_deleted=set(), processed_at=LATER)
    assert entries == {doc(1): entry()}
    assert needs_read(
        entries[doc(1)], unread(1, updated_at=MOVED_AT), chunker_fingerprint=FP
    )
    again = classify(manifest(entries), rows, FP).with_chunk_tails({doc(1): 3})
    assert again.chunks_to_delete == (
        ChunkCoordinate(doc(1), 3),
        ChunkCoordinate(doc(1), 4),
    )


def test_a_published_metadata_change_keeps_the_chunk_count() -> None:
    row = ref(1, metadata_sha256="e" * 64, file_size=48213)
    result = classify(published(1), [row], FP)
    assert record(result, {doc(1)}, tails_deleted=set(), processed_at=LATER) == {
        doc(1): entry(
            source_updated_at=MOVED_AT, metadata_sha256="e" * 64, processed_at=LATER
        )
    }


def test_a_metadata_change_that_failed_keeps_the_previous_entry() -> None:
    result = classify(published(1), [ref(1, metadata_sha256="e" * 64)], FP)
    assert record(result, set(), tails_deleted=set(), processed_at=LATER) == {
        doc(1): entry()
    }


def test_an_unread_unchanged_document_is_carried_verbatim() -> None:
    result = classify(published(1), [unread(1)], FP)
    assert record(result, set(), tails_deleted=set(), processed_at=LATER) == {
        doc(1): entry()
    }


def test_a_read_unchanged_document_advances_so_gate_2_spares_it() -> None:
    """Its row moved but nothing published did, a BOM change for instance.
    Without advancing, it would be read again every run forever."""
    row = ref(1, raw_sha256="f" * 64, file_size=50000)
    entries = record(
        classify(published(1), [row], FP),
        set(),
        tails_deleted=set(),
        processed_at=LATER,
    )
    assert entries == {
        doc(1): entry(source_updated_at=MOVED_AT, raw_sha256="f" * 64, file_size=50000)
    }
    assert not needs_read(
        entries[doc(1)], unread(1, updated_at=MOVED_AT), chunker_fingerprint=FP
    )


@pytest.mark.parametrize(
    "row",
    [
        unread(1, status="in_review", updated_at=MOVED_AT),
        unread(1, content_key=None, content_origin=None, updated_at=MOVED_AT),
        unread(1, skip_reason="sidecar_missing", updated_at=MOVED_AT),
    ],
    ids=["not-ready", "no-content", "reader-skip"],
)
def test_a_held_document_is_carried_verbatim_and_never_advanced(
    row: DocumentRef,
) -> None:
    result = classify(published(1), [row], FP)
    assert result.held
    entries = record(result, set(), tails_deleted=set(), processed_at=LATER)
    assert entries == {doc(1): entry()}
    assert entries[doc(1)].source_updated_at == PUBLISHED_AT


def test_a_never_published_skipped_document_is_absent() -> None:
    result = classify(None, [unread(1, status="in_review")], FP)
    assert record(result, set(), tails_deleted=set(), processed_at=LATER) == {}


def test_deleted_documents_leave_the_manifest() -> None:
    rows = [ref(1, is_deleted=True), ref(2, status="not_found")]
    result = classify(published(1, 2, 3), rows, FP)
    assert len(result.deleted) == 3
    assert record(result, set(), tails_deleted=set(), processed_at=LATER) == {}


def test_record_is_sorted_by_document_id() -> None:
    rows = [ref(3), ref(1), ref(2)]
    result = classify(None, rows, FP).with_chunk_tails({doc(n): 1 for n in (1, 2, 3)})
    entries = record(
        result, {doc(1), doc(2), doc(3)}, tails_deleted=set(), processed_at=LATER
    )
    assert list(entries) == [doc(1), doc(2), doc(3)]


def test_a_manifest_built_from_record_classifies_as_nothing_to_do() -> None:
    rows = [
        ref(1),  # unchanged, read
        ref(2, content_sha256="d" * 64),
        ref(3, metadata_sha256="e" * 64),
        unread(4, status="in_review", updated_at=MOVED_AT),  # held
        ref(5),  # new
        ref(6, is_deleted=True),  # never published
    ]
    result = classify(published(1, 2, 3, 4), rows, FP).with_chunk_tails(
        {doc(2): 4, doc(5): 2}
    )
    entries = record(
        result, {doc(2), doc(3), doc(5)}, tails_deleted={doc(2)}, processed_at=LATER
    )
    again = classify(manifest(entries), rows, FP)
    assert again.counts() == {
        "new": 0,
        "content_changed": 0,
        "metadata_changed": 0,
        "unchanged": 4,
        "deleted": 0,
        "skipped": 1,
    }


def test_record_ignores_the_manifest_sink_id() -> None:
    rows = [ref(1, metadata_sha256="e" * 64), ref(2)]

    def entries_for(sink_id: str) -> dict[str, ManifestDocumentEntry]:
        result = classify(manifest({doc(1): entry()}, sink_id=sink_id), rows, FP)
        result = result.with_chunk_tails({doc(2): 1})
        return record(result, {doc(1), doc(2)}, tails_deleted=set(), processed_at=LATER)

    assert entries_for("object_store") == entries_for("llm_module")


@pytest.mark.parametrize(
    ("published_ids", "message"),
    [
        ({doc(1)}, "nothing to publish"),  # unchanged
        ({doc(3)}, "nothing to publish"),  # held
        ({doc(9)}, "nothing to publish"),  # unknown
        ({doc(2)}, "no chunk count"),  # new, never counted
    ],
)
def test_record_refuses_a_published_set_that_cannot_be_right(
    published_ids: set[str], message: str
) -> None:
    rows = [ref(1), ref(2), unread(3, status="in_review")]
    result = classify(published(1, 3), rows, FP)
    with pytest.raises(ValueError, match=message):
        record(result, published_ids, tails_deleted=set(), processed_at=LATER)


@pytest.mark.parametrize("processed_at", ["", None])
def test_record_needs_a_processed_at(processed_at: object) -> None:
    with pytest.raises(ValueError, match="processed_at"):
        record(
            classify(None, [], FP),
            set(),
            tails_deleted=set(),
            processed_at=processed_at,
        )  # type: ignore[arg-type]


def test_a_demoted_content_changed_document_is_carried_and_retried() -> None:
    rows = [ref(1, content_sha256="d" * 64)]
    result = classify(published(1), rows, FP).with_chunk_tails({doc(1): 3})
    demoted = result.demote_to_skipped(doc(1), "too_many_chunks")
    entries = record(demoted, set(), tails_deleted=set(), processed_at=LATER)
    assert entries == {doc(1): entry()}
    # Nothing about it was published, so the next run reads it again.
    again = classify(manifest(entries), rows, FP)
    assert only(again).bucket is Bucket.CONTENT_CHANGED


def test_a_demoted_new_document_is_absent_and_cannot_be_published() -> None:
    demoted = (
        classify(None, [ref(1)], FP)
        .with_chunk_tails({doc(1): 3})
        .demote_to_skipped(doc(1), "too_many_chunks")
    )
    assert record(demoted, set(), tails_deleted=set(), processed_at=LATER) == {}
    with pytest.raises(ValueError, match="nothing to publish"):
        record(demoted, {doc(1)}, tails_deleted=set(), processed_at=LATER)


def test_a_failed_publish_across_a_fingerprint_change_keeps_the_old_geometry() -> None:
    """The manifest moves to the new fingerprint; the failed document's
    entry does not, so the next run re-chunks it even with an unmoved row."""
    previous = manifest({doc(1): entry(), doc(2): entry()}, fingerprint=OTHER_FP)
    rows = [ref(1, updated_at=PUBLISHED_AT), ref(2, updated_at=PUBLISHED_AT)]
    result = classify(previous, rows, FP).with_chunk_tails({doc(1): 5, doc(2): 5})
    assert result.fingerprint_changed
    entries = record(result, {doc(1)}, tails_deleted=set(), processed_at=LATER)
    assert entries[doc(1)].chunker_fingerprint == FP
    assert entries[doc(2)] == previous.documents[doc(2)]
    assert entries[doc(2)].chunker_fingerprint == OTHER_FP
    unmoved = [unread(1), unread(2)]
    again = classify(manifest(entries, stamp=False), unmoved[:1] + [rows[1]], FP)
    assert again.fingerprint_changed is False
    assert bucket_of(again, 1) is Bucket.UNCHANGED
    assert bucket_of(again, 2) is Bucket.CONTENT_CHANGED
    assert needs_read(entries[doc(2)], unmoved[1], chunker_fingerprint=FP)


def test_a_first_run_checkpoint_records_only_what_published() -> None:
    """F15: a first-run checkpoint commits a partial manifest. The next run
    finds the checkpointed documents unchanged at zero reads and the rest
    still new."""
    rows = [ref(n) for n in (1, 2, 3, 4)]
    result = classify(None, rows, FP).with_chunk_tails(
        {doc(n): 2 for n in (1, 2, 3, 4)}
    )
    checkpoint = record(
        result, {doc(1), doc(2)}, tails_deleted=set(), processed_at=LATER
    )
    assert set(checkpoint) == {doc(1), doc(2)}
    resumed = [unread(1, updated_at=MOVED_AT), unread(2, updated_at=MOVED_AT)]
    again = classify(manifest(checkpoint), resumed + rows[2:], FP)
    assert [e.document_id for e in again.unchanged] == [doc(1), doc(2)]
    assert [e.document_id for e in again.new] == [doc(3), doc(4)]
    assert not again.deleted


def test_record_after_a_partial_count_refuses_an_uncounted_publish() -> None:
    rows = [ref(1, content_sha256="d" * 64), ref(2, content_sha256="d" * 64)]
    partial = classify(published(1, 2), rows, FP).with_chunk_tails(
        {doc(1): 7}, require_complete=False
    )
    entries = record(partial, {doc(1)}, tails_deleted=set(), processed_at=LATER)
    assert entries[doc(1)].chunk_count == 7
    assert entries[doc(2)] == entry()  # uncounted and unpublished: carried
    with pytest.raises(ValueError, match="no chunk count"):
        record(partial, {doc(1), doc(2)}, tails_deleted=set(), processed_at=LATER)


def test_a_forced_read_of_an_unmoved_row_refreshes_only_the_raw_side() -> None:
    row = ref(1, updated_at=PUBLISHED_AT, raw_sha256="f" * 64, file_size=1)
    result = classify(published(1), [row], FP)
    assert only(result).bucket is Bucket.UNCHANGED
    entries = record(result, set(), tails_deleted=set(), processed_at=LATER)
    assert entries == {doc(1): entry(raw_sha256="f" * 64, file_size=1)}


def test_a_bool_is_not_a_chunk_count() -> None:
    result = classify(published(1), [ref(1, content_sha256="d" * 64)], FP)
    with pytest.raises(ValueError, match="chunk count"):
        result.with_chunk_tails({doc(1): True})
    with pytest.raises(ValueError, match="chunk count"):
        dataclasses.replace(result, chunk_counts={doc(1): False})
    with pytest.raises(ValueError, match="ordinal"):
        ChunkCoordinate(doc(1), True)


def test_a_row_with_no_content_origin_is_held_before_anything_publishes() -> None:
    """Review finding: without this, the missing origin surfaced only in
    record(), after the destination had been written."""
    row = ref(1, content_origin=None, content_sha256="d" * 64)
    change = only(classify(published(1), [row], FP))
    assert (change.bucket, change.reason, change.held) == (
        Bucket.SKIPPED,
        SkipReason.NO_CONTENT_ORIGIN,
        True,
    )
    assert not needs_read(entry(), row, chunker_fingerprint=FP)
    assert only(classify(None, [row], FP)).bucket is Bucket.SKIPPED
    with pytest.raises(ValueError, match="content_origin"):
        DocumentDiff(doc(1), Bucket.NEW, None, row, None)


def test_record_refuses_a_hand_built_verdict_that_was_never_read() -> None:
    change = DocumentDiff(doc(1), Bucket.NEW, None, unread(1), None)
    result = DiffResult(
        first_run=True,
        fingerprint_changed=False,
        chunker_fingerprint=FP,
        new=(change,),
        chunk_counts={doc(1): 1},
    )
    with pytest.raises(ValueError, match="not been read"):
        record(result, {doc(1)}, tails_deleted=set(), processed_at=LATER)


# --- C11, C12, C18, C20 (C10's no-DVC check is in test_core_purity.py) -------


@pytest.mark.parametrize("word", ["sink", "store", "blob", "http"])
def test_the_diff_names_no_destination_or_transport(word: str) -> None:
    """Stricter than test_core_purity: not even the prose of this module
    names a destination."""
    assert word not in DIFF_SOURCE.lower()


def test_the_module_header_records_the_keying_decision() -> None:
    header = ast.get_docstring(ast.parse(DIFF_SOURCE)) or ""
    assert "source_file.base_id" in header
    assert "files_map[file_hash]" in header


@pytest.mark.parametrize(
    "phrase",
    [
        "RAG-Module-Internal/src/vector_indexer/diff_identifier/",
        "does not flow back",
        "mark_files_processed()",  # C17: no re-read, no re-hash
        "force_metadata_update",
        "document_hash",  # the hash-convention divergence
        "per manifest entry",
    ],
)
def test_the_module_header_records_the_fork_provenance(phrase: str) -> None:
    """C18: the origin, that the fork is one-way, and every divergence."""
    header = " ".join((ast.get_docstring(ast.parse(DIFF_SOURCE)) or "").split())
    assert phrase in header


def test_error_messages_carry_no_hash_or_url() -> None:
    changed = classify(published(1), [ref(1, content_sha256="d" * 64)], FP)
    cases = [
        lambda: classify(published(1), [unread(1, updated_at=MOVED_AT)], FP),
        lambda: classify(published(1), [ref(1, agency_id="x")], FP),
        lambda: classify(None, [ref(1), ref(1)], FP),
        lambda: changed.with_chunk_tails({doc(7): 1}),
        lambda: record(changed, {doc(1)}, tails_deleted=set(), processed_at=LATER),
        lambda: record(changed, {doc(7)}, tails_deleted=set(), processed_at=LATER),
        lambda: dataclasses.replace(changed, chunk_counts={doc(7): 1}),
    ]
    for case in cases:
        with pytest.raises(ValueError) as caught:
            case()
        message = str(caught.value)
        assert not FULL_HEX.search(message), message
        assert "http" not in message, message
