"""What starting an instance costs, plain and from a wizer snapshot, and with which allocator.

    python restore.py <research folder> <artifacts folder> OUT.json [n]

For each module (perl.wasm, the construct snapshot, the preload snapshot) and each engine set-up
(wasmtime's default on-demand allocator, the same with copy-on-write memory images off, and the
pooling allocator), it times in one warm process, median of n:
  - instantiate: a new store and WASI context, then Linker.instantiate;
  - instantiate_pre: the same through one InstancePre made beforehand;
  - each program below, a whole call (instantiate_pre, then _start or run, to the exit).
A metered pass counts the guest instructions of each program on each module.
The preopens and argv are the runner's: the library at /usr/local/lib/perl5, /dev, argv[0] perl.
Wall time is taken under the machine lock by the caller.
"""

from __future__ import annotations

import ctypes
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

import wasmtime as wt
from wasmtime import _bindings

PROGRAMS = {
    "e_1": ["-e", "1"],
    "ne_file": ["-ne", "print if /7/", "/dev/null"],
    "MPOSIX": ["-MPOSIX", "-e", "1"],
    "MJSON_PP": ["-MJSON::PP", "-e", "1"],
    "five": ["-e", "use strict; use warnings; use POSIX; use JSON::PP; use List::Util; 1"],
    "six": [
        "-MPOSIX",
        "-MList::Util",
        "-MData::Dumper",
        "-MJSON::PP",
        "-MStorable",
        "-MEncode",
        "-e",
        "1",
    ],
}


def engine(kind: str, metered: bool = False) -> wt.Engine:
    cfg = wt.Config()
    cfg.wasm_exceptions = True
    cfg.consume_fuel = metered
    if kind == "nocow":
        cfg.memory_init_cow = False
    if kind in ("pooling", "pooling_resident"):
        p = _bindings.wasmtime_pooling_allocation_config_new()
        _bindings.wasmtime_pooling_allocation_config_total_memories_set(p, 32)
        _bindings.wasmtime_pooling_allocation_config_total_tables_set(p, 32)
        _bindings.wasmtime_pooling_allocation_config_total_core_instances_set(p, 32)
        _bindings.wasmtime_pooling_allocation_config_total_stacks_set(p, 32)
        _bindings.wasmtime_pooling_allocation_config_max_memory_size_set(p, 1 << 30)
        _bindings.wasmtime_pooling_allocation_config_table_elements_set(p, 1 << 16)
        _bindings.wasmtime_pooling_allocation_config_max_unused_warm_slots_set(p, 8)
        if kind == "pooling_resident":
            # A freed slot's first 32 MiB are reset in place rather than handed back to the
            # kernel, so the next instance in that slot does not fault them in again.
            _bindings.wasmtime_pooling_allocation_config_linear_memory_keep_resident_set(p, 32 << 20)
        _bindings.wasmtime_pooling_allocation_strategy_set(
            ctypes.cast(cfg.ptr(), ctypes.POINTER(_bindings.wasm_config_t)), p
        )
        _bindings.wasmtime_pooling_allocation_config_delete(p)
    return wt.Engine(cfg)


def wasi(argv: list[str], lib: str, dev: str, t: str) -> wt.WasiConfig:
    w = wt.WasiConfig()
    w.argv = ["perl", *argv]
    w.env = []
    w.stdout_file = str(Path(t, "out"))
    w.stderr_file = str(Path(t, "err"))
    w.preopen_dir(lib, "/usr/local/lib/perl5", False)
    w.preopen_dir(dev, "/dev", False)
    return w


def call(eng, pre, entry, argv, lib, dev, fuel=None, run=True):
    with tempfile.TemporaryDirectory() as t:
        store = wt.Store(eng)
        store.set_wasi(wasi(argv, lib, dev, t))
        if fuel:
            store.set_fuel(fuel)
        inst = pre.instantiate(store)
        code = None
        if run:
            try:
                inst.exports(store)[entry](store)
            except wt.ExitTrap as e:
                code = e.code
        used = fuel - store.get_fuel() if fuel else None
        out = Path(t, "out").read_bytes()
        err = Path(t, "err").read_bytes()
    return code, used, out, err


def med(xs):
    return round(statistics.median(xs) * 1000, 3)


def main() -> None:
    research, art, dest = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    n = int(sys.argv[4]) if len(sys.argv) > 4 else 30
    lib, dev = str(art / "lib" / "perl5"), str(art / "dev")
    mods = {
        "perl.wasm": (art / "perl.wasm", "_start"),
        "snap-construct": (research / "snap-construct.wasm", "run"),
        "snap-preload": (research / "snap-preload.wasm", "run"),
    }
    res: dict = {"n": n, "sizes": {k: p.stat().st_size for k, (p, _) in mods.items()}}
    # Instructions: one metered call per program per module.
    me = engine("ondemand", metered=True)
    fuel: dict = {}
    for name, (path, entry) in mods.items():
        m = wt.Module.from_file(me, str(path))
        linker = wt.Linker(me)
        linker.define_wasi()
        pre = linker.instantiate_pre(m)
        for prog, argv in PROGRAMS.items():
            code, used, out, err = call(me, pre, entry, argv, lib, dev, fuel=10**13)
            fuel[f"{name} {prog}"] = {"exit": code, "fuel": used, "stderr": err[:200].decode()}
            print("fuel", name, prog, code, used, flush=True)
    res["fuel"] = fuel
    timing: dict = {}
    for kind in ("ondemand", "nocow", "pooling"):
        eng = engine(kind)
        for name, (path, entry) in mods.items():
            m = wt.Module.from_file(eng, str(path))
            linker = wt.Linker(eng)
            linker.define_wasi()
            pre = linker.instantiate_pre(m)
            row: dict = {}
            with tempfile.TemporaryDirectory() as t:
                xs = []
                for i in range(n + 2):
                    s = time.perf_counter()
                    store = wt.Store(eng)
                    store.set_wasi(wasi(["-e", "1"], lib, dev, t))
                    linker.instantiate(store, m)
                    xs.append(time.perf_counter() - s)
                row["instantiate"] = med(xs[2:])
                xs = []
                for i in range(n + 2):
                    s = time.perf_counter()
                    store = wt.Store(eng)
                    store.set_wasi(wasi(["-e", "1"], lib, dev, t))
                    pre.instantiate(store)
                    xs.append(time.perf_counter() - s)
                row["instantiate_pre"] = med(xs[2:])
            for prog, argv in PROGRAMS.items():
                xs = []
                for i in range(n + 2):
                    s = time.perf_counter()
                    call(eng, pre, entry, argv, lib, dev)
                    xs.append(time.perf_counter() - s)
                row[prog] = med(xs[2:])
            timing[f"{kind} {name}"] = row
            print(kind, name, row, flush=True)
    res["ms_median"] = timing
    Path(dest).write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
