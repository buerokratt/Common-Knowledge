"""The `s3` store backend — boto3 against any S3-compatible endpoint (D6, D10).

NOT A VENDOR. This is boto3 against whatever EXTERNAL_S3_ENDPOINT_URL names:
SeaweedFS, MinIO, AWS or anything else that covers the surface below. No class,
setting or message here names a vendor, and none may — swapping the store is a
configuration change and the store conformance suite
(tests/test_store_conformance.py) is how a candidate is accepted.

THE SURFACE. Five operations and nothing else: PutObject, GetObject,
HeadObject, ListObjectsV2, DeleteObjects. No multipart, versioning, ACLs,
lifecycle, conditional writes, presigned URLs or tagging. The one exception is
D11's fallback: a store that declares `supports_batch_delete=False` gets one
DeleteObject per key instead of DeleteObjects, with an identical
`DeleteOutcome`, so a missing batch API is a performance note and never a
blocker.

PATH-STYLE ADDRESSING. Every S3-compatible store accepts `endpoint/bucket/key`;
virtual-hosted `bucket.endpoint` needs wildcard DNS that a local or in-cluster
store usually does not have. Path style is the portable choice.

ITS OWN CREDENTIALS, ALWAYS EXPLICIT. The client is built from a Session given
the access key and secret directly, so boto3 never consults its default chain.
That chain would find AWS_ACCESS_KEY_ID in this container's environment —
CKB's own read-only keys for CKB's own bucket — and the external tenant would
then be written with the Classifier's credentials (rule 6). Equally, this
class never touches file-processing's provider singleton.

"MISSING" NEEDS A LOOK FIRST. S3 answers a delete of an absent key with
success, so DeleteObjects alone cannot tell `deleted` from `missing` — and
`missing` is what makes a replayed delete idempotent rather than a fake
success (D3). So `_delete_many` HEADs each key first. That costs one request
per key, which is acceptable because deletes are rare (an exclusion, a
deletion, a 404'd page, a shortened document). If operator-invoked purges of a
large corpus ever make it hot, this pre-check is the one place to optimise.
It is also why the destination credentials need s3:GetObject (HEAD is
authorised as a GET) as well as PutObject, DeleteObject and ListBucket.

ABSENT MEANS S3 SAID SO. A GET is absent only on `NoSuchKey`, which S3 sends
as an XML body. A bare 404 with no S3 body — a proxy or an ingress in front
of a mistyped EXTERNAL_S3_ENDPOINT_URL — raises instead, because reading it as
a missing manifest would be a silent first run. HEAD has no body to read, so
for HEAD alone a bare 404 has to mean absent. The consequence is a known
limit: a missing bucket also answers HEAD with a bare 404, so `exists` reads
it as absent. The fail-closed path does not depend on HEAD — the manifest read
is a GET, whose `NoSuchBucket` body is distinguishable and raises.

PARTIAL DELETES REPORT, NOT RAISE. Once any DeleteObjects batch or any
DeleteObject call has gone through, a later failure is folded into the outcome
as `failed` for every key not yet confirmed, rather than raised: raising would
discard the keys already deleted, and the deferred-deletion journal needs
both. Only a failure before anything has changed raises.
"""

from collections.abc import Iterator, Mapping
from typing import Any

from boto3.session import Session
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    ParamValidationError,
)

from exporter.core.redaction import sanitize_sensitive_text
from exporter.stores.base import (
    DeleteOutcome,
    ExternalStoreClient,
    StoreCapabilities,
    StoreError,
    StoreObject,
)

__all__ = ["S3Store"]

# The DeleteObjects request limit, fixed by the S3 API.
_MAX_KEYS_PER_BATCH_DELETE = 1000

# Bounded, so one hung connection cannot stall a run indefinitely. Retries are
# botocore's standard mode: exponential backoff, throttles included.
_CONNECT_TIMEOUT_SECONDS = 5
_READ_TIMEOUT_SECONDS = 60
_MAX_ATTEMPTS = 3

# Error codes meaning "this key definitively does not exist". NoSuchBucket is
# deliberately absent from both: a missing bucket is a configuration fault,
# and reading it as a missing manifest would be a silent first run. GET has a
# body, so only S3's own NoSuchKey counts; HEAD has none, so botocore can only
# report the bare status (see the module docstring).
_GET_ABSENT_CODES = frozenset({"NoSuchKey"})
_HEAD_ABSENT_CODES = frozenset({"NoSuchKey", "404", "NotFound"})

