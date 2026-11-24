"""Runs probe.wasm under wasmtime-py and prints, per kind of WASI call, the guest's own
nanoseconds per call (median of several runs) and the guest instructions per call (fuel).

    python cost.py <probe.wasm> <folder holding f.txt> <n> <runs> <out.jsonl> [options]

    --lib DIR        put DIR first on sys.path, so `import wasmtime` loads the copy there
    --blocking       call wasi_config_allow_blocking_current_thread(config, true) before the
                     preopen; only a C API built with the patch beside this script has it
    --blocking-late  the same call, but after the preopen (the folder keeps the old setting)
    --kinds a,b,...  the kinds to run (default: read,eof,fstat,fdstat,pstat,open,clock)
    --fuel           count instructions per call instead of timing (fuel on)
    --threads        print the process's thread count after the runs

The guest times its own loop with clock_time_get, so instantiation and the call's setup are
not in the number.
"""

import json
import os
import statistics
import sys
import tempfile

args = sys.argv[1:]
opts = {"--lib": None, "--kinds": "read,eof,fstat,fdstat,pstat,open,clock"}
flags = set()
pos = []
i = 0
while i < len(args):
    a = args[i]
    if a in opts:
        opts[a] = args[i + 1]
        i += 2
    elif a.startswith("--"):
        flags.add(a)
        i += 1
    else:
        pos.append(a)
        i += 1
probe, folder, n, runs, out = pos[0], pos[1], int(pos[2]), int(pos[3]), pos[4]
if opts["--lib"]:
    sys.path.insert(0, opts["--lib"])

import ctypes  # noqa: E402

import wasmtime as wt  # noqa: E402
from wasmtime import _ffi as ffi  # noqa: E402

blocking = None
late = "--blocking-late" in flags
if "--blocking" in flags or late:
    blocking = ffi.dll.wasi_config_allow_blocking_current_thread
    blocking.argtypes = [ctypes.c_void_p, ctypes.c_bool]
    blocking.restype = None


def engine(fuel):
    cfg = wt.Config()
    cfg.epoch_interruption = True
    cfg.consume_fuel = fuel
    return wt.Engine(cfg)


def once(eng, mod, linker, kind, count, fuel):
    store = wt.Store(eng)
    store.set_epoch_deadline(1 << 40)
    if fuel:
        store.set_fuel(1 << 60)
    w = wt.WasiConfig()
    w.argv = ["probe", kind, str(count)]
    if blocking is not None and not late:
        blocking(w.ptr(), True)
    w.preopen_dir(folder, "/d", False)
    if blocking is not None and late:
        blocking(w.ptr(), True)
    fd, path = tempfile.mkstemp()
    os.close(fd)
    w.stdout_file = path
    w.inherit_stderr()
    store.set_wasi(w)
    inst = linker.instantiate(store, mod)
    try:
        inst.exports(store)["_start"](store)
    except wt.ExitTrap as e:
        assert e.code == 0, e.code
    used = (1 << 60) - store.get_fuel() if fuel else None
    store.close() if hasattr(store, "close") else None
    with open(path) as f:
        line = f.read().strip()
    os.unlink(path)
    ns = float(line.rsplit("=", 1)[1])
    return ns, used


rows = []
for fuel in ([True] if "--fuel" in flags else [False]):
    eng = engine(fuel)
    mod = wt.Module.from_file(eng, probe)
    linker = wt.Linker(eng)
    linker.define_wasi()
    for kind in opts["--kinds"].split(","):
        if fuel:
            _, small = once(eng, mod, linker, kind, 10, True)
            _, large = once(eng, mod, linker, kind, n, True)
            rows.append({"kind": kind, "instructions_per_call": (large - small) / (n - 10)})
            continue
        once(eng, mod, linker, kind, 10, False)  # warm the runtime's threads
        samples = [once(eng, mod, linker, kind, n, False)[0] for _ in range(runs)]
        rows.append(
            {
                "kind": kind,
                "n": n,
                "runs": runs,
                "blocking": "late" if late else blocking is not None,
                "lib": opts["--lib"] or "stock",
                "us_per_call_median": round(statistics.median(samples) / 1000, 3),
                "us_per_call_min": round(min(samples) / 1000, 3),
                "us_per_call_max": round(max(samples) / 1000, 3),
            }
        )
        print(json.dumps(rows[-1]), flush=True)
if "--threads" in flags:
    print("threads", len(os.listdir("/proc/self/task")), flush=True)
with open(out, "a") as f:
    for r in rows:
        f.write(json.dumps(r) + "\n")
if "--fuel" in flags:
    print(json.dumps([r for r in rows if "instructions_per_call" in r]))
