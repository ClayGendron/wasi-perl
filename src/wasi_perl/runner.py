"""The runner: one program, one call, in a fresh instance of perl.wasm.

    from wasi_perl import run

    done = run(["-e", 'print "hello\\n"'])
    done.exit_code, done.stdout

Adapted from wasi-python's `runner.py` and `_call.py` (same owner, Apache-2.0): the same
`Defaults`, `Completed` and limits, trimmed to one call with grants and mounts and no root,
tools or sessions. From `_call.py` at f9bf678 come the mounts' glue: `_Call`, the shared host
function (`_shared`), the mount table's host (`_Context`) and the thread's stack of calls,
trimmed of the door, the host-side `poll_oneoff`, `cancel` and the guest's own thread, which
wasi-perl does not have: here the guest runs on the calling thread, so a provider is called
directly. It is safe by default: the guest sees the library and `/dev` read-only and nothing
else unless granted or mounted, nothing is writable unless a grant or a mount says so, and a
call that names no limit has the ones `defaults()` returns."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import wasi_perl
from wasi_perl import _artifacts, _cache, _engines, _grants, _limits, _outputs, _stop
from wasi_perl._artifacts import Artifacts
from wasi_perl._fs_memory import EBADF, EIO, STAGING_CEILING
from wasi_perl._grants import DEV_GUEST, LIBRARY_GUEST, Grant
from wasi_perl._limits import UNSET
from wasi_perl.mounts import FileRecord, Mount

if TYPE_CHECKING:
    from wasi_perl._fs_table import _Files

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
        file_calls: The most provider calls a call with mounts makes; then it ends "out of file
            calls".
    """

    memory_bytes: int | None
    deadline_s: float | None
    output_bytes: int | None
    file_calls: int | None = 10_000

    def __post_init__(self) -> None:
        _limits.check_limits(None, self.deadline_s, self.memory_bytes, self.output_bytes)
        _limits.integer_limit("file_calls", self.file_calls)


DEFAULTS = Defaults(memory_bytes=1 << 30, deadline_s=60.0, output_bytes=16 << 20)
_defaults_lock = threading.Lock()


