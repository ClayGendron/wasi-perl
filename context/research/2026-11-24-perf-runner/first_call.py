"""A fresh process's first and second run() with a warm module cache, split by phase.

    python first_call.py <cache dir>

Prints one JSON line: import, the first call, its artifact check and module load (each timed by
wrapping the runner's own function), and the second call.
"""
import json
import sys
import time

t = time.perf_counter()
import wasi_perl
from wasi_perl import _artifacts, _engines

imp = time.perf_counter() - t
acc = {}


def wrap(owner, name):
    fn = getattr(owner, name)

    def w(*a, **k):
        t = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            acc[name] = acc.get(name, 0) + time.perf_counter() - t

    setattr(owner, name, w)


wrap(_artifacts, "checked")
wrap(_engines, "module")
cache = sys.argv[1]
t = time.perf_counter()
r = wasi_perl.run(["-e", "1"], cache_dir=cache)
first = time.perf_counter() - t
assert r.exit_code == 0, r
t = time.perf_counter()
wasi_perl.run(["-e", "1"], cache_dir=cache)
second = time.perf_counter() - t
print(json.dumps({"import_ms": round(imp * 1e3, 1), "first_call_ms": round(first * 1e3, 1),
                  "artifact_check_ms": round(acc["checked"] * 1e3, 1),
                  "module_ms": round(acc["module"] * 1e3, 1),
                  "second_call_ms": round(second * 1e3, 1), "counts": _engines.counts()}))
