"""wasmtime-py's import, the two engines, the compiled modules and the epoch ticker. Adapted
from wasi-python's `_engines.py` (same owner, Apache-2.0): `_wasmtime`, `_engine`, `_module`,
`_compile` and `_epoch_ticker`, trimmed to one binary and no async stack."""

from __future__ import annotations

import atexit
import hashlib
import threading
import time
from pathlib import Path
from typing import Any

from wasi_perl import _artifacts, _cache
from wasi_perl._artifacts import ArtifactsMissing
from wasi_perl._limits import TICK_S

_lock = threading.Lock()  # engines, modules, counts, the ticker's engine list
_engines: dict[bool, Any] = {}
_modules: dict[tuple[bool, str], Any] = {}
_compiling: dict[tuple[bool, str], threading.Lock] = {}
_counts = {"compiled": 0, "loaded": 0, "written": 0}


def wasmtime() -> Any:
    """wasmtime-py, or one ImportError whatever stopped it loading, its own error the cause."""
    try:
        import wasmtime as wt
    except (ImportError, RuntimeError, OSError) as e:
        raise ImportError(
            "wasi_perl.run needs wasmtime-py (wasmtime>=51,<52), which installs with wasi-perl; "
            "reinstall it or install wasmtime directly"
        ) from e
    return wt


def wasmtime_version() -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("wasmtime")
    except PackageNotFoundError:
        return None  # no version to key a .cwasm by: modules stay in this process


def counts() -> dict[str, int]:
    with _lock:
        return dict(_counts)


def forget() -> None:
    """Drop this process's compiled modules, so the next call goes to the disk cache."""
    with _lock:
        _modules.clear()


def engine(metered: bool) -> Any:
    """Two engines per process: fuel metering changes the compiled code. Both take exceptions,
    which perl.wasm's die and eval need, and epoch interruption, which the deadline needs."""
    wt = wasmtime()
    with _lock:
        if metered not in _engines:
            cfg = wt.Config()
            cfg.wasm_exceptions = True
            cfg.epoch_interruption = True
            cfg.consume_fuel = metered
            _engines[metered] = wt.Engine(cfg)
        return _engines[metered]


def module(metered: bool, binary: Path, want: str | None, cache: Path | None) -> Any:
    """The compiled module: from this process, else the disk cache, else compiled from one read
    of `binary` and cached. `want` is a found set's checked digest, which the bytes read must
    have; else the digest is theirs as read now. One thread compiles; others wait for it."""
    wt = wasmtime()
    eng = engine(metered)
    # The digest is the checked one, or the binary's as hashed now (kept per file stamp).
    digest = want if want is not None else _artifacts.digest(binary)
    key = (metered, digest)
    with _lock:
        found = _modules.get(key)
        guard = _compiling.setdefault(key, threading.Lock())
    if found is not None:
        return found
    with guard:
        with _lock:
            found = _modules.get(key)
        if found is not None:
            return found
        mod = _miss(wt, eng, metered, binary, digest, cache)
        with _lock:
            _modules[key] = mod
        return mod


def _miss(
    wt: Any,
    eng: Any,
    metered: bool,
    binary: Path,
    digest: str,
    cache: Path | None,
) -> Any:
    cwasm = None
    version = wasmtime_version()
    if cache is not None and version is not None:
        folder = _cache.folder(cache)
        if folder is not None:
            flavor = "fuel" if metered else "plain"
            cwasm = folder / f"{digest[:32]}-wasmtime{version}-{flavor}.cwasm"
    may_write = False
    if cwasm is not None:
        blob, may_write = _cache.read(cwasm)
        if blob is not None:
            try:
                mod = wt.Module.deserialize(eng, blob)
            except wt.WasmtimeError:
                may_write = True  # stale or foreign: compiled again and replaced
            else:
                with _lock:
                    _counts["loaded"] += 1
                return mod
    # Compiled from one read, held to the digest: a file changed between its hash and its
    # compile is never compiled, so it is never cached under a digest it does not have.
    data = binary.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ArtifactsMissing(f"{binary} changed after it was hashed; it is not compiled")
    mod = wt.Module(eng, data)
    with _lock:
        _counts["compiled"] += 1
    if cwasm is not None and may_write and _cache.write(mod.serialize(), cwasm):
        with _lock:
            _counts["written"] += 1
    return mod


def _release_at_exit() -> None:
    """Free the modules and engines while wasmtime-py's bindings still exist: left to the
    interpreter's own teardown, their finalizers run after the bindings are gone and print an
    ignored TypeError."""
    with _lock:
        _modules.clear()
        _engines.clear()


atexit.register(_release_at_exit)


class _Ticker:
    """While at least one call with a deadline runs, one thread per process advances every
    engine's epoch once per TICK_S of monotonic time elapsed, catching up when a sleep runs long,
    so a deadline of n ticks ends close to n * TICK_S."""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.active = 0
        self.thread: threading.Thread | None = None

    def enter(self) -> None:
        with self.cond:
            self.active += 1
            if self.thread is None:
                self.thread = threading.Thread(
                    target=self._tick, name="wasi_perl-epoch", daemon=True
                )
                self.thread.start()
            self.cond.notify_all()

    def leave(self) -> None:
        with self.cond:
            self.active -= 1

    def _tick(self) -> None:
        while True:
            with self.cond:
                while self.active == 0:
                    self.cond.wait()
            start, done = time.monotonic(), 0
            while True:
                with self.cond:
                    if self.active == 0:
                        break
                due = int((time.monotonic() - start) / TICK_S)
                with _lock:
                    engines = list(_engines.values())
                for _ in range(due - done):
                    for eng in engines:
                        eng.increment_epoch()
                done = due
                time.sleep(TICK_S / 4)


TICKER = _Ticker()
