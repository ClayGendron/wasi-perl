"""The runner: one program, one call, in a fresh instance of perl.wasm.

    from wasi_perl import run

    done = run(["-e", 'print "hello\\n"'])
    done.exit_code, done.stdout

Adapted from wasi-python's `runner.py` and `_call.py` (same owner, Apache-2.0): the same
`Defaults`, `Completed` and limits, trimmed to one call with grants and no root, mounts, tools
or sessions. It is safe by default: the guest sees the library and `/dev` read-only and nothing
else unless granted, nothing is writable unless a grant says so, and a call that names no limit
has the ones `defaults()` returns."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import wasi_perl
from wasi_perl import _artifacts, _cache, _engines, _grants, _limits, _stop, _wasmtime_ffi
from wasi_perl._artifacts import Artifacts
from wasi_perl._grants import DEV_GUEST, LIBRARY_GUEST, Grant
from wasi_perl._limits import UNSET

ARGV0 = "perl"
UNMETERED = 2**63 - 1  # the fuel instantiation runs on, before the cap is set
EXIT_STATUS_EXPORT = "wasi_perl_exit_status"
INVALID_EXIT = "exit with invalid exit status"  # wasmtime's refusal of a status of 126 or more
__all__ = ["Completed", "Defaults", "Grant", "run"]


@dataclass(frozen=True)
class Defaults:
    """The limits a call has when it names none: `defaults()` returns the ones in force and
    `set_defaults()` replaces them. Each is checked by `run`'s own rules; `None` is no limit.

    Attributes:
        memory_bytes: The most linear memory a guest may hold, in bytes.
        deadline_s: The wall-clock seconds a call may run, enforced by epoch interruption.
        output_bytes: The most bytes kept per stream; the rest are dropped and the stream is
            named in `truncated`.
    """

    memory_bytes: int | None
    deadline_s: float | None
    output_bytes: int | None

    def __post_init__(self) -> None:
        _limits.check_limits(None, self.deadline_s, self.memory_bytes, self.output_bytes)


DEFAULTS = Defaults(memory_bytes=1 << 30, deadline_s=60.0, output_bytes=16 << 20)
_defaults_lock = threading.Lock()


def defaults() -> Defaults:
    """The `Defaults` a call reads now. On import: 1 GiB of memory, a 60 s deadline and 16 MiB of
    output per stream."""
    return DEFAULTS


def set_defaults(new: Defaults) -> Defaults:
    """Replace the `Defaults` calls read, and return the previous ones. A call reads them once,
    when it is made.

    Raises:
        TypeError: `new` is not a `Defaults`.
    """
    global DEFAULTS
    if not isinstance(new, Defaults):
        raise TypeError(f"set_defaults takes a Defaults; got {type(new).__name__}")
    with _defaults_lock:
        old, DEFAULTS = DEFAULTS, new
    return old


@dataclass(frozen=True)
class Completed:
    """How one program ended: what `run` returns.

    Attributes:
        exit_code: The guest's exit status: 0 at a normal end, the code `exit` gave, 255 for an
            uncaught `die`. `None` when it trapped.
        stdout: What the guest wrote to standard output, up to the output limit.
        stderr: What the guest wrote to standard error, up to the output limit.
        trapped: `None` when the program ended by itself; else "out of fuel", "deadline", or
            wasmtime's words: a trap's kind ("stack overflow", "unreachable") or an error's first
            line. The two fixed strings are promised; wasmtime's words are not.
        fuel_used: The guest's instructions spent, when a `fuel` cap was given and no deadline
            hit; else `None`.
        truncated: The streams ("stdout", "stderr") whose output passed the limit.
    """

    exit_code: int | None
    stdout: bytes
    stderr: bytes
    trapped: str | None
    fuel_used: int | None
    truncated: tuple[str, ...] = ()


class _Stream:
    """One output stream, kept up to its cap. wasmtime calls `write` with each piece the guest
    writes; past the cap the bytes are dropped, never kept, and the full length is still returned,
    so the guest's write succeeds and does not retry. Nothing goes to disk."""

    def __init__(self, name: str, cap: int | None) -> None:
        self.name, self.cap = name, cap
        self.parts: list[bytes] = []
        self.kept = 0
        self.dropped = False

    def write(self, data: bytes) -> None:
        if self.cap is None:
            self.parts.append(data)
        else:
            room = self.cap - self.kept
            if len(data) > room:
                self.dropped = True
                data = data[: max(room, 0)]
            if data:
                self.parts.append(data)
                self.kept += len(data)

    def value(self) -> bytes:
        return b"".join(self.parts)


