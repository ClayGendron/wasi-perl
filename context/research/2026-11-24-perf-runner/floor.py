"""The wasmtime-py floor of one perl.wasm call, step by step, under engine and linker variants.

No runner code: a Store, a WasiConfig, the preopens, instantiate, _start, the exit-status read and
close, as the runner makes them. Prints the median per step in microseconds.

    python floor.py <artifacts dir> <calls> [out.json]
"""
import ctypes
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import wasmtime as wt
from wasmtime import _ffi as ffi

art = Path(sys.argv[1])
calls = int(sys.argv[2])
wasm = (art / "perl.wasm").read_bytes()
lib, dev = str(art / "lib" / "perl5"), str(art / "dev")


def engine(epoch=True, fuel=False, pooling=False, cow=True, keep=0, reservation=None):
    cfg = wt.Config()
    cfg.wasm_exceptions = True
    cfg.epoch_interruption = epoch
    cfg.consume_fuel = fuel
    cfg.memory_init_cow = cow
    if reservation is not None:
        cfg.memory_reservation = reservation
    if pooling:
        pc = ffi.wasmtime_pooling_allocation_config_new()
        ffi.wasmtime_pooling_allocation_config_total_memories_set(pc, 16)
        ffi.wasmtime_pooling_allocation_config_total_tables_set(pc, 16)
        ffi.wasmtime_pooling_allocation_config_total_core_instances_set(pc, 16)
        ffi.wasmtime_pooling_allocation_config_total_stacks_set(pc, 16)
        ffi.wasmtime_pooling_allocation_config_max_memory_size_set(pc, 1 << 30)
        ffi.wasmtime_pooling_allocation_config_table_elements_set(pc, 1 << 16)
        ffi.wasmtime_pooling_allocation_config_max_core_instance_size_set(pc, 1 << 20)
        if keep:
            ffi.wasmtime_pooling_allocation_config_linear_memory_keep_resident_set(pc, keep)
            ffi.wasmtime_pooling_allocation_config_table_keep_resident_set(pc, keep)
        ffi.wasmtime_pooling_allocation_strategy_set(cfg.ptr(), pc)
        ffi.wasmtime_pooling_allocation_config_delete(pc)
    return wt.Engine(cfg)


VARIANTS = {
    "new-linker": dict(),
    "shared-linker": dict(share="linker"),
    "instance-pre": dict(share="pre"),
    "instance-pre+pooling": dict(share="pre", pooling=True),
    "instance-pre+pooling+keep16M": dict(share="pre", pooling=True, keep=16 << 20),
    "instance-pre+reserve1G": dict(share="pre", reservation=1 << 30),
    "instance-pre+no-cow": dict(share="pre", cow=False),
    "instance-pre+no-epoch": dict(share="pre", epoch=False),
    "instance-pre+fuel": dict(share="pre", fuel=True),
    "instance-pre+pooling+fuel": dict(share="pre", pooling=True, fuel=True),
}


def bench(name, share=None, **eng):
    e = engine(**eng)
    t = time.perf_counter()
    mod = wt.Module(e, wasm)
    compile_s = time.perf_counter() - t
    shared_linker = wt.Linker(e)
    shared_linker.define_wasi()
    pre = shared_linker.instantiate_pre(mod)
    ticker = None
    per = defaultdict(list)

    def one(rec):
        marks = [("start", time.perf_counter())]
        mark = lambda k: marks.append((k, time.perf_counter()))
        out = []
        store = wt.Store(e)
        if eng.get("fuel"):
            store.set_fuel(10**12)
        store.set_limits(memory_size=1 << 30)
        mark("store")
        w = wt.WasiConfig()
        w.argv = ["perl", "-e", 'print "hello\\n"']
        w.env = []
        w.stdout_custom = lambda b: (out.append(b), len(b))[1]
        w.stderr_custom = lambda b: (out.append(b), len(b))[1]
        w.preopen_dir(lib, "/usr/local/lib/perl5", False)
        w.preopen_dir(dev, "/dev", False)
        store.set_wasi(w)
        mark("wasi config")
        if eng.get("epoch", True):
            store.set_epoch_deadline(1 << 40)
        if share is None:
            linker = wt.Linker(e)
            linker.define_wasi()
            mark("linker+define_wasi")
            inst = linker.instantiate(store, mod)
        elif share == "linker":
            inst = shared_linker.instantiate(store, mod)
        else:
            inst = pre.instantiate(store)
        mark("instantiate")
        ex = inst.exports(store)
        start = ex["_start"]
        mark("exports")
        try:
            start(store)
        except wt.ExitTrap as x:
            assert x.code == 0
        mark("_start")
        status = ex["wasi_perl_exit_status"](store)
        mark("status read")
        store.close()
        mark("close")
        assert b"".join(out) == b"hello\n", out
        if rec:
            for (_, a), (k, b) in zip(marks, marks[1:]):
                per[k].append(b - a)
            per["TOTAL"].append(marks[-1][1] - marks[0][1])

    for _ in range(5):
        one(False)
    for _ in range(calls):
        one(True)
    res = {k: round(statistics.median(v) * 1e6, 1) for k, v in per.items()}
    res["compile_ms"] = round(compile_s * 1e3)
    print(name, json.dumps(res), flush=True)
    return res


which = sys.argv[4].split(",") if len(sys.argv) > 4 else list(VARIANTS)
results = {n: bench(n, **VARIANTS[n]) for n in which}
if len(sys.argv) > 3:
    json.dump(results, open(sys.argv[3], "w"), indent=1)
