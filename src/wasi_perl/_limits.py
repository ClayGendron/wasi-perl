"""The checks of `run`'s limits and inputs, each before any work, and the sentinel for an
argument not given. Adapted from wasi-python's `_limits.py` (same owner, Apache-2.0), trimmed to
the four limits and the three inputs this runner takes."""

from __future__ import annotations

import math
import numbers
import operator
import sys
import threading
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple

TICK_S = 0.01  # the epoch ticker's period
NO_DEADLINE = 2**62  # ticks: a store with epochs on and no deadline would trap at once
LIMIT_MAX = 2**63 - 1  # wasmtime reads a limit as signed: 2**63 and above would mean none


def _shown(value: Any) -> str:
    """A value for an error message: an integer too long to print is named by its size."""
    if isinstance(value, int) and value.bit_length() > 128:
        return f"an integer of {value.bit_length()} bits"
    return repr(value)


def integer_limit(name: str, value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer, or None; got {value!r}")
    try:
        number = operator.index(value)
    except TypeError:
        raise TypeError(f"{name} must be an integer, or None; got {value!r}") from None
    if not 0 <= number <= LIMIT_MAX:
        raise ValueError(f"{name} must be from 0 to 2**63 - 1, or None; got {_shown(number)}")
    return number


def deadline_limit(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise TypeError(f"deadline_s must be a number of seconds, or None; got {value!r}")
    rule = "deadline_s must be finite and above 0, and at most about 4.6e16 s, or None"
    try:
        seconds = float(value)
        ticks = math.ceil(seconds / TICK_S) if seconds > 0 else 0
    except (OverflowError, ValueError):
        raise ValueError(f"{rule}; got {_shown(value)}") from None
    if not math.isfinite(seconds) or seconds <= 0 or not 0 < ticks < NO_DEADLINE:
        raise ValueError(f"{rule}; got {_shown(value)}")
    return seconds


def ticks(deadline_s: float | None) -> int:
    """The epoch ticks a store's deadline is set to."""
    return NO_DEADLINE if deadline_s is None else max(1, math.ceil(deadline_s / TICK_S))


class Limits(NamedTuple):
    fuel: int | None
    deadline_s: float | None
    memory_bytes: int | None
    output_bytes: int | None


def check_limits(fuel: Any, deadline_s: Any, memory_bytes: Any, output_bytes: Any) -> Limits:
    return Limits(
        integer_limit("fuel", fuel),
        deadline_limit(deadline_s),
        integer_limit("memory_bytes", memory_bytes),
        integer_limit("output_bytes", output_bytes),
    )


def text(where: str, value: Any) -> str:
    """A plain str the guest sees exactly: no NUL, valid UTF-8. Messages name `where`, never the
    value: environment values are often secrets."""
    if not isinstance(value, str):
        raise TypeError(f"{where} must be a str, not {type(value).__name__}")
    plain = str.__str__(value)
    if "\0" in plain:
        raise ValueError(f"{where}: embedded null byte")
    try:
        str.encode(plain, "utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{where}: not valid UTF-8 (a lone surrogate)") from None
    return plain


class Inputs(NamedTuple):
    argv: tuple[str, ...]
    env: dict[str, str]
    stdin: bytes


def check_inputs(argv: Any, env: Any, stdin: Any) -> Inputs:
    if isinstance(argv, (str, bytes, bytearray)) or not isinstance(argv, Iterable):
        raise TypeError(f"argv must be an iterable of str, not {type(argv).__name__}")
    args = tuple(text(f"argv[{i}]", item) for i, item in enumerate(argv))
    plain_env: dict[str, str] = {}
    if env is not None:
        if not isinstance(env, Mapping):
            raise TypeError(f"env must be a mapping of str to str, not {type(env).__name__}")
        for key, value in env.items():
            name = text("env key", key)
            if not name or "=" in name:
                raise ValueError("env key: illegal environment variable name")  # never quoted
            shown = name if len(name) <= 40 else name[:40] + "…"
            if name == "PWD":
                raise ValueError(
                    "env: PWD is the guest's working folder, which perl.wasm moves to at "
                    "start-up; set it with cwd= so the runner can check it"
                )
            plain_env[name] = text(f"env value for {shown!r}", value)
    data = b""
    if stdin is not None:
        if type(stdin) is bytes:
            data = stdin
        else:
            try:
                data = memoryview(stdin).tobytes()
            except TypeError:
                raise TypeError(
                    f"stdin must be bytes-like, or None; got {type(stdin).__name__}"
                ) from None
    return Inputs(args, plain_env, data)


# The host descriptors each live call is counted as: the cap of the guest's own descriptor table,
# 256 in the build since the guest-side table landed (spec 011). A perl.wasm without that table
# holds as many as wasmtime lets it; the count is then the runner's estimate, not a bound.
GUEST_DESCRIPTORS = 256
WINDOWS_DESCRIPTORS = 4096  # Windows' budget, which has no soft limit to halve
OPEN_MAX = 10240  # macOS's OPEN_MAX: the soft limit its setrlimit(2) has long accepted


class TooManyCalls(RuntimeError):
    """Raised by `run` before any work when other calls of this process are running and one more
    would pass its budget of host descriptors: half its soft RLIMIT_NOFILE on POSIX (4,096 on
    Windows), each call counted at the guest table's cap of 256. A call with no other call
    running is never refused. Retry once a call has ended."""


_descriptor_lock = threading.Lock()
_held: dict[object, int] = {}  # each live call's slot: the descriptors it is counted at
_soft_raised = False


def _raise_soft_limit() -> None:
    """Once per process, the soft RLIMIT_NOFILE raised to the hard limit (to OPEN_MAX where the
    hard limit is unlimited, and on macOS at most OPEN_MAX), so a low default (macOS's 256) does
    not leave room for one call alone; a refusal leaves the limit as it was. The embedder's own
    files gain from it too. Windows has no such limit."""
    global _soft_raised
    if _soft_raised:
        return
    _soft_raised = True
    try:
        import resource
    except ImportError:
        return
    try:
        which, infinity = resource.RLIMIT_NOFILE, resource.RLIM_INFINITY
        soft, hard = resource.getrlimit(which)
        target = OPEN_MAX if hard == infinity else hard
        if sys.platform == "darwin":
            target = min(target, OPEN_MAX)
        if soft == infinity or soft >= target:
            return
        for want in dict.fromkeys((target, min(target, OPEN_MAX))):
            if want <= soft:
                continue
            try:
                resource.setrlimit(which, (want, hard))
                return
            except (ValueError, OSError):
                continue
    except (ValueError, OSError, AttributeError):
        return


def descriptor_budget() -> int:
    """The host descriptors this process's live calls may hold in all: half the soft
    RLIMIT_NOFILE, read at each call after the once-per-process raise, so an embedder that raises
    it later is seen."""
    _raise_soft_limit()
    try:
        import resource
    except ImportError:
        return WINDOWS_DESCRIPTORS
    soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    if soft == resource.RLIM_INFINITY or soft < 0:
        return 1 << 20
    return soft // 2


def descriptors_reserved() -> int:
    with _descriptor_lock:
        return sum(_held.values())


def reserve_descriptors(slot: object) -> None:
    """Count one more live call, under `slot`, which the caller made before its `try`, so
    `release_descriptors(slot)` in its `finally` releases it whatever interrupts. The count is
    one dictionary store, so no signal falls between taking it and being able to give it back.
    A call alone is never refused; beside others, TooManyCalls when the budget is spent."""
    budget = descriptor_budget()
    with _descriptor_lock:
        used = sum(_held.values())
        if _held and used + GUEST_DESCRIPTORS > budget:
            raise TooManyCalls(
                f"wasi_perl: {len(_held)} calls are running, each counted at "
                f"{GUEST_DESCRIPTORS} host descriptors (the guest table's cap), and one more "
                f"would pass this process's budget of {budget} (half its soft open-file limit); "
                "retry once a call has ended, or raise the limit"
            )
        _held[slot] = GUEST_DESCRIPTORS


def release_descriptors(slot: object) -> None:
    with _descriptor_lock:
        _held.pop(slot, None)


class _Unset:
    def __repr__(self) -> str:
        return "<default>"


UNSET: Any = _Unset()
