"""Step 2: what one preview1 call costs in wasmtime-py 51, when wasmtime answers it and when a
Python host function does. A small WAT guest opens one file on preopen 3 and then calls one
function N times in a loop; the time of the loop, divided by N, is the cost of one call.

    flock /home/user/wasi-perl-wt/machine.lock python bench_calls.py results/calls.json

Each case runs 5 times; the median is kept. Wall-clock: secondary evidence, run under the lock.
"""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

import wasmtime as wt

from wasi_perl import _mounts

WAT = """
(module
  (import "wasi_snapshot_preview1" "path_open"
    (func $open (param i32 i32 i32 i32 i32 i64 i64 i32 i32) (result i32)))
  (import "wasi_snapshot_preview1" "fd_pread"
    (func $pread (param i32 i32 i32 i64 i32) (result i32)))
  (import "wasi_snapshot_preview1" "fd_fdstat_get"
    (func $fdstat (param i32 i32) (result i32)))
  (import "wasi_snapshot_preview1" "fd_filestat_get"
    (func $filestat (param i32 i32) (result i32)))
  (import "wasi_snapshot_preview1" "fd_read"
    (func $read (param i32 i32 i32 i32) (result i32)))
  (import "wasi_snapshot_preview1" "fd_seek"
    (func $seek (param i32 i64 i32 i32) (result i32)))
  (import "bench" "noop" (func $noop (param i32 i32) (result i32)))
  (memory (export "memory") 120)
  (data (i32.const 0) "f.bin")
  (global $fd (mut i32) (i32.const 0))
  (func (export "open") (result i32)
    (call $open (i32.const 3) (i32.const 0) (i32.const 0) (i32.const 5) (i32.const 0)
      (i64.const 0x20002e) (i64.const 0) (i32.const 0) (i32.const 64))
    (global.set $fd (i32.load (i32.const 64))))
  ;; iovec at 128: buffer at 65536, length given
  (func (export "pread") (param $n i32) (param $len i32) (result i32)
    (local $r i32)
    (i32.store (i32.const 128) (i32.const 65536))
    (i32.store (i32.const 132) (local.get $len))
    (block $done (loop $l
      (br_if $done (i32.eqz (local.get $n)))
      (local.set $r (call $pread (global.get $fd) (i32.const 128) (i32.const 1)
        (i64.const 0) (i32.const 136)))
      (local.set $n (i32.sub (local.get $n) (i32.const 1)))
      (br $l)))
    (i32.load (i32.const 136)))
  ;; fd_seek to 0, then fd_read of $len: one read of $len bytes from the start
  (func (export "seekread") (param $n i32) (param $len i32) (result i32)
    (local $r i32)
    (i32.store (i32.const 128) (i32.const 65536))
    (i32.store (i32.const 132) (local.get $len))
    (block $done (loop $l
      (br_if $done (i32.eqz (local.get $n)))
      (local.set $r (call $seek (global.get $fd) (i64.const 0) (i32.const 0) (i32.const 144)))
      (local.set $r (call $read (global.get $fd) (i32.const 128) (i32.const 1) (i32.const 136)))
      (local.set $n (i32.sub (local.get $n) (i32.const 1)))
      (br $l)))
    (i32.load (i32.const 136)))
  (func (export "seek") (param $n i32) (result i32)
    (local $r i32)
    (block $done (loop $l
      (br_if $done (i32.eqz (local.get $n)))
      (local.set $r (call $seek (global.get $fd) (i64.const 0) (i32.const 0) (i32.const 144)))
      (local.set $n (i32.sub (local.get $n) (i32.const 1)))
      (br $l)))
    (local.get $r))
  (func (export "fdstat") (param $n i32) (result i32)
    (local $r i32)
    (block $done (loop $l
      (br_if $done (i32.eqz (local.get $n)))
      (local.set $r (call $fdstat (i32.const 1) (i32.const 256)))
      (local.set $n (i32.sub (local.get $n) (i32.const 1)))
      (br $l)))
    (local.get $r))
  (func (export "filestat") (param $n i32) (result i32)
    (local $r i32)
    (block $done (loop $l
      (br_if $done (i32.eqz (local.get $n)))
      (local.set $r (call $filestat (global.get $fd) (i32.const 512)))
      (local.set $n (i32.sub (local.get $n) (i32.const 1)))
      (br $l)))
    (local.get $r))
  (func (export "noop") (param $n i32) (result i32)
    (local $r i32)
    (block $done (loop $l
      (br_if $done (i32.eqz (local.get $n)))
      (local.set $r (call $noop (i32.const 1) (i32.const 256)))
      (local.set $n (i32.sub (local.get $n) (i32.const 1)))
      (br $l)))
    (local.get $r))
)
"""

