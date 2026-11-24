"""The host cost of one WASI preview1 filesystem call under wasmtime-py 51: a WAT module calls
path_filestat_get on a file in a preopened folder N times in one export call; the time per call
is the slope. Also counts the futex calls per WASI call when run under strace (see the memo).

    python wasi_fs_call_cost.py <folder holding f.txt> [n]
"""
import statistics
import sys
import time

import wasmtime as wt

WAT = """(module
  (import "wasi_snapshot_preview1" "path_filestat_get"
    (func $fs (param i32 i32 i32 i32 i32) (result i32)))
  (memory (export "memory") 1)
  (data (i32.const 0) "f.txt")
  (func (export "go") (param $n i32) (result i32)
    (local $r i32)
    (loop $l (local.set $r (call $fs (i32.const 3) (i32.const 0) (i32.const 0) (i32.const 5)
                                     (i32.const 64)))
      (local.set $n (i32.sub (local.get $n) (i32.const 1))) (br_if $l (local.get $n)))
    (local.get $r)))"""
cfg = wt.Config()
cfg.epoch_interruption = True
eng = wt.Engine(cfg)
mod = wt.Module(eng, WAT)
linker = wt.Linker(eng)
linker.define_wasi()
folder = sys.argv[1]
big = int(sys.argv[2]) if len(sys.argv) > 2 else 2010


def once(n):
    s = wt.Store(eng)
    w = wt.WasiConfig()
    w.preopen_dir(folder, "/d", False)
    s.set_wasi(w)
    s.set_epoch_deadline(1 << 40)
    inst = linker.instantiate(s, mod)
    t = time.perf_counter()
    errno = inst.exports(s)["go"](s, n)
    dt = time.perf_counter() - t
    s.close()
    assert errno == 0, errno
    return dt


small = statistics.median(once(10) for _ in range(15))
large = statistics.median(once(big) for _ in range(15))
print(f"path_filestat_get: {(large - small) / (big - 10) * 1e6:.1f} us per WASI call")
