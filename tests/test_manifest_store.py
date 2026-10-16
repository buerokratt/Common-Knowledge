"""D16–D20 — the manifest store, separated from the destination.

Pure: an in-memory store, no network. The S3 wiring is asserted by capturing
the StoreConnection the factory builds, never by connecting.
"""

import dataclasses
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from exporter_fakes import InMemoryStore
from pydantic import SecretStr

from exporter.api.config import ConfigurationError, Settings
from exporter.core.constants import (
    MANIFEST_DOCUMENT_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
)
from exporter.core.ids import manifest_key, pending_deletions_key
from exporter.core.schemas import Manifest, ManifestDocumentEntry
from exporter.services.manifest_store import (
    JOURNAL_CONTENT_TYPE,
    ManifestStore,
    ManifestUnreadable,
    build_manifest_store,
)
from exporter.stores import factory
from exporter.stores.base import ExternalStoreClient, StoreError
from exporter.stores.factory import StoreConnection, build_manifest_store_client
from exporter.stores.local_store import LocalFileStore
from exporter.stores.s3_store import S3Store

AGENCY = "agency-1"
PREFIX = "content"
KEY = manifest_key(PREFIX, AGENCY)


def _entry(**overrides: object) -> ManifestDocumentEntry:
    values: dict[str, Any] = {
        "source_base_id": "source-1",
        "content_origin": "cleaned",
        "source_updated_at": "2026-09-01T14:02:11Z",
        "source_status": "finished",
        "raw_sha256": "a" * 64,
        "content_sha256": "b" * 64,
        "metadata_sha256": "c" * 64,
        "file_size": 48213,
        "chunk_count": 12,
        "chunker_fingerprint": "f" * 64,
        "state": "published",
        "processed_at": "2026-09-02T08:18:55Z",
    }
    return ManifestDocumentEntry(**(values | overrides))


def _manifest(**overrides: object) -> Manifest:
    documents = {"doc-1": _entry(), "doc-2": _entry(file_size=None, chunk_count=1)}
    values: dict[str, Any] = {
        "schema_version": MANIFEST_DOCUMENT_SCHEMA_VERSION,
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "agency_id": AGENCY,
        "sink_id": "object_store",
        "corpus_watermark": "2026-09-02T08:14:22Z",
        "document_count": len(documents),
        "chunker_fingerprint": "f" * 64,
        "committed_at": "2026-09-02T08:19:03Z",
        "documents": documents,
    }
    return Manifest(**(values | overrides))


def _store() -> tuple[ManifestStore, InMemoryStore]:
    client = InMemoryStore()
    return ManifestStore(client, prefix=PREFIX), client


def _write_raw(client: InMemoryStore, payload: object) -> None:
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    client.put_bytes(KEY, raw)


def _committed_json() -> dict[str, Any]:
    """A valid manifest as committed, as a dict a test can then break."""
    store, client = _store()
    store.commit(_manifest())
    return json.loads(client.objects[KEY].payload)


# --------------------------------------------------------------------------
# D16 — the client: one commit, one key, nothing else
# --------------------------------------------------------------------------


def test_a_committed_manifest_loads_back_equal() -> None:
    store, _ = _store()
    original = _manifest()

    store.commit(original)

    assert store.load(AGENCY) == original


def test_a_corpus_watermark_of_null_round_trips() -> None:
    """Null until the first full run completes (F15, F20) — a legal value,
    not a missing one."""
    store, _ = _store()
    store.commit(_manifest(corpus_watermark=None))

    loaded = store.load(AGENCY)

    assert loaded is not None
    assert loaded.corpus_watermark is None


def test_an_estonian_value_round_trips_readably() -> None:
    """ensure_ascii=False (D5): stored as UTF-8, not as \\u escapes."""
    store, client = _store()
    store.commit(_manifest(documents={"doc-õ": _entry()}))

    assert "doc-õ".encode() in client.objects[KEY].payload
    loaded = store.load(AGENCY)
    assert loaded is not None
    assert list(loaded.documents) == ["doc-õ"]


def test_commit_is_exactly_one_put_and_nothing_else() -> None:
    """F6: the single commit point. No read-modify-write, no second object."""
    store, client = _store()

    store.commit(_manifest())

    assert client.calls == [("put", KEY)]
    assert list(client.objects) == [KEY]