SIZE = 5_000_000


def instance(engine: wt.Engine, folder: Path, mode: str) -> tuple[wt.Store, wt.Instance]:
    """mode: "native" (wasmtime's WASI) or "mount" (a Python shadow that serves the file from
    staged bytes and forwards every other descriptor)."""
    store = wt.Store(engine)
    wasi = wt.WasiConfig()
    if mode == "mount":
        wasi.preopen_dir(str(_mounts.placeholder()), "/m", False)
    else:
        wasi.preopen_dir(str(folder), "/m", False)
    store.set_wasi(wasi)
    module = wt.Module(engine, WAT)
    sigs = {
        k: _mounts.SIGNATURES[k]
        for k in ("path_open", "fd_pread", "fd_fdstat_get", "fd_filestat_get", "fd_read", "fd_seek")
    }
    if mode == "native":
        linker = wt.Linker(engine)
        linker.define_wasi()
    else:
        linker = _mounts.linker(wt, engine, sigs)
    i32 = wt.ValType.i32()
    linker.allow_shadowing = True
    linker.define_func("bench", "noop", wt.FuncType([i32, i32], [i32]), lambda c, a, b: 0, True)
    inst = linker.instantiate(store, module)
    if mode != "native":
        provider = _mounts.MemoryProvider({"/f.bin": bytes(SIZE)})
        table = _mounts.Table([(_mounts.Mount(provider, "/m", memo=False), 3)])
        _mounts.bind(wt, store, engine, table, inst, sigs)
        _mounts.enter(table)
    return store, inst


def timed(fn, *args) -> float:
    t = time.perf_counter()
    fn(*args)
    return time.perf_counter() - t


def main() -> None:
    out = Path(sys.argv[1])
    engine = wt.Engine()
    folder = Path(tempfile.mkdtemp())
    (folder / "f.bin").write_bytes(bytes(SIZE))
    results: dict[str, dict[str, float]] = {}
    cases = [
        ("noop host function (Python)", "noop", (), 20000),
        ("fd_fdstat_get", "fdstat", (), 20000),
        ("fd_filestat_get", "filestat", (), 20000),
        ("fd_seek", "seek", (), 20000),
        ("fd_pread 8 KiB", "pread", (8192,), 20000),
        ("fd_pread 64 KiB (wasmtime answers at most 64 KiB)", "pread", (65536,), 5000),
        ("fd_seek + fd_read 8 KiB", "seekread", (8192,), 20000),
        ("fd_seek + fd_read 64 KiB", "seekread", (65536,), 5000),
        ("fd_seek + fd_read 1 MiB", "seekread", (1 << 20,), 500),
        ("fd_seek + fd_read 5 MB", "seekread", (SIZE,), 100),
    ]
    for mode in ("native", "forward", "mount"):
        if mode == "forward":
            store, inst = _forward_instance(engine, folder)
        else:
            store, inst = instance(engine, folder, mode)
        ex = inst.exports(store)
        assert ex["open"](store) == 0, mode
        for label, fn, args, n in cases:
            runs = [timed(ex[fn], store, n, *args) / n for _ in range(5)]
            results.setdefault(label, {})[mode] = statistics.median(runs) * 1e6
        _mounts.leave()
    out.write_text(json.dumps(results, indent=1))
    print(
        "| call | wasmtime's WASI (µs) | Python shadow forwarding to it (µs) |"
        " Python serving a mount (µs) |"
    )
    print("|---|---|---|---|")
    for label, row in results.items():
        cells = [f"{row.get(m, float('nan')):.2f}" for m in ("native", "forward", "mount")]
        print(f"| {label} | " + " | ".join(cells) + " |")


def _forward_instance(engine: wt.Engine, folder: Path) -> tuple[wt.Store, wt.Instance]:
    """Every call through a Python shadow over a table with no mounts, which forwards it."""
    store = wt.Store(engine)
    wasi = wt.WasiConfig()
    wasi.preopen_dir(str(folder), "/m", False)
    store.set_wasi(wasi)
    module = wt.Module(engine, WAT)
    sigs = {
        k: _mounts.SIGNATURES[k]
        for k in ("path_open", "fd_pread", "fd_fdstat_get", "fd_filestat_get", "fd_read", "fd_seek")
    }
    linker = _mounts.linker(wt, engine, sigs)
    i32 = wt.ValType.i32()
    linker.define_func("bench", "noop", wt.FuncType([i32, i32], [i32]), lambda c, a, b: 0, True)
    inst = linker.instantiate(store, module)
    table = _mounts.Table([])
    _mounts.bind(wt, store, engine, table, inst, sigs)
    _mounts.enter(table)
    return store, inst


if __name__ == "__main__":
    main()
