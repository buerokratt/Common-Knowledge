"""Azure Blob storage beneath the object-store sink and manifest store.

Credentials are explicit and passed unchanged to the SDK; this backend never
consults the environment or manages the container. Only BlobNotFound means
absent, including on HEAD: a missing container or a proxy's bare 404 must
not turn a failed manifest read into a bootstrap.

The SDK owns retries, bounded to the S3 backend's attempt and timeout budget.
There is no additional service retry loop. Azure batch delete reports each
blob's status without a HEAD pre-check. Once a response has been recorded,
a later failure reports the remaining keys as failed rather than discarding
the partial result that the deletion journal needs.
"""

import re
from collections.abc import Iterator, Mapping

from azure.core.exceptions import (
    AzureError,
    ClientAuthenticationError,
    HttpResponseError,
)
from azure.storage.blob import ContainerClient, ContentSettings, ExponentialRetry

from exporter.core.redaction import sanitize_sensitive_text
from exporter.stores.base import (
    DeleteOutcome,
    ExternalStoreClient,
    StoreCapabilities,
    StoreError,
    StoreObject,
)

__all__ = ["AzureBlobStore"]

# Azure's batch API limit, independent of the listing page size.
_MAX_KEYS_PER_BATCH_DELETE = 256

# Match s3_store.py; Azure counts retries in addition to the initial attempt.
_CONNECT_TIMEOUT_SECONDS = 5
_READ_TIMEOUT_SECONDS = 60
_MAX_ATTEMPTS = 3
# The SDK default (15 s + 3**n, jitter 3) sleeps about 40 s per failing
# operation; these keep the two retries' waits to about 2-4 s and 4-6 s.
_INITIAL_BACKOFF_SECONDS = 1
_BACKOFF_INCREMENT_BASE = 2
_BACKOFF_JITTER_SECONDS = 1
_RETRYABLE_STATUSES = frozenset({408, 409, 429})
_METADATA_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _code(exc: AzureError) -> str:
    if not isinstance(exc, HttpResponseError):
        return type(exc).__name__
    code = getattr(exc, "error_code", None)
    if code is None and exc.response is not None:
        code = getattr(exc.response, "headers", {}).get("x-ms-error-code")
    # StorageErrorCode is a str enum; its str() includes the enum class name.
    return str(getattr(code, "value", code)) if code else "Unknown"


def _is_permanent(exc: AzureError) -> bool:
    if isinstance(exc, HttpResponseError) and exc.status_code is not None:
        return (
            400 <= exc.status_code < 500 and exc.status_code not in _RETRYABLE_STATUSES
        )
    return isinstance(exc, ClientAuthenticationError)


def _store_error(operation: str, key: str, exc: AzureError) -> StoreError:
    message = f"Azure Blob {operation} failed for key {key!r}: {_code(exc)}"
    if isinstance(exc, HttpResponseError):
        message += f" (HTTP {exc.status_code})"
    return StoreError(sanitize_sensitive_text(message), permanent=_is_permanent(exc))


def _etag(raw: str | None) -> str | None:
    return raw.strip('"') if raw else None


