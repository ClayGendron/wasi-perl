"""Measure one perl.wasm build under one wasmtime engine configuration.

The workloads are the baseline memo's six (2026-11-24-wasi-perl-against-wasi-python-on-speed,
bench.py), with the same inputs, plus four that reach the paths the setjmp/longjmp lowering
touches: sub calls, die caught by eval, DESTROY (call_sv with G_EVAL, one JMPENV per call) and
a numeric sort.

Subcommands (run each in a fresh process, from the worktree with `uv run python`):
  data DIR                         write the inputs (identical to the baseline's)
  fuel WASM DIR OUT.json           guest instructions per workload, and the outputs
  wall WASM DIR ENGINE OUT.json    compile, serialize, deserialize, then per-call medians
  compile WASM ENGINE OUT.json     compile, serialize and deserialize only
  ab DIR OUT.json ROUNDS WORKS NAME...  paired per-call times of build/variants/NAME.wasm
  native PERL DIR OUT.json         the same workloads under a native perl, for scale

WASM is a perl.wasm; the library, dev/ and lists come from BASE_ART (the base build's set),
passed to run() unchecked. ENGINE names a wasmtime Config in ENGINES.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

import wasmtime as wt

from wasi_perl import Artifacts, Grant, _engines, run

BASE_ART = Path(
    os.environ.get("BASE_ART", "/home/user/wasi-perl-wt/perf-codegen/build/base-artifacts")
)
N_LINES = 200_000

PERL = {
    "hello": ["-e", 'print "hello\\n"'],
    "sum_csv": ["-F,", "-lane", "$s += $F[2]; END { print $s }", "/data/t.csv"],
    "grep_log": ["-ne", "$n++ if /ERROR \\d+/; END { print $n }", "/data/t.log"],
    "inplace": ["-pi", "-e", "s/ERROR/WARN/g", "/w/t.log"],
    "json": [
        "-MJSON::PP",
        "-0777",
        "-ne",
        'print scalar(@{decode_json($_)}), "\\n"',
        "/data/t.json",
    ],
    "loop": ["-e", 'my $s = 0; $s += $_ * $_ for 1 .. 1_000_000; print "$s\\n"'],
    "subcall": [
        "-e",
        'sub f { return $_[0] + 1 } my $s = 0; $s = f($s) for 1 .. 300_000; print "$s\\n"',
    ],
    "eval_die": [
        "-e",
        'my $n = 0; for (1 .. 50_000) { eval { die "x\\n" }; $n++ if $@ } print "$n\\n"',
    ],
    "destroy": [
        "-e",
        'package X; sub DESTROY { $main::d++ } package main; for (1 .. 100_000) { my $o = bless {}, "X" } print "$main::d\\n"',  # noqa: E501
    ],
    "sort": [
        "-e",
        'my @a = map { ($_ * 7919) % 10007 } 1 .. 200_000; my @b = sort { $a <=> $b } @a; print "$b[-1]\\n"',  # noqa: E501
    ],
}
REPS = {
    "hello": 30,
    "sum_csv": 7,
    "grep_log": 7,
    "inplace": 7,
    "json": 3,
    "loop": 5,
    "subcall": 5,
    "eval_die": 5,
    "destroy": 5,
    "sort": 5,
}


def _cfg(**kw) -> wt.Config:
    c = wt.Config()
    c.wasm_exceptions = True  # perl.wasm's die and eval
    c.epoch_interruption = True  # the runner's deadline
    for k, v in kw.items():
        if k == "flags":
            for name, val in v:
                c.cranelift_flag_set(name, val)
        else:
            setattr(c, k, v)
    return c


ENGINES = {
    "default": {},
    "opt_none": {"cranelift_opt_level": "none"},
    "opt_speed_and_size": {"cranelift_opt_level": "speed_and_size"},
    "serial": {"parallel_compilation": False},
    "no_signals": {"signals_based_traps": False},
    "bounds_checked": {
        "memory_reservation": 0,
        "memory_guard_size": 0,
        "memory_reservation_for_growth": 1 << 20,
    },
    "small_guard": {"memory_guard_size": 64 << 10},
    "no_cow": {"memory_init_cow": False},
    "single_pass_ra": {"flags": [("regalloc_algorithm", "single_pass")]},
}


def make_data(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "t.csv", "w", newline="\n") as f:
        for i in range(N_LINES):
            f.write(f"{i},name{i % 977},{(i * 7919) % 10007},{'yes' if i % 3 else 'no'}\n")
    with open(d / "t.log", "w", newline="\n") as f:
        for i in range(N_LINES):
            level = f"ERROR {i % 500}" if i % 7 == 0 else "INFO ok"
            f.write(f"2026-11-24T10:{i % 60:02d}:{i % 59:02d} worker{i % 16} {level} request {i}\n")
    rows = [
        {"id": i, "name": f"n{i}", "tags": ["a", "b", i % 5], "v": i * 0.5} for i in range(20_000)
    ]
    (d / "t.json").write_text(json.dumps(rows))


def arts(wasm: Path) -> Artifacts:
    a = Artifacts.at(BASE_ART)
    return Artifacts(wasm.resolve(), a.lib, a.dev, a.manifest, a.sums)


class Caller:
    def __init__(self, wasm: Path, d: Path, fuel: int | None = None):
        self.a, self.d, self.fuel = arts(wasm), d, fuel
        self.w = Path(tempfile.mkdtemp(prefix="w-"))

    def __call__(self, work: str):
        shutil.copyfile(self.d / "t.log", self.w / "t.log")
        g = [Grant(str(self.d), "/data")]
        if work == "inplace":
            g.append(Grant(str(self.w), "/w", writable=True))
        t = time.perf_counter()
        r = run(
            PERL[work], grants=g, fuel=self.fuel, cache_dir=False, artifacts=self.a, deadline_s=600
        )
        s = time.perf_counter() - t
        if r.exit_code != 0:
            raise SystemExit(f"{work}: {r.exit_code} {r.trapped} {r.stderr[:300]!r}")
        out = r.stdout
        if work == "inplace":
            out = str((self.w / "t.log").read_bytes().count(b"WARN")).encode() + b"\n"
        return s, out.decode().strip(), r.fuel_used


def install(engine: str) -> None:
    """Put the named Config into the runner's two engine slots before its first call."""
    kw = ENGINES[engine]
    _engines._engines[False] = wt.Engine(_cfg(**kw))
    _engines._engines[True] = wt.Engine(_cfg(consume_fuel=True, **kw))


