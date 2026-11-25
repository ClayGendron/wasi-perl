"""The guest's stdout and stderr as wasmtime's custom outputs reach them: a table of each live
call's two streams by keys that are never reused, and the one write wasmtime calls with a key.

wasmtime hands each custom output to a task on a worker thread of its own, and frees the output
when it drops that task, some time after the store is closed. A free in Python would run there
after the call had returned; once Python is finalizing, CPython 3.11 ends that thread with
`pthread_exit`, which aborts the whole process in Rust's thread entry. So wasmtime is given no
free: the runner takes a call's keys before its store is set up and drops them on the calling
thread once its store is closed, however the call ended. A write is complete when the guest's
`fd_write` returns, so no write is left for a key once its call has ended; one that came would
find no stream, and its bytes would be dropped and reported written.

Adapted from wasi-python's `_outputs.py` at f9bf678 (same owner, Apache-2.0): `_write_output`
and `_CallbackCapture`'s `open` and `close`, here `take` and `drop`. Trimmed: `retarget` and
`settle`, which only its sessions need, and `_free_output`, the Python free it gives wasmtime,
which this module does not have."""

from __future__ import annotations

import ctypes
import itertools
import threading
from typing import Any, Protocol


class Stream(Protocol):
    """What the table holds: the runner's capped stream."""

    dropped: bool

    def write(self, data: bytes) -> None: ...


_lock = threading.Lock()  # writes come on wasmtime's threads while calls take and drop keys
_streams: dict[int, Stream] = {}
_keys = itertools.count(1)  # never 0: a NULL data pointer comes back to `write` as None


def take(out: Stream, err: Stream) -> tuple[int, int]:
    """Two fresh keys, for `out` and `err`, held until `drop`."""
    with _lock:
        keys = next(_keys), next(_keys)
        _streams[keys[0]], _streams[keys[1]] = out, err
    return keys


def drop(keys: tuple[int, int]) -> None:
    """Drop a call's two keys; called after its store is closed, on the calling thread."""
    with _lock:
        for key in keys:
            _streams.pop(key, None)


def holds(key: object) -> bool:
    """Whether `key` names a live stream."""
    with _lock:
        return key in _streams


def write(key: int | None, data: Any, size: int) -> int:
    """wasmtime's write of `size` bytes at `data` to the stream `key` names. It never raises
    and never prints: a piece the stream fails to keep marks it truncated, and every piece is
    reported written, as one past the output cap is."""
    stream = None
    try:
        with _lock:
            stream = _streams.get(key or 0)
        if stream is not None:
            stream.write(ctypes.string_at(data, size))
    except BaseException:
        if stream is not None:  # the result names the stream as truncated
            stream.dropped = True
    return size