def _writer(stream: _Stream) -> Any:
    def write(data: bytes) -> int:
        size = len(data)
        stream.write(data)
        return size

    return write


def _set_stdin(wt: Any, wasi: Any, data: bytes) -> None:
    """stdin as bytes, through the C API's `wasi_config_set_stdin_bytes`, which wasmtime-py does
    not wrap: no file holds it. The vector is handed over, and wasmtime frees it."""
    if not data:
        return  # wasmtime's default stdin is empty
    _wasmtime_ffi.set_stdin_bytes(wasi, data)


def _first_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[0] if lines else "trap"


def _error_words(text: str) -> str:
    """A wasmtime error in a few words: its cause's last line when it names one (the heading is
    "error while executing at wasm backtrace:" for every error from inside the guest), else its
    first line."""
    head, sep, cause = text.partition("Caused by:")
    lines = [line.strip() for line in cause.strip().splitlines() if line.strip()] if sep else []
    return lines[-1] if lines else _first_line(head)


def _exit_status(wt: Any, store: Any, instance: Any) -> int | None:
    """The status the guest passed to exit, from its `wasi_perl_exit_status` export, or None when
    it has none, never called exit (-1), or cannot answer. wasmtime refuses `proc_exit` with a
    status of 126 or more, so perl.wasm keeps the status itself (Perl's uncaught die is 255). It
    is read only after a normal exit or that refusal, never after a trap: a store a trap stopped
    is not asked to run more guest code."""
    func = instance.exports(store).get(EXIT_STATUS_EXPORT)
    if not isinstance(func, wt.Func):
        return None
    try:
        status = func(store)
    except (wt.Trap, wt.WasmtimeError):
        return None
    return status if isinstance(status, int) and 0 <= status <= 255 else None


def _trap_name(wt: Any, e: Any) -> str:
    if e.trap_code == wt.TrapCode.OUT_OF_FUEL:
        return "out of fuel"
    if e.trap_code == wt.TrapCode.INTERRUPT:
        return "deadline"
    if e.trap_code is not None:
        return str(e.trap_code.name).lower().replace("_", " ")
    return _first_line(e.message)


def _runtime_folders(found: Artifacts) -> tuple[Any, ...]:
    """The folders a writable grant may not reach: the set's, this package's and wasmtime-py's,
    and the host Python's own code (its stdlib, its site-packages and scripts, and the user's
    site-packages it imports at start-up), since a guest that rewrote any of them would change
    what the next call or the next host process runs. As wasi-python's rule."""
    import site
    import sysconfig

    import wasmtime

    folders = [found.perl.parent, found.manifest.parent, found.sums.parent, found.lib, found.dev]
    folders += [Path(wasi_perl.__file__).parent, Path(wasmtime.__file__).parent]
    paths = sysconfig.get_paths()
    keys = ("stdlib", "platstdlib", "purelib", "platlib", "scripts")
    own = [Path(paths[k]) for k in keys if k in paths]
    if site.ENABLE_USER_SITE:
        own.append(Path(site.getusersitepackages()))
    # The base interpreter: the folder of the real executable (a venv's python is a link to it)
    # and the base prefix's scripts, which a venv's own paths above do not name.
    own.append(Path(os.path.realpath(sys.executable)).parent)
    own.append(Path(sys.base_prefix) / ("Scripts" if os.name == "nt" else "bin"))
    folders += [d for d in own if d.is_dir()]  # one not there holds no code to rewrite
    return tuple(dict.fromkeys(Path(os.path.abspath(f)) for f in folders))


def _runtime_files(found: Artifacts) -> list[Path]:
    """The files a writable grant may not hold a second name of: wasmtime-py's (its native
    library above all), this package's, and the artifact set's, the library tree included."""
    import wasmtime

    files: list[Path] = []
    for top in (Path(wasmtime.__file__).parent, Path(wasi_perl.__file__).parent):
        files += [p for p in top.rglob("*") if p.is_file()]
    files += [found.perl, found.manifest, found.sums, found.dev / "null"]
    files += [p for p in found.lib.rglob("*") if p.is_file()]
    return files