# 4xx answers that a retry can still fix: botocore's own throttle and
# transient codes, plus S3's conflicting-operation, clock-skew and
# corrupted-upload answers. Every other 4xx is permanent — a configuration
# fault that will fail identically for every document.
_RETRYABLE_CODES = frozenset(
    {
        "BadDigest",
        "BandwidthLimitExceeded",
        "LimitExceededException",
        "OperationAborted",
        "PriorRequestNotComplete",
        "ProvisionedThroughputExceededException",
        "RequestLimitExceeded",
        "RequestThrottled",
        "RequestThrottledException",
        "RequestTimeTooSkewed",
        "RequestTimeout",
        "RequestTimeoutException",
        "SlowDown",
        "ThrottledException",
        "Throttling",
        "ThrottlingException",
        "TooManyRequestsException",
    }
)
_RETRYABLE_STATUSES = frozenset({408, 429})


def _code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", "Unknown"))


def _status(exc: ClientError) -> int:
    return int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))


def _is_permanent(exc: ClientError) -> bool:
    code, status = _code(exc), _status(exc)
    if code in _RETRYABLE_CODES or status in _RETRYABLE_STATUSES:
        return False
    return 400 <= status < 500


def _store_error(operation: str, key: str, exc: Exception) -> StoreError:
    """A StoreError naming the operation, the key and the error code — never
    a response body, never a URL with credentials in it."""
    if isinstance(exc, ClientError):
        message = (
            f"S3 {operation} failed for key {key!r}: {_code(exc)} (HTTP {_status(exc)})"
        )
        permanent = _is_permanent(exc)
    else:
        message = f"S3 {operation} failed for key {key!r}: {type(exc).__name__}"
        # No credentials or a malformed request fails the same way every time;
        # a connection error or a timeout may not.
        permanent = isinstance(exc, NoCredentialsError | ParamValidationError)
    return StoreError(sanitize_sensitive_text(message), permanent=permanent)


def _all_failed(keys: list[str], exc: Exception) -> DeleteOutcome[str]:
    """Every key in `keys` failed for the one reason `exc` gives."""
    reason = _code(exc) if isinstance(exc, ClientError) else type(exc).__name__
    return DeleteOutcome(failed=dict.fromkeys(keys, reason))


def _etag(raw: object) -> str | None:
    return raw.strip('"') if isinstance(raw, str) and raw else None


