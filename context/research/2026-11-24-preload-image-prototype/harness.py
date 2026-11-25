"""Research prototype, not shipped: the making of an image and the calls from it, on wasmtime-py
and the runner of today's dev.

- `make(code)` runs the reactor once, as spec 022's Decision 5 lists: a fresh store, `_initialize`,
  then `prepare` with the argv `perl -e <code>`, the library and /dev preopened as `run` does, then
  the whole linear memory copied once into `bytes`. The reactor it runs is the checked one with
  one more export, of its mutable global, so the host can read the stack pointer after `prepare`
  (`image_module.with_global_export`); its code is the reactor's.
- `image_modules(made)` writes the image module (`image_module.write`) and compiles it, once per
  engine flavour.
- `run_plain(argv, ...)` is `wasi_perl.run`, unchanged. `run_image(argv, mods, ...)` is the same
  `wasi_perl.run`, with the runner's module lookup answering the image module and `_start` read
  as `run`: every check, limit, grant and stream of the runner applies to both.
"""

from __future__ import annotations

import contextlib
import ctypes
import dataclasses
import hashlib
import sys
import time
from pathlib import Path
from typing import Any

import wasmtime as wt

import wasi_perl
from wasi_perl import _engines, _limits, runner

sys.path.insert(0, str(Path(__file__).resolve().parent))
import image_module as im  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
PROTO = ROOT / "build" / "proto022"
ART = ROOT / "build" / "artifacts"
REACTOR = PROTO / "perl-reactor.opt.wasm"

# spec 022, Decision 5: _snapshot_make.PACKAGES, the eight of study D
PACKAGES = (
    "strict",
    "warnings",
    "POSIX",
    "List::Util",
    "JSON::PP",
    "Data::Dumper",
    "Storable",
    "Encode",
)
PRELUDE = "".join(f"require {m};\n" for m in PACKAGES)
FIVE = "use strict; use warnings; use POSIX; use JSON::PP; use List::Util; 1"
UNMETERED = runner.UNMETERED


@dataclasses.dataclass
class Made:
    code: str
    status: int
    open_files: int
    sp_initial: int
    sp_after: int
    image: bytes
    stdout: bytes
    stderr: bytes
    fuel: int | None
    seconds: dict[str, float]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.image).hexdigest()


_probe_modules: dict[bool, Any] = {}


def reactor_bytes() -> bytes:
    return REACTOR.read_bytes()


def _probe(metered: bool) -> Any:
    """The checked reactor plus the stack-pointer export, compiled once per flavour."""
    if metered not in _probe_modules:
        data = reactor_bytes()
        form = im.check_form(data)
        _probe_modules[metered] = (
            wt.Module(_engines.engine(metered), im.with_global_export(data, form.stack_global)),
            form,
        )
    return _probe_modules[metered]


def make(
    code: str,
    *,
    env: dict[str, str] | None = None,
    grants: tuple[tuple[str, str, bool], ...] = (),
    metered: bool = False,
) -> Made:
    seconds: dict[str, float] = {}
    t = time.perf_counter()
    mod, form = _probe(metered)
    seconds["reactor_compile_or_cached"] = time.perf_counter() - t
    eng = _engines.engine(metered)
    out = runner._Stream("stdout", None)
    err = runner._Stream("stderr", None)
    store = wt.Store(eng)
    try:
        if metered:
            store.set_fuel(UNMETERED)
        store.set_limits(memory_size=1 << 30)
        store.set_epoch_deadline(_limits.NO_DEADLINE)
        wasi = wt.WasiConfig()
        wasi.argv = ["perl", "-e", code]
        wasi.env = sorted((env or {}).items())
        wasi.stdout_custom = runner._writer(out)
        wasi.stderr_custom = runner._writer(err)
        wasi.preopen_dir(str(ART / "lib" / "perl5"), "/usr/local/lib/perl5", False)
        wasi.preopen_dir(str(ART / "dev"), "/dev", False)
        for host, guest, writable in grants:
            wasi.preopen_dir(host, guest, writable)
        store.set_wasi(wasi)
        linker = wt.Linker(eng)
        linker.define_wasi()
        t = time.perf_counter()
        inst = linker.instantiate(store, mod)
        ex = inst.exports(store)
        ex["_initialize"](store)
        if metered:
            store.set_fuel(UNMETERED)
        status = ex["prepare"](store)
        seconds["prelude"] = time.perf_counter() - t
        fuel = UNMETERED - store.get_fuel() if metered else None
        open_files = ex["wasi_perl_open_files"](store)
        sp_after = ex["proto_stack_pointer"].value(store)
        mem = ex["memory"]
        t = time.perf_counter()
        image = ctypes.string_at(mem.data_ptr(store), mem.data_len(store))
        seconds["capture"] = time.perf_counter() - t
    finally:
        store.close()
    return Made(
        code,
        status,
        open_files,
        form.stack_init,
        sp_after,
        image,
        out.value(),
        err.value(),
        fuel,
        seconds,
    )


def write_module(made: Made) -> tuple[bytes, dict, float]:
    t = time.perf_counter()
    data, stats = im.write(reactor_bytes(), made.image)
    return data, stats, time.perf_counter() - t


def compile_module(data: bytes, metered: bool) -> tuple[Any, float]:
    t = time.perf_counter()
    mod = wt.Module(_engines.engine(metered), data)
    return mod, time.perf_counter() - t


def image_modules(made: Made, flavours: tuple[bool, ...] = (False, True)) -> dict[bool, Any]:
    data, _, _ = write_module(made)
    return {m: compile_module(data, m)[0] for m in flavours}


class _Instance:
    def __init__(self, inst: Any) -> None:
        self._inst = inst

    def exports(self, store: Any) -> dict[str, Any]:
        ex = self._inst.exports(store)
        return {"_start": ex["run"], runner.EXIT_STATUS_EXPORT: ex[runner.EXIT_STATUS_EXPORT]}


class _Pre:
    def __init__(self, pre: Any) -> None:
        self._pre = pre

    def instantiate(self, store: Any) -> _Instance:
        return _Instance(self._pre.instantiate(store))


_pres: dict[int, _Pre] = {}


@contextlib.contextmanager
def _on_image(mods: dict[bool, Any]):
    real_module, real_linked = _engines.module, _engines.linked

    def module(metered: bool, binary: Path, want: str | None, cache: Path | None) -> Any:
        return mods[metered]

    def linked(metered: bool, mod: Any) -> Any:
        found = _pres.get(id(mod))
        if found is None:
            found = _pres[id(mod)] = _Pre(real_linked(metered, mod))
        return found

    _engines.module, _engines.linked = module, linked
    try:
        yield
    finally:
        _engines.module, _engines.linked = real_module, real_linked


def run_plain(argv: list[str], **kw: Any) -> runner.Completed:
    return wasi_perl.run(argv, **kw)


def run_image(argv: list[str], mods: dict[bool, Any], **kw: Any) -> runner.Completed:
    with _on_image(mods):
        return wasi_perl.run(argv, **kw)


def shown(c: runner.Completed) -> dict:
    return {
        "exit_code": c.exit_code,
        "stdout": c.stdout.decode("utf-8", "backslashreplace"),
        "stderr": c.stderr.decode("utf-8", "backslashreplace"),
        "trapped": c.trapped,
    }


def same(a: runner.Completed, b: runner.Completed) -> bool:
    return (a.exit_code, a.stdout, a.stderr, a.trapped) == (
        b.exit_code,
        b.stdout,
        b.stderr,
        b.trapped,
    )
