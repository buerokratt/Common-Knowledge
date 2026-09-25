"""A14 — the flock primitive and the startup lock self-test.

Pure filesystem work: no Docker, no compose stack, no network.

These tests cover the primitive F2 will build the whole-run lock on, so they
are asserting the property F2 depends on rather than this module's own API:
an exclusive lock either excludes a second holder or the volume is refused at
startup. What they deliberately cannot cover is the NFS case — there flock
succeeds on two nodes at once, which no single-host test can reproduce. That
gap is why warn_if_locking_is_unreliable() exists and why the chart pins
ReadWriteOnce.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from exporter.api.config import ConfigurationError, Settings, assert_work_dir_usable
from exporter.services.locking import (
    filesystem_type,
    probe_lockable,
    try_exclusive_lock,
    warn_if_locking_is_unreliable,
)


def _settings(work_dir: Path) -> Settings:
    return Settings(content_work_dir=work_dir.as_posix())  # pyright: ignore[reportCallIssue]


# --------------------------------------------------------------------------
# try_exclusive_lock
# --------------------------------------------------------------------------


def test_lock_is_acquired_on_a_free_path(tmp_path: Path) -> None:
    with try_exclusive_lock(tmp_path / "run.lock") as acquired:
        assert acquired is True


def test_lock_file_is_created(tmp_path: Path) -> None:
    lock = tmp_path / "run.lock"
    with try_exclusive_lock(lock):
        assert lock.exists()


def test_lock_file_survives_release(tmp_path: Path) -> None:
    """Unlinking on release would open the race this design must not have:
    one process removing the file another has just opened and locked, leaving
    two holders of two different inodes — which reads as success on both
    sides."""
    lock = tmp_path / "run.lock"
    with try_exclusive_lock(lock):
        pass
    assert lock.exists()


def test_a_second_acquire_while_held_is_refused(tmp_path: Path) -> None:
    """flock is per-descriptor, so a second open+lock in the same process is
    a faithful test of exclusion and not a quirk of it."""
    lock = tmp_path / "run.lock"
    with try_exclusive_lock(lock) as first:
        assert first is True
        with try_exclusive_lock(lock) as second:
            assert second is False


def test_contention_yields_false_rather_than_raising(tmp_path: Path) -> None:
    """F2 reports `busy` and exits 0 on contention, so the primitive must not
    make a normal outcome an exception."""
    lock = tmp_path / "run.lock"
    with try_exclusive_lock(lock):
        with try_exclusive_lock(lock) as second:
            assert second is False  # no raise


def test_release_allows_reacquisition(tmp_path: Path) -> None:
    lock = tmp_path / "run.lock"
    with try_exclusive_lock(lock) as first:
        assert first is True
    with try_exclusive_lock(lock) as again:
        assert again is True


def test_parent_directories_are_created(tmp_path: Path) -> None:
    lock = tmp_path / "nested" / "deeper" / "run.lock"
    with try_exclusive_lock(lock) as acquired:
        assert acquired is True
    assert lock.exists()


def test_an_uncreatable_lock_path_raises(tmp_path: Path) -> None:
    """A path that cannot hold a lock file is not contention — it is an
    unusable work directory, and the startup assertion must see it."""
    blocker = tmp_path / "afile"
    blocker.write_text("", encoding="utf-8")

    with pytest.raises(OSError):  # noqa: PT011 - platform decides the subclass
        with try_exclusive_lock(blocker / "run.lock"):
            pass


@pytest.mark.skipif(
    os.name == "nt", reason="lock inheritance across processes differs on Windows"
)
def test_a_lock_held_by_another_process_is_refused(tmp_path: Path) -> None:
    """The case that actually matters: two processes, one volume. This is the
    export-versus-drain overlap F2's lock exists to prevent."""
    lock = tmp_path / "run.lock"
    script = textwrap.dedent(f"""
        import fcntl, os, sys, time
        fd = os.open({str(lock)!r}, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sys.stdout.write("held\\n")
        sys.stdout.flush()
        time.sleep(30)
    """)
    holder = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"

        with try_exclusive_lock(lock) as acquired:
            assert acquired is False
    finally:
        holder.kill()
        holder.wait(timeout=10)

    # The OS releases the lock when the holder dies, including on SIGKILL, so
    # a crashed run cannot wedge the next one.
    with try_exclusive_lock(lock) as after:
        assert after is True


