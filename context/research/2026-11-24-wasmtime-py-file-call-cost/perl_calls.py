"""wasi_perl.run on perl.wasm, stock against the patched C API with the blocking flag on:
median ms per warm call by scenario, and each call's exit code, stdout and stderr, so the two
arms can be checked equal.

    python perl_calls.py <src dir holding wasi_perl> <data dir> <calls> <out.jsonl> [--lib DIR [--blocking]]

The flag is set by wrapping wasmtime.WasiConfig's constructor, so it comes before the
runner's preopens, which keep the setting they were made with.
"""

import hashlib
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

args = sys.argv[1:]
src, data, calls, out = args[0], Path(args[1]), int(args[2]), args[3]
lib = args[args.index("--lib") + 1] if "--lib" in args else None
if lib:
    sys.path.insert(0, lib)
sys.path.insert(0, src)

import ctypes  # noqa: E402

import wasmtime as wt  # noqa: E402

if "--blocking" in args:
    fn = wt._ffi.dll.wasi_config_allow_blocking_current_thread
    fn.argtypes = [ctypes.c_void_p, ctypes.c_bool]
    fn.restype = None
    original = wt.WasiConfig.__init__

    def init(self, *a, **k):
        original(self, *a, **k)
        fn(self.ptr(), True)

    wt.WasiConfig.__init__ = init

import wasi_perl  # noqa: E402

G = wasi_perl.Grant
d = str(data)
many = sorted(str(p).replace(d, "/data") for p in (data / "many").glob("*.log"))
cases = {
    "bare -e 1": dict(argv=["-e", "1"]),
    "use strict, warnings, List::Util": dict(argv=["-MList::Util=sum", "-Mstrict", "-Mwarnings", "-e", "print sum(1..10)"]),
    "-ne over one 1 MB file": dict(argv=["-ne", "$n++ if /7/; END { print $n }", "/data/big.txt"], grants=[G(d, "/data")]),
    "-ne over 200 small files": dict(argv=["-ne", "$n++; END { print $n }", *many], grants=[G(d, "/data")]),
    "2,000 -e stats": dict(argv=["-e", 'for (1..2000) { -e "/data/big.txt" or die } print "ok"'], grants=[G(d, "/data")]),
}
cache = Path(tempfile.mkdtemp())
cache.chmod(0o700)
rows = []
for name, kw in cases.items():
    kw = dict(kw)
    argv = kw.pop("argv")
    for _ in range(2):
        wasi_perl.run(argv, cache_dir=cache, **kw)
    xs, seen = [], set()
    for _ in range(calls):
        t = time.perf_counter()
        r = wasi_perl.run(argv, cache_dir=cache, **kw)
        xs.append((time.perf_counter() - t) * 1000)
        seen.add((r.exit_code, hashlib.sha256(r.stdout).hexdigest()[:16], hashlib.sha256(r.stderr).hexdigest()[:16]))
    row = {
        "case": name,
        "arm": "patched+blocking" if "--blocking" in args else ("patched" if lib else "stock"),
        "calls": calls,
        "ms_median": round(statistics.median(xs), 2),
        "ms_min": round(min(xs), 2),
        "results": sorted(seen),
        "stdout": r.stdout.decode()[:40],
    }
    rows.append(row)
    print(json.dumps(row), flush=True)

# A sleep past the deadline: does the deadline still end it, and when?
t = time.perf_counter()
r = wasi_perl.run(["-e", "sleep 2; print 1"], deadline_s=0.5, cache_dir=cache)
row = {"case": "sleep 2 under a 0.5 s deadline", "arm": rows[0]["arm"], "s": round(time.perf_counter() - t, 2),
       "trapped": r.trapped, "exit_code": r.exit_code, "stdout": r.stdout.decode()}
rows.append(row)
print(json.dumps(row), flush=True)
with open(out, "a") as f:
    for row in rows:
        f.write(json.dumps(row) + "\n")
