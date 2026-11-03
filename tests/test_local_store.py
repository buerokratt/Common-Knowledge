"""D19 — the `local` store backend: development only, and fail-closed.

The behaviour every store shares runs in tests/test_store_conformance.py,
which has a `local` variant. This file covers what is specific to a directory:
the refusal, the warning, absent-vs-error on a filesystem, traversal, and
atomic writes.
"""

import logging
from pathlib import Path

import pytest

from exporter.api.config import CONFIG_LOG_PREFIX, ConfigurationError
from exporter.stores.base import StoreError
from exporter.stores.local_store import LocalFileStore


@pytest.fixture
def store(tmp_path: Path) -> LocalFileStore:
    return LocalFileStore(tmp_path / "manifest-store", allow_local=True)


def test_it_refuses_to_construct_without_the_dev_flag(tmp_path: Path) -> None:
    """Not only the config validator: a store built any other way refuses too."""
    with pytest.raises(ConfigurationError) as excinfo:
        LocalFileStore(tmp_path, allow_local=False)
    message = str(excinfo.value)
    assert "MANIFEST_STORE_ALLOW_LOCAL" in message
    assert "first run" in message


def test_it_warns_when_it_does_construct(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="exporter.stores.local_store"):
        LocalFileStore(tmp_path, allow_local=True)

    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    assert CONFIG_LOG_PREFIX in record.getMessage()
    assert "Development only" in record.getMessage()


def test_a_missing_key_is_absent(store: LocalFileStore) -> None:
    assert store.get_bytes("a/b/manifest.json") is None
    assert store.exists("a/b/manifest.json") is False


def test_a_directory_where_an_object_should_be_is_an_error(
    store: LocalFileStore,
) -> None:
    """D4. Something is there and it is not an object: reading that as a
    missing manifest would be a silent first run."""
    store.put_bytes("a/b/manifest.json", b"{}")

    with pytest.raises(StoreError):
        store.get_bytes("a/b")
    with pytest.raises(StoreError):
        store.exists("a/b")


@pytest.mark.parametrize(
    "key",
    ["a/../../escape.json", "../escape.json", "a//b.json", "./a.json", "a/"],
)
def test_a_traversal_or_malformed_key_is_refused(
    store: LocalFileStore, key: str
) -> None:
    with pytest.raises(ValueError, match="segment"):
        store.put_bytes(key, b"{}")


