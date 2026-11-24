import os, sys, time, fcntl, resource, wasmtime as wt
from pathlib import Path
way, cwasm = sys.argv[1], Path(sys.argv[2])
from wasi_perl import _engines; eng = _engines.engine(False)
t = time.perf_counter()
fd = os.open(cwasm, os.O_RDONLY)
size = os.fstat(fd).st_size
if way == "bytes":
    mod = wt.Module.deserialize(eng, os.read(fd, size + 1))
elif way == "map":
    mod = wt.Module.deserialize_file(eng, f"/proc/self/fd/{fd}")
else:
    m = os.memfd_create("wasi-perl-module", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    off = 0
    while off < size:
        n = os.copy_file_range(fd, m, size - off, off, off) if way == "memfd-cfr" else os.sendfile(m, fd, off, size - off)
        if n == 0: break
        off += n
    fcntl.fcntl(m, 1033, 1|2|4|8); assert fcntl.fcntl(m, 1034) == 15
    mod = wt.Module.deserialize_file(eng, f"/proc/self/fd/{m}")
    os.close(m)
os.close(fd)
dt = (time.perf_counter() - t) * 1000
store = wt.Store(eng); store.set_epoch_deadline(1 << 40); l = wt.Linker(eng); l.define_wasi()
w = wt.WasiConfig(); w.argv = ["perl", "-e", "print 1"]; store.set_wasi(w)
inst = l.instantiate(store, mod)
try: inst.exports(store)["_start"](store)
except wt.ExitTrap: pass
print(way, f"{dt:.1f} ms", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024, "MiB")
