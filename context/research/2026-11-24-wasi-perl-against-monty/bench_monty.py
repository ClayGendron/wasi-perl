"""Monty's side of the comparison: pydantic-monty 1.6.0 from PyPI, in its own venv.
Run with `python -I`; this folder is put on sys.path by hand for `workloads`.

Modes of a call:
  session   one checkout, every call a `feed_run` on it (a REPL: state persists between calls)
  checkout  a pool already open, a new checkout per call (the worker process is reused)
  fresh     the same with max_checkouts_per_worker=1: every session a new worker process
  cli       `monty -m DATA::/data -c CODE` as a subprocess

Subcommands:
  out DATA OUT.json                 each workload's stdout, from the `checkout` mode
  calls MODE DATA OUT.json [REPS]   warm calls: median, min, max per workload
  memory DATA OUT.json              VmHWM of a fresh worker after each workload
  first DATA                        one fresh process: import, pool, first and second call
  hostdata DATA OUT.json            read_big and read_small through inputs and host functions
  idle OUT.json                     a new checkout and hello after 1 s asleep, 15 times (Monty's method)
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workloads import W  # noqa: E402

REPS = {"hello": 100, "fib25": 10, "loop": 10, "strcat": 10, "strjoin": 10}
DEFAULT_REPS = 10


def summary(xs: list[float]) -> dict:
    xs = sorted(xs)
    return {
        "n": len(xs),
        "median_ms": round(statistics.median(xs) * 1000, 3),
        "min_ms": round(xs[0] * 1000, 3),
        "max_ms": round(xs[-1] * 1000, 3),
    }


def binary() -> str:
    from pydantic_monty._binary import find_monty_binary

    return find_monty_binary()


class Caller:
    def __init__(self, mode: str, data: Path):
        import pydantic_monty as pm

        self.pm, self.mode, self.data = pm, mode, data
        self.mount = pm.MountDir(host_path=str(data), virtual_path="/data", mode="read-only")
        self.pool = None
        self.session = None
        if mode in ("session", "checkout", "fresh"):
            per = 1 if mode == "fresh" else 1000
            self.pool = pm.Monty(max_checkouts_per_worker=per).__enter__()
        if mode == "session":
            # Every mount read is a host round trip, and a session allows 1,000 by default:
            # a REPL fed these workloads 14 times over reaches that, so it gets a larger budget.
            self.session = self.pool.checkout(limits={"max_suspensions": 10**9}).__enter__()
        self.bin = binary()

    def __call__(self, name: str, **feed) -> tuple[float, str, int | None]:
        code = W[name][1]
        pm = self.pm
        pid = None
        if self.mode == "cli":
            t = time.perf_counter()
            p = subprocess.run(
                [self.bin, "-m", f"{self.data}::/data", "-c", code], capture_output=True, env={}
            )
            s = time.perf_counter() - t
            if p.returncode != 0:
                raise SystemExit(f"cli {name}: {p.returncode} {p.stderr[:300]!r}")
            return s, p.stdout.decode(), None
        out = pm.CollectString()
        t = time.perf_counter()
        if self.session is not None:
            self.session.feed_run(code, mount=self.mount, print_callback=out, **feed)
        else:
            with self.pool.checkout() as sess:
                sess.feed_run(code, mount=self.mount, print_callback=out, **feed)
                pid = sess.worker_pid
        s = time.perf_counter() - t
        return s, out.output, pid

    def close(self):
        if self.session is not None:
            self.session.__exit__(None, None, None)
        if self.pool is not None:
            self.pool.__exit__(None, None, None)
        self.mount.close()


def out(data: Path, dest: Path) -> None:
    c = Caller("checkout", data)
    res = {n: c(n)[1] for n in W}
    c.close()
    dest.write_text(json.dumps(res, indent=1))


def calls(mode: str, data: Path, dest: Path, reps: int | None = None) -> None:
    c = Caller(mode, data)
    res = {}
    for n in W:
        k = reps or REPS.get(n, DEFAULT_REPS)
        if c(n)[0] > 2.0:  # a cliff (strcat, sub_log): one warm-up and three timed calls
            k = 3
        else:
            c(n)
        res[n] = summary([c(n)[0] for _ in range(k)])
        print(mode, n, res[n], flush=True)
    c.close()
    dest.write_text(json.dumps(res, indent=1))


def vm_hwm_kib(pid: int) -> int:
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmHWM:"):
            return int(line.split()[1])
    raise RuntimeError("no VmHWM")


def memory(data: Path, dest: Path) -> None:
    """A fresh worker per session; its peak RSS read while the session is still checked out."""
    import pydantic_monty as pm

    mount = pm.MountDir(host_path=str(data), virtual_path="/data", mode="read-only")
    res = {}
    with pm.Monty(max_checkouts_per_worker=1) as pool:
        for n in W:
            hw = []
            for _ in range(3):
                with pool.checkout() as sess:
                    sess.feed_run(W[n][1], mount=mount, print_callback=pm.CollectString())
                    hw.append(vm_hwm_kib(sess.worker_pid))
            res[n] = {"vm_hwm_mib": [round(h / 1024, 2) for h in hw]}
            print(n, res[n], flush=True)
    mount.close()
    dest.write_text(json.dumps(res, indent=1))


def first(data: Path) -> None:
    import resource

    t0 = time.perf_counter()
    import pydantic_monty as pm

    imp = time.perf_counter() - t0
    t = time.perf_counter()
    pool = pm.Monty().__enter__()
    pool_s = time.perf_counter() - t
    t = time.perf_counter()
    with pool.checkout() as sess:
        sess.feed_run(W["hello"][1], print_callback=pm.CollectString())
        pid = sess.worker_pid
    first_s = time.perf_counter() - t
    t = time.perf_counter()
    with pool.checkout() as sess:
        sess.feed_run(W["hello"][1], print_callback=pm.CollectString())
    second_s = time.perf_counter() - t
    worker_hwm = vm_hwm_kib(pid) / 1024
    pool.__exit__(None, None, None)
    print(
        json.dumps(
            {
                "import_ms": round(imp * 1000, 2),
                "pool_ms": round(pool_s * 1000, 2),
                "first_call_ms": round(first_s * 1000, 2),
                "second_call_ms": round(second_s * 1000, 2),
                "max_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
                "worker_hwm_mib": round(worker_hwm, 1),
            }
        )
    )


def hostdata(data: Path, dest: Path) -> None:
    """The ways a Monty program gets host data besides a mount, on one open checkout per call."""
    import pydantic_monty as pm

    body = (data / "t.log").read_text()
    small = [(data / "small" / f"f{i}.txt").read_text() for i in range(100)]
    cases = {
        "read_big_inputs": ("print(len(data))", {"inputs": {"data": body}}),
        "read_big_hostfn": (
            "print(len(get_data()))",
            {"external_lookup": {"get_data": lambda: body}},
        ),
        "read_small_hostfn": (
            "n = 0\nfor i in range(100):\n    n += len(get(i))\nprint(n)",
            {"external_lookup": {"get": lambda i: small[i]}},
        ),
    }
    res = {}
    with pm.Monty() as pool:
        for k, (code, kw) in cases.items():
            ts, o = [], None
            for i in range(12):
                out = pm.CollectString()
                t = time.perf_counter()
                with pool.checkout() as sess:
                    sess.feed_run(code, print_callback=out, **kw)
                if i >= 2:
                    ts.append(time.perf_counter() - t)
                o = out.output
            res[k] = {**summary(ts), "stdout": o}
            print(k, res[k], flush=True)
    dest.write_text(json.dumps(res, indent=1))


def idle(dest: Path) -> None:
    import pydantic_monty as pm

    ts = []
    with pm.Monty() as pool:
        for _ in range(3):
            with pool.checkout() as sess:
                sess.feed_run(W["hello"][1], print_callback=pm.CollectString())
        for _ in range(15):
            time.sleep(1.0)
            t = time.perf_counter()
            with pool.checkout() as sess:
                sess.feed_run(W["hello"][1], print_callback=pm.CollectString())
            ts.append(time.perf_counter() - t)
    res = summary(ts)
    print(res)
    dest.write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    cmd, *a = sys.argv[1:]
    if cmd == "calls":
        calls(a[0], Path(a[1]), Path(a[2]), int(a[3]) if len(a) > 3 else None)
    elif cmd == "first":
        first(Path(a[0]))
    elif cmd == "idle":
        idle(Path(a[0]))
    else:
        {"out": out, "memory": memory, "hostdata": hostdata}[cmd](Path(a[0]), Path(a[1]))
    sys.stdout.flush()
    os._exit(0)
