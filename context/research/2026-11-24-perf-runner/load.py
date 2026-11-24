"""One fresh process's load of a cached .cwasm, by one method; prints ms. Run it several times.

    python load.py <bytes|file|procfd> <cwasm> <artifacts dir>

bytes: read through a descriptor, then Module.deserialize (the runner today).
file: Module.deserialize_file on the path.
procfd: open and check the descriptor, then Module.deserialize_file on /proc/self/fd/N, which
    reopens the very file the descriptor holds (wasi-python's way).
Then one instantiate and _start of `perl -e 1`, so lazily mapped code is paid for too.
"""
import json
import os
import sys
import time

t0 = time.perf_counter()
import wasmtime as wt

how, cwasm, art = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = wt.Config()
cfg.wasm_exceptions = True
cfg.epoch_interruption = True
eng = wt.Engine(cfg)
t1 = time.perf_counter()
if how == "bytes":
    fd = os.open(cwasm, os.O_RDONLY)
    size = os.fstat(fd).st_size
    parts = []
    while True:
        b = os.read(fd, max(size, 1 << 20))
        if not b:
            break
        parts.append(b)
    os.close(fd)
    mod = wt.Module.deserialize(eng, b"".join(parts))
elif how == "file":
    mod = wt.Module.deserialize_file(eng, cwasm)
else:
    fd = os.open(cwasm, os.O_RDONLY | os.O_NOFOLLOW)
    os.fstat(fd)
    mod = wt.Module.deserialize_file(eng, f"/proc/self/fd/{fd}")
    os.close(fd)
t2 = time.perf_counter()
store = wt.Store(eng)
w = wt.WasiConfig()
w.argv = ["perl", "-e", "1"]
w.preopen_dir(f"{art}/lib/perl5", "/usr/local/lib/perl5", False)
w.preopen_dir(f"{art}/dev", "/dev", False)
store.set_wasi(w)
store.set_epoch_deadline(1 << 40)
linker = wt.Linker(eng)
linker.define_wasi()
inst = linker.instantiate(store, mod)
try:
    inst.exports(store)["_start"](store)
except wt.ExitTrap:
    pass
t3 = time.perf_counter()
import resource

print(json.dumps({"how": how, "load_ms": round((t2 - t1) * 1e3, 2),
                  "first_start_ms": round((t3 - t2) * 1e3, 2),
                  "maxrss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024}))
