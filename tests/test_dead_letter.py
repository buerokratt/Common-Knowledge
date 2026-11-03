"""E9 — the dead-letter record: one current set per agency, never failing a run."""

import json
import logging
from pathlib import Path

import pytest

from exporter.services.dead_letter import (
    DEAD_LETTER_DIRNAME,
    DeadLetterEntry,
    DeadLetterStore,
)

AGENCY = "agency-1"


def _store(tmp_path: Path) -> DeadLetterStore:
    return DeadLetterStore(tmp_path, agency_id=AGENCY)


def test_no_record_reads_as_empty(tmp_path: Path) -> None:
    assert _store(tmp_path).load() == {}


def test_a_complete_run_replaces_the_set_and_keeps_first_seen(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.update(
        {"d1": "content_too_large", "d2": "sidecar_invalid_json"},
        resolved=(),
        complete=True,
        run_id="r1",
        at="T1",
    )

    record = store.update(
        {"d1": "content_undecodable"}, resolved=(), complete=True, run_id="r2", at="T2"
    )

    assert record == {
        "d1": DeadLetterEntry(
            reason="content_undecodable",
            first_seen_at="T1",
            last_seen_at="T2",
            last_run_id="r2",
            runs=2,
        )
    }
    assert store.load() == record


def test_a_partial_run_keeps_what_it_did_not_resolve(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.update(
        {"d1": "content_too_large", "d2": "content_too_large"},
        resolved=(),
        complete=True,
        run_id="r1",
        at="T1",
    )

    record = store.update(
        {"d3": "too_many_chunks"},
        resolved={"d2"},
        complete=False,
        run_id="r2",
        at="T2",
    )

    assert set(record) == {"d1", "d3"}
    assert record["d1"].last_run_id == "r1"
    assert record["d3"].first_seen_at == "T2"


def test_the_file_holds_ids_reasons_and_times_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.update(
        {"d1": "content_too_large"}, resolved=(), complete=True, run_id="r1", at="T1"
    )

    raw = json.loads(
        (tmp_path / DEAD_LETTER_DIRNAME / f"{AGENCY}.json").read_text(encoding="utf-8")
    )
    assert raw == {
        "agency_id": AGENCY,
        "updated_at": "T1",
        "updated_by_run": "r1",
        "documents": {
            "d1": {
                "reason": "content_too_large",
                "first_seen_at": "T1",
                "last_seen_at": "T1",
                "last_run_id": "r1",
                "runs": 1,
            }
        },
    }


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        json.dumps({"agency_id": "agency-2", "documents": {}}),
        json.dumps({"agency_id": AGENCY, "documents": {"d1": {"reason": "x"}}}),
    ],
    ids=["not-json", "not-an-object", "another-agency", "missing-fields"],
)
def test_an_unreadable_record_reads_as_empty_and_is_rewritten(
    tmp_path: Path, content: str, caplog: pytest.LogCaptureFixture
) -> None:
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_text(content, encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert store.load() == {}
    assert "dead-letter record" in caplog.text

    store.update(
        {"d1": "content_too_large"}, resolved=(), complete=True, run_id="r", at="T"
    )
    assert set(store.load()) == {"d1"}


def test_a_write_failure_is_logged_never_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A file where the directory should be makes every write fail.
    (tmp_path / DEAD_LETTER_DIRNAME).write_text("", encoding="utf-8")
    store = _store(tmp_path)

    with caplog.at_level(logging.WARNING):
        record = store.update(
            {"d1": "content_too_large"}, resolved=(), complete=True, run_id="r", at="T"
        )

    assert set(record) == {"d1"}
    assert "could not write the dead-letter record" in caplog.text


def test_new_and_cleared_documents_are_logged_by_id_and_reason(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = _store(tmp_path)
    with caplog.at_level(logging.INFO):
        store.update(
            {"d1": "content_too_large"},
            resolved=(),
            complete=True,
            run_id="r1",
            at="T1",
        )
        store.update(
            {"d1": "content_too_large"},
            resolved=(),
            complete=True,
            run_id="r2",
            at="T2",
        )
        store.update({}, resolved=(), complete=True, run_id="r3", at="T3")

    messages = caplog.messages
    assert messages.count("document d1 dead-lettered: content_too_large") == 1
    assert "document d1 left the dead-letter record" in messages


def test_the_agency_id_cannot_escape_the_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        DeadLetterStore(tmp_path, agency_id="../agency")
