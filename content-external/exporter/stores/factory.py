"""The one place a store backend name becomes a store class (D7, A13).

No other module may branch on CONTENT_EXTERNAL_STORE_BACKEND (or on
MANIFEST_STORE_BACKEND, which names the same set of backends one role over).
tests/test_exporter_sink_resolution.py enforces that: this file, sinks/factory.py
and api/config.py are the only ones allowed to compare against a backend name
or key a dispatch table on one.

TWO ENTRY POINTS, ONE TABLE. `build_store` maps a backend name plus explicit
connection values to a client, and knows nothing about which role the store
plays. `build_destination_store` reads the object-store sink's settings and
calls it. The manifest store (D16) is the second caller: it passes the
`resolved_manifest_store_*` values to the same `build_store`, so there is one
dispatch table for both roles and no second factory to drift from this one.

Backends that do not exist yet refuse with the task that builds them, the
same convention as sinks/factory.py: `azure_blob` is Stage H, `local` is D19.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from exporter.api.config import ConfigurationError, Settings
from exporter.stores.base import ExternalStoreClient
from exporter.stores.s3_store import S3Store

__all__ = ["StoreConnection", "build_destination_store", "build_store"]

# A pool smaller than the number of documents in flight makes F16's
# concurrency queue on connections instead of on the store.
_MIN_POOL_CONNECTIONS = 10


@dataclass(frozen=True)
class StoreConnection:
    """What any backend needs to address one bucket or container.

    Plain strings, already unwrapped: the SecretStr discipline belongs to
    Settings, which is where values are echoed. The credentials are still
    kept out of the repr, which would otherwise print them in a traceback
    or a pytest assertion diff.
    """

    endpoint_url: str
    bucket: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    region: str
    supports_batch_delete: bool = True
    max_pool_connections: int = _MIN_POOL_CONNECTIONS


def _build_s3(connection: StoreConnection) -> ExternalStoreClient:
    return S3Store(
        endpoint_url=connection.endpoint_url,
        bucket=connection.bucket,
        access_key_id=connection.access_key_id,
        secret_access_key=connection.secret_access_key,
        region=connection.region,
        supports_batch_delete=connection.supports_batch_delete,
        max_pool_connections=connection.max_pool_connections,
    )


# Backend name -> builder.
_BACKENDS: dict[str, Callable[[StoreConnection], ExternalStoreClient]] = {
    "s3": _build_s3,
}

# Backend name -> the task that will register a builder.
_NOT_YET_BUILT: dict[str, str] = {
    "azure_blob": "Stage H",
    "local": "D19",
}


def build_store(backend: str, connection: StoreConnection) -> ExternalStoreClient:
    """The store client for `backend`, connected as `connection` says."""
    builder = _BACKENDS.get(backend)
    if builder is not None:
        return builder(connection)
    task = _NOT_YET_BUILT.get(backend)
    if task is not None:
        raise ConfigurationError(
            f"store backend {backend!r} has no implementation in this build yet "
            f"({task})."
        )
    raise ConfigurationError(f"store backend {backend!r} is not a known backend")


def build_destination_store(settings: Settings) -> ExternalStoreClient:
    """The object-store sink's own store, from CONTENT_EXTERNAL_STORE_BACKEND
    and EXTERNAL_S3_*.

    Missing values are refused here, all of them in one message, rather than
    at startup: /health must come up before a destination is configured, and
    Stage F decides when a run first builds this.
    """
    backend = settings.content_external_store_backend
    if backend in _BACKENDS:
        required = {
            "EXTERNAL_S3_ENDPOINT_URL": settings.external_s3_endpoint_url,
            "EXTERNAL_S3_BUCKET_NAME": settings.external_s3_bucket_name,
            "EXTERNAL_S3_ACCESS_KEY_ID": (
                settings.external_s3_access_key_id.get_secret_value()
            ),
            "EXTERNAL_S3_SECRET_ACCESS_KEY": (
                settings.external_s3_secret_access_key.get_secret_value()
            ),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ConfigurationError(
                f"CONTENT_EXTERNAL_STORE_BACKEND={backend} needs "
                f"{', '.join(missing)} to be set. The destination is a different "
                "bucket with different credentials from CKB's own: the AWS_* "
                "keys are CKB's read-only keys and are never used to write."
            )
        if (
            settings.external_s3_bucket_name == settings.s3_bucket_name
            and settings.external_s3_endpoint_url.rstrip("/")
            == settings.s3_endpoint_url.rstrip("/")
        ):
            # Rule 1's bucket-level twin: CKB's own bucket holds the scraped
            # tree (uploads/scrapped-data/), and the destination never shares it.
            raise ConfigurationError(
                f"EXTERNAL_S3_BUCKET_NAME={settings.external_s3_bucket_name!r} on "
                "EXTERNAL_S3_ENDPOINT_URL is CKB's own bucket (S3_BUCKET_NAME on "
                "S3_ENDPOINT_URL). The external destination must be a different "
                "bucket."
            )
    return build_store(
        backend,
        StoreConnection(
            endpoint_url=settings.external_s3_endpoint_url,
            bucket=settings.external_s3_bucket_name,
            access_key_id=settings.external_s3_access_key_id.get_secret_value(),
            secret_access_key=settings.external_s3_secret_access_key.get_secret_value(),
            region=settings.external_s3_region,
            supports_batch_delete=settings.external_s3_supports_batch_delete,
            max_pool_connections=max(
                _MIN_POOL_CONNECTIONS, settings.export_concurrency * 2
            ),
        ),
    )