def test_an_unchanged_manifest_commits_byte_identically() -> None:
    """Verification 20 compares bytes. A manifest that reordered itself would
    look like a change on every run."""
    store, client = _store()

    store.commit(_manifest())
    first = client.objects[KEY].payload
    store.commit(_manifest())

    assert client.objects[KEY].payload == first


def test_the_deletion_journal_is_mirrored_verbatim() -> None:
    store, client = _store()
    payload = b'{"document_id":"doc-1"}\n{"document_id":"doc-2"}\n'

    stored = store.mirror_deletion_journal("run-1", payload)

    key = pending_deletions_key(PREFIX, "run-1")
    assert stored.key == key
    assert client.objects[key].payload == payload
    assert client.objects[key].content_type == JOURNAL_CONTENT_TYPE
    assert list(client.objects) == [key]


@pytest.mark.parametrize(
    "overrides",
    [
        {"manifest_schema_version": 1},
        {"schema_version": 2},
        {"documents": {"a/b": _entry()}},
        {"documents": {"doc-1": _entry(chunk_count=-1)}},
        {"documents": {"doc-1": _entry(source_status="")}},
        {"sink_id": ""},
    ],
    ids=["old-version", "new-entry-version", "bad-id", "negative", "empty", "no-sink"],
)
def test_commit_refuses_what_load_would_refuse(overrides: dict[str, object]) -> None:
    """Load and commit are symmetric. Without this, a coordinator bug writes a
    manifest that wedges the agency on every later run, and the only way out —
    deleting it by hand — is a silent full republish."""
    store, client = _store()

    with pytest.raises(ManifestUnreadable, match="refusing to commit"):
        store.commit(_manifest(**overrides))

    assert client.objects == {}
    assert client.calls == []


def test_a_refused_commit_keeps_the_previous_manifest() -> None:
    store, client = _store()
    store.commit(_manifest())
    previous = client.objects[KEY].payload

    with pytest.raises(ManifestUnreadable):
        store.commit(_manifest(manifest_schema_version=1))

    assert client.objects[KEY].payload == previous


def test_the_prefix_is_validated_on_construction() -> None:
    with pytest.raises(ValueError, match="rule 1"):
        ManifestStore(InMemoryStore(), prefix="uploads/scrapped-data")


# --------------------------------------------------------------------------
# D20 — fail-closed: None is a first run, everything else raises
# --------------------------------------------------------------------------


def test_an_absent_manifest_is_a_first_run() -> None:
    store, _ = _store()
    assert store.load(AGENCY) is None


def test_a_store_error_propagates_and_is_never_a_first_run() -> None:
    client = InMemoryStore(fail_keys=[KEY])
    store = ManifestStore(client, prefix=PREFIX)

    with pytest.raises(StoreError) as excinfo:
        store.load(AGENCY)

    assert not isinstance(excinfo.value, ManifestUnreadable)


def test_a_permanent_store_error_propagates_untouched() -> None:
    """The 403-for-a-missing-key case: a permissions fault that looks like
    absence must not become a silent first run."""

    class Forbidden(InMemoryStore):
        def _get_bytes(self, key: str) -> bytes | None:
            raise StoreError("S3 get failed: AccessDenied (HTTP 403)", permanent=True)

    store = ManifestStore(Forbidden(), prefix=PREFIX)

    with pytest.raises(StoreError, match="AccessDenied") as excinfo:
        store.load(AGENCY)
    assert excinfo.value.permanent


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"{not json",
        b"\xff\xfe",
        b"[]",
        b'"a string"',
        b"null",
    ],
    ids=["empty", "malformed", "not-utf8", "array", "string", "null"],
)
def test_a_manifest_that_is_not_a_json_object_is_unreadable(payload: bytes) -> None:
    store, client = _store()
    _write_raw(client, payload)

    with pytest.raises(ManifestUnreadable):
        store.load(AGENCY)


def test_unreadable_is_a_permanent_store_error() -> None:
    """So the coordinator's one `except StoreError` aborts on both paths."""
    store, client = _store()
    _write_raw(client, b"{not json")

    with pytest.raises(StoreError) as excinfo:
        store.load(AGENCY)
    assert excinfo.value.permanent


@pytest.mark.parametrize(
    "field", ["sink_id", "agency_id", "corpus_watermark", "committed_at"]
)
def test_a_missing_envelope_field_is_unreadable(field: str) -> None:
    store, client = _store()
    document = _committed_json()
    del document[field]
    _write_raw(client, document)

    with pytest.raises(ManifestUnreadable, match=f"envelope is missing {field}"):
        store.load(AGENCY)


