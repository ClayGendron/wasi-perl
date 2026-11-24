"""Per-call cost of the grants alone: none, one read-only, one read-only plus one writable."""
import json, os, statistics, sys, tempfile, time
from pathlib import Path
import wasi_perl, wasi_python
d = Path(sys.argv[1]).resolve(); w = Path(tempfile.mkdtemp()); cache = Path(tempfile.mkdtemp()); os.chmod(cache, 0o700)
def med(f, n=30):
    for _ in range(3): f()
    xs = []
    for _ in range(n):
        t = time.perf_counter(); r = f(); xs.append(time.perf_counter() - t); assert r.exit_code == 0, r
    return round(statistics.median(xs) * 1000, 2)
res = {}
for name, mod, call in [
    ("perl", wasi_perl, lambda g: wasi_perl.run(["-e", 'print "hello\\n"'], grants=g, cache_dir=cache)),
    ("python/snapshot", wasi_python, lambda g: wasi_python.run(["-c", "print('hello')"], grants=g, cache_dir=cache)),
]:
    G = mod.Grant
    res[name] = {
        "no grant": med(lambda: call([])),
        "read-only grant": med(lambda: call([G(str(d), "/data")])),
        "read-only + writable grant": med(lambda: call([G(str(d), "/data"), G(str(w), "/w", writable=True)])),
    }
    print(name, res[name], flush=True)
# where a fresh process's first perl call goes (warm disk cache)
json.dump(res, open(sys.argv[2], "w"), indent=1)
