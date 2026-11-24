"""Median ms per warm run() call, by scenario, for whichever wasi_perl is imported.

    python calls.py <label> <calls> <out.jsonl>
"""
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

import wasi_perl

label, calls, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
data = Path(tempfile.mkdtemp())
(data / "f.txt").write_text("a\nb\n")
w = Path(tempfile.mkdtemp())
cache = Path(tempfile.mkdtemp())
cache.chmod(0o700)
G = wasi_perl.Grant
HELLO = ["-e", 'print "hello\\n"']
cases = {
    "bare": dict(argv=HELLO),
    "ro": dict(argv=HELLO, grants=[G(str(data), "/data")]),
    "rw": dict(argv=HELLO, grants=[G(str(data), "/data"), G(str(w), "/w", writable=True)]),
    "stdin+env": dict(argv=["-ne", "print"], stdin=b"x\n" * 100, env={"A": "1"}),
    "fuel": dict(argv=HELLO, fuel=10**10),
    "cat ro file": dict(argv=["-ne", "print", "/data/f.txt"], grants=[G(str(data), "/data")]),
}
res = {"label": label, "module": wasi_perl.__file__}
for name, kw in cases.items():
    argv = kw.pop("argv")
    for _ in range(3):
        wasi_perl.run(argv, cache_dir=cache, **kw)
    xs = []
    for _ in range(calls):
        t = time.perf_counter()
        r = wasi_perl.run(argv, cache_dir=cache, **kw)
        xs.append(time.perf_counter() - t)
        assert r.exit_code == 0, r
    res[name] = round(statistics.median(xs) * 1e3, 3)
print(json.dumps(res))
with open(out, "a") as f:
    f.write(json.dumps(res) + "\n")
