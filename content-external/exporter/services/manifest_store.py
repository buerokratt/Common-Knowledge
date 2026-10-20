"""The manifest store — where manifest.json lives, separated from the
destination (D16–D20).

WHAT IT IS. A separately configured `ExternalStoreClient`, used for exactly two
things: the per-agency `manifest.json`, and the mirror of the deferred-deletion
journal (F7). In every real deployment, the llm-module one included, it is an
object store; the one exception is `local`, which is development only (D19).
Nothing else reads or writes through it, and it knows nothing about sinks.

WHY IT IS NOT THE DESTINATION. One destination is an HTTP API with nowhere to
PUT a manifest, and the manifest cannot be delegated to it: asking the consumer
of a publish to remember what was published makes a failed run
indistinguishable from a successful one. On the object-store sink it defaults
to the sink's own store, bucket and prefix (D17), so that deployment is byte-
identical to the pre-change design. The rationale and the rejected alternatives
are in content-external-pipeline.md, "Where state lives — deliberate split".

WHAT THE COORDINATOR HOLDS. A `ContentSink` and one of these — never an
`ExternalStoreClient` of its own (Stage D "Do not").

FAIL-CLOSED, UNCHANGED (D20). `load` returns `None` only when the store says
definitively that no manifest exists: a genuine first run. Every store error
propagates untouched, and a manifest that exists but cannot be trusted raises
`ManifestUnreadable`. Reading any of those as `None` would be a silent first
run: the corpus republished, and every document CKB deleted while the manifest
was unreadable orphaned for good. "Everything is new" and "everything is
deleted" must never be confusable, and this function is where they are kept
apart.

LOAD AND COMMIT ARE SYMMETRIC. `commit` parses the exact bytes it is about to
write with the parser `load` uses, and refuses to write what `load` would
refuse to read. Without that, a coordinator bug could commit a manifest that
wedges the agency on every later run — and the only way out, deleting the
object by hand, is itself a silent full republish.

SCHEMA VERSIONS. The two version fields are checked before anything else, so a
manifest from another schema says so instead of reporting whichever field
happens to differ. There is no migration: no build has ever written a manifest
at any version but the current one. The field set per version is pinned by
tests/test_manifest_store.py, so editing `Manifest` or `ManifestDocumentEntry`
without bumping a version fails CI — and a bump that changes the shape of a
manifest already written must ship its migration with it.

WHAT IT DOES NOT DECIDE. The `sink_id` guard (F17) and the
DIFF_ON_MISSING_MANIFEST policy (F6) are the manifest service's. This module
returns a faithfully parsed manifest, or `None`, or raises; it interprets
neither. Nor does it compare `document_count` with the number of entries: that
field is the corpus watermark's row count from CKB (Gate 1), which includes
rows that are not published and so never equals `len(documents)` in general.
"""

import dataclasses
import json
import logging
import types
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, get_args, get_type_hints

from exporter.core.constants import (
    MANIFEST_DOCUMENT_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
)
from exporter.core.ids import (
    manifest_key,
    pending_deletions_key,
    validate_prefix,
    validate_segment,
)
from exporter.core.schemas import Manifest, ManifestDocumentEntry
from exporter.stores.base import (
    JSON_CONTENT_TYPE,
    ExternalStoreClient,
    StoreError,
    StoreObject,
    encode_json,
)
from exporter.stores.factory import build_manifest_store_client

if TYPE_CHECKING:
    from exporter.api.config import Settings

__all__ = [
    "JOURNAL_CONTENT_TYPE",
    "ManifestStore",
    "ManifestUnreadable",
    "build_manifest_store",
]

logger = logging.getLogger(__name__)

JOURNAL_CONTENT_TYPE = "application/x-ndjson"