# --------------------------------------------------------------------------
# probe_lockable — the startup self-test
# --------------------------------------------------------------------------


def test_probe_succeeds_and_cleans_up(tmp_path: Path) -> None:
    probe_lockable(tmp_path)
    assert not list(tmp_path.glob(".startup-lock-probe"))


def test_probe_raises_when_the_probe_path_is_already_locked(tmp_path: Path) -> None:
    """The clean way to simulate a volume that cannot grant a lock."""
    with try_exclusive_lock(tmp_path / ".startup-lock-probe") as held:
        assert held is True
        with pytest.raises(OSError, match="exclusive lock"):
            probe_lockable(tmp_path)


def test_probe_raises_on_an_unwritable_directory(tmp_path: Path) -> None:
    """The lock probe subsumes the write probe it replaced: you cannot lock a
    file you cannot create."""
    blocker = tmp_path / "afile"
    blocker.write_text("", encoding="utf-8")

    with pytest.raises(OSError):  # noqa: PT011 - platform decides the subclass
        probe_lockable(blocker)


# --------------------------------------------------------------------------
# The filesystem warning — the case the probe cannot see
# --------------------------------------------------------------------------


def test_filesystem_type_is_none_off_linux(tmp_path: Path) -> None:
    if sys.platform == "linux":
        pytest.skip("/proc/mounts is readable here")
    assert filesystem_type(tmp_path) is None


def test_no_warning_for_an_ordinary_filesystem(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING", logger="exporter.services.locking"):
        assert warn_if_locking_is_unreliable(tmp_path) is None
    assert not caplog.records


def test_a_network_filesystem_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Asserted through the real predicate rather than a restated one, so a
    refactor cannot leave this passing against stale duplicated logic."""
    monkeypatch.setattr(
        "exporter.services.locking.filesystem_type", lambda _path: "nfs4"
    )
    with caplog.at_level("WARNING", logger="exporter.services.locking"):
        assert warn_if_locking_is_unreliable(tmp_path) == "nfs4"

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "advisory" in message
    assert "ReadWriteOnce" in message


@pytest.mark.parametrize("fs_type", ["nfs", "nfs4", "cifs", "fuse.sshfs", "ceph"])
def test_every_unsafe_filesystem_family_warns(
    fs_type: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "exporter.services.locking.filesystem_type", lambda _path: fs_type
    )
    assert warn_if_locking_is_unreliable(tmp_path) == fs_type


def test_an_unknown_filesystem_is_not_treated_as_safe_or_unsafe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """None means "cannot tell", and cannot-tell must not produce a warning
    that operators learn to ignore."""
    monkeypatch.setattr("exporter.services.locking.filesystem_type", lambda _path: None)
    assert warn_if_locking_is_unreliable(tmp_path) is None


# --------------------------------------------------------------------------
# Wired into startup
# --------------------------------------------------------------------------


def test_assert_work_dir_usable_runs_the_lock_probe(tmp_path: Path) -> None:
    resolved = assert_work_dir_usable(_settings(tmp_path / "work"))
    assert resolved.is_dir()
    assert not list(resolved.glob(".startup-lock-probe"))


def test_startup_refuses_when_an_exclusive_lock_cannot_be_taken(
    tmp_path: Path,
) -> None:
    """A14's headline: a silent network-filesystem problem becomes a refusal
    to start rather than a concurrency bug found months later."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    with try_exclusive_lock(work_dir / ".startup-lock-probe") as held:
        assert held is True
        with pytest.raises(ConfigurationError) as excinfo:
            assert_work_dir_usable(_settings(work_dir))

    message = str(excinfo.value)
    assert "CONTENT_WORK_DIR" in message
    # The message has to explain the stake, not just refuse: the person
    # reading it is looking at a crashed container.
    assert "lock" in message
    assert "drain" in message