def run(
    argv: Iterable[str],
    *,
    grants: Iterable[Grant] = (),
    env: Mapping[str, str] | None = None,
    stdin: bytes | bytearray | memoryview | None = None,
    cwd: str | None = None,
    fuel: int | None = None,
    deadline_s: float | None = UNSET,
    memory_bytes: int | None = UNSET,
    output_bytes: int | None = UNSET,
    cache_dir: str | os.PathLike[str] | bool | None = None,
    artifacts: Artifacts | None = None,
) -> Completed:
    """Run one Perl program in a fresh, sealed perl.wasm and return how it ended.

    The guest sees the library read-only at /usr/local/lib/perl5, an empty `null` read-only at
    /dev, and the folders `grants` names, nothing else. Every argument is checked before any
    work. Nothing the guest does makes `run` raise.

    Args:
        argv: The arguments after `perl`, as for `perl`: `["-ne", "print if /x/", "/data/f"]`.
        grants: Host folders the guest sees, each a `Grant`, read-only unless it says
            `writable=True`.
        env: The guest's whole environment (plus `PWD` from `cwd`); nothing of the host's.
        stdin: The guest's standard input, whole; `None` is empty.
        cwd: The guest's working folder: a guest path inside a grant. `None` leaves it at /,
            which no grant serves, so relative paths fail.
        fuel: A cap on the guest's executed instructions; past it the call ends "out of fuel".
        deadline_s: Wall-clock seconds; past them the call ends "deadline". Default 60.
        memory_bytes: The most linear memory the guest may hold. Default 1 GiB.
        output_bytes: The most bytes kept per stream. Default 16 MiB.
        cache_dir: Where compiled modules are kept as .cwasm files, a folder only you and the
            runner write. `None` or `True`: `$WASI_PERL_CACHE`, else the user's cache folder;
            `False`: none on disk.
        artifacts: The artifact set; `None` finds it and checks it against its SHA256SUMS. A set
            you pass is used as you give it.

    Raises:
        TypeError: An argument has the wrong type.
        ValueError: An argument is out of range, or a grant breaks a rule.
        ArtifactsMissing: There is no usable artifact set.
        ImportError: wasmtime-py cannot load, or is older than 51.
        OSError: A host filesystem call failed while the call was prepared.
        TooManyCalls: This process's live calls already hold its budget of host descriptors.
        KeyboardInterrupt: Ctrl-C, on the main thread: the guest is stopped first.
    """
    wt = _engines.wasmtime()
    d = DEFAULTS
    inputs = _limits.check_inputs(argv, env, stdin)
    limits = _limits.check_limits(
        fuel,
        d.deadline_s if deadline_s is UNSET else deadline_s,
        d.memory_bytes if memory_bytes is UNSET else memory_bytes,
        d.output_bytes if output_bytes is UNSET else output_bytes,
    )
    if artifacts is not None and not isinstance(artifacts, Artifacts):
        raise TypeError(f"artifacts must be an Artifacts, or None; got {type(artifacts).__name__}")
    asked = _grants.check(grants)
    cwd_guest = _grants.check_cwd(cwd)
    cache = _cache.given(cache_dir)
    if artifacts is None:
        found, want = _artifacts.checked()
    else:
        found, want = artifacts, None
    writing = any(g.writable for g in asked)
    # A rule's inputs are found only when a grant can break the rule: the cache rule binds every
    # grant, the runtime rules only a writable one. A call with no grant has nothing to judge.
    placed, cwd_guest = _grants.judge(
        asked,
        cwd_guest,
        caches=_cache.every(cache) if asked else (),
        runtime=_runtime_folders(found) if writing else (),
        runtime_files=_runtime_files(found) if writing else (),
    )
    guest_env = dict(inputs.env)
    if cwd_guest is not None:
        guest_env["PWD"] = cwd_guest
    slot = object()  # this call's reservation, released in the finally whatever interrupts
    try:
        _limits.reserve_descriptors(slot)  # TooManyCalls, before any work
        _grants.hold(placed, slot)  # until the store, which holds the grants open, is closed
        try:
            metered = limits.fuel is not None
            module = _engines.module(metered, found.perl, want, cache.folder)
            engine = _engines.engine(metered)
            return _call(wt, engine, module, found, placed, inputs, guest_env, limits)
        finally:
            _grants.let_go(slot)
    finally:
        _limits.release_descriptors(slot)


