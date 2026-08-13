from __future__ import annotations

import errno
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

if os.name == "nt":  # pragma: no cover - imported on Windows.
    import msvcrt
else:  # pragma: no cover - imported on POSIX.
    import fcntl


def set_file_mode(descriptor: int, path: str | os.PathLike[str], mode: int) -> None:
    """Apply restrictive POSIX modes without assuming fchmod exists on Windows."""
    if hasattr(os, "fchmod"):
        os.fchmod(descriptor, mode)
    else:  # pragma: no cover - Windows does not expose POSIX descriptor modes.
        os.chmod(path, mode)


def _lock(handle: BinaryIO) -> None:
    if os.name != "nt":
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return

    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    while True:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as error:
            if error.errno not in {errno.EACCES, errno.EDEADLK} and getattr(error, "winerror", None) not in {33, 36}:
                raise
            time.sleep(0.05)


def _unlock(handle: BinaryIO) -> None:
    if os.name != "nt":
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def exclusive_file_lock(path: str | os.PathLike[str], *, mode: int = 0o600) -> Iterator[None]:
    """Hold an exclusive advisory lock shared by processes on POSIX and Windows."""
    lock_path = Path(path)
    with lock_path.open("a+b") as handle:
        set_file_mode(handle.fileno(), lock_path, mode)
        _lock(handle)
        try:
            yield
        finally:
            _unlock(handle)
