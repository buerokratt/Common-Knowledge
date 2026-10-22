"""CKB's own bucket needs a separate GetObject-only client (README rule 6).

Credentials are explicit, so boto3 cannot fall back to its default chain.
Client creation is lazy and locked: enumeration needs no credentials, and
concurrent reads share a client without sharing a mutable Session.
"""

from contextlib import closing
from threading import Lock
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import boto3.session
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from exporter.stores.base import StoreError

if TYPE_CHECKING:
    from exporter.api.config import Settings


class ObjectTooLargeError(Exception):
    """A size guard rejected the object before decoding."""


class InvalidObjectKeyError(Exception):
    """The row names neither a bare key nor a URL in CKB's bucket."""


def _object_key(value: str | None, bucket: str) -> str | None:
    if not value:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if value.startswith("s3://"):
        prefix = f"s3://{bucket}/"
        if not value.startswith(prefix) or parts.query or parts.fragment:
            return None
        return value.removeprefix(prefix) or None
    if parts.scheme or parts.netloc or value.startswith("/"):
        return None
    return value


class CkbBucket:
    def __init__(self, settings: "Settings") -> None:
        self._settings = settings
        self._client: Any = None
        self._client_lock = Lock()

    def _ensure_client(self) -> None:
        with self._client_lock:
            if self._client is not None:
                return
            settings = self._settings
            access_key = settings.aws_access_key_id.get_secret_value()
            secret_key = settings.aws_secret_access_key.get_secret_value()
            missing = [
                name
                for name, value in (
                    ("S3_ENDPOINT_URL", settings.s3_endpoint_url),
                    ("S3_BUCKET_NAME", settings.s3_bucket_name),
                    ("AWS_ACCESS_KEY_ID", access_key),
                    ("AWS_SECRET_ACCESS_KEY", secret_key),
                )
                if not value
            ]
            if missing:
                raise StoreError(
                    f"CKB reads require {', '.join(missing)}", permanent=True
                )
            try:
                session = boto3.session.Session(
                    aws_access_key_id=access_key,
                    aws_secret_access_key=secret_key,
                    region_name=settings.aws_region,
                )
                self._client = session.client(
                    "s3",
                    endpoint_url=settings.s3_endpoint_url,
                    config=Config(
                        signature_version="s3v4",
                        s3={"addressing_style": "path"},
                        connect_timeout=settings.ckb_s3_connect_timeout,
                        read_timeout=settings.ckb_s3_read_timeout,
                        # The coordinator decides whether to retry a failed read.
                        retries={"total_max_attempts": 1},
                    ),
                )
            except (BotoCoreError, ClientError, ValueError) as exc:
                raise StoreError(
                    f"CKB S3 client creation failed: {type(exc).__name__}"
                ) from exc

    def get_bytes(self, value: str | None, limit: int) -> bytes | None:
        self._ensure_client()
        key = _object_key(value, self._settings.s3_bucket_name)
        if key is None:
            raise InvalidObjectKeyError
        try:
            response = self._client.get_object(
                Bucket=self._settings.s3_bucket_name, Key=key
            )
            with closing(response["Body"]) as body:
                if response["ContentLength"] > limit:
                    raise ObjectTooLargeError
                data = body.read(limit + 1)
            if len(data) > limit:
                raise ObjectTooLargeError
            return data
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            # Only S3's own NoSuchKey body means missing. A bare 404 can come from
            # a proxy in front of a mistyped endpoint and must fail the run, as
            # in s3_store. NoSuchBucket is a configuration fault, and a 403
            # cannot distinguish a missing key from denied permission.
            if status == 404 and code == "NoSuchKey":
                return None
            raise StoreError("CKB S3 GetObject failed") from exc
        except (BotoCoreError, OSError) as exc:
            raise StoreError(f"CKB S3 read failed: {type(exc).__name__}") from exc
