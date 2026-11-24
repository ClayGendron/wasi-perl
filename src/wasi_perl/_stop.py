"""How a running call is stopped: its deadline and Ctrl-C, both through one epoch callback per
store. The ticker advances every engine's epoch each 10 ms; a store's callback runs on the
guest's own thread every few ticks and either lets it run a few ticks more or ends it with an
error naming why. Adapted from wasi-python's epoch callback (`_call._on_epoch`; same owner,
Apache-2.0), trimmed to the deadline and Ctrl-C.

Ctrl-C. Python runs a signal's handler only in Python code on the main thread, and a guest
running there executes no Python but this callback and the output callbacks. So, when the call
is made on the main thread and SIGINT has Python's default handler, the call puts its own
handler in place for its duration, which only notes the signal; the callback then ends the
guest, and `run` raises KeyboardInterrupt once the store is closed. A handler raising inside the
output callback, which wasmtime-py would print and drop, is never in place."""

from __future__ import annotations

import contextlib
import signal
import threading
from collections.abc import Iterator
from typing import Any

from wasi_perl import _wasmtime_ffi
from wasi_perl._engines import TICKER

POLL_TICKS = 5  # the callback runs at least this often: every 50 ms of a computing guest
PREFIX = "wasi_perl stopped the guest: "
DEADLINE, INTERRUPTED = "deadline", "interrupted"


class Stop:
    """One call's stop: the ticker count at which its deadline passes (None for none), and
    whether Ctrl-C was seen."""

    def __init__(self, ticks: int | None) -> None:
        self.stop_at = None if ticks is None else TICKER.count + ticks
        self.interrupted = False
        self.keep: list[Any] = []  # the C callbacks, alive as long as the store

    def first(self) -> int:
        """The epoch deadline the store starts with."""
        if self.stop_at is None:
            return POLL_TICKS
        return max(1, min(POLL_TICKS, self.stop_at - TICKER.count))


def _error(word: str) -> int:
    return _wasmtime_ffi.error(PREFIX + word)


def arm(store: Any, stop: Stop) -> None:
    """Install `stop`'s callback on `store`."""

    def on_epoch(delta: Any, kind: Any) -> int:
        try:
            if stop.interrupted:
                return _error(INTERRUPTED)
            now = TICKER.count
            if stop.stop_at is not None and now >= stop.stop_at:
                return _error(DEADLINE)
            step = POLL_TICKS if stop.stop_at is None else min(POLL_TICKS, stop.stop_at - now)
            delta[0] = max(1, step)
            kind[0] = 0  # continue
            return 0
        except BaseException:  # a handler that raised here, KeyboardInterrupt above all
            stop.interrupted = True
            return _error(INTERRUPTED)

    stop.keep += _wasmtime_ffi.on_epoch(store, on_epoch)


def word(text: str) -> str | None:
    """ "deadline" or "interrupted" when an error is this module's own stop, else None."""
    if PREFIX + DEADLINE in text:
        return DEADLINE
    if PREFIX + INTERRUPTED in text:
        return INTERRUPTED
    return None


@contextlib.contextmanager
def watching_ctrl_c(stop: Stop) -> Iterator[None]:
    """For the call's duration, SIGINT notes itself on `stop`, when the call is on the main
    thread and SIGINT has Python's default handler; else it is left as it is."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    try:
        previous = signal.getsignal(signal.SIGINT)
    except (ValueError, OSError):
        yield
        return
    if previous is not signal.default_int_handler:
        yield  # the embedder's own handler: theirs to keep
        return

    def note(_signum: int, _frame: Any) -> None:
        stop.interrupted = True

    signal.signal(signal.SIGINT, note)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)
