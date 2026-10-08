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

DIFF_SOURCE = Path(diff.__file__).read_text(encoding="utf-8")
CONTENT_EXTERNAL_DIR = Path(diff.__file__).parents[2]
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
        "state": "published",
        "processed_at": PUBLISHED_AT,
    }
    return ManifestDocumentEntry(**(values | overrides))


def manifest(
    documents: dict[str, ManifestDocumentEntry],
    *,
    fingerprint: str = FP,
    sink_id: str = "object_store",
) -> Manifest:
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
    change = classify_document(None, ref(1), fingerprint_changed=False)
    assert change is not None
    result = DiffResult(
        first_run=True,
        fingerprint_changed=False,
        chunker_fingerprint=FP,
        new=[change],  # type: ignore[arg-type]
    )
    assert result.new == (change,)


def test_an_entry_in_the_wrong_bucket_is_refused() -> None:
    change = classify_document(None, ref(1), fingerprint_changed=False)
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
    change = classify_document(entry(), ref(1), fingerprint_changed=False)
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
            previous.documents.get(row.document_id), row, fingerprint_changed=False
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
    assert needs_read(entry(), row, fingerprint_changed=False)
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
    assert needs_read(previous, row, fingerprint_changed=fp_changed) is expected
    if expected:
        with pytest.raises(ValueError, match="not been read"):
            classify_document(previous, row, fingerprint_changed=fp_changed)
    else:
        classify_document(previous, row, fingerprint_changed=fp_changed)


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
        classify_document(None, ref(2, is_deleted=True), fingerprint_changed=False)
        is None
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
        (classify_document(None, row, fingerprint_changed=False) for row in rows),
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


# --- C10, C11, C12, C20 -------------------------------------------------------


def _code_words(path: Path) -> list[str]:
    """Everything in a Python file a program could act on: names, imports,
    attributes and string literals, but not docstrings or comments. Prose
    may record that DVC was dropped; code may not use it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    words: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            words.append(node.id)
        elif isinstance(node, ast.Attribute):
            words.append(node.attr)
        elif isinstance(node, ast.alias):
            words.append(node.name)
        elif isinstance(node, ast.FunctionDef | ast.ClassDef):
            words.append(node.name)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            words.append(node.value)
    return words


def test_nothing_in_the_service_uses_dvc() -> None:
    """C10: no DVC functions, no DVC config and none of the ancestor's
    direct-credential settings. Python files are checked by their code,
    other files (Dockerfile, config) by their whole text; Markdown is prose."""
    offenders: list[str] = []
    for path in CONTENT_EXTERNAL_DIR.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".md":
            continue
        if path.suffix == ".py":
            text = "\n".join(_code_words(path))
        else:
            text = path.read_text(encoding="utf-8", errors="ignore")
        if "dvc" in text.lower():
            offenders.append(path.relative_to(CONTENT_EXTERNAL_DIR).as_posix())
    assert not offenders


def test_the_dvc_check_sees_code_but_not_prose(tmp_path: Path) -> None:
    """The check's own regression test, so it cannot pass vacuously."""
    prose = tmp_path / "prose.py"
    prose.write_text('"""No DVC here."""\n# nor dvc here\nx = 1\n', encoding="utf-8")
    code = tmp_path / "code.py"
    code.write_text("def initialize_dvc() -> None:\n    pass\n", encoding="utf-8")
    assert not any("dvc" in w.lower() for w in _code_words(prose))
    assert "initialize_dvc" in _code_words(code)


@pytest.mark.parametrize("word", ["sink", "store", "blob", "http"])
def test_the_diff_names_no_destination_or_transport(word: str) -> None:
    assert word not in DIFF_SOURCE.lower()


def test_the_module_header_records_the_keying_decision() -> None:
    header = ast.get_docstring(ast.parse(DIFF_SOURCE)) or ""
    assert "source_file.base_id" in header
    assert "files_map[file_hash]" in header


def test_error_messages_carry_no_hash_or_url() -> None:
    changed = classify(published(1), [ref(1, content_sha256="d" * 64)], FP)
    cases = [
        lambda: classify(published(1), [unread(1, updated_at=MOVED_AT)], FP),
        lambda: classify(published(1), [ref(1, agency_id="x")], FP),
        lambda: classify(None, [ref(1), ref(1)], FP),
        lambda: changed.with_chunk_tails({doc(7): 1}),
    ]
    for case in cases:
        with pytest.raises(ValueError) as caught:
            case()
        message = str(caught.value)
        assert not FULL_HEX.search(message), message
        assert "http" not in message, message