class ManifestUnreadable(StoreError):  # noqa: N818
    """A manifest exists and cannot be trusted, or one is refused before it
    is written because it could not be read back. Never a first run.

    A StoreError, so the coordinator's one `except StoreError` aborts the
    agency and commits nothing on every path — a store that failed, a
    manifest that is corrupt, and a manifest this run built wrongly.
    Permanent: retrying reads, or builds, the same bytes. Named as a state,
    like `NotSupported`, rather than as an Error.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, permanent=True)


class ManifestStore:
    """manifest.json and the journal mirror, over one injected store client."""

    def __init__(self, client: ExternalStoreClient, *, prefix: str) -> None:
        self._client = client
        self._prefix = validate_prefix(prefix)

    @property
    def prefix(self) -> str:
        return self._prefix

    def key_for(self, agency_id: str) -> str:
        """Where this agency's manifest lives, for a report or a log line."""
        return manifest_key(self._prefix, agency_id)

    def load(self, agency_id: str) -> Manifest | None:
        """The committed manifest, `None` for a genuine first run, or raise.

        A StoreError from the client propagates untouched — never caught,
        never mapped to `None` (D20). See the module docstring.
        """
        key = self.key_for(agency_id)
        raw = self._client.get_bytes(key)
        if raw is None:
            logger.info("no manifest at %s; first run for this agency", key)
            return None
        manifest = _parse_manifest(raw, key=key, agency_id=agency_id)
        logger.info(
            "manifest loaded from %s: %d bytes, %d documents",
            key,
            len(raw),
            len(manifest.documents),
        )
        return manifest

    def commit(self, manifest: Manifest) -> StoreObject:
        """The single commit point (F6): one atomic PUT, no read-modify-write.

        The bytes are the deterministic encoding `put_json` uses (D5), so an
        unchanged manifest commits byte-identically — what verification 20
        compares. They are parsed with `load`'s own parser before the PUT,
        and a manifest that parser would refuse is never written (see the
        module docstring).
        """
        key = self.key_for(manifest.agency_id)
        payload = encode_json(manifest)
        try:
            _parse_manifest(payload, key=key, agency_id=manifest.agency_id)
        except ManifestUnreadable as exc:
            raise ManifestUnreadable(
                f"refusing to commit a manifest that could not be loaded back: {exc}"
            ) from None
        stored = self._client.put_bytes(key, payload, content_type=JSON_CONTENT_TYPE)
        logger.info(
            "manifest committed to %s: %d bytes, %d documents, etag %s",
            key,
            stored.size_bytes,
            len(manifest.documents),
            stored.etag,
        )
        return stored

    def mirror_deletion_journal(self, run_id: str, payload: bytes) -> StoreObject:
        """Copy a run's local deferred-deletion journal here (F7).

        A mirror only. The journal of record is the local JSONL on
        CONTENT_WORK_DIR, written before any delete is attempted — when the
        destination is what is broken, it cannot hold the incident record.
        """
        key = pending_deletions_key(self._prefix, run_id)
        stored = self._client.put_bytes(key, payload, content_type=JOURNAL_CONTENT_TYPE)
        logger.info("deletion journal mirrored to %s: %d bytes", key, stored.size_bytes)
        return stored


def build_manifest_store(settings: "Settings") -> ManifestStore:
    """The configured manifest store.

    Client and prefix come from the same resolution, so an inherited store
    can never be paired with a non-inherited prefix (D17: all-or-nothing).
    Built per run, like the destination store, so /health comes up before
    either is configured — D18's refusals that matter happen at startup in
    api/config.py, not here.
    """
    return ManifestStore(
        build_manifest_store_client(settings),
        prefix=settings.resolved_manifest_store_prefix,
    )


# --------------------------------------------------------------------------
# The parser. Strict by design: the version first, then every field present,
# every field the type the schema declares, nothing extra, no duplicate keys,
# no id that could not be a key segment. A field this code does not know means
# a writer this code does not know, and that is a reason to stop, not to guess.
# --------------------------------------------------------------------------

_SUPPORTED_VERSIONS = {
    "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
    "schema_version": MANIFEST_DOCUMENT_SCHEMA_VERSION,
}


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """object_pairs_hook: json.loads keeps the LAST of two equal keys, which
    would silently drop a document id or replace the agency_id."""
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise _DuplicateKeyError(name)
        result[name] = value
    return result


def _allowed_types(annotation: object) -> tuple[type, ...]:
    """`str` -> (str,); `int | None` -> (int, NoneType)."""
    if isinstance(annotation, types.UnionType):
        return tuple(arg for arg in get_args(annotation) if isinstance(arg, type))
    if isinstance(annotation, type):
        return (annotation,)
    raise TypeError(f"unsupported manifest field annotation {annotation!r}")


def _field_types(cls: type, *, skip: frozenset[str] = frozenset()) -> dict[str, Any]:
    hints = get_type_hints(cls)
    return {
        f.name: _allowed_types(hints[f.name])
        for f in dataclasses.fields(cls)
        if f.name not in skip
    }


_MANIFEST_FIELDS = _field_types(Manifest, skip=frozenset({"documents"}))
_ENTRY_FIELDS = _field_types(ManifestDocumentEntry)


def _type_name(allowed: tuple[type, ...]) -> str:
    return " | ".join("null" if t is type(None) else t.__name__ for t in allowed)