def defaults() -> Defaults:
    """The `Defaults` a call reads now. On import: 1 GiB of memory, a 60 s deadline, 16 MiB of
    output per stream and 10,000 provider calls."""
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
        trapped: `None` when the program ended by itself; else "out of fuel", "deadline",
            "out of file calls", or wasmtime's words: a trap's kind ("stack overflow",
            "unreachable") or an error's first line. The three fixed strings are promised;
            wasmtime's words are not.
        fuel_used: The guest's instructions spent, when a `fuel` cap was given and no deadline
            hit; else `None`.
        truncated: The streams ("stdout", "stderr") whose output passed the limit.
        files: For a call with mounts, its provider calls, faults and refused writes; else
            `None`.
    """

    exit_code: int | None
    stdout: bytes
    stderr: bytes
    trapped: str | None
    fuel_used: int | None
    truncated: tuple[str, ...] = ()
    files: FileRecord | None = None  # the mounts' record, for a call with mounts


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


def _set_stdin(wt: Any, wasi: Any, data: bytes) -> None:
    """stdin as bytes, through the C API's `wasi_config_set_stdin_bytes`, which wasmtime-py does
    not wrap: no file holds it. The vector is handed over, and wasmtime frees it."""
    if not data:
        return  # wasmtime's default stdin is empty
    from wasi_perl import _wasmtime_ffi

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

    wasmtime = _engines.wasmtime()  # with the seam it gates, so its import is in the scan

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


def _runtime_files(found: Artifacts) -> _grants.RuntimeFiles:
    """The files a writable grant may not hold a second name of: wasmtime-py's (its native
    library above all), this package's, and the artifact set's, the library tree included. The
    scan is kept, and made again when a folder in it changes. wasmtime-py is imported through
    its gate, which imports the engine seam too, so the scan sees this package's files as every
    call leaves them."""
    wasmtime = _engines.wasmtime()

    tops = (Path(wasmtime.__file__).parent, Path(wasi_perl.__file__).parent, found.lib)
    singles = (found.perl, found.manifest, found.sums, found.dev / "null")
    return _grants.runtime_scan(tops, singles)


def run(
    argv: Iterable[str],
    *,
    grants: Iterable[Grant] = (),
    mounts: Iterable[Mount] = (),
    env: Mapping[str, str] | None = None,
    stdin: bytes | bytearray | memoryview | None = None,
    cwd: str | None = None,
    fuel: int | None = None,
    deadline_s: float | None = UNSET,
    memory_bytes: int | None = UNSET,
    output_bytes: int | None = UNSET,
    cache_dir: str | os.PathLike[str] | bool | None = None,
    artifacts: Artifacts | None = None,
    max_file_calls: int | None = UNSET,
) -> Completed:
    """Run one Perl program in a fresh, sealed perl.wasm and return how it ended.

    The guest sees the library read-only at /usr/local/lib/perl5, an empty `null` read-only at
    /dev, the folders `grants` names and the providers' files `mounts` names, nothing else.
    Every argument is checked before any work. Nothing the guest does makes `run` raise; a
    provider's own BaseException that is not an Exception does, once the guest is stopped.

    Args:
        argv: The arguments after `perl`, as for `perl`: `["-ne", "print if /x/", "/data/f"]`.
        grants: Host folders the guest sees, each a `Grant`, read-only unless it says
            `writable=True`.
        mounts: Providers whose files the guest sees at their guest paths, each a `Mount`,
            served from your code, read-only unless it says `writable=True`.
        env: The guest's whole environment (plus `PWD` from `cwd`); nothing of the host's.
        stdin: The guest's standard input, whole; `None` is empty.
        cwd: The guest's working folder: a guest path inside a grant or a mount. `None` leaves
            it at /, which no grant serves, so relative paths fail unless a mount is at /. In a
            mount it is checked by its text; the guest's start-up asks the provider, and stays
            at / if the provider does not answer a folder.
        fuel: A cap on the guest's executed instructions; past it the call ends "out of fuel".
        deadline_s: Wall-clock seconds; past them the call ends "deadline". Default 60.
        memory_bytes: The most linear memory the guest may hold. Default 1 GiB.
        output_bytes: The most bytes kept per stream. Default 16 MiB.
        cache_dir: Where compiled modules are kept as .cwasm files, a folder only you and the
            runner write. `None` or `True`: `$WASI_PERL_CACHE`, else the user's cache folder;
            `False`: none on disk.
        artifacts: The artifact set; `None` finds it and checks it against its SHA256SUMS. A set
            you pass is used as you give it.
        max_file_calls: The most provider calls a call with mounts makes; then it ends "out
            of file calls". Default: `defaults().file_calls`, 10,000. `None` means no limit.

    Raises:
        TypeError: An argument has the wrong type.
        ValueError: An argument is out of range, or a grant or a mount breaks a rule.
        ArtifactsMissing: There is no usable artifact set.
        ImportError: wasmtime-py cannot load, or is older than 51.
        OSError: A host filesystem call failed while the call was prepared.
        TooManyCalls: This process's live calls already hold its budget of host descriptors.
        KeyboardInterrupt: Ctrl-C, on the main thread: the guest is stopped first.
        BaseException: A provider's own, that is not an Exception, raised once the guest is
            stopped; nothing is written back.
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
    mounted = _grants.check_mounts(mounts, asked)
    file_calls = _limits.integer_limit(
        "max_file_calls", d.file_calls if max_file_calls is UNSET else max_file_calls
    )
    cwd_guest = _grants.check_cwd(cwd, asked, mounted)
    cache = _cache.given(cache_dir)
    if artifacts is None:
        found, want = _artifacts.checked()
    else:
        found, want = artifacts, None
    writing = any(g.writable for g in asked)
    # A rule's inputs are found only when a grant can break the rule: the cache rule binds every
    # grant, the runtime rules only a writable one. A call with no grant has nothing to judge; a
    # mount has no host folder, so no host rule binds it, writable or not.
    placed, cwd_guest = _grants.judge(
        asked,
        cwd_guest,
        caches=_cache.every(cache) if asked else (),
        runtime=_runtime_folders(found) if writing else (),
        runtime_files=_runtime_files(found) if writing else (),
        mounts=tuple(m.guest for m in mounted),
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
            pre = _engines.linked(metered, module, mounts=bool(mounted))
            return _call(
                wt, engine, pre, found, placed, inputs, guest_env, limits, mounted, file_calls
            )
        finally:
            _grants.let_go(slot)
    finally:
        _limits.release_descriptors(slot)


def _set_up(
    wt: Any,
    store: Any,
    found: Artifacts,
    placed: tuple[_grants.Placed, ...],
    inputs: _limits.Inputs,
    guest_env: dict[str, str],
    keys: tuple[int, int],
    mounts: tuple[Mount, ...] = (),
    placeholder: str | None = None,
) -> None:
    """The store's WASI context, which the module's one linking reads for this store alone.
    stdout and stderr go to the streams `keys` names in `_outputs`, with no free. wasmtime opens
    each grant here, and raises WasmtimeError for a folder it cannot open; the caller reports
    that as the call's trap. Each mount is preopened last, in the order given, read-only on the
    call's empty placeholder."""
    from wasi_perl import _wasmtime_ffi

    wasi = wt.WasiConfig()
    wasi.argv = [ARGV0, *inputs.argv]
    wasi.env = sorted(guest_env.items())
    _wasmtime_ffi.set_custom_outputs(wasi, *keys)
    _set_stdin(wt, wasi, inputs.stdin)
    wasi.preopen_dir(str(found.lib), LIBRARY_GUEST, False)
    wasi.preopen_dir(str(found.dev), DEV_GUEST, False)
    for p in placed:
        wasi.preopen_dir(str(p.host), p.guest, p.writable)
    for m in mounts:
        assert placeholder is not None
        wasi.preopen_dir(placeholder, m.guest, False)
    store.set_wasi(wasi)


# The placeholder's folder is held by a descriptor where the system can: made, entered and
# removed through it, so no name a guest replaced is followed. Windows has neither O_NOFOLLOW nor
# dir_fd; there the folder cannot be renamed while the call's store holds the placeholder open,
# and its identity is checked by name before each removal.
_BY_DESCRIPTOR = (
    os.mkdir in os.supports_dir_fd
    and os.rmdir in os.supports_dir_fd
    and hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
)


class _Placeholder:
    """The call's placeholder: an empty, read-only folder `mount` in a fresh folder of this
    user's own (0700) in the system's temporary folder. Every mount's preopen is made on it, so
    wasi-libc's preopen scan and the guest's own table find a mount as they find a grant; every
    path call on such a preopen is the table's, so nothing reads its entries.

    The temporary folder may be inside a writable grant, so a guest may rename the call's folder
    away and put a file, a folder or a link of its name in its place. So the folder is opened
    once, without following a link, and checked to be the one `mkdtemp` made; `mount` is made
    and removed through that descriptor. The folder itself is removed only while its name still
    gives that identity. Every removal is an `rmdir`, never a tree removal: it removes only an
    empty folder, and one that cannot be removed is left, with the call's result unchanged."""

    def __init__(self) -> None:
        self.folder = tempfile.mkdtemp(prefix="wasi-perl-")
        self.path = os.path.join(self.folder, "mount")
        self.fd: int | None = None
        self.identity: tuple[int, int] | None = None
        try:
            st = os.lstat(self.folder)
            self.identity = (st.st_dev, st.st_ino)
            if _BY_DESCRIPTOR:
                self.fd = os.open(self.folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                held = os.fstat(self.fd)
                if (held.st_dev, held.st_ino) != self.identity:
                    raise OSError(f"{self.folder} was replaced before the runner could hold it")
                os.mkdir("mount", 0o500, dir_fd=self.fd)
            else:
                os.mkdir(self.path, 0o700)
                os.chmod(self.path, 0o500)
        except BaseException:
            self.remove()
            raise

    def _ours(self) -> bool:
        """Whether the folder's name still gives the folder `mkdtemp` made, not a link."""
        try:
            st = os.lstat(self.folder)
        except OSError:
            return False
        return stat.S_ISDIR(st.st_mode) and (st.st_dev, st.st_ino) == self.identity

    def remove(self) -> None:
        try:
            if self.fd is not None:
                try:
                    os.rmdir("mount", dir_fd=self.fd)  # the runner's own, wherever it is now
                except OSError:
                    pass
            elif self._ours():
                if os.name == "nt":  # a read-only folder cannot be removed there
                    try:
                        os.chmod(self.path, stat.S_IWRITE)
                    except OSError:
                        pass
                try:
                    os.rmdir(self.path)
                except OSError:
                    pass
            if self._ours():
                try:
                    os.rmdir(self.folder)
                except OSError:
                    pass
        finally:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None


def _call(
    wt: Any,
    engine: Any,
    pre: Any,
    found: Artifacts,
    placed: tuple[_grants.Placed, ...],
    inputs: _limits.Inputs,
    guest_env: dict[str, str],
    limits: _limits.Limits,
    mounts: tuple[Mount, ...] = (),
    file_calls: int | None = None,
) -> Completed:
    metered = limits.fuel is not None
    out = _Stream("stdout", limits.output_bytes)
    err = _Stream("stderr", limits.output_bytes)
    stop = _stop.Stop(None if limits.deadline_s is None else _limits.ticks(limits.deadline_s))
    store = wt.Store(engine)
    placeholder: _Placeholder | None = None
    table: _Files | None = None
    call: _Call | None = None
    files: FileRecord | None = None
    # The streams' keys, dropped here once the store is closed, however the call ends: wasmtime
    # is given no free, so it runs nothing of Python after the call has returned.
    keys = _outputs.take(out, err)
    try:
        if metered:
            store.set_fuel(UNMETERED)
        if limits.memory_bytes is not None:
            store.set_limits(memory_size=limits.memory_bytes)
        if mounts:
            from wasi_perl._fs_table import _Files

            placeholder = _Placeholder()
            staging = STAGING_CEILING if limits.memory_bytes is None else limits.memory_bytes
            # The library, /dev and the grants are wasmtime's preopens from 3; the mounts' come
            # after every other.
            others = [LIBRARY_GUEST, DEV_GUEST, *(p.guest for p in placed)]
            first = 3 + len(others)
            numbers = [(m, first + i) for i, m in enumerate(mounts)]
            context = _Context()
            table = _Files(numbers, others, file_calls, staging, metered, engine, context=context)
            call = _Call(store, table, stop)
        try:
            where = None if placeholder is None else placeholder.path
            _set_up(wt, store, found, placed, inputs, guest_env, keys, mounts, where)
        except wt.WasmtimeError as e:  # a host failure at the guest's start: its trap
            files = None if table is None else table.record()
            return Completed(None, b"", b"", _error_words(str(e)), None, (), files)
        _stop.arm(store, stop)
        store.set_epoch_deadline(stop.first())
        _engines.TICKER.enter()
        try:
            with _stop.watching_ctrl_c(stop):
                exit_code, trapped, fuel_used = _drive(wt, store, pre, limits, call)
        finally:
            _engines.TICKER.leave()
        if table is not None:
            # What the guest wrote is handed over at every ending that returns a result, after
            # the fuel is read; a call that raises never gets here.
            table.walk()
            files = table.record()
    finally:
        try:
            store.close()
        finally:
            _outputs.drop(keys)
        if table is not None:  # the rest dropped now
            table.release()
            table.context.leave()  # type: ignore[attr-defined]
        if placeholder is not None:
            placeholder.remove()
    if stop.interrupted:
        raise KeyboardInterrupt  # Ctrl-C ended the guest; the store is closed
    truncated = tuple(s.name for s in (out, err) if s.dropped)
    return Completed(exit_code, out.value(), err.value(), trapped, fuel_used, truncated, files)


def _drive(
    wt: Any, store: Any, pre: Any, limits: _limits.Limits, call: _Call | None = None
) -> tuple[int | None, str | None, int | None]:
    """Instantiate and run the guest; how it ended: (exit code, trap, fuel used). A call with
    mounts is on this thread's stack while its guest runs, its trampoline bound after the
    instance and before the fuel cap. What a host function caught and recorded comes first: a
    trap or an error is the call's result, and any other exception (a provider's own
    BaseException) is raised here."""
    metered = limits.fuel is not None
    exit_code: int | None = None
    trapped: str | None = None
    fuel_used: int | None = None
    is_metered = False
    instance = None
    read_status = False  # whether the guest's own record of its exit status is asked for
    stack = _stack()
    if call is not None:
        stack.append(call)
    try:
        instance = pre.instantiate(store)
        if call is not None:
            call.table.bind(wt, store, _engines.linker(store.engine), instance)
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
    finally:
        if call is not None:
            stack.remove(call)
    cause = None if call is None else call.stopped
    ended = None if call is None else call.ended
    if cause is not None:
        exit_code, trapped, read_status = None, None, False
        if isinstance(cause, wt.Trap):
            trapped = _stop.word(cause.message) or _trap_name(wt, cause)
        elif isinstance(cause, wt.WasmtimeError):
            text = str(cause)
            trapped = _stop.word(text) or _error_words(text)
        else:
            raise cause
        cause.__traceback__ = None  # a reported stop's frames hold this call
    elif ended == _stop.FILE_CALLS:  # the file-call limit stopped it
        exit_code, trapped, read_status = None, ended, False
    if metered and trapped not in (_stop.DEADLINE, _stop.INTERRUPTED, _stop.FILE_CALLS):
        held = None if call is None else call.table.stop_fuel
        if held is not None and ended is None and cause is held[0]:
            # A forward's own trap or error, or an ABI error a served call raised: the count
            # read then is the guest's, as when wasmtime's WASI meets it directly.
            fuel_used = limits.fuel - held[1]  # type: ignore[operator]
        elif cause is not None:
            fuel_used = None
        else:
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


# The mounts' glue. Each shadowed call is one host function per engine, which finds its call on
# this thread's stack and serves it only when the caller's store is that call's.

_calls = threading.local()


def _stack() -> list[_Call]:
    """This thread's stack of calls with mounts: a provider may itself call `run`, so a call may
    run inside another's host function, on the same thread."""
    stack: list[_Call] | None = getattr(_calls, "stack", None)
    if stack is None:
        stack = _calls.stack = []
    return stack


class _Call:
    """One call with mounts, as its host functions find it on the thread's stack: its store's
    context address, its table, and its stop, where the first exception a host function caught
    and the name the call's end was given are kept for the epoch callback."""

    def __init__(self, store: Any, table: _Files, watch: _stop.Stop) -> None:
        from wasi_perl import _wasmtime_ffi as ffi

        self.context = ffi.context_address(store)
        self.table = table
        self.watch = watch
        context = table.context
        if isinstance(context, _Context):
            context.enter(self)

    @property
    def stopped(self) -> BaseException | None:
        return self.watch.stopped

    @stopped.setter
    def stopped(self, cause: BaseException | None) -> None:
        self.watch.stopped = cause

    @property
    def ended(self) -> str | None:
        return self.watch.ended

    @ended.setter
    def ended(self, name: str | None) -> None:
        self.watch.ended = name

    def files(self, caller: Any, name: str, *args: int) -> int:
        """One of the 29 shadowed calls."""
        return self.table(caller, name, *args)

    def stop(self, caller: Any, cause: BaseException) -> None:
        """Record the cause and stop the guest: an epoch deadline of 0, set through the caller's
        context, meets the epoch callback at its next function entry, with nothing raised
        through wasmtime-py."""
        if self.stopped is None:
            self.stopped = cause
        _epoch_now(caller)


def _shared(name: str) -> Callable[..., int]:
    """A host function defined once per engine, shadowing the preview1 call `name`. It serves
    the call on top of this thread's stack, and only when the caller is that call's store:
    EBADF otherwise, and EIO once its call has stopped or ended. It never raises into
    wasmtime-py, whose exceptions travel by a process-wide slot."""
    import wasmtime as wt

    from wasi_perl import _wasmtime_ffi as ffi

    wasmtime_errors = (wt.Trap, wt.WasmtimeError)

    def function(caller: Any, *args: int) -> int:
        call = None
        try:
            stack = _stack()
            call = stack[-1] if stack else None
            if call is None or ffi.context_address(caller) != call.context:
                return EBADF
            # Ctrl-C comes before the guard below: a noted Ctrl-C stops the guest even for a
            # call the epoch callback has ended.
            if call.watch.interrupted:
                _epoch_now(caller)
                return EIO
            if call.stopped is not None or call.ended is not None:
                return EIO  # no host function runs for a stopped or ended call
            answer: int = call.files(caller, name, *args)
            if call.watch.interrupted:  # Ctrl-C during a provider call, noted and deferred
                _epoch_now(caller)
            return answer
        except BaseException as cause:
            try:
                if (
                    call is not None
                    and call.ended is not None
                    and isinstance(cause, wasmtime_errors)
                ):
                    # While the guest runs, `ended` is set only where the runner also stops the
                    # guest in the same step: the epoch callback, on a path that returns an
                    # error; or the file-call limit, which sets the epoch deadline to 0 before
                    # it returns. So a Trap or WasmtimeError caught here after `ended` is set is
                    # the runner's own stop surfacing at a forward's trampoline entry. It is not
                    # a stop: `_drive` names the run from `ended`. Any other exception, or one
                    # caught before `ended`, is a stop.
                    _epoch_now(caller)
                elif call is not None:
                    call.stop(caller, cause)
            except BaseException:
                pass
            return EIO

    return function


def _epoch_now(caller: Any) -> None:
    """An epoch deadline of 0 for the caller's store: its guest meets its epoch callback at its
    next function entry."""
    from wasi_perl import _wasmtime_ffi as ffi

    ffi.set_epoch_deadline(caller, 0)


class _Context:
    """The runner's answers to the mount table's nine questions (`mounts._CallContext`): the
    four process-wide ones, and the five of the call the table serves, which `enter` and `leave`
    set and clear with the call's life; with no call they answer "no call"."""

    def __init__(self) -> None:
        self.call: _Call | None = None

    def enter(self, call: _Call) -> None:
        self.call = call

    def leave(self) -> None:
        self.call = None

    def ino_of(self, path: str) -> int:
        return _grants._siphash13(path.encode())

    def trampoline(self, wt: Any, engine: Any) -> Any:
        return _engines.trampoline(engine)

    def exiting(self) -> bool:
        return sys.is_finalizing()

    def ctrl_c_pending(self) -> bool:
        call = self.call
        return call is not None and call.watch.interrupted

    def stop_now(self, caller: Any) -> None:
        _epoch_now(caller)

    def cancelled(self) -> bool:
        return False  # wasi-perl has no cancel; the gate's order stays wasi-python's

    def deadline_passed(self) -> bool:
        call = self.call
        stop_at = None if call is None else call.watch.stop_at
        return stop_at is not None and _engines.TICKER.count >= stop_at

    def name_end(self, reason: str) -> None:
        call = self.call
        if call is not None and call.ended is None:
            call.ended = reason

    def on_home(self, fn: Callable[..., Any], *args: Any) -> Any:
        return fn(*args)  # the guest runs on the calling thread: the provider's thread
