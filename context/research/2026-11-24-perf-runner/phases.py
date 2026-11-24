"""A per-phase profile of wasi_perl.run() in a warm process.

Each phase is a function of the runner or a wasmtime-py method, wrapped with a timer, so the
code that runs is the runner's own. Prints the median per call of each phase, in microseconds.

    python phases.py <scenario> <calls> [out.json]

Scenarios: bare (no grant), ro (one read-only grant), rw (read-only and writable), stdin,
fuel (a fuel cap).
"""
import json
import statistics
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import wasmtime as wt
import wasi_perl
from wasi_perl import _artifacts, _cache, _engines, _grants, _limits, _stop, runner

acc: dict[str, float] = defaultdict(float)


def timed(owner, name, label=None):
    fn = getattr(owner, name)
    label = label or f"{getattr(owner, '__name__', owner)}.{name}"

    def wrapper(*a, **k):
        t = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            acc[label] += time.perf_counter() - t

    setattr(owner, name, wrapper)


for owner, names in [
    (_limits, ["check_inputs", "check_limits", "reserve_descriptors", "release_descriptors"]),
    (_grants, ["check", "check_cwd", "judge", "hold", "let_go"]),
    (_cache, ["given", "every"]),
    (_artifacts, ["checked"]),
    (runner, ["_runtime_folders", "_runtime_files", "_call", "_set_up", "_drive"]),
    (_engines, ["module", "engine", "wasmtime"]),
    (_stop, ["arm"]),
]:
    for n in names:
        timed(owner, n, f"{owner.__name__.split('.')[-1]}.{n}")
for cls, names in [
    (wt.Store, ["__init__", "close", "set_wasi", "set_epoch_deadline", "set_limits", "set_fuel"]),
    (wt.WasiConfig, ["__init__", "preopen_dir"]),
    (wt.Linker, ["__init__", "define_wasi", "instantiate"]),
    (wt.Func, ["__call__"]),
    (wt.Instance, ["exports"]),
]:
    for n in names:
        timed(cls, n, f"wt.{cls.__name__}.{n}")

scenario = sys.argv[1]
calls = int(sys.argv[2])
data = Path(tempfile.mkdtemp())
(data / "f.txt").write_text("a\nb\n")
w = Path(tempfile.mkdtemp())
cache = Path(tempfile.mkdtemp())
cache.chmod(0o700)
G = wasi_perl.Grant
grants = {
    "bare": [],
    "ro": [G(str(data), "/data")],
    "rw": [G(str(data), "/data"), G(str(w), "/w", writable=True)],
    "stdin": [],
    "fuel": [],
}[scenario]
kw = {"stdin": b"x\n" * 100} if scenario == "stdin" else {}
if scenario == "fuel":
    kw["fuel"] = 10**10


def one():
    r = wasi_perl.run(["-e", 'print "hello\\n"'], grants=grants, cache_dir=cache, **kw)
    assert r.exit_code == 0 and r.stdout == b"hello\n", r


for _ in range(3):
    one()
per: dict[str, list[float]] = defaultdict(list)
for _ in range(calls):
    acc.clear()
    t = time.perf_counter()
    one()
    acc["TOTAL run()"] = time.perf_counter() - t
    for k, v in acc.items():
        per[k].append(v)
med = {k: round(statistics.median(v) * 1e6, 1) for k, v in per.items()}
for k, v in sorted(med.items(), key=lambda kv: -kv[1]):
    print(f"{v:10.1f} us  {k}")
if len(sys.argv) > 3:
    json.dump({"scenario": scenario, "calls": calls, "median_us": med}, open(sys.argv[3], "w"), indent=1)
