from __future__ import annotations

import fcntl
import time
from contextlib import contextmanager
from typing import Iterator, TextIO

from .events import emit
from .paths import CACHE_ROOT, prepare_directories


@contextmanager
def worker_slot(maximum_workers: int = 4) -> Iterator[int]:
    """Limit the desktop app and MCP bridge to four shared workers."""
    prepare_directories()
    lock_root = CACHE_ROOT / "worker-slots"
    lock_root.mkdir(parents=True, exist_ok=True)
    waiting_reported = False
    handle: TextIO | None = None
    selected = -1
    while handle is None:
        for index in range(maximum_workers):
            candidate = (lock_root / f"slot-{index + 1}.lock").open("a+")
            try:
                fcntl.flock(candidate.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                candidate.close()
                continue
            handle = candidate
            selected = index + 1
            break
        if handle is None:
            if not waiting_reported:
                emit("status", message="Queued • waiting for an available worker")
                waiting_reported = True
            time.sleep(0.25)

    try:
        yield selected
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