def _set_up(
    wt: Any,
    store: Any,
    engine: Any,
    found: Artifacts,
    placed: tuple[_grants.Placed, ...],
    inputs: _limits.Inputs,
    guest_env: dict[str, str],
    out: _Stream,
    err: _Stream,
) -> Any:
    """The store's WASI context and the linker. wasmtime opens each grant here, and raises
    WasmtimeError for a folder it cannot open; the caller reports that as the call's trap."""
    wasi = wt.WasiConfig()
    wasi.argv = [ARGV0, *inputs.argv]
    wasi.env = sorted(guest_env.items())
    wasi.stdout_custom = _writer(out)
    wasi.stderr_custom = _writer(err)
    _set_stdin(wt, wasi, inputs.stdin)
    wasi.preopen_dir(str(found.lib), LIBRARY_GUEST, False)
    wasi.preopen_dir(str(found.dev), DEV_GUEST, False)
    for p in placed:
        wasi.preopen_dir(str(p.host), p.guest, p.writable)
    store.set_wasi(wasi)
    linker = wt.Linker(engine)
    linker.define_wasi()
    return linker


def _call(
    wt: Any,
    engine: Any,
    module: Any,
    found: Artifacts,
    placed: tuple[_grants.Placed, ...],
    inputs: _limits.Inputs,
    guest_env: dict[str, str],
    limits: _limits.Limits,
) -> Completed:
    metered = limits.fuel is not None
    out = _Stream("stdout", limits.output_bytes)
    err = _Stream("stderr", limits.output_bytes)
    stop = _stop.Stop(None if limits.deadline_s is None else _limits.ticks(limits.deadline_s))
    store = wt.Store(engine)
    try:
        if metered:
            store.set_fuel(UNMETERED)
        if limits.memory_bytes is not None:
            store.set_limits(memory_size=limits.memory_bytes)
        try:
            linker = _set_up(wt, store, engine, found, placed, inputs, guest_env, out, err)
        except wt.WasmtimeError as e:  # a host failure at the guest's start: its trap
            return Completed(None, b"", b"", _error_words(str(e)), None, ())
        _stop.arm(store, stop)
        store.set_epoch_deadline(stop.first())
        _engines.TICKER.enter()
        try:
            with _stop.watching_ctrl_c(stop):
                exit_code, trapped, fuel_used = _drive(wt, store, linker, module, limits)
        finally:
            _engines.TICKER.leave()
    finally:
        store.close()
    if stop.interrupted:
        raise KeyboardInterrupt  # Ctrl-C ended the guest; the store is closed
    truncated = tuple(s.name for s in (out, err) if s.dropped)
    return Completed(exit_code, out.value(), err.value(), trapped, fuel_used, truncated)


def _drive(
    wt: Any, store: Any, linker: Any, module: Any, limits: _limits.Limits
) -> tuple[int | None, str | None, int | None]:
    """Instantiate and run the guest; how it ended: (exit code, trap, fuel used)."""
    metered = limits.fuel is not None
    exit_code: int | None = None
    trapped: str | None = None
    fuel_used: int | None = None
    is_metered = False
    instance = None
    read_status = False  # whether the guest's own record of its exit status is asked for
    try:
        instance = linker.instantiate(store, module)
        if metered:
            store.set_fuel(limits.fuel)
            is_metered = True
        instance.exports(store)["_start"](store)
        exit_code = 0
    except wt.ExitTrap as e:
        exit_code, read_status = e.code, True
    except wt.Trap as e:  # fuel, the deadline, or the guest's own fault: never an exit
        trapped = _stop.word(e.message) or _trap_name(wt, e)
        e.__traceback__ = None
    except wt.WasmtimeError as e:
        text = str(e)
        trapped = _stop.word(text) or _error_words(text)
        read_status = trapped not in (_stop.DEADLINE, _stop.INTERRUPTED) and INVALID_EXIT in text
        e.__traceback__ = None
    if metered and trapped not in (_stop.DEADLINE, _stop.INTERRUPTED):
        # Read before the status export runs, which spends a little fuel of its own.
        fuel_used = limits.fuel - store.get_fuel() if is_metered else 0  # type: ignore[operator]
    if read_status and instance is not None:
        # The guest may have reached its exit with no fuel or time left; the export gets a
        # budget of its own, so it answers rather than trapping. A trap never reaches here.
        if is_metered:
            store.set_fuel(UNMETERED)
        store.set_epoch_deadline(_limits.NO_DEADLINE)
        status = _exit_status(wt, store, instance)
        if status is not None:
            exit_code, trapped = status, None
    return exit_code, trapped, fuel_used