def test_a_symlink_out_of_the_root_is_refused(
    store: LocalFileStore, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    store.root.mkdir(parents=True)
    link = store.root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not creatable here (Windows without privilege)")

    with pytest.raises(StoreError, match="outside the store root"):
        store.put_bytes("link/escape.json", b"{}")
    assert not (outside / "escape.json").exists()


def test_the_tmp_suffix_is_reserved(store: LocalFileStore) -> None:
    with pytest.raises(ValueError, match="reserves"):
        store.put_bytes("a/manifest.json.tmp", b"{}")


def test_a_write_leaves_no_temp_file_behind(store: LocalFileStore) -> None:
    store.put_bytes("a/manifest.json", b"one")
    store.put_bytes("a/manifest.json", b"two")

    assert store.get_bytes("a/manifest.json") == b"two"
    assert sorted(p.name for p in (store.root / "a").iterdir()) == ["manifest.json"]


def test_a_stray_temp_file_is_never_listed(store: LocalFileStore) -> None:
    """A write that crashed before os.replace must not surface as an object."""
    store.put_bytes("a/manifest.json", b"{}")
    (store.root / "a" / "manifest.json.0123.tmp").write_bytes(b"{half")

    assert [obj.key for obj in store.list_keys("a/")] == ["a/manifest.json"]


def test_a_failed_write_keeps_the_previous_object(
    store: LocalFileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The single-commit-point property an object store's PUT gives: a crash
    mid-commit leaves the old manifest, never half of the new one."""
    store.put_bytes("a/manifest.json", b"previous")

    def _fail(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("exporter.stores.local_store.os.replace", _fail)
    with pytest.raises(StoreError) as excinfo:
        store.put_bytes("a/manifest.json", b"next")

    assert excinfo.value.permanent
    monkeypatch.undo()
    assert store.get_bytes("a/manifest.json") == b"previous"
    assert [p.name for p in (store.root / "a").iterdir()] == ["manifest.json"]


def test_a_failed_cleanup_never_masks_the_original_error(
    store: LocalFileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If removing the temp file also fails, the caller still sees a
    StoreError caused by the write — not a raw OSError from the cleanup."""
    replace_error = OSError("disk full")

    def _fail_replace(*_args: object) -> None:
        raise replace_error

    def _fail_unlink(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("locked by another process")

    monkeypatch.setattr("exporter.stores.local_store.os.replace", _fail_replace)
    monkeypatch.setattr(Path, "unlink", _fail_unlink)

    with pytest.raises(StoreError) as excinfo:
        store.put_bytes("a/manifest.json", b"next")
    assert excinfo.value.__cause__ is replace_error


def test_an_interrupt_mid_write_leaves_no_temp_file(
    store: LocalFileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _interrupt(*_args: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("exporter.stores.local_store.os.replace", _interrupt)
    with pytest.raises(KeyboardInterrupt):
        store.put_bytes("a/manifest.json", b"next")

    assert list((store.root / "a").iterdir()) == []


def test_a_file_where_a_parent_directory_should_be_is_an_error(
    store: LocalFileStore,
) -> None:
    """Linux reports this as NotADirectoryError, Windows as FileNotFoundError.
    Either way it is a broken layout, never an absent manifest."""
    store.put_bytes("a/manifest.json", b"{}")

    with pytest.raises(StoreError):
        store.get_bytes("a/manifest.json/nested.json")
    with pytest.raises(StoreError):
        store.exists("a/manifest.json/nested.json")


def test_absence_under_a_missing_root_is_still_absence(tmp_path: Path) -> None:
    """A fresh volume has no store directory yet: a genuine first run."""
    store = LocalFileStore(tmp_path / "not-yet-created", allow_local=True)
    assert store.get_bytes("a/manifest.json") is None
    assert store.exists("a/manifest.json") is False


def test_listing_under_a_file_is_an_error(store: LocalFileStore) -> None:
    store.put_bytes("a/manifest.json", b"{}")

    with pytest.raises(StoreError, match="names a file"):
        list(store.list_keys("a/manifest.json/"))


def test_an_unreadable_listing_root_is_a_store_error(
    store: LocalFileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not a raw OSError: every caller catches StoreError and nothing else."""
    store.put_bytes("a/manifest.json", b"{}")
    start = store.root / "a"
    real_stat = Path.stat

    def _stat(self: Path, *args: object, **kwargs: object) -> object:
        if self == start:
            raise PermissionError("denied")
        return real_stat(self, *args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(Path, "stat", _stat)
    with pytest.raises(StoreError, match="list"):
        list(store.list_keys("a/"))


@pytest.mark.parametrize("key", ["a/../../escape.json", "a//b.json"])
def test_deleting_a_malformed_key_is_a_failure_not_a_raise(
    store: LocalFileStore, key: str
) -> None:
    """delete_many returns per-key failures; it never raises for one key."""
    outcome = store.delete_many([key])

    assert list(outcome.failed) == [key]


def test_listing_an_absent_prefix_is_empty(store: LocalFileStore) -> None:
    assert list(store.list_keys("never/")) == []


def test_deletes_are_per_key_and_declared_so(store: LocalFileStore) -> None:
    """D11: no batch API, the same outcome shape."""
    store.put_bytes("a/x.json", b"{}")

    outcome = store.delete_many(["a/x.json", "a/y.json"])

    assert store.capabilities.supports_batch_delete is False
    assert outcome.deleted == ("a/x.json",)
    assert outcome.missing == ("a/y.json",)


def test_deleting_a_directory_key_is_a_failure_not_a_raise(
    store: LocalFileStore,
) -> None:
    store.put_bytes("a/b/x.json", b"{}")

    outcome = store.delete_many(["a/b"])

    assert list(outcome.failed) == ["a/b"]
