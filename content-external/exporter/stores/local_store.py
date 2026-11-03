"""The `local` store backend — a directory, for the manifest store only (D19).

DEVELOPMENT ONLY, AND REFUSED WITHOUT A DELIBERATE ACT. `MANIFEST_STORE_BACKEND=
local` puts manifest.json under CONTENT_WORK_DIR. That volume is already
durable (a named volume, a PVC), already there, and needs no configuration,
which is exactly what makes it tempting and exactly why it is refused outside
local work.

The danger is not that the manifest can be lost — any store can lose an
object — but what losing it *looks like*. A re-provisioned volume carries no
manifest, and a missing manifest reads as `first_run`: the whole corpus is
republished, and every document CKB deleted while the manifest was gone is
orphaned for good. Absent from CKB's rows *and* absent from the manifest, such
a document can never classify as `deleted`, so nothing will ever remove it.
That is precisely the state `DIFF_ON_MISSING_MANIFEST=fail` exists to make
loud, and with a filesystem manifest it becomes reachable by a routine volume
event instead of by a deliberate act. An object store does not get
re-provisioned when a pod is rescheduled.

So this class refuses to construct unless `allow_local` is true. api/config.py
already refuses `local` without MANIFEST_STORE_ALLOW_LOCAL at startup; the
constructor refuses again so that the config validator is not the only thing
between a deployment and this failure mode. When it does construct, it warns.

The full rationale is in content-external-pipeline.md, "Where state lives",
under the rejected alternatives.

WHAT IT IMPLEMENTS. The five hooks of `ExternalStoreClient`, over the files
under `root`, with the same absent-vs-error rule as every other backend (D4):
only a `FileNotFoundError` whose nearest existing ancestor is a directory means
absent. A directory where an object should be, a permissions fault or a file
where a parent directory should be all raise StoreError, because reading any
of them as a missing manifest would be a silent first run. The ancestor check
exists for Windows, which reports a file in an intermediate path component as
FileNotFoundError rather than NotADirectoryError. One Windows limit is not
covered: a path past MAX_PATH without long-path support also reads as
FileNotFoundError. It cannot arise from a UUID key under a sane work dir, and
this backend is development only.

Writes are atomic — temp file, fsync, os.replace — so a crash mid-commit
leaves the previous manifest rather than half of the next one. On POSIX the
parent directory is fsynced after the replace too, so a committed manifest
survives a power loss rather than reverting to the previous one while the
documents it records stay published. Windows cannot fsync a directory, so
there the rename is atomic but not guaranteed durable — acceptable for a
development backend, and not the same guarantee an object store's PUT gives.

Content type and object metadata are accepted and not persisted: the manifest
store never reads either back, and a sidecar file per object would be a second
thing to keep consistent with the first.
"""

import contextlib
import hashlib
import logging
import os
import stat
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path

from exporter.api.config import CONFIG_LOG_PREFIX, ConfigurationError
from exporter.core.redaction import sanitize_sensitive_text
from exporter.stores.base import (
    DeleteOutcome,
    ExternalStoreClient,
    StoreCapabilities,
    StoreError,
    StoreObject,
)

__all__ = ["LocalFileStore"]

logger = logging.getLogger(__name__)

# The suffix of an in-flight write. Reserved: a key may not end with it, and a
# listing skips it, so a write that crashed before os.replace never surfaces
# as an object.
_TMP_SUFFIX = ".tmp"


def _store_error(operation: str, key: str, exc: OSError) -> StoreError:
    """A StoreError naming the operation, the key and the error class.

    Permanent: a local filesystem fault (permissions, a read-only mount, a
    directory where a file should be) fails identically on every retry.
    """
    return StoreError(
        sanitize_sensitive_text(
            f"local {operation} failed for key {key!r}: {type(exc).__name__}"
        ),
        permanent=True,
    )


def _reraise(exc: OSError) -> None:
    raise exc