def compile_stats(wasm: Path, engine: str) -> dict:
    data = wasm.read_bytes()
    eng = wt.Engine(_cfg(**ENGINES[engine]))
    cs, ds = [], []
    blob = b""
    for _ in range(3):
        t = time.perf_counter()
        m = wt.Module(eng, data)
        cs.append(time.perf_counter() - t)
        blob = m.serialize()
        del m
    for _ in range(5):
        t = time.perf_counter()
        m = wt.Module.deserialize(eng, blob)
        ds.append(time.perf_counter() - t)
        del m
    return {
        "wasm_bytes": len(data),
        "compile_s": round(statistics.median(cs), 3),
        "compile_all_s": [round(x, 3) for x in cs],
        "cwasm_bytes": len(blob),
        "deserialize_ms": round(statistics.median(ds) * 1000, 2),
    }


def main() -> None:
    cmd = sys.argv[1]
    if cmd == "data":
        make_data(Path(sys.argv[2]))
    elif cmd == "fuel":
        wasm, d, out = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
        c = Caller(wasm, d, fuel=10**13)
        res = {}
        for w in PERL:
            _, o, f = c(w)
            res[w] = {"fuel": f, "stdout": o}
            print(w, f, o, flush=True)
        out.write_text(json.dumps(res, indent=1))
    elif cmd == "compile":
        wasm, engine, out = Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
        res = compile_stats(wasm, engine)
        print(res, flush=True)
        out.write_text(json.dumps(res, indent=1))
    elif cmd == "wall":
        wasm, d, engine, out = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], Path(sys.argv[5])
        works = sys.argv[6].split(",") if len(sys.argv) > 6 else list(PERL)
        res = {"engine": engine, "compile": compile_stats(wasm, engine)}
        install(engine)
        c = Caller(wasm, d)
        for w in works:
            for _ in range(2):
                _, o, _ = c(w)
            xs = sorted(c(w)[0] for _ in range(REPS[w]))
            res[w] = {
                "median_ms": round(statistics.median(xs) * 1000, 2),
                "min_ms": round(xs[0] * 1000, 2),
                "n": len(xs),
                "stdout": o,
            }
            print(w, res[w], flush=True)
        out.write_text(json.dumps(res, indent=1))
    elif cmd == "ab":
        # Paired timing: every variant's call of a workload in turn, round after round, in one
        # process and one engine, so drift in the machine's speed falls on all variants alike.
        d, out, rounds, works = (
            Path(sys.argv[2]),
            Path(sys.argv[3]),
            int(sys.argv[4]),
            sys.argv[5].split(","),
        )
        names = sys.argv[6:]
        callers = {n: Caller(Path(f"build/variants/{n}.wasm"), d) for n in names}
        res = {"rounds": rounds, "variants": names}
        for w in works:
            for c in callers.values():
                c(w)  # compile and warm
            xs = {n: [] for n in names}
            for _ in range(rounds):
                for n, c in callers.items():
                    xs[n].append(c(w)[0])
            res[w] = {
                n: {
                    "median_ms": round(statistics.median(v) * 1000, 2),
                    "min_ms": round(min(v) * 1000, 2),
                }
                for n, v in xs.items()
            }
            ref = res[w][names[0]]
            print(
                w,
                " ".join(
                    f"{n}={r['median_ms']}({r['median_ms'] / ref['median_ms']:.3f}) min {r['min_ms']}"  # noqa: E501
                    f"({r['min_ms'] / ref['min_ms']:.3f})"
                    for n, r in res[w].items()
                ),
                flush=True,
            )
        out.write_text(json.dumps(res, indent=1))
    elif cmd == "native":
        # native perl 5.42.3 on the same workloads, as subprocesses, for scale only
        import subprocess

        perl, d, out = sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
        w = Path(tempfile.mkdtemp(prefix="w-"))
        res = {}
        for work, argv in PERL.items():
            argv = [a.replace("/data/", f"{d}/").replace("/w/", f"{w}/") for a in argv]
            xs = []
            for i in range(REPS[work] + 2):
                shutil.copyfile(d / "t.log", w / "t.log")
                t = time.perf_counter()
                subprocess.run([perl, *argv], check=True, capture_output=True)
                if i >= 2:
                    xs.append(time.perf_counter() - t)
            res[work] = {"median_ms": round(statistics.median(xs) * 1000, 2), "n": len(xs)}
            print(work, res[work], flush=True)
        out.write_text(json.dumps(res, indent=1))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
