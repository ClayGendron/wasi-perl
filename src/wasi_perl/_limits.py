"""The checks of `run`'s limits and inputs, each before any work, and the sentinel for an
argument not given. Adapted from wasi-python's `_limits.py` (same owner, Apache-2.0), trimmed to
the four limits and the three inputs this runner takes."""

from __future__ import annotations

import math
import numbers
import operator
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


GUEST_DESCRIPTORS = 256  # the host descriptors each live call reserves: the guest table's cap
WINDOWS_DESCRIPTORS = 4096  # Windows' budget, which has no soft limit to halve


class TooManyCalls(RuntimeError):
    """Raised by `run` before any work when this process's live calls already hold as many host
    descriptors as its budget allows: half its soft RLIMIT_NOFILE on POSIX (4,096 on Windows),
    each call counted at 256. The rest are the embedder's own. Retry once a call has ended."""


_descriptor_lock = threading.Lock()
_descriptors = 0


def descriptor_budget() -> int:
    """The host descriptors this process's live calls may hold in all: half the soft
    RLIMIT_NOFILE, read at each call, so an embedder that raises it is seen."""
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
        return _descriptors


def reserve_descriptors() -> None:
    """Count one more live call's descriptors, or TooManyCalls when the budget is spent."""
    global _descriptors
    budget = descriptor_budget()
    with _descriptor_lock:
        if _descriptors + GUEST_DESCRIPTORS > budget:
            raise TooManyCalls(
                f"wasi_perl: {_descriptors // GUEST_DESCRIPTORS} calls are running, each holding "
                f"up to {GUEST_DESCRIPTORS} host descriptors, and one more would pass this "
                f"process's budget of {budget} (half its soft open-file limit); retry once a "
                "call has ended, or raise the limit"
            )
        _descriptors += GUEST_DESCRIPTORS


def release_descriptors() -> None:
    global _descriptors
    with _descriptor_lock:
        _descriptors -= GUEST_DESCRIPTORS


class _Unset:
    def __repr__(self) -> str:
        return "<default>"


UNSET: Any = _Unset()
