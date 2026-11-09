"""The store client — Key Design #1, one level below the sink.

`ExternalStoreClient` is a key-value view of an object store: put, get,
exists, list, delete. Since the two-sink change it is no longer the
coordinator's interface. It is consumed by exactly two things: the
object-store sink (D14), which owns the blob key layout and the publish write
order on top of it, and the manifest store (D16), which is a separately
configured instance of this same client. `services/publish_service.py` holds a
`ContentSink` and a manifest store and never one of these.

SHAPED AFTER CKB'S OWN PROVIDER, WITH THREE DELIBERATE DIFFERENCES (D2).
file-processing/app/services/blob_storage.py (`BlobStorageProvider`) is the
in-repo precedent, and this ABC is recognisably its shape. It differs in
exactly three places, each for a reason:

1. `delete_many` returns a `DeleteOutcome` instead of `delete_file -> bool`.
   Deletion is inherently partial, and the caller has to tell deleted,
   already-gone, deferred and failed apart to decide what goes in the
   deferred-deletion journal. A bool cannot say "already gone", which is a
   *success* for idempotency (D3).
2. `get_bytes` distinguishes absent (`None`) from error (raise). The
   fail-closed manifest read rests entirely on this: `None` is a genuine
   first run, an error must abort the agency and commit nothing (C9, D4).
   Conflating the two is the ancestor's bug, and it turns an outage into a
   republish of the whole corpus.
3. It is a separate class with no module-level singleton, not a second
   provider registered inside file-processing. CKB's provider is bound to
   CKB's own bucket and credentials; the external destination has different
   ones, and routing external writes through the Classifier's dependency
   would make rule 6 unenforceable.

THE S3 SURFACE (D10). A backend may depend on exactly five operations —
PutObject, GetObject, HeadObject, ListObjectsV2, DeleteObjects — and nothing
else: no multipart, versioning, ACLs, lifecycle, conditional writes, presigned
URLs or tagging. No conditional writes is why `overwrite=False` is refused
rather than emulated: the run lock provides mutual exclusion instead.

PUBLIC VERBS ARE CONCRETE, BACKENDS IMPLEMENT THE HOOKS. Each public method
enforces what must not vary between backends — key validation, capability
checks, de-duplication, the ASCII metadata rule, that a listing stays under
its prefix, and that every requested key is accounted for in a delete — and
then calls one underscore hook. A new
backend (s3 in D6, azure_blob in Stage H) implements the hooks only, so it
cannot forget a rule the next backend remembers.
"""

import json
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Hashable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from exporter.core.redaction import sanitize_sensitive_text
from exporter.core.schemas import StoreObject, to_json_dict

__all__ = [
    "DeleteOutcome",
    "ExternalStoreClient",
    "NotSupported",
    "StoreCapabilities",
    "StoreError",
    "StoreObject",
    "JSON_CONTENT_TYPE",
    "account_for_requested",
    "encode_json",
    "validate_object_metadata",
]

JSON_CONTENT_TYPE = "application/json; charset=utf-8"
DEFAULT_CONTENT_TYPE = "application/octet-stream"

# The reason recorded for a key a backend's delete hook did not report on. It
# lands in `failed`, so the journal retries it, rather than vanishing from the
# outcome and never being journalled at all.
UNREPORTED_DELETE_REASON = "not-reported-by-backend"


class StoreError(RuntimeError):
    """The store failed, and it was not a definitive "absent".

    The one type a backend raises for 403, 5xx, timeouts, bad credentials or
    a malformed response. Backends wrap the SDK exception `from exc`, with a
    message that has been through sanitize_sensitive_text and that names keys
    and error codes only — never an object body, never a signed URL.

    `permanent` says whether retrying can help. True for a configuration-class
    fault — bad credentials, a missing bucket, a refused request — which will
    fail identically for every document; False for an outage, a throttle or a
    timeout. The object-store sink maps it to FAILED_PERMANENT versus
    FAILED_RETRYABLE, which is what lets F19's consecutive-permanent-failure
    abort fire on a rotated-away key instead of failing the corpus one
    document at a time.
    """

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


# Named as the design names it (D0, D15, L13) rather than NotSupportedError.
class NotSupported(StoreError):  # noqa: N818
    """A capability that was not declared was invoked.

    Raised rather than silently no-op'ing, at both layers: a store that cannot
    list, and a sink that cannot merge metadata. A capability lie is worse
    than a missing capability, because the degradation paths are the only
    thing between a non-listable destination and a sweep that deletes live
    content. Defined here so the sink layer shares one type with this one.
    """


