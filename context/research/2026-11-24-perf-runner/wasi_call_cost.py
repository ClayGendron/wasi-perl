"""The host cost of one WASI preview1 call under wasmtime-py 51: a WAT module calls
fd_fdstat_get(1) or clock_time_get N times in one _start; the time per call is the slope."""
import statistics
import sys
import time

import wasmtime as wt

WAT = """(module
  (import "wasi_snapshot_preview1" "fd_fdstat_get" (func $st (param i32 i32) (result i32)))
  (import "wasi_snapshot_preview1" "clock_time_get" (func $ck (param i32 i64 i32) (result i32)))
  (memory (export "memory") 1)
  (func (export "stat") (param $n i32)
    (loop $l (drop (call $st (i32.const 1) (i32.const 64)))
      (local.set $n (i32.sub (local.get $n) (i32.const 1))) (br_if $l (local.get $n))))
  (func (export "clock") (param $n i32)
    (loop $l (drop (call $ck (i32.const 1) (i64.const 1) (i32.const 64)))
      (local.set $n (i32.sub (local.get $n) (i32.const 1))) (br_if $l (local.get $n)))))"""
cfg = wt.Config()
cfg.epoch_interruption = True
eng = wt.Engine(cfg)
mod = wt.Module(eng, WAT)
linker = wt.Linker(eng)
linker.define_wasi()
for fn in ("stat", "clock"):
    for n in (10, 10010):
        xs = []
        for _ in range(15):
            s = wt.Store(eng)
            s.set_wasi(wt.WasiConfig())
            s.set_epoch_deadline(1 << 40)
            inst = linker.instantiate(s, mod)
            f = inst.exports(s)[fn]
            t = time.perf_counter()
            f(s, n)
            xs.append(time.perf_counter() - t)
            s.close()
        med = statistics.median(xs)
        if n == 10:
            base = med
        else:
            print(f"{fn}: {(med - base) / 10000 * 1e9:.0f} ns per WASI call")
