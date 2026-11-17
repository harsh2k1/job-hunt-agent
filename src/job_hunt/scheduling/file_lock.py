"""A lock held by one process at a time, released when the holder ends
(even if it crashes): fcntl on macOS and Linux, msvcrt on Windows."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


# yields True when this process got the lock, False when another holds it
@contextmanager
def exclusive_lock(path: Path) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            _lock(handle.fileno())
        except OSError:
            yield False
            return
        yield True


def _lock(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
