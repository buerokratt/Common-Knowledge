"""Advisory file locking on CONTENT_WORK_DIR.

This is the primitive F2 builds the whole-run lock on, written here because
A14 needs the same mechanism for its startup self-test. One flock
implementation, not two: if the self-test and the real lock could diverge,
the self-test would stop being evidence about the real lock.

WHAT F2 ADDS, AND WHAT THIS DELIBERATELY DOES NOT DECIDE. This module answers
one question — "can I take an exclusive lock on this path right now?" — and
has no opinion about what a caller should do with "no". F2 owns that: a single
non-blocking lock taken before anything else, and when it is held the run
reports `busy` and exits 0, because a concurrent run is not an error.
`drain_deletions` takes the same lock, which is the entire point — the overlap
that corrupts state is a drain deleting a key an export just re-published
(flagged risk 8). None of that belongs in a lock primitive.

THE LIMIT OF THIS MECHANISM, STATED BECAUSE IT IS LOAD-BEARING. `flock` is
advisory and sound only within one kernel. On an RWX volume (NFS, EFS,
CephFS) it is routinely not honoured across nodes, so two pods would both
"acquire" it and run concurrent exports with no error at all. That is why the
chart pins `replicas: 1` and `strategy: Recreate` and why the PVC must be
block/local-backed — see charts/ckb/templates/content-external-pvc.yaml and
the accessMode guard in _helpers.tpl. Those are the actual defence; this
module cannot substitute for them, and `probe_lockable()` below is explicit
about the case it cannot detect.

The OS releases the lock when the process dies, including on SIGKILL, so a
crashed run cannot wedge the next one. That property is why a lock file is
used rather than a lock *record*, and it must survive any future rewrite.
"""

import logging
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

# Network filesystems where flock either does nothing or silently succeeds
# without excluding another node. Matched against the filesystem type in
# /proc/mounts; a prefix match covers the fuse.* family.
_UNSAFE_FS_PREFIXES = ("nfs", "cifs", "smb", "fuse", "9p", "gluster", "ceph")

# Branching on sys.platform rather than os.name is what lets a type checker
# narrow these imports: fcntl's stubs are POSIX-only and msvcrt's are
# Windows-only, so on either platform the other branch must be provably
# unreachable rather than merely unused at runtime.
if sys.platform == "win32":  # pragma: no cover - developer machines only
    import msvcrt
else:
    import fcntl


def _try_lock_fd(fd: int) -> bool:
    """Take a non-blocking exclusive lock on an open descriptor.

    Windows is supported only so the unit tests run on a developer machine —
    the service itself always ships in a Linux container. The same carve-out,
    for the same reason, as normalise_container_path()'s host-absolute path
    branch in exporter/api/config.py.
    """
    if sys.platform == "win32":  # pragma: no cover - developer machines only
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock_fd(fd: int) -> None:
    """Release the lock. Best-effort by design.

    Closing the descriptor releases it anyway, so raising here would turn a
    successful run into a failed one at teardown.
    """
    if sys.platform == "win32":  # pragma: no cover - developer machines only
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            logger.debug("releasing lock failed; close will release it")
        return

    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        logger.debug("releasing lock failed; close will release it")


@contextmanager
def try_exclusive_lock(path: Path) -> Iterator[bool]:
    """Yield whether an exclusive lock on `path` was acquired.

    Never blocks and never raises on contention — a held lock yields False,
    because for F2 "someone else is running" is a normal outcome and not an
    error. A lock file that cannot be *created* is a different matter and does
    raise OSError: that means the work directory is unusable, which A14's
    startup assertion exists to catch before any run starts.

    The file is left in place on release. Unlinking it would open the classic
    race where one process removes the file another has just opened and
    locked, leaving two holders of two different inodes — which reads as
    success on both sides.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # os.open rather than open(): O_CREAT without truncation, so a concurrent
    # holder's descriptor is never invalidated by our arrival.
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    acquired = False
    try:
        acquired = _try_lock_fd(fd)
        yield acquired
    finally:
        if acquired:
            _unlock_fd(fd)
        os.close(fd)


def filesystem_type(path: Path) -> str | None:
    """The filesystem type backing `path`, or None if it cannot be determined.

    Reads /proc/mounts and picks the longest matching mount point, which is
    the nested-mount rule. Linux-only by construction; returns None anywhere
    else, and None means "unknown", never "safe".
    """
    if sys.platform != "linux":
        return None

    try:
        mounts = Path("/proc/mounts").read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        target = path.resolve()
    except OSError:
        return None

    best_point = ""
    best_type: str | None = None
    for line in mounts.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        mount_point, fs_type = fields[1], fields[2]
        if len(mount_point) <= len(best_point):
            continue
        try:
            target.relative_to(mount_point)
        except ValueError:
            continue
        best_point, best_type = mount_point, fs_type

    return best_type


def warn_if_locking_is_unreliable(path: Path) -> str | None:
    """Warn when `path` is on a filesystem where flock does not mean anything.

    Returns the offending filesystem type, or None.

    This exists because probe_lockable() cannot detect the worst case. On NFS,
    flock is advisory and frequently *succeeds* on two nodes at once — so the
    probe passes and two concurrent exports run with no error. A log line is
    the only signal available for that, and a signal beats nothing.
    """
    fs_type = filesystem_type(path)
    if fs_type is None:
        return None
    if not fs_type.startswith(_UNSAFE_FS_PREFIXES):
        return None

    logger.warning(
        "CONTENT_WORK_DIR is on a %s filesystem. flock is advisory there and "
        "is routinely not honoured across nodes, so the run lock may not "
        "exclude a concurrent export or drain — and it will not report an "
        "error when it fails to. Use a block or local-backed volume "
        "(ReadWriteOnce), which is what the chart's PVC requires.",
        fs_type,
    )
    return fs_type


def probe_lockable(work_dir: Path, *, name: str = ".startup-lock-probe") -> None:
    """Prove an exclusive lock can be taken under `work_dir`, or raise OSError.

    A14's startup self-test. It subsumes the plain write probe it replaced:
    creating the lock file proves the directory is writable, and locking it
    proves the filesystem supports the mechanism F2 depends on. A volume that
    cannot do this turns into a refusal to start instead of a concurrency bug
    discovered months later.

    Raises OSError on failure so the caller — assert_work_dir_usable() in
    exporter/api/config.py — can present it as a ConfigurationError with the
    variable named. The probe file is unlinked on success; a leftover from a
    killed process is harmless, since the lock died with the process.
    """
    probe = work_dir / name
    with try_exclusive_lock(probe) as acquired:
        if not acquired:
            raise OSError(
                f"an exclusive lock on {probe} could not be taken. Either "
                "another content-external process is starting up on this "
                "volume, or the filesystem does not support locking."
            )
    try:
        probe.unlink()
    except OSError:
        # Cosmetic. The lock has already been proven and released; a probe
        # file we cannot remove must not fail startup.
        logger.debug("could not remove the lock probe file at %s", probe)