def _fsync_directory(directory: Path) -> None:
    """Make a rename in `directory` durable. POSIX only: Windows cannot open a
    directory for fsync, and this backend is development only there."""
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class LocalFileStore(ExternalStoreClient):
    """A directory as an object store. Development only — see the module
    docstring for why it refuses to construct without `allow_local`."""

    def __init__(self, root: Path, *, allow_local: bool) -> None:
        if not allow_local:
            raise ConfigurationError(
                "the local store backend is development only and requires "
                "MANIFEST_STORE_ALLOW_LOCAL=true. A filesystem manifest means "
                "a re-provisioned volume reads as a first run: the corpus is "
                "republished and every document deleted in CKB meanwhile is "
                "orphaned permanently."
            )
        self._root = root
        self._capabilities = StoreCapabilities(
            can_delete=True,
            can_list=True,
            can_read=True,
            supports_batch_delete=False,
        )
        logger.warning(
            "%s MANIFEST_STORE_BACKEND=local: the manifest is on the work "
            "volume at %s. Development only — if this volume is ever "
            "re-provisioned the next run reads as a first run, republishing "
            "the corpus and permanently orphaning every document deleted in "
            "CKB in the meantime.",
            CONFIG_LOG_PREFIX,
            root,
        )

    @property
    def capabilities(self) -> StoreCapabilities:
        return self._capabilities

    @property
    def root(self) -> Path:
        return self._root

    # -- key -> path -------------------------------------------------------

    def _path(self, key: str) -> Path:
        """The file for `key`, refused if it could land outside `root`.

        Segment checks first, so a malformed key is a ValueError naming it;
        then a resolved containment check, which also catches a symlink inside
        the root that points out of it.
        """
        parts = key.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError(
                f"store key {key!r} has an empty, '.' or '..' segment; "
                "keys are relative paths with no traversal"
            )
        if parts[-1].endswith(_TMP_SUFFIX):
            raise ValueError(
                f"store key {key!r} ends with {_TMP_SUFFIX!r}, which this "
                "backend reserves for in-flight writes"
            )
        path = self._root.joinpath(*parts)
        root = self._root.resolve()
        if not path.resolve().is_relative_to(root):
            raise StoreError(
                f"local key {key!r} resolves outside the store root; refusing",
                permanent=True,
            )
        return path

    def _key(self, path: Path) -> str:
        return path.relative_to(self._root).as_posix()

    def _require_absent(self, path: Path, key: str, operation: str) -> None:
        """Confirm a FileNotFoundError means absent, or raise StoreError.

        Absent only if the nearest existing ancestor, up to the root, is a
        directory. Windows reports a file in an intermediate component as
        FileNotFoundError; without this, `get_bytes` would return None for a
        broken layout — a silent first run.
        """
        for ancestor in path.parents:
            try:
                mode = ancestor.stat().st_mode
            except FileNotFoundError:
                if ancestor == self._root:
                    return
                continue
            except OSError as exc:
                raise _store_error(operation, key, exc) from exc
            if not stat.S_ISDIR(mode):
                raise StoreError(
                    f"local key {key!r} has a file where a parent directory should be",
                    permanent=True,
                )
            return

    # -- backend hooks -----------------------------------------------------

    def _put_bytes(
        self,
        key: str,
        payload: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> StoreObject:
        path = self._path(key)
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}{_TMP_SUFFIX}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            _fsync_directory(path.parent)
        except BaseException as exc:
            # BaseException so an interrupt between write and replace does not
            # strand a temp file. The cleanup's own failure is suppressed so
            # it can never mask the error that matters.
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            if isinstance(exc, OSError):
                raise _store_error("put", key, exc) from exc
            raise
        return StoreObject(
            key=key,
            size_bytes=len(payload),
            etag=hashlib.sha256(payload).hexdigest()[:32],
        )

    def _get_bytes(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            self._require_absent(path, key, "get")
            return None
        except OSError as exc:
            raise _store_error("get", key, exc) from exc

    def _exists(self, key: str) -> bool:
        path = self._path(key)
        try:
            mode = path.stat().st_mode
        except FileNotFoundError:
            self._require_absent(path, key, "exists")
            return False
        except OSError as exc:
            raise _store_error("exists", key, exc) from exc
        if not stat.S_ISREG(mode):
            # Not absent: something is there, and it is not an object.
            raise StoreError(
                f"local key {key!r} names something other than a file",
                permanent=True,
            )
        return True

    def _list_keys(self, prefix: str) -> Iterator[StoreObject]:
        # Walk from the deepest directory the prefix names, then filter by the
        # full prefix: `documents/d1/` must not yield `documents/d10/`, and a
        # prefix that ends mid-name (`chunks/000`) must still match its files.
        directory, _, _ = prefix.rpartition("/")
        start = self._path(directory) if directory else self._root
        # stat, not is_dir(): is_dir() lets a PermissionError escape as a raw
        # OSError, past every caller's `except StoreError`.
        try:
            mode = start.stat().st_mode
        except FileNotFoundError:
            return
        except OSError as exc:
            raise _store_error("list", prefix, exc) from exc
        if not stat.S_ISDIR(mode):
            raise StoreError(
                f"local prefix {prefix!r} names a file where a directory should be",
                permanent=True,
            )
        found: list[StoreObject] = []
        try:
            # onerror re-raises: os.walk's default skips an unreadable
            # directory silently, and a listing that quietly omits keys is
            # one a sweep would act on.
            for current, _dirs, files in os.walk(start, onerror=_reraise):
                for name in files:
                    if name.endswith(_TMP_SUFFIX):
                        continue
                    path = Path(current) / name
                    key = self._key(path)
                    if key.startswith(prefix):
                        found.append(
                            StoreObject(key=key, size_bytes=path.stat().st_size)
                        )
        except OSError as exc:
            raise _store_error("list", prefix, exc) from exc
        yield from sorted(found, key=lambda obj: obj.key)

    def _delete_many(self, keys: tuple[str, ...]) -> DeleteOutcome[str]:
        # No batch API to fall back from: one unlink per key (D11), with the
        # same outcome shape a batch-capable store returns.
        deleted: list[str] = []
        missing: list[str] = []
        failed: dict[str, str] = {}
        for key in keys:
            try:
                self._path(key).unlink()
            except FileNotFoundError:
                missing.append(key)
            except (OSError, StoreError, ValueError) as exc:
                failed[key] = type(exc).__name__
            else:
                deleted.append(key)
        return DeleteOutcome(
            deleted=tuple(deleted), missing=tuple(missing), failed=failed
        )