class AzureBlobStore(ExternalStoreClient):
    """An existing container, with no credential or endpoint defaults."""

    def __init__(
        self,
        *,
        account_url: str,
        container: str,
        credential: object,
        supports_batch_delete: bool = True,
        list_page_size: int = 1000,
    ) -> None:
        missing = [
            name
            for name, value in (
                ("account_url", account_url),
                ("container", container),
                ("credential", credential),
            )
            if not value or (isinstance(value, str) and not value.strip())
        ]
        if missing:
            raise ValueError(f"AzureBlobStore needs a non-empty {', '.join(missing)}")
        if "?" in account_url:
            raise ValueError(
                "account_url must not contain a query; pass SAS as credential"
            )
        if list_page_size < 1:
            raise ValueError(f"list_page_size must be >= 1, got {list_page_size}")
        self._list_page_size = list_page_size
        self._capabilities = StoreCapabilities(
            can_delete=True,
            can_list=True,
            can_read=True,
            supports_batch_delete=supports_batch_delete,
        )
        self._client = ContainerClient(
            account_url=account_url,
            container_name=container,
            # The SDK validates credential types; this boundary keeps them opaque.
            credential=credential,  # pyright: ignore[reportArgumentType]
            retry_policy=ExponentialRetry(
                retry_total=_MAX_ATTEMPTS - 1,
                retry_connect=_MAX_ATTEMPTS - 1,
                retry_read=_MAX_ATTEMPTS - 1,
                retry_status=_MAX_ATTEMPTS - 1,
                initial_backoff=_INITIAL_BACKOFF_SECONDS,
                increment_base=_BACKOFF_INCREMENT_BASE,
                random_jitter_range=_BACKOFF_JITTER_SECONDS,
            ),
            connection_timeout=_CONNECT_TIMEOUT_SECONDS,
            read_timeout=_READ_TIMEOUT_SECONDS,
        )

    @property
    def capabilities(self) -> StoreCapabilities:
        return self._capabilities

    def _put_bytes(
        self,
        key: str,
        payload: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> StoreObject:
        for name in metadata:
            if not _METADATA_NAME.fullmatch(name):
                raise ValueError(
                    f"object metadata key {name!r} must be a C# identifier"
                )
        try:
            response = self._client.get_blob_client(key).upload_blob(
                payload,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
                metadata=dict(metadata),
            )
        except AzureError as exc:
            raise _store_error("upload_blob", key, exc) from exc
        return StoreObject(
            key=key, size_bytes=len(payload), etag=_etag(response.get("etag"))
        )

    def _get_bytes(self, key: str) -> bytes | None:
        try:
            return self._client.download_blob(key).readall()
        except AzureError as exc:
            if _code(exc) == "BlobNotFound":
                return None
            raise _store_error("download_blob", key, exc) from exc

    def _exists(self, key: str) -> bool:
        try:
            self._client.get_blob_client(key).get_blob_properties()
        except AzureError as exc:
            if _code(exc) == "BlobNotFound":
                return False
            raise _store_error("get_blob_properties", key, exc) from exc
        return True

    def _list_keys(self, prefix: str) -> Iterator[StoreObject]:
        try:
            for entry in self._client.list_blobs(
                name_starts_with=prefix, results_per_page=self._list_page_size
            ):
                yield StoreObject(
                    key=entry.name, size_bytes=entry.size, etag=_etag(entry.etag)
                )
        except AzureError as exc:
            raise _store_error("list_blobs", prefix, exc) from exc

    def _delete_many(self, keys: tuple[str, ...]) -> DeleteOutcome[str]:
        if self._capabilities.supports_batch_delete:
            return self._delete_batched(keys)
        return self._delete_one_by_one(keys)

    def _delete_batched(self, keys: tuple[str, ...]) -> DeleteOutcome[str]:
        deleted: list[str] = []
        missing: list[str] = []
        failed: dict[str, str] = {}
        for start in range(0, len(keys), _MAX_KEYS_PER_BATCH_DELETE):
            batch = keys[start : start + _MAX_KEYS_PER_BATCH_DELETE]
            processed = 0
            try:
                responses = self._client.delete_blobs(
                    *batch, raise_on_any_failure=False
                )
                for key, response in zip(batch, responses, strict=False):
                    code = response.headers.get("x-ms-error-code", "Unknown")
                    if response.status_code == 202:
                        deleted.append(key)
                    elif response.status_code == 404 and code == "BlobNotFound":
                        missing.append(key)
                    else:
                        failed[key] = code
                    processed += 1
            except AzureError as exc:
                if start == 0 and processed == 0:
                    key = (
                        batch[0]
                        if len(batch) == 1
                        else f"{batch[0]} (+{len(batch) - 1} more)"
                    )
                    raise _store_error("delete_blobs", key, exc) from exc
                failed.update(dict.fromkeys(keys[start + processed :], _code(exc)))
                break
        return DeleteOutcome(
            deleted=tuple(deleted), missing=tuple(missing), failed=failed
        )

    def _delete_one_by_one(self, keys: tuple[str, ...]) -> DeleteOutcome[str]:
        deleted: list[str] = []
        missing: list[str] = []
        failed: dict[str, str] = {}
        for index, key in enumerate(keys):
            try:
                self._client.delete_blob(key)
            except HttpResponseError as exc:
                if exc.status_code == 404 and _code(exc) == "BlobNotFound":
                    missing.append(key)
                else:
                    failed[key] = _code(exc)
            except AzureError as exc:
                if index == 0:
                    raise _store_error("delete_blob", key, exc) from exc
                failed.update(dict.fromkeys(keys[index:], _code(exc)))
                break
            else:
                deleted.append(key)
        return DeleteOutcome(
            deleted=tuple(deleted), missing=tuple(missing), failed=failed
        )