def test_a_missing_documents_object_is_unreadable() -> None:
    store, client = _store()
    document = _committed_json()
    del document["documents"]
    _write_raw(client, document)

    with pytest.raises(ManifestUnreadable, match="'documents' must be an object"):
        store.load(AGENCY)


@pytest.mark.parametrize("field", ["content_sha256", "chunk_count", "file_size"])
def test_a_missing_entry_field_is_unreadable(field: str) -> None:
    store, client = _store()
    document = _committed_json()
    del document["documents"]["doc-1"][field]
    _write_raw(client, document)

    with pytest.raises(ManifestUnreadable, match=field):
        store.load(AGENCY)


@pytest.mark.parametrize(
    ("where", "field", "value"),
    [
        ("entry", "chunk_count", True),
        ("entry", "chunk_count", "12"),
        ("entry", "chunk_count", 12.0),
        ("entry", "content_sha256", None),
        ("entry", "file_size", "48213"),
        ("envelope", "document_count", False),
        ("envelope", "sink_id", 1),
    ],
)
def test_a_mistyped_field_is_unreadable(where: str, field: str, value: object) -> None:
    """bool is an int to isinstance and never what an int field means."""
    store, client = _store()
    document = _committed_json()
    target = document["documents"]["doc-1"] if where == "entry" else document
    target[field] = value
    _write_raw(client, document)

    with pytest.raises(ManifestUnreadable, match=field):
        store.load(AGENCY)


def test_an_unknown_field_is_unreadable() -> None:
    """A field this build does not know means a writer it does not know."""
    store, client = _store()
    document = _committed_json()
    document["documents"]["doc-1"]["embedding_model"] = "x"
    _write_raw(client, document)

    with pytest.raises(ManifestUnreadable, match="embedding_model"):
        store.load(AGENCY)


def test_an_entry_that_is_not_an_object_is_unreadable() -> None:
    store, client = _store()
    document = _committed_json()
    document["documents"]["doc-1"] = ["published"]
    _write_raw(client, document)

    with pytest.raises(ManifestUnreadable, match="doc-1"):
        store.load(AGENCY)


@pytest.mark.parametrize("field", ["schema_version", "manifest_schema_version"])
def test_an_unknown_schema_version_is_unreadable(field: str) -> None:
    """Anchored on ': <field> <n>', so `schema_version` cannot be satisfied by
    a message about `manifest_schema_version`."""
    store, client = _store()
    document = _committed_json()
    document[field] += 1
    _write_raw(client, document)

    with pytest.raises(ManifestUnreadable, match=rf": {field} \d+ is not supported"):
        store.load(AGENCY)


def test_a_pre_two_sink_manifest_is_named_by_its_version() -> None:
    """A v1 manifest has no sink_id. It must be reported as an unsupported
    version — the cause — not as a missing field."""
    store, client = _store()
    document = _committed_json()
    document["manifest_schema_version"] = 1
    del document["sink_id"]
    _write_raw(client, document)

    with pytest.raises(ManifestUnreadable) as excinfo:
        store.load(AGENCY)
    message = str(excinfo.value)
    assert "manifest_schema_version 1 is not supported" in message
    assert "sink_id" not in message


@pytest.mark.parametrize("value", [None, "2", True])
def test_a_missing_or_mistyped_version_is_named_as_a_version(value: object) -> None:
    store, client = _store()
    document = _committed_json()
    document["manifest_schema_version"] = value
    _write_raw(client, document)

    with pytest.raises(
        ManifestUnreadable, match="manifest_schema_version is missing or not an int"
    ):
        store.load(AGENCY)


# The field set each schema version reads. Editing Manifest or
# ManifestDocumentEntry without bumping the matching version fails here: the
# parser is derived from those dataclasses, so a silent field change would make
# every stored manifest unreadable.
_ENVELOPE_FIELDS_BY_VERSION = {
    2: {
        "schema_version",
        "manifest_schema_version",
        "agency_id",
        "sink_id",
        "corpus_watermark",
        "document_count",
        "chunker_fingerprint",
        "committed_at",
        "documents",
    },
}
_ENTRY_FIELDS_BY_VERSION = {
    1: {
        "source_base_id",
        "content_origin",
        "source_updated_at",
        "source_status",
        "raw_sha256",
        "content_sha256",
        "metadata_sha256",
        "file_size",
        "chunk_count",
        "chunker_fingerprint",
        "state",
        "processed_at",
    },
}


