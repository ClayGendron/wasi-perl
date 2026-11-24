"""Interleaved A/B of whole calls: perl.wasm against the preload snapshot, one program at a time,
alternating module on every call so that load on the shared machine falls on both alike.

    python ab.py <research folder> <artifacts folder> OUT.json [n]

Reports the median and the 10th and 90th percentiles in ms per call, for the default allocator and
the pooling allocator. The call is the same as restore.py's: InstancePre, then _start or run.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

import wasmtime as wt

sys.path.insert(0, str(Path(__file__).parent))
from restore import PROGRAMS, call, engine  # noqa: E402


def q(xs, p):
    xs = sorted(xs)
    return round(xs[int(p * (len(xs) - 1))] * 1000, 3)


def main() -> None:
    research, art, dest = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    n = int(sys.argv[4]) if len(sys.argv) > 4 else 100
    lib, dev = str(art / "lib" / "perl5"), str(art / "dev")
    mods = {
        "perl.wasm": (art / "perl.wasm", "_start"),
        "snap-preload": (research / "snap-preload.wasm", "run"),
    }
    out: dict = {"n": n}
    for kind in ("ondemand", "pooling"):
        eng = engine(kind)
        pres = {}
        for name, (path, entry) in mods.items():
            linker = wt.Linker(eng)
            linker.define_wasi()
            pres[name] = (linker.instantiate_pre(wt.Module.from_file(eng, str(path))), entry)
        for prog in ("e_1", "ne_file", "MPOSIX", "five"):
            argv = PROGRAMS[prog]
            xs: dict = {k: [] for k in mods}
            for i in range(n + 3):
                for name, (pre, entry) in pres.items():
                    s = time.perf_counter()
                    call(eng, pre, entry, argv, lib, dev)
                    if i >= 3:
                        xs[name].append(time.perf_counter() - s)
            for name in mods:
                row = {
                    "median": round(statistics.median(xs[name]) * 1000, 3),
                    "p10": q(xs[name], 0.1),
                    "p90": q(xs[name], 0.9),
                }
                out[f"{kind} {name} {prog}"] = row
                print(kind, name, prog, row, flush=True)
    Path(dest).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
