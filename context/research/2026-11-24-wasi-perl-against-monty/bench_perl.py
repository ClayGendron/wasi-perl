"""wasi-perl's side of the Monty comparison. Run with the 014 worktree's venv and
WASI_PERL_ARTIFACTS set to the perf-base artifact set.

Subcommands:
  out DATA OUT.json           each workload's stdout, once
  calls DATA OUT.json [REPS]  warm calls in one process: median, min, max per workload
  fuel DATA OUT.json          guest WebAssembly instructions per workload (metered pass)
  memory DATA OUT.json        the smallest linear memory each workload runs in (bisection, 64 KiB)
  first CACHE DATA            one fresh process: import, first call, second call; prints JSON
  stdin DATA OUT.json         read_big through stdin instead of a grant
  idle OUT.json               hello after the process has slept 1 s, 15 times (Monty's own method)
  flag LIBDIR DATA OUT.json [--blocking]
                              warm calls through a wasmtime-py copy whose C API carries the
                              allow_blocking_current_thread patch (the file-call memo's), with the
                              flag off or on; the workloads that touch files, and hello
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workloads import W  # noqa: E402

REPS = {"hello": 100, "fib25": 10, "loop": 10, "strcat": 10, "strjoin": 10}
DEFAULT_REPS = 10


def caller(data: Path):
    import wasi_perl

    g = [wasi_perl.Grant(str(data), "/data")]

    def call(name: str, **kw):
        t = time.perf_counter()
        r = wasi_perl.run(W[name][0], grants=g, **kw)
        s = time.perf_counter() - t
        if r.exit_code != 0:
            raise SystemExit(f"{name}: {r.exit_code} {r.trapped} {r.stderr[:300]!r}")
        return s, r

    return call


def summary(xs: list[float]) -> dict:
    xs = sorted(xs)
    return {
        "n": len(xs),
        "median_ms": round(statistics.median(xs) * 1000, 3),
        "min_ms": round(xs[0] * 1000, 3),
        "max_ms": round(xs[-1] * 1000, 3),
    }


def out(data: Path, dest: Path) -> None:
    call = caller(data)
    dest.write_text(json.dumps({n: call(n)[1].stdout.decode() for n in W}, indent=1))


def calls(data: Path, dest: Path, reps: int | None = None) -> None:
    call = caller(data)
    res = {}
    for n in W:
        for _ in range(2):
            call(n)
        k = reps or REPS.get(n, DEFAULT_REPS)
        res[n] = summary([call(n)[0] for _ in range(k)])
        print(n, res[n], flush=True)
    dest.write_text(json.dumps(res, indent=1))


def fuel(data: Path, dest: Path) -> None:
    call = caller(data)
    res = {n: call(n, fuel=10**13, deadline_s=None)[1].fuel_used for n in W}
    print(res)
    dest.write_text(json.dumps(res, indent=1))


def memory(data: Path, dest: Path) -> None:
    """Linear memory is counted in 64 KiB pages; find the fewest pages each call completes in."""
    import wasi_perl

    g = [wasi_perl.Grant(str(data), "/data")]
    page = 64 * 1024
    res = {}
    for n in W:
        lo, hi = 1, (1 << 30) // page  # lo fails (or is untested), hi succeeds
        while hi - lo > 1:
            mid = (lo + hi) // 2
            r = wasi_perl.run(W[n][0], grants=g, memory_bytes=mid * page)
            if r.exit_code == 0 and r.trapped is None:
                hi = mid
            else:
                lo = mid
        res[n] = {"pages": hi, "mib": round(hi * page / 2**20, 2)}
        print(n, res[n], flush=True)
    dest.write_text(json.dumps(res, indent=1))


def first(cache: str, data: Path) -> None:
    import resource

    t0 = time.perf_counter()
    import wasi_perl

    imp = time.perf_counter() - t0
    t = time.perf_counter()
    r = wasi_perl.run(W["hello"][0], cache_dir=cache)
    first_s = time.perf_counter() - t
    t = time.perf_counter()
    r2 = wasi_perl.run(W["hello"][0], cache_dir=cache)
    second_s = time.perf_counter() - t
    assert r.exit_code == 0 and r2.exit_code == 0
    print(
        json.dumps(
            {
                "import_ms": round(imp * 1000, 2),
                "first_call_ms": round(first_s * 1000, 2),
                "second_call_ms": round(second_s * 1000, 2),
                "max_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
            }
        )
    )


def stdin(data: Path, dest: Path) -> None:
    import wasi_perl

    body = (data / "t.log").read_bytes()
    argv = ["-0777", "-ne", 'print length, "\\n"']
    ts = []
    for i in range(12):
        t = time.perf_counter()
        r = wasi_perl.run(argv, stdin=body)
        if i >= 2:
            ts.append(time.perf_counter() - t)
        assert r.exit_code == 0, r
    res = {"read_big_stdin": summary(ts), "stdout": r.stdout.decode()}
    print(res)
    dest.write_text(json.dumps(res, indent=1))


def idle(dest: Path) -> None:
    import wasi_perl

    for _ in range(3):
        wasi_perl.run(W["hello"][0])
    ts = []
    for _ in range(15):
        time.sleep(1.0)
        t = time.perf_counter()
        wasi_perl.run(W["hello"][0])
        ts.append(time.perf_counter() - t)
    res = summary(ts)
    print(res)
    dest.write_text(json.dumps(res, indent=1))


FLAG_WORK = ["hello", "sum_csv", "grep_log", "split_join", "wordfreq", "read_big", "read_small"]


def flag(lib: str, data: Path, dest: Path, blocking: bool) -> None:
    """The flag must be set before the first preopen, so WasiConfig's constructor is wrapped."""
    sys.path.insert(0, lib)
    import ctypes

    import wasmtime as wt

    assert wt.__file__.startswith(lib), wt.__file__
    if blocking:
        fn = wt._ffi.dll.wasi_config_allow_blocking_current_thread
        fn.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        fn.restype = None
        original = wt.WasiConfig.__init__

        def init(self, *a, **k):
            original(self, *a, **k)
            fn(self.ptr(), True)

        wt.WasiConfig.__init__ = init
    call = caller(data)
    res = {}
    for n in FLAG_WORK:
        for _ in range(2):
            o = call(n)[1].stdout.decode()
        k = REPS.get(n, DEFAULT_REPS)
        res[n] = {**summary([call(n)[0] for _ in range(k)]), "stdout": o}
        print(n, res[n], flush=True)
    dest.write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    cmd, *a = sys.argv[1:]
    if cmd == "first":
        first(a[0], Path(a[1]))
    elif cmd == "flag":
        flag(a[0], Path(a[1]), Path(a[2]), "--blocking" in a)
    elif cmd == "idle":
        idle(Path(a[0]))
    elif cmd == "calls":
        calls(Path(a[0]), Path(a[1]), int(a[2]) if len(a) > 2 else None)
    else:
        {"out": out, "fuel": fuel, "memory": memory, "stdin": stdin}[cmd](Path(a[0]), Path(a[1]))