def test_the_manifest_field_set_is_pinned_to_its_version() -> None:
    envelope = {f.name for f in dataclasses.fields(Manifest)}
    entry = {f.name for f in dataclasses.fields(ManifestDocumentEntry)}

    assert envelope == _ENVELOPE_FIELDS_BY_VERSION[MANIFEST_SCHEMA_VERSION], (
        "Manifest's fields changed: bump MANIFEST_SCHEMA_VERSION, pin the new "
        "set here, and ship a migration for manifests already written."
    )
    assert entry == _ENTRY_FIELDS_BY_VERSION[MANIFEST_DOCUMENT_SCHEMA_VERSION], (
        "ManifestDocumentEntry's fields changed: bump "
        "MANIFEST_DOCUMENT_SCHEMA_VERSION, pin the new set here, and ship a "
        "migration for manifests already written."
    )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"agency_id": "agency-1", "agency_id": "agency-2"}',
        b'{"documents": {"doc-1": {}, "doc-1": {}}}',
    ],
    ids=["envelope-key", "document-id"],
)
def test_a_duplicate_key_is_unreadable(raw: bytes) -> None:
    """json.loads keeps the last of two equal keys: one agency_id or one
    document would vanish without a trace."""
    store, client = _store()
    _write_raw(client, raw)

    with pytest.raises(ManifestUnreadable, match="repeats the key"):
        store.load(AGENCY)


def test_a_duplicate_key_in_a_real_manifest_is_unreadable() -> None:
    store, client = _store()
    store.commit(_manifest())
    payload = client.objects[KEY].payload.replace(b'"doc-2": {', b'"doc-1": {', 1)
    _write_raw(client, payload)

    with pytest.raises(ManifestUnreadable, match="'doc-1'"):
        store.load(AGENCY)


def test_deeply_nested_json_is_unreadable_not_a_crash() -> None:
    store, client = _store()
    _write_raw(client, b"[" * 100_000 + b"]" * 100_000)

    with pytest.raises(ManifestUnreadable):
        store.load(AGENCY)


@pytest.mark.parametrize(
    "document_id", ["", ".", "..", "a/b", " doc-1", "doc-1 ", "   "]
)
def test_a_document_id_that_is_not_a_key_segment_is_unreadable(
    document_id: str,
) -> None:
    """These ids would later raise ValueError in a key builder, past the
    coordinator's `except StoreError`. Refused at load, they fail closed."""
    store, client = _store()
    document = _committed_json()
    document["documents"][document_id] = document["documents"].pop("doc-1")
    _write_raw(client, document)

    with pytest.raises(ManifestUnreadable, match="document id"):
        store.load(AGENCY)


@pytest.mark.parametrize(
    ("where", "field"),
    [
        ("entry", "chunk_count"),
        ("entry", "file_size"),
        ("envelope", "document_count"),
    ],
)
def test_a_negative_count_is_unreadable(where: str, field: str) -> None:
    store, client = _store()
    document = _committed_json()
    target = document["documents"]["doc-1"] if where == "entry" else document
    target[field] = -1
    _write_raw(client, document)

    with pytest.raises(ManifestUnreadable, match=f"{field!r} must not be negative"):
        store.load(AGENCY)


@pytest.mark.parametrize(
    ("where", "field"),
    [
        ("entry", "content_sha256"),
        ("entry", "source_updated_at"),
        ("envelope", "sink_id"),
        ("envelope", "corpus_watermark"),
    ],
)
def test_an_empty_string_is_unreadable(where: str, field: str) -> None:
    """corpus_watermark's "not yet" value is null, never an empty string."""
    store, client = _store()
    document = _committed_json()
    target = document["documents"]["doc-1"] if where == "entry" else document
    target[field] = ""
    _write_raw(client, document)

    with pytest.raises(ManifestUnreadable, match=f"{field!r} must not be empty"):
        store.load(AGENCY)


def test_document_count_is_not_compared_with_the_entries() -> None:
    """document_count is Gate 1's row count from CKB, which includes rows that
    were never published — so it legitimately differs from len(documents)."""
    store, _ = _store()
    store.commit(_manifest(document_count=214))

    loaded = store.load(AGENCY)

    assert loaded is not None
    assert loaded.document_count == 214
    assert len(loaded.documents) == 2