@dataclass(frozen=True)
class StoreCapabilities:
    """What a backend can actually do. Declared honestly, never optimistically.

    `supports_batch_delete` is D11's field: when it is False the backend
    issues one delete per key and returns the same `DeleteOutcome`, so a
    missing batch API is a performance note and never a blocker.
    """

    can_delete: bool
    can_list: bool
    can_read: bool
    supports_batch_delete: bool


@dataclass(frozen=True)
class DeleteOutcome[T: Hashable]:
    """What a delete actually did, per item (D3).

    Generic in the item type because each caller reports in its own
    vocabulary: a store reports keys (`str`), a sink reports document ids
    (`str`) for `delete_documents` and ordinals (`int`) for `delete_chunks`.
    The deferred-deletion journal (F7) then records exactly what the caller
    asked to delete, with no translation step.

    Every requested item is in exactly one category:

    - `deleted`  — it existed and is now gone.
    - `missing`  — it was already gone. A *success*: a replayed delete must
                   not fail because the first attempt worked.
    - `deferred` — not attempted, by design (a backend with `can_delete=False`).
    - `failed`   — attempted and refused; maps the item to a short reason,
                   an error code, sanitised, never a response body.
    """

    deleted: tuple[T, ...] = ()
    missing: tuple[T, ...] = ()
    deferred: tuple[T, ...] = ()
    failed: Mapping[T, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("deleted", "missing", "deferred"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(
            self,
            "failed",
            MappingProxyType(
                {
                    item: sanitize_sensitive_text(str(reason))
                    for item, reason in dict(self.failed).items()
                }
            ),
        )

        # An item may be claimed once. "Deleted and failed" is two answers to
        # one question, and the journal would have to guess which is true.
        tally = Counter([*self.deleted, *self.missing, *self.deferred, *self.failed])
        repeated = sorted(repr(item) for item, seen in tally.items() if seen > 1)
        if repeated:
            raise ValueError(
                "DeleteOutcome: an item may appear in exactly one category, "
                f"found more than once: {', '.join(repeated)}"
            )

    @classmethod
    def deferring(cls, items: Iterable[T]) -> "DeleteOutcome[T]":
        """Every item deferred — what a `can_delete=False` backend returns."""
        return cls(deferred=tuple(dict.fromkeys(items)))

    @classmethod
    def combine(cls, *outcomes: "DeleteOutcome[T]") -> "DeleteOutcome[T]":
        """Merge per-batch outcomes into one. Raises if two batches both
        report the same item, which would mean it was sent twice."""
        failed: dict[T, str] = {}
        for outcome in outcomes:
            # A dict merge would quietly keep the later reason, so the
            # failed-in-two-batches case is caught here rather than by the
            # tally in __post_init__.
            twice = failed.keys() & outcome.failed.keys()
            if twice:
                raise ValueError(
                    "DeleteOutcome: an item may appear in exactly one category, "
                    f"found more than once: {', '.join(sorted(map(repr, twice)))}"
                )
            failed.update(outcome.failed)
        return cls(
            deleted=tuple(item for o in outcomes for item in o.deleted),
            missing=tuple(item for o in outcomes for item in o.missing),
            deferred=tuple(item for o in outcomes for item in o.deferred),
            failed=failed,
        )

    @property
    def succeeded(self) -> tuple[T, ...]:
        """Deleted or already gone — both mean "not there any more"."""
        return self.deleted + self.missing

    @property
    def unresolved(self) -> tuple[T, ...]:
        """Deferred or failed — what the deferred-deletion journal keeps."""
        return self.deferred + tuple(self.failed)

    @property
    def is_complete(self) -> bool:
        """True when nothing is left for the journal."""
        return not self.unresolved

    def counts(self) -> dict[str, int]:
        """Per-category counts, for a report or a log line."""
        return {
            "deleted": len(self.deleted),
            "missing": len(self.missing),
            "deferred": len(self.deferred),
            "failed": len(self.failed),
        }


def account_for_requested[T: Hashable](
    requested: tuple[T, ...], outcome: DeleteOutcome[T], *, reporter: str
) -> DeleteOutcome[T]:
    """`outcome`, checked to answer for exactly the `requested` items.

    Shared by both layers, because the deferred-deletion journal (F7) is only
    as good as this guarantee. An item the implementation forgot to report is
    recorded as failed, so it is journalled and retried rather than silently
    dropped. An item it reported but was never asked about means the outcome
    cannot be trusted at all, so that raises StoreError.
    """
    reported: set[T] = {
        *outcome.deleted,
        *outcome.missing,
        *outcome.deferred,
        *outcome.failed,
    }
    unexpected = reported - set(requested)
    if unexpected:
        raise StoreError(
            f"{reporter} reported on {len(unexpected)} item(s) that were not "
            "requested; refusing to trust the delete outcome"
        )
    unreported = [item for item in requested if item not in reported]
    if not unreported:
        return outcome
    return DeleteOutcome.combine(
        outcome,
        DeleteOutcome(failed=dict.fromkeys(unreported, UNREPORTED_DELETE_REASON)),
    )


def encode_json(value: object) -> bytes:
    """`value` as the deterministic UTF-8 JSON `put_json` writes (D5).

    Public so a caller can validate the exact bytes it is about to write — the
    manifest store parses its own commit before the PUT.
    """
    return json.dumps(
        to_json_dict(value),
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def validate_object_metadata(metadata: Mapping[str, str] | None) -> dict[str, str]:
    """Object metadata as a plain dict, or ValueError if any of it is not ASCII.

    Object metadata travels as HTTP headers, and headers must be ASCII: an
    Estonian `title` would 400 on one backend and be silently mangled on
    another (C6). So metadata carries ids and hex hashes only, and every
    human-readable field lives in the JSON body, where it stays full UTF-8.
    This is a store-transport rule and stays one — the llm-module sink sends
    a JSON body and must never strip Estonian to satisfy it.

    The message names the offending key but never echoes the value, which
    could be document text.
    """
    if not metadata:
        return {}
    checked: dict[str, str] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(
                f"object metadata must map str to str, got key {key!r} "
                f"with a {type(value).__name__} value"
            )
        if not key.isascii():
            raise ValueError(f"object metadata key {key!r} is not ASCII")
        if not value.isascii():
            raise ValueError(
                f"object metadata value for {key!r} is not ASCII. Metadata "
                "carries ids and hex hashes only; put human-readable text in "
                "the JSON body."
            )
        checked[key] = value
    return checked


def _require_key(key: str) -> str:
    if not isinstance(key, str) or not key:
        raise ValueError(f"store key must be a non-empty string, got {key!r}")
    return key


def _within_prefix(
    prefix: str, listing: Iterator[StoreObject]
) -> Iterator[StoreObject]:
    for obj in listing:
        if not obj.key.startswith(prefix):
            raise StoreError(
                f"backend listed {obj.key!r} outside the requested prefix "
                f"{prefix!r}; refusing to trust the listing"
            )
        yield obj


class ExternalStoreClient(ABC):
    """A key-value object store, as the object-store sink and the manifest
    store see it. See the module docstring for the D2 differences and the
    five-operation surface."""

    @property
    @abstractmethod
    def capabilities(self) -> StoreCapabilities:
        """What this backend can do — declared, never inferred at runtime."""

    # -- public verbs (Key Design #1) --------------------------------------

    def put_bytes(
        self,
        key: str,
        payload: bytes,
        *,
        content_type: str = DEFAULT_CONTENT_TYPE,
        metadata: Mapping[str, str] | None = None,
        overwrite: bool = True,
    ) -> StoreObject:
        """Write `payload` at `key`, replacing anything there.

        Every write is an upsert: keys are deterministic, so a replayed
        publish overwrites the same objects with the same bytes.
        `overwrite=False` would need a conditional write, which D10 forbids
        because S3-compatible stores implement it inconsistently, so it
        raises NotSupported instead of being emulated with a racy
        exists-then-put.
        """
        _require_key(key)
        if not isinstance(payload, bytes):
            raise TypeError(
                f"payload must be bytes, got {type(payload).__name__}; "
                "use put_json for structured data"
            )
        if not overwrite:
            raise NotSupported(
                "put_bytes(overwrite=False) needs a conditional write, which "
                "this service does not depend on (D10). The run lock provides "
                "mutual exclusion instead."
            )
        return self._put_bytes(
            key,
            payload,
            content_type=content_type,
            metadata=validate_object_metadata(metadata),
        )

    def put_json(
        self,
        key: str,
        value: object,
        *,
        metadata: Mapping[str, str] | None = None,
    ) -> StoreObject:
        """Write `value` as deterministic UTF-8 JSON (D5).

        `sort_keys=True` so an unchanged manifest re-serialises byte-
        identically — verification 20 compares bytes, and a manifest that
        reordered itself would look like a change on every run.
        `ensure_ascii=False` so Estonian stays readable in the stored object
        rather than becoming `\\u00f5` escapes. `allow_nan=False` so nothing
        non-standard reaches a consumer's JSON parser.

        `to_json_dict` first is required, not cosmetic: the frozen schemas
        hold MappingProxyType fields (`DocumentRecord.source`,
        `Manifest.documents`), which json.dumps cannot serialise.
        """
        return self.put_bytes(
            key, encode_json(value), content_type=JSON_CONTENT_TYPE, metadata=metadata
        )

    def get_bytes(self, key: str) -> bytes | None:
        """The object at `key`; `None` ONLY when it definitively does not
        exist (D4).

        Every other failure raises StoreError — and that includes the one
        that looks like absence. S3 answers 403, not 404, for a missing key
        when the caller lacks ListBucket permission. A backend that mapped
        that 403 to `None` would turn a permissions fault into a silent first
        run: the corpus republished, and every deletion made while the
        manifest was "missing" orphaned for good.
        """
        _require_key(key)
        self._require_capability(self.capabilities.can_read, "can_read")
        return self._get_bytes(key)

    def exists(self, key: str) -> bool:
        """Whether an object is at `key`. Same absent-vs-error rule as
        get_bytes: False only for a definitive not-found, StoreError for
        anything else."""
        _require_key(key)
        self._require_capability(self.capabilities.can_read, "can_read")
        return self._exists(key)

    def list_keys(self, prefix: str) -> Iterator[StoreObject]:
        """Every object under `prefix`, paginated by the backend.

        An empty prefix is refused: listing a whole bucket is never what a
        sweep or a reconcile means, and on a shared bucket it would walk
        other tenants' objects. Callers must PARSE the returned keys, never
        count them — some S3-compatible gateways return pseudo-directory
        entries (D12).

        A key outside `prefix` raises StoreError rather than being yielded.
        Listings feed prefix deletes, so a backend or gateway that ignored the
        prefix would otherwise delete another agency's content.
        """
        if not isinstance(prefix, str) or not prefix:
            raise ValueError(
                f"list_keys needs a non-empty prefix, got {prefix!r}; "
                "listing an entire bucket is never intended"
            )
        self._require_capability(self.capabilities.can_list, "can_list")
        return _within_prefix(prefix, self._list_keys(prefix))

    def delete_many(self, keys: Iterable[str]) -> DeleteOutcome[str]:
        """Delete `keys`, reporting each one (D3).

        RETURNS, never raises, for per-key failures — a partial delete is
        normal, and the caller needs to know which keys to journal. Only a
        whole-store failure (unreachable, bad credentials) may raise
        StoreError, because then no key's state is known.

        Duplicates are reported once. Empty input costs no backend call. A
        `can_delete=False` backend defers everything rather than failing
        mid-run. A key the backend forgets to report is recorded as failed,
        so it is journalled and retried rather than silently dropped.
        """
        requested = tuple(dict.fromkeys(_require_key(key) for key in keys))
        if not requested:
            return DeleteOutcome()
        if not self.capabilities.can_delete:
            return DeleteOutcome.deferring(requested)

        return account_for_requested(
            requested, self._delete_many(requested), reporter="backend"
        )

    # -- backend hooks -----------------------------------------------------

    @abstractmethod
    def _put_bytes(
        self,
        key: str,
        payload: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> StoreObject:
        """Unconditionally write. `metadata` is already ASCII-checked."""

    @abstractmethod
    def _get_bytes(self, key: str) -> bytes | None:
        """`None` for a definitive not-found; StoreError for anything else."""

    @abstractmethod
    def _exists(self, key: str) -> bool:
        """False for a definitive not-found; StoreError for anything else."""

    @abstractmethod
    def _list_keys(self, prefix: str) -> Iterator[StoreObject]:
        """Every object under a non-empty prefix, across all pages."""

    @abstractmethod
    def _delete_many(self, keys: tuple[str, ...]) -> DeleteOutcome[str]:
        """Delete de-duplicated, non-empty `keys`, reporting each one. A
        backend without batch delete issues one call per key (D11)."""

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _require_capability(declared: bool, name: str) -> None:
        if not declared:
            raise NotSupported(f"this store does not declare {name}")
