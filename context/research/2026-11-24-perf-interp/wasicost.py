"""Run proto/wasicost.wasm under wasmtime-py 51 with the runner's preopens (library, /dev, /data)
and print its per-call costs. Usage: wasicost.py WASICOST.wasm LIBSET DATA [N]"""

import sys
import tempfile

import wasmtime

wasm, libset, data = sys.argv[1:4]
n = sys.argv[4] if len(sys.argv) > 4 else "5000"
engine = wasmtime.Engine()
store = wasmtime.Store(engine)
cfg = wasmtime.WasiConfig()
cfg.argv = ["wasicost", n]
out = tempfile.NamedTemporaryFile(delete=False)
cfg.stdout_file = out.name  # the runner captures stdout too; a file keeps the 1-byte writes apart
cfg.preopen_dir(f"{libset}/lib/perl5", "/usr/local/lib/perl5", False)  # as the runner does
cfg.preopen_dir(f"{libset}/dev", "/dev", False)
cfg.preopen_dir(data, "/data", False)
store.set_wasi(cfg)
linker = wasmtime.Linker(engine)
linker.define_wasi()
inst = linker.instantiate(store, wasmtime.Module.from_file(engine, wasm))
try:
    inst.exports(store)["_start"](store)
except wasmtime.ExitTrap as e:
    if e.code:
        raise
for line in open(out.name, "rb").read().split(b"\n"):
    if b" " in line and not line.startswith(b"\x00"):
        print(line.decode(errors="replace").lstrip("\x00"))