def test_another_agencys_manifest_is_unreadable() -> None:
    """At this agency's key, a manifest for another agency is corrupt — and
    diffing against it would classify this agency's whole corpus wrongly."""
    store, client = _store()
    document = _committed_json()
    document["agency_id"] = "agency-2"
    _write_raw(client, document)

    with pytest.raises(ManifestUnreadable, match="different agency"):
        store.load(AGENCY)


def test_an_unreadable_message_never_echoes_a_value() -> None:
    store, client = _store()
    document = _committed_json()
    document["documents"]["doc-1"]["source_status"] = 73_919_461
    document["documents"]["doc-1"]["content_origin"] = "Õppetoetuse taotlus"
    _write_raw(client, document)

    with pytest.raises(ManifestUnreadable) as excinfo:
        store.load(AGENCY)
    message = str(excinfo.value)
    assert "source_status" in message
    assert "73919461" not in message
    assert "Õppetoetuse" not in message


def test_the_sink_id_is_returned_not_judged() -> None:
    """F17's guard is the manifest service's. The store returns what is
    there; a mismatch is not this module's decision to make."""
    store, _ = _store()
    store.commit(_manifest(sink_id="llm_module"))

    loaded = store.load(AGENCY)

    assert loaded is not None
    assert loaded.sink_id == "llm_module"


# --------------------------------------------------------------------------
# D17 — inherited by default, all-or-nothing; explicit uses EXTERNAL_S3_*
# --------------------------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "content_sink": "object_store",
        "content_external_store_backend": "s3",
        "content_external_prefix": "content",
        "manifest_store_backend": "",
        "manifest_store_endpoint_url": "",
        "manifest_store_bucket": "",
        "manifest_store_prefix": "content-manifests",
        "manifest_store_allow_local": False,
        "content_work_dir": "/var/lib/content-external",
        "external_s3_endpoint_url": "http://store.invalid:9000",
        "external_s3_bucket_name": "agency-content",
        "external_s3_access_key_id": SecretStr("external-key"),
        "external_s3_secret_access_key": SecretStr("external-secret"),
        "external_s3_region": "eu-north-1",
        "external_s3_supports_batch_delete": True,
        "export_concurrency": 4,
        "s3_endpoint_url": "http://ckb-store.invalid:9000",
        "s3_bucket_name": "ckb",
        "aws_access_key_id": SecretStr("ckb-read-only-key"),
        "aws_secret_access_key": SecretStr("ckb-read-only-secret"),
    }
    values.update(overrides)
    return Settings.model_construct(**values)  # pyright: ignore[reportArgumentType]


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[StoreConnection]:
    """Every StoreConnection the s3 builder is handed, with no client built."""
    seen: list[StoreConnection] = []

    def _record(connection: StoreConnection) -> ExternalStoreClient:
        seen.append(connection)
        return InMemoryStore()

    monkeypatch.setitem(factory._BACKENDS, "s3", _record)
    return seen


def test_an_object_store_deployment_inherits_the_destination(
    captured: list[StoreConnection],
) -> None:
    """Verification 20: no MANIFEST_STORE_* set means the destination bucket,
    the destination keys and CONTENT_EXTERNAL_PREFIX — the pre-change key."""
    store = build_manifest_store(_settings())

    (connection,) = captured
    assert connection.bucket == "agency-content"
    assert connection.endpoint_url == "http://store.invalid:9000"
    assert connection.access_key_id == "external-key"
    assert store.prefix == "content"
    assert store.key_for(AGENCY) == manifest_key("content", AGENCY)


