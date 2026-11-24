"""Time wasi-perl 0.1.0 against wasi-python 0.1.6 on the same wasmtime-py 51, same machine.

Subcommands:
  data DIR                     write the inputs (deterministic)
  calls DIR OUT.json           per-call times in one warm process, every tool and mode
  fuel DIR OUT.json            guest instructions per workload (metered engine), every mode
  first TOOL MODE CACHE DIR    one fresh process's first call; prints JSON (driven by `firsts`)
  firsts DIR OUT.json          `first` in fresh subprocesses: cold cache, warm cache, no cache
  floor DIR OUT.json           perl.wasm instantiated without running main, against `-e 1`
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

N_LINES = 200_000

PERL = {
    "hello": ["-e", 'print "hello\\n"'],
    "sum_csv": ["-F,", "-lane", "$s += $F[2]; END { print $s }", "/data/t.csv"],
    "grep_log": ["-ne", "$n++ if /ERROR \\d+/; END { print $n }", "/data/t.log"],
    "inplace": ["-pi", "-e", "s/ERROR/WARN/g", "/w/t.log"],
    "json": ["-MJSON::PP", "-0777", "-ne", 'print scalar(@{decode_json($_)}), "\\n"', "/data/t.json"],
    "loop": ["-e", 'my $s = 0; $s += $_ * $_ for 1 .. 1_000_000; print "$s\\n"'],
}
PY = {
    "hello": 'print("hello")',
    "sum_csv": "s = 0\nfor line in open('/data/t.csv'):\n    s += int(line.split(',')[2])\nprint(s)",
    "grep_log": (
        "import re\nr = re.compile(r'ERROR \\d+')\n"
        "print(sum(1 for line in open('/data/t.log') if r.search(line)))"
    ),
    "inplace": (
        "import re\np = '/w/t.log'\nt = open(p).read()\n"
        "open(p, 'w').write(re.sub('ERROR', 'WARN', t))"
    ),
    "json": "import json\nprint(len(json.load(open('/data/t.json'))))",
    "loop": "s = 0\nfor i in range(1, 1000001):\n    s += i * i\nprint(s)",
}
PRELUDE = "import json, re"  # what the workloads import: the embedder's snapshot image
MODES = [("perl", "none"), ("python", "plain"), ("python", "snapshot"), ("python", "image")]
REPS = {"hello": 30, "sum_csv": 10, "grep_log": 10, "inplace": 10, "json": 5, "loop": 5}


def make_data(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "t.csv", "w", newline="\n") as f:
        for i in range(N_LINES):
            f.write(f"{i},name{i % 977},{(i * 7919) % 10007},{'yes' if i % 3 else 'no'}\n")
    with open(d / "t.log", "w", newline="\n") as f:
        for i in range(N_LINES):
            level = f"ERROR {i % 500}" if i % 7 == 0 else "INFO ok"
            f.write(f"2026-11-24T10:{i % 60:02d}:{i % 59:02d} worker{i % 16} {level} request {i}\n")
    rows = [{"id": i, "name": f"n{i}", "tags": ["a", "b", i % 5], "v": i * 0.5} for i in range(20_000)]
    (d / "t.json").write_text(json.dumps(rows))


def grants(tool: str, d: Path, w: Path, work: str):
    """Read-only /data for every workload; a writable /w only for the in-place edit, since a
    writable grant has a per-call cost of its own (measured apart, in grants_probe.py)."""
    mod = __import__("wasi_perl" if tool == "perl" else "wasi_python")
    g = [mod.Grant(str(d), "/data")]
    if work == "inplace":
        g.append(mod.Grant(str(w), "/w", writable=True))
    return g


class Caller:
    """One tool in one mode: a call of a workload, with the same grants every time."""

    def __init__(self, tool: str, mode: str, d: Path, cache, fuel=None):
        self.tool, self.mode, self.d, self.cache, self.fuel = tool, mode, d, cache, fuel
        self.w = Path(tempfile.mkdtemp(prefix="w-"))
        self.image = None
        self.image_s = None
        if tool == "perl":
            import wasi_perl

            self.mod = wasi_perl
        else:
            import wasi_python

            self.mod = wasi_python
            if mode == "image":
                t = time.perf_counter()
                self.image = wasi_python.make_snapshot(PRELUDE, cache_dir=cache)
                self.image_s = time.perf_counter() - t

    def __call__(self, work: str):
        shutil.copyfile(self.d / "t.log", self.w / "t.log")  # in-place edits start fresh
        g = grants(self.tool, self.d, self.w, work)
        kw = {"grants": g, "cache_dir": self.cache, "fuel": self.fuel}
        t = time.perf_counter()
        if self.tool == "perl":
            r = self.mod.run(PERL[work], **kw)
        else:
            snap = {"plain": False, "snapshot": True, "image": self.image}[self.mode]
            r = self.mod.run(["-c", PY[work]], snapshot=snap, **kw)
        s = time.perf_counter() - t
        if r.exit_code != 0:
            raise SystemExit(f"{self.tool}/{self.mode}/{work}: {r.exit_code} {r.trapped} {r.stderr[:300]!r}")
        out = r.stdout
        if work == "inplace":
            out = str((self.w / "t.log").read_bytes().count(b"WARN")).encode() + b"\n"
        return s, out, r.fuel_used


def summary(xs: list[float]) -> dict:
    xs = sorted(xs)
    return {
        "n": len(xs),
        "median_ms": round(statistics.median(xs) * 1000, 2),
        "min_ms": round(xs[0] * 1000, 2),
        "p90_ms": round(xs[min(len(xs) - 1, int(len(xs) * 0.9))] * 1000, 2),
    }


def calls(d: Path, out: Path) -> None:
    cache = Path(tempfile.mkdtemp(prefix="cache-"))
    os.chmod(cache, 0o700)
    res = {}
    for tool, mode in MODES:
        c = Caller(tool, mode, d, cache)
        key = f"{tool}/{mode}"
        res[key] = {"image_make_s": c.image_s} if c.image_s else {}
        for work in PERL:
            for _ in range(2):  # warm-up: compile, cache, first tree check
                _, o, _ = c(work)
            times = [c(work)[0] for _ in range(REPS[work])]
            res[key][work] = {**summary(times), "stdout": o.decode().strip()}
            print(key, work, res[key][work], flush=True)
    out.write_text(json.dumps(res, indent=1))


def fuel(d: Path, out: Path) -> None:
    cache = Path(tempfile.mkdtemp(prefix="cache-"))
    os.chmod(cache, 0o700)
    res = {}
    for tool, mode in MODES:
        c = Caller(tool, mode, d, cache, fuel=10**13)
        res[f"{tool}/{mode}"] = {w: c(w)[2] for w in PERL}
        print(tool, mode, res[f"{tool}/{mode}"], flush=True)
    out.write_text(json.dumps(res, indent=1))


def first(tool: str, mode: str, cache_kind: str, d: Path) -> None:
    """In a fresh process: import, then one hello call. CACHE is a folder, or 'off'."""
    t0 = time.perf_counter()
    c = Caller.__new__(Caller)
    cache = False if cache_kind == "off" else cache_kind
    t = time.perf_counter()
    if tool == "perl":
        import wasi_perl as m
    else:
        import wasi_python as m
    imp = time.perf_counter() - t
    w = Path(tempfile.mkdtemp(prefix="w-"))
    t = time.perf_counter()
    if tool == "perl":
        r = m.run(PERL["hello"], cache_dir=cache)
    else:
        r = m.run(["-c", PY["hello"]], snapshot=(mode == "snapshot"), cache_dir=cache)
    call = time.perf_counter() - t
    t = time.perf_counter()
    if tool == "perl":
        r2 = m.run(PERL["hello"], cache_dir=cache)
    else:
        r2 = m.run(["-c", PY["hello"]], snapshot=(mode == "snapshot"), cache_dir=cache)
    second = time.perf_counter() - t
    assert r.exit_code == 0 and r2.exit_code == 0, (r, r2)
    del c, w
    import resource

    print(
        json.dumps(
            {
                "import_s": imp,
                "first_call_s": call,
                "second_call_s": second,
                "in_process_s": time.perf_counter() - t0,
                "max_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            }
        )
    )


def firsts(d: Path, out: Path) -> None:
    res = {}
    for tool, mode in [("perl", "none"), ("python", "plain"), ("python", "snapshot")]:
        cache = Path(tempfile.mkdtemp(prefix="cache-"))
        os.chmod(cache, 0o700)
        for kind, reps in [("cold", 3), ("warm", 5), ("off", 3)]:
            rows = []
            for _ in range(reps):
                if kind == "cold":
                    shutil.rmtree(cache)
                    cache.mkdir(mode=0o700)
                arg = "off" if kind == "off" else str(cache)
                t = time.perf_counter()
                p = subprocess.run(
                    [sys.executable, __file__, "first", tool, mode, arg, str(d)],
                    capture_output=True, text=True, check=True,
                )
                wall = time.perf_counter() - t
                rows.append({**json.loads(p.stdout.strip().splitlines()[-1]), "process_wall_s": wall})
            agg = {k: round(statistics.median(r[k] for r in rows), 3) for k in rows[0]}
            res[f"{tool}/{mode}/{kind}"] = agg
            print(tool, mode, kind, agg, flush=True)
        res[f"{tool}/{mode}/cwasm_bytes"] = sum(f.stat().st_size for f in cache.rglob("*.cwasm"))
    out.write_text(json.dumps(res, indent=1))


def floor(d: Path, out: Path) -> None:
    """perl.wasm instantiated with the runner's engine and module, main not run; against a full
    `-e 1` call. The gap is all a snapshot of a started perl could save on such a call."""
    import wasi_perl
    from wasi_perl import _artifacts, _engines

    cache = Path(tempfile.mkdtemp(prefix="cache-"))
    os.chmod(cache, 0o700)
    for _ in range(3):
        wasi_perl.run(["-e", "1"], cache_dir=cache)
    found, want = _artifacts.checked()
    wt = _engines.wasmtime()
    eng = _engines.engine(False)
    mod = _engines.module(False, found.perl, want, cache)

    def inst():
        store = wt.Store(eng)
        wasi = wt.WasiConfig()
        wasi.argv = ["perl", "-e", "1"]
        wasi.preopen_dir(str(found.lib), "/usr/local/lib/perl5", False)
        store.set_wasi(wasi)
        store.set_epoch_deadline(1 << 40)
        linker = wt.Linker(eng)
        linker.define_wasi()
        linker.instantiate(store, mod)
        store.close()

    def timed(f, n=50):
        xs = []
        for _ in range(n):
            t = time.perf_counter()
            f()
            xs.append(time.perf_counter() - t)
        return summary(xs)

    res = {
        "instantiate_only": timed(inst),
        "run_e_1": timed(lambda: wasi_perl.run(["-e", "1"], cache_dir=cache)),
        "run_MPOSIX_e_1": timed(lambda: wasi_perl.run(["-MPOSIX", "-e", "1"], cache_dir=cache)),
        "run_MJSON_PP_e_1": timed(lambda: wasi_perl.run(["-MJSON::PP", "-e", "1"], cache_dir=cache)),
    }
    print(res)
    out.write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    cmd, *a = sys.argv[1:]
    if cmd == "data":
        make_data(Path(a[0]))
    elif cmd == "first":
        first(a[0], a[1], a[2], Path(a[3]))
    else:
        {"calls": calls, "fuel": fuel, "firsts": firsts, "floor": floor}[cmd](Path(a[0]), Path(a[1]))