def _checked_fields(
    raw: Mapping[str, Any],
    expected: Mapping[str, tuple[type, ...]],
    *,
    key: str,
    where: str,
) -> dict[str, Any]:
    """`raw` restricted to `expected`, or ManifestUnreadable naming the field.

    Beyond the declared type: an int is never negative (counts and sizes),
    and a str is never empty. Messages name the field and the expected type,
    never the value.
    """
    missing = sorted(set(expected) - set(raw))
    if missing:
        raise ManifestUnreadable(
            f"manifest at {key!r}: {where} is missing {', '.join(missing)}"
        )
    unknown = sorted(set(raw) - set(expected))
    if unknown:
        raise ManifestUnreadable(
            f"manifest at {key!r}: {where} carries unknown field(s) "
            f"{', '.join(unknown)}; written by a newer or different writer"
        )
    for name, allowed in expected.items():
        value = raw[name]
        # bool is an int to isinstance, and never what an int field means.
        if isinstance(value, bool) or not isinstance(value, allowed):
            raise ManifestUnreadable(
                f"manifest at {key!r}: {where} field {name!r} must be "
                f"{_type_name(allowed)}, got {type(value).__name__}"
            )
        if isinstance(value, int) and value < 0:
            raise ManifestUnreadable(
                f"manifest at {key!r}: {where} field {name!r} must not be negative"
            )
        if isinstance(value, str) and not value:
            raise ManifestUnreadable(
                f"manifest at {key!r}: {where} field {name!r} must not be empty"
            )
    return {name: raw[name] for name in expected}


def _check_versions(document: Mapping[str, Any], *, key: str) -> None:
    """Before any field check, so another schema is named as such."""
    for name, supported in _SUPPORTED_VERSIONS.items():
        value = document.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ManifestUnreadable(
                f"manifest at {key!r}: {name} is missing or not an int; this "
                f"build reads {name} {supported} only"
            )
        if value != supported:
            raise ManifestUnreadable(
                f"manifest at {key!r}: {name} {value} is not supported (this "
                f"build reads {supported}). There is no migration; a manifest "
                "from another schema must not be diffed against."
            )


def _check_document_id(document_id: str, *, key: str) -> None:
    """The key builders' segment rule, plus no surrounding whitespace.

    Real ids are source_file.base_id UUIDs. Anything else feeds a delete or
    sweep key later, where a malformed one would raise ValueError past the
    coordinator's `except StoreError` instead of failing closed here.
    """
    try:
        validate_segment("document id", document_id)
    except ValueError:
        raise ManifestUnreadable(
            f"manifest at {key!r} has a document id that is not a valid key "
            "segment (empty, '.', '..' or containing '/')"
        ) from None
    if document_id.strip() != document_id:
        raise ManifestUnreadable(
            f"manifest at {key!r} has a document id with surrounding whitespace"
        )


def _parse_manifest(raw: bytes, *, key: str, agency_id: str) -> Manifest:
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except _DuplicateKeyError as exc:
        raise ManifestUnreadable(
            f"manifest at {key!r} repeats the key {str(exc)!r} in one object; "
            "JSON would silently keep only the last"
        ) from None
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ManifestUnreadable(
            f"manifest at {key!r} is not UTF-8 JSON ({type(exc).__name__})"
        ) from None
    if not isinstance(document, dict):
        raise ManifestUnreadable(
            f"manifest at {key!r} must be a JSON object, got {type(document).__name__}"
        )

    _check_versions(document, key=key)

    documents = document.pop("documents", None)
    if not isinstance(documents, dict):
        raise ManifestUnreadable(
            f"manifest at {key!r}: 'documents' must be an object, got "
            f"{type(documents).__name__}"
        )
    fields = _checked_fields(document, _MANIFEST_FIELDS, key=key, where="envelope")

    if fields["agency_id"] != agency_id:
        # Ids, not content, so naming the requested one is safe and useful.
        raise ManifestUnreadable(
            f"manifest at {key!r} describes a different agency than "
            f"{agency_id!r}; refusing to diff against it"
        )

    entries: dict[str, ManifestDocumentEntry] = {}
    for document_id, entry in documents.items():
        _check_document_id(document_id, key=key)
        if not isinstance(entry, dict):
            raise ManifestUnreadable(
                f"manifest at {key!r}: entry {document_id!r} must be an object, "
                f"got {type(entry).__name__}"
            )
        entries[document_id] = ManifestDocumentEntry(
            **_checked_fields(
                entry, _ENTRY_FIELDS, key=key, where=f"entry {document_id!r}"
            )
        )

    return Manifest(**fields, documents=entries)