class S3Store(ExternalStoreClient):
    """An S3-compatible bucket, addressed by endpoint alone."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        region: str,
        supports_batch_delete: bool = True,
        list_page_size: int = 1000,
        max_pool_connections: int = 10,
    ) -> None:
        # Refused here and not only in the factory: boto3 silently drops empty
        # credentials and falls back to its default chain — CKB's own AWS_*
        # keys in this container (rule 6) — and an empty endpoint resolves to
        # real AWS. Every caller, the manifest store included, gets this check.
        missing = [
            name
            for name, value in (
                ("endpoint_url", endpoint_url),
                ("bucket", bucket),
                ("access_key_id", access_key_id),
                ("secret_access_key", secret_access_key),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                f"S3Store needs a non-empty {', '.join(missing)}; it never falls "
                "back to boto3's default credentials or endpoint"
            )
        if list_page_size < 1:
            raise ValueError(f"list_page_size must be >= 1, got {list_page_size}")
        self._bucket = bucket
        self._list_page_size = list_page_size
        self._capabilities = StoreCapabilities(
            can_delete=True,
            can_list=True,
            can_read=True,
            supports_batch_delete=supports_batch_delete,
        )
        session = Session(
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
        )
        # Any: boto3 ships no type information for generated client methods.
        self._client: Any = session.client(
            "s3",
            endpoint_url=endpoint_url,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"mode": "standard", "max_attempts": _MAX_ATTEMPTS},
                connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                read_timeout=_READ_TIMEOUT_SECONDS,
                max_pool_connections=max_pool_connections,
            ),
        )

    @property
    def capabilities(self) -> StoreCapabilities:
        return self._capabilities

    # -- hooks -------------------------------------------------------------

    def _put_bytes(
        self,
        key: str,
        payload: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> StoreObject:
        try:
            response = self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=payload,
                ContentType=content_type,
                Metadata=dict(metadata),
            )
        except (ClientError, BotoCoreError) as exc:
            raise _store_error("PutObject", key, exc) from exc
        return StoreObject(
            key=key, size_bytes=len(payload), etag=_etag(response.get("ETag"))
        )

    def _get_bytes(self, key: str) -> bytes | None:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read()
        except ClientError as exc:
            if _code(exc) in _GET_ABSENT_CODES:
                return None
            raise _store_error("GetObject", key, exc) from exc
        except BotoCoreError as exc:
            raise _store_error("GetObject", key, exc) from exc

    def _exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _code(exc) in _HEAD_ABSENT_CODES:
                return False
            raise _store_error("HeadObject", key, exc) from exc
        except BotoCoreError as exc:
            raise _store_error("HeadObject", key, exc) from exc
        return True

    def _list_keys(self, prefix: str) -> Iterator[StoreObject]:
        """Every entry under `prefix`, across all pages.

        Pseudo-directory entries are yielded, not filtered. Callers parse keys
        rather than counting them (D12), and hiding a gateway's quirk here
        would also hide it from the conformance suite that should report it.
        botocore's paginator raises on a repeated continuation token, which is
        what stops a misbehaving gateway from looping a sweep forever.
        """
        paginator = self._client.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket=self._bucket,
            Prefix=prefix,
            PaginationConfig={"PageSize": self._list_page_size},
        )
        try:
            for page in pages:
                for entry in page.get("Contents", ()):
                    yield StoreObject(
                        key=entry["Key"],
                        size_bytes=int(entry.get("Size", 0)),
                        etag=_etag(entry.get("ETag")),
                    )
        except (ClientError, BotoCoreError) as exc:
            raise _store_error("ListObjectsV2", prefix, exc) from exc

    def _delete_many(self, keys: tuple[str, ...]) -> DeleteOutcome[str]:
        # HEAD first: S3 reports a delete of an absent key as a success, so
        # this is the only way to report `missing`. A HEAD that fails for any
        # other reason raises — no key's state is then known.
        present = [key for key in keys if self._exists(key)]
        present_set = set(present)
        missing = tuple(key for key in keys if key not in present_set)
        if not present:
            return DeleteOutcome(missing=missing)
        if self._capabilities.supports_batch_delete:
            deleted = self._delete_batched(present)
        else:
            deleted = self._delete_one_by_one(present)
        return DeleteOutcome.combine(DeleteOutcome(missing=missing), deleted)

    # -- delete paths ------------------------------------------------------

    def _delete_batched(self, keys: list[str]) -> DeleteOutcome[str]:
        """DeleteObjects in batches of 1000, reported per key from the
        response's Deleted[] and Errors[]. A key in neither is left out, and
        the base class records it as failed so it is journalled. A failed
        call after the first reports the rest as failed (module docstring)."""
        outcomes: list[DeleteOutcome[str]] = []
        for start in range(0, len(keys), _MAX_KEYS_PER_BATCH_DELETE):
            batch = keys[start : start + _MAX_KEYS_PER_BATCH_DELETE]
            try:
                response = self._client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": key} for key in batch], "Quiet": False},
                )
            except (ClientError, BotoCoreError) as exc:
                if not outcomes:
                    raise _store_error(
                        "DeleteObjects", f"{batch[0]} (+{len(batch) - 1} more)", exc
                    ) from exc
                outcomes.append(_all_failed(keys[start:], exc))
                break
            requested = set(batch)
            outcomes.append(
                DeleteOutcome(
                    deleted=tuple(
                        entry["Key"]
                        for entry in response.get("Deleted", ())
                        if entry.get("Key") in requested
                    ),
                    failed={
                        entry["Key"]: str(entry.get("Code", "Unknown"))
                        for entry in response.get("Errors", ())
                        if entry.get("Key") in requested
                    },
                )
            )
        return DeleteOutcome.combine(*outcomes)

    def _delete_one_by_one(self, keys: list[str]) -> DeleteOutcome[str]:
        """D11's fallback: one DeleteObject per key, same outcome shape."""
        deleted: list[str] = []
        failed: dict[str, str] = {}
        for index, key in enumerate(keys):
            try:
                self._client.delete_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                failed[key] = _code(exc)
            except BotoCoreError as exc:
                # A transport failure, not a per-key refusal: stop sending.
                if index == 0:
                    raise _store_error("DeleteObject", key, exc) from exc
                failed.update(_all_failed(keys[index:], exc).failed)
                break
            else:
                deleted.append(key)
        return DeleteOutcome(deleted=tuple(deleted), failed=failed)
