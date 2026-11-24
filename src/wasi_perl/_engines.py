"""wasmtime-py's import, the two engines, the compiled modules and the epoch ticker. Adapted
from wasi-python's `_engines.py` (same owner, Apache-2.0): `_wasmtime`, `_engine`, `_module`,
`_compile` and `_epoch_ticker`, trimmed to one binary and no async stack."""

from __future__ import annotations

import atexit
import hashlib
import os
import re
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
# id(module): the module, held so that its id names no other, and its InstancePre
_linked: dict[int, tuple[Any, Any]] = {}
_counts = {"compiled": 0, "loaded": 0, "written": 0}


WASMTIME_FLOOR = 51  # the major version the runner is tested on, and the first without the
# drive-relative escape on Windows (GHSA-f2pq-268w-886c); wasmtime>=51,<52 in pyproject.toml


def wasmtime() -> Any:
    """wasmtime-py, or one ImportError whatever stopped it loading, its own error the cause. A
    wasmtime-py older than 51, which an embedder's environment or `--no-deps` can bring, is
    refused by its version before it is imported."""
    found = wasmtime_version()
    if found is not None:
        try:
            major = int(found.split(".")[0])
        except ValueError:
            major = 0
        if major < WASMTIME_FLOOR:
            raise ImportError(
                f"wasi_perl.run needs wasmtime-py {WASMTIME_FLOOR} (wasmtime>=51,<52); found "
                f"{found}, under which a drive-relative name on Windows reaches outside its "
                "grant (GHSA-f2pq-268w-886c). Install wasmtime>=51,<52."
            )
        if major > WASMTIME_FLOOR:
            raise ImportError(
                f"wasi_perl.run is built and tested on wasmtime-py {WASMTIME_FLOOR} "
                f"(wasmtime>=51,<52); found {found}, whose private C bindings, which the runner "
                "calls, may differ. Install wasmtime>=51,<52."
            )
    try:
        import wasmtime as wt
    except (ImportError, RuntimeError, OSError) as e:
        raise ImportError(
            "wasi_perl.run needs wasmtime-py (wasmtime>=51,<52), which installs with wasi-perl; "
            "reinstall it or install wasmtime directly"
        ) from e
    return wt


_version: list[str | None] = []  # wasmtime-py's version, once read: [] until then


def wasmtime_version() -> str | None:
    """wasmtime-py's installed version, read once per process. The wasmtime-py a process imports
    cannot change after the import, so the version that gated it stays the one that keys its
    compiled modules; reading it again searched every distribution on the path, on every call.
    It is the version in the one `wasmtime-*.dist-info` folder beside the package an import
    loads; with no such folder, or more than one, `importlib.metadata` decides."""
    if _version:
        return _version[0]
    found = _version_beside(_wasmtime_site())
    if found is None:
        from importlib.metadata import PackageNotFoundError, version

        try:
            found = version("wasmtime")
        except PackageNotFoundError:
            found = None  # no version to key a .cwasm by: modules stay in this process
    _version[:] = [found]
    return found


def _wasmtime_site() -> Path | None:
    """The folder that holds the wasmtime package an import loads (the one already imported, if
    any), found without importing it; None when there is no such package."""
    import importlib.util

    try:
        spec = importlib.util.find_spec("wasmtime")
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.origin:
        return None
    return Path(spec.origin).resolve().parent.parent


def _version_beside(site: Path | None) -> str | None:
    """The version that the one `wasmtime-*.dist-info` in `site` names in its METADATA, read
    without importing `importlib.metadata`, one of the slowest imports of a process's first call;
    None when there is not exactly one such folder, or its METADATA names another package or no
    version."""
    if site is None:
        return None
    try:
        infos = [
            e.path
            for e in os.scandir(site)
            if e.name.lower().startswith("wasmtime-") and e.name.endswith(".dist-info")
        ]
    except OSError:
        return None
    if len(infos) != 1:
        return None
    name = version = None
    try:
        with open(os.path.join(infos[0], "METADATA"), encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    break  # the headers end at the first blank line
                key, sep, value = line.partition(":")
                if sep and key == "Name":
                    name = value.strip()
                elif sep and key == "Version":
                    version = value.strip()
    except (OSError, UnicodeDecodeError):
        return None
    if name is None or re.sub(r"[-_.]+", "-", name).lower() != "wasmtime":
        return None
    return version or None


def counts() -> dict[str, int]:
    with _lock:
        return dict(_counts)


def forget() -> None:
    """Drop this process's compiled modules, so the next call goes to the disk cache."""
    with _lock:
        _modules.clear()
        _linked.clear()


def linked(metered: bool, mod: Any) -> Any:
    """`mod` linked once against WASI preview1, as an `InstancePre` every call instantiates from.
    The WASI functions a linker defines read the WASI context of the store they are called in, so
    one linking serves every store of the engine, each with its own grants, arguments and
    environment; and no import is resolved again per call."""
    wt = wasmtime()
    with _lock:
        found = _linked.get(id(mod))
    if found is not None and found[0] is mod:
        return found[1]
    linker = wt.Linker(engine(metered))
    linker.define_wasi()
    pre = linker.instantiate_pre(mod)
    with _lock:
        found = _linked.setdefault(id(mod), (mod, pre))  # a thread that linked first wins
    return found[1]


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
        _linked.clear()
        _modules.clear()
        _engines.clear()


atexit.register(_release_at_exit)


class _Ticker:
    """While at least one call runs, one thread per process advances every engine's epoch once
    per TICK_S of monotonic time elapsed, catching up when a sleep runs long, and counts the ticks
    in `count`, which each call's epoch callback compares with its own deadline. Every call is
    ticked, deadline or not, so Ctrl-C reaches a guest that computes."""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.active = 0
        self.thread: threading.Thread | None = None
        self.count = 0  # ticks advanced so far; read without the lock, a race reads one behind

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
                    self.count += 1
                done = due
                time.sleep(TICK_S / 4)


TICKER = _Ticker()
