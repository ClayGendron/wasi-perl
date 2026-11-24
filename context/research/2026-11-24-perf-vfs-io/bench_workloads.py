"""Step 3: the workloads' wall time per call, through a grant and through the prototype's mount
under each design, with an in-memory provider and with one that sleeps on every call to stand
in for a database round trip. Also design (d), staging to a host folder that wasmtime opens
natively, done eagerly: the files the program will touch are read from the provider and written
to a fresh folder before the call, and changed files are written back after it.

    flock /home/user/wasi-perl-wt/machine.lock python bench_workloads.py results/workloads.json

Median of REPEAT calls after one warm-up, in one process with a warm module cache.
"""

from __future__ import annotations

import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

import harness as h

from wasi_perl import Grant, run
from wasi_perl._mounts import MemoryProvider, Mount

REPEAT = 5
DELAYED_REPEAT = 3
DELAY_S = 0.001
DESIGNS = {
    "a_wasi_python": dict(defer_create=False, memo=False, stat_reads=False),
    "all_four": dict(defer_create=True, memo=True, stat_reads=True, defer_close=True),
}
NAMES = (
    "hello",
    "ne_50k",
    "lane_5m",
    "slurp_5m",
    "pi_50k",
    "grep_args_500",
    "grep_glob_500",
    "write_new",
)


class Counting(MemoryProvider):
    """A MemoryProvider that counts its calls."""

    def __init__(self, files: dict[str, bytes], delay_s: float = 0.0) -> None:
        super().__init__(files, delay_s)
        self.calls = 0

    def __getattribute__(self, name: str):  # type: ignore[no-untyped-def]
        if name in ("stat", "list", "read", "write", "mkdir", "remove", "rmdir", "rename"):
            object.__setattr__(self, "calls", object.__getattribute__(self, "calls") + 1)
        return object.__getattribute__(self, name)


def staged_eagerly(w: h.Workload, files: dict[str, bytes], delay_s: float) -> tuple[float, int]:
    """Design (d), eager: stage what the program names (a file argument, or the folder a glob
    or readdir walks), run on a grant, write back what changed. (seconds, provider calls)"""
    provider = Counting(files, delay_s)
    t0 = time.perf_counter()
    folder = Path(tempfile.mkdtemp(prefix="stage-"))
    wanted: list[str] = []
    for arg in w.argv:
        if arg.startswith("/work/") and " " not in arg:
            wanted.append(arg[len("/work") :])
    if "glob" in w.name or "opendir" in w.name:
        wanted += [f"/notes/{n}" for n, _ in provider.list("/notes")]  # type: ignore[union-attr]
    if w.name == "write_new":
        (folder / "data").mkdir()
    before: dict[str, bytes] = {}
    for path in wanted:
        data = provider.read(path)
        assert isinstance(data, bytes)
        target = folder / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        before[path] = data
    done = run(w.argv, grants=[Grant(folder, "/work", writable=w.writable)])
    assert done.exit_code == 0, done
    if w.writable:
        for p in folder.rglob("*"):
            if p.is_file():
                path = "/" + str(p.relative_to(folder))
                data = p.read_bytes()
                if before.get(path) != data:
                    provider.write(path, data)
    shutil.rmtree(folder)
    return time.perf_counter() - t0, provider.calls


def median_of(fn, n: int) -> tuple[float, int]:
    fn()
    runs = [fn() for _ in range(n)]
    return statistics.median(r[0] for r in runs), runs[-1][1]


def main() -> None:
    out = Path(sys.argv[1])
    files = h.make_data()
    host = Path(tempfile.mkdtemp())
    results: dict[str, dict[str, list[float]]] = {}
    for w in [w for w in h.workloads("/work") if w.name in NAMES]:
        row: dict[str, list[float]] = {}

        def grant() -> tuple[float, int]:
            shutil.rmtree(host, ignore_errors=True)
            h.write_tree(files, host)
            t = time.perf_counter()
            done = run(w.argv, grants=[Grant(host, "/work", writable=w.writable)])
            assert done.exit_code == 0, done
            return time.perf_counter() - t, 0

        row["grant"] = list(median_of(grant, REPEAT))
        for delay, n in ((0.0, REPEAT), (DELAY_S, DELAYED_REPEAT)):
            for design, switches in DESIGNS.items():

                def mounted() -> tuple[float, int]:
                    provider = Counting(files, delay)
                    mount = Mount(provider, "/work", writable=w.writable, **switches)
                    t = time.perf_counter()
                    done = run(w.argv, mounts=[mount])
                    assert done.exit_code == 0, done
                    return time.perf_counter() - t, provider.calls

                row[f"{design}@{delay * 1000:g}ms"] = list(median_of(mounted, n))
            row[f"d_staged@{delay * 1000:g}ms"] = list(
                median_of(lambda: staged_eagerly(w, files, delay), n)
            )
        results[w.name] = row
        print(w.name, {k: (round(v[0] * 1000, 1), v[1]) for k, v in row.items()}, flush=True)
    out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
