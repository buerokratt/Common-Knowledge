"""Tests for content-external's shared data shapes (B2)."""

import dataclasses
import json
import typing
from collections.abc import Mapping
from typing import Any

import pytest

from exporter.core import schemas
from exporter.core.schemas import (
    Chunk,
    DocumentRecord,
    Manifest,
    ManifestDocumentEntry,
    RunReport,
    TextSpan,
    to_json_dict,
)

SCHEMA_CLASSES = sorted(
    (
        value
        for value in vars(schemas).values()
        if isinstance(value, type)
        and dataclasses.is_dataclass(value)
        and value.__module__ == schemas.__name__
    ),
    key=lambda cls: cls.__name__,
)


def entry(**overrides: str | int) -> ManifestDocumentEntry:
    values: dict[str, Any] = {
        "source_base_id": "s1",
        "content_origin": "cleaned",
        "source_updated_at": "2026-03-18T08:47:10Z",
        "source_status": "finished",
        "raw_sha256": "r",
        "content_sha256": "c",
        "metadata_sha256": "m",
        "file_size": 10,
        "chunk_count": 2,
        "chunker_fingerprint": "f",
        "state": "published",
        "processed_at": "2026-10-06T00:00:00Z",
    }
    return ManifestDocumentEntry(**(values | overrides))


def manifest(documents: dict[str, ManifestDocumentEntry]) -> Manifest:
    return Manifest(
        schema_version=1,
        manifest_schema_version=1,
        agency_id="a1",
        sink_id="object_store",
        corpus_watermark=None,
        document_count=len(documents),
        chunker_fingerprint="f",
        committed_at="2026-10-06T00:00:00Z",
        documents=documents,
    )


def record(source: dict[str, Any]) -> DocumentRecord:
    return DocumentRecord(
        document_id="d1",
        source_base_id="s1",
        content_origin="cleaned",
        source_url="https://www.example.ee/teenus",
        source=source,
        raw_sha256="r",
        content_sha256="c",
        metadata_sha256="m",
        chunk_count=1,
        synced_at="2026-10-06T00:00:00Z",
    )


def test_every_schema_is_found() -> None:
    names = {cls.__name__ for cls in SCHEMA_CLASSES}
    assert names >= {
        "DocumentRef",
        "Chunk",
        "TextSpan",
        "Manifest",
        "DocumentRecord",
        "StoreObject",
        "RunReport",
    }


@pytest.mark.parametrize("cls", SCHEMA_CLASSES, ids=lambda cls: cls.__name__)
def test_every_schema_is_frozen_with_no_mutable_field_type(cls: type) -> None:
    assert cls.__dataclass_params__.frozen  # type: ignore[attr-defined]
    hints = typing.get_type_hints(cls)
    for field in dataclasses.fields(cls):
        origin = typing.get_origin(hints[field.name]) or hints[field.name]
        assert origin not in (dict, list, set), (
            f"{cls.__name__}.{field.name} is a {origin.__name__}: mutable in place "
            "even on a frozen dataclass. Use Mapping and freeze it in __post_init__."
        )


def test_attributes_cannot_be_rebound() -> None:
    span = TextSpan(0, 3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        span.start = 1  # type: ignore[misc]


def test_manifest_documents_cannot_be_mutated_in_place() -> None:
    m = manifest({"d1": entry()})
    with pytest.raises(TypeError):
        m.documents["d2"] = entry()  # type: ignore[index]
    with pytest.raises(TypeError):
        del m.documents["d1"]  # type: ignore[attr-defined]


def test_caller_dict_cannot_change_a_frozen_value() -> None:
    documents = {"d1": entry()}
    m = manifest(documents)
    documents["d2"] = entry()
    assert set(m.documents) == {"d1"}


def test_document_record_source_is_frozen_all_the_way_down() -> None:
    source = {"file_type": ".html", "metadata": {"cleaned": True}, "tags": ["a"]}
    r = record(source)
    with pytest.raises(TypeError):
        r.source["metadata"]["cleaned"] = False  # type: ignore[index]
    assert r.source["tags"] == ("a",)
    source["metadata"]["cleaned"] = False
    assert r.source["metadata"]["cleaned"] is True


def test_run_report_skipped_reasons_cannot_be_mutated() -> None:
    report = RunReport(
        run_id="r1",
        agency_id="a1",
        sink_id="object_store",
        outcome="success",
        started_at="t0",
        finished_at=None,
        skipped_reasons={"d1": "too large"},
    )
    with pytest.raises(TypeError):
        report.skipped_reasons["d2"] = "x"  # type: ignore[index]
    # The way to change one is a new value, and that keeps it frozen.
    updated = dataclasses.replace(report, skipped_reasons={"d2": "x"})
    assert dict(updated.skipped_reasons) == {"d2": "x"}
    with pytest.raises(TypeError):
        updated.skipped_reasons["d3"] = "y"  # type: ignore[index]


def test_frozen_mappings_still_compare_equal_to_plain_dicts() -> None:
    assert manifest({"d1": entry()}) == manifest({"d1": entry()})
    assert record({"a": {"b": 1}}).source == {"a": {"b": 1}}


# --- serialisation ----------------------------------------------------------


def test_manifest_serialises_with_json_dump() -> None:
    m = manifest({"d1": entry()})
    loaded = json.loads(json.dumps(to_json_dict(m)))
    assert loaded["agency_id"] == "a1"
    assert loaded["corpus_watermark"] is None
    assert loaded["documents"] == {"d1": dataclasses.asdict(entry())}


def test_document_record_round_trips_its_sidecar_verbatim() -> None:
    source = {"file_type": ".html", "metadata": {"cleaned": True}, "tags": ["a"]}
    loaded = json.loads(json.dumps(to_json_dict(record(source))))
    assert loaded["source"] == source


def test_chunk_serialises_its_span_as_an_object() -> None:
    chunk = Chunk(
        chunk_id="c1", document_id="d1", ordinal=0, text="õäöü", span=TextSpan(0, 4)
    )
    loaded = json.loads(json.dumps(to_json_dict(chunk), ensure_ascii=False))
    assert loaded == {
        "chunk_id": "c1",
        "document_id": "d1",
        "ordinal": 0,
        "text": "õäöü",
        "span": {"start": 0, "end": 4},
    }


@pytest.mark.parametrize("cls", SCHEMA_CLASSES, ids=lambda cls: cls.__name__)
def test_to_json_dict_leaves_nothing_json_cannot_take(cls: type) -> None:
    """Every field type, filled with a representative value, survives
    json.dumps — so a new field of an unserialisable type fails here."""
    samples: dict[Any, Any] = {
        str: "x",
        int: 1,
        bool: True,
        str | None: None,
        int | None: None,
        TextSpan: TextSpan(0, 1),
        Mapping[str, Any]: {"k": {"n": [1]}},
        Mapping[str, str]: {"k": "v"},
        Mapping[str, ManifestDocumentEntry]: {"d1": entry()},
    }
    hints = typing.get_type_hints(cls)
    instance = cls(**{f.name: samples[hints[f.name]] for f in dataclasses.fields(cls)})
    json.dumps(to_json_dict(instance))