def test_an_inherited_manifest_lands_where_the_pre_change_design_put_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Byte for byte: the same bucket, the same key, the same bytes a direct
    put_json to the destination would have written."""
    destination = InMemoryStore()
    monkeypatch.setitem(factory._BACKENDS, "s3", lambda _connection: destination)
    manifest = _manifest()

    build_manifest_store(_settings()).commit(manifest)

    reference = InMemoryStore()
    reference.put_json(manifest_key("content", AGENCY), manifest)
    assert destination.objects == reference.objects


def test_a_manifest_prefix_alone_does_not_relocate_the_manifest(
    captured: list[StoreConnection],
) -> None:
    store = build_manifest_store(_settings(manifest_store_prefix="elsewhere"))

    assert store.prefix == "content"
    assert captured[0].bucket == "agency-content"


def test_inheritance_keeps_the_ckb_bucket_guard() -> None:
    """Built through build_destination_store, so its rule-1 guard applies."""
    settings = _settings(
        s3_endpoint_url="http://store.invalid:9000", s3_bucket_name="agency-content"
    )
    with pytest.raises(ConfigurationError, match="CKB's own bucket"):
        build_manifest_store_client(settings)


def test_an_explicit_s3_store_uses_the_external_keys(
    captured: list[StoreConnection],
) -> None:
    """The user-decided credential source: EXTERNAL_S3_*, never AWS_*."""
    store = build_manifest_store(
        _settings(
            content_sink="llm_module",
            manifest_store_backend="s3",
            manifest_store_endpoint_url="https://manifests.example",
            manifest_store_bucket="content-manifests",
        )
    )

    (connection,) = captured
    assert connection.endpoint_url == "https://manifests.example"
    assert connection.bucket == "content-manifests"
    assert connection.access_key_id == "external-key"
    assert connection.secret_access_key == "external-secret"
    assert connection.region == "eu-north-1"
    assert "ckb-read-only" not in (
        connection.access_key_id + connection.secret_access_key
    )
    assert store.prefix == "content-manifests"
    assert store.key_for(AGENCY) == manifest_key("content-manifests", AGENCY)


def test_an_explicit_s3_store_is_a_real_s3_store() -> None:
    client = build_manifest_store_client(
        _settings(
            manifest_store_backend="s3",
            manifest_store_endpoint_url="https://manifests.example",
            manifest_store_bucket="content-manifests",
        )
    )
    assert isinstance(client, S3Store)


def test_an_explicit_azure_store_names_stage_h() -> None:
    with pytest.raises(ConfigurationError, match="Stage H"):
        build_manifest_store_client(
            _settings(
                manifest_store_backend="azure_blob",
                manifest_store_bucket="content-manifests",
            )
        )


def test_an_explicit_local_store_lives_under_the_work_dir(tmp_path: Path) -> None:
    client = build_manifest_store_client(
        _settings(
            manifest_store_backend="local",
            manifest_store_allow_local=True,
            content_work_dir=tmp_path.as_posix(),
        )
    )

    assert isinstance(client, LocalFileStore)
    assert client.root == tmp_path / factory.LOCAL_MANIFEST_STORE_DIRNAME


def test_a_local_store_without_the_flag_is_refused_by_the_factory_too(
    tmp_path: Path,
) -> None:
    """Defence in depth: Settings already refuses this at startup, and
    model_construct bypasses that, which is exactly the case this covers."""
    with pytest.raises(ConfigurationError, match="MANIFEST_STORE_ALLOW_LOCAL"):
        build_manifest_store_client(
            _settings(
                manifest_store_backend="local",
                content_work_dir=tmp_path.as_posix(),
            )
        )


def test_a_local_manifest_store_round_trips(tmp_path: Path) -> None:
    store = build_manifest_store(
        _settings(
            content_sink="llm_module",
            manifest_store_backend="local",
            manifest_store_allow_local=True,
            content_work_dir=tmp_path.as_posix(),
        )
    )
    original = _manifest()

    assert store.load(AGENCY) is None
    store.commit(original)

    assert store.load(AGENCY) == original


# --------------------------------------------------------------------------
# Import graph. manifest_store -> stores.factory -> api.config ->
# services.locking is acyclic today; a fresh interpreter per order is what
# catches a cycle a later import adds, which an already-warm test process
# cannot.
# --------------------------------------------------------------------------

_EXPORTER_ROOT = Path(__file__).resolve().parents[1] / "content-external"


@pytest.mark.parametrize(
    "first",
    [
        "exporter.services.manifest_store",
        "exporter.stores.local_store",
        "exporter.stores.factory",
        "exporter.api.config",
        "exporter.api.app",
    ],
)
def test_the_exporter_imports_cleanly_from_any_entry_point(first: str) -> None:
    code = (
        f"import sys; sys.path.insert(0, {str(_EXPORTER_ROOT)!r}); "
        f"import {first}; import exporter.services.manifest_store; "
        "import exporter.stores.local_store"
    )
    result = subprocess.run(  # noqa: S603 — fixed interpreter, literal code
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
