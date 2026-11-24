"""Page faults and time per bare call, by module and allocator: why a snapshot that runs fewer
instructions can still take longer. Minor faults are this process's, read with getrusage around
each call; they count the pages the call touched for the first time.

    python faults.py <research folder> <artifacts folder> OUT.json [n]
"""

from __future__ import annotations

import json
import resource
import statistics
import sys
import time
from pathlib import Path

import wasmtime as wt

sys.path.insert(0, str(Path(__file__).parent))
from restore import PROGRAMS, call, engine  # noqa: E402


def main() -> None:
    research, art, dest = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    n = int(sys.argv[4]) if len(sys.argv) > 4 else 60
    lib, dev = str(art / "lib" / "perl5"), str(art / "dev")
    mods = {
        "perl.wasm": (art / "perl.wasm", "_start"),
        "snap-construct": (research / "snap-construct.wasm", "run"),
        "snap-preload": (research / "snap-preload.wasm", "run"),
    }
    out: dict = {"n": n}
    for kind in ("ondemand", "pooling", "pooling_resident"):
        eng = engine(kind)
        pres = {}
        for name, (path, entry) in mods.items():
            linker = wt.Linker(eng)
            linker.define_wasi()
            pres[name] = (linker.instantiate_pre(wt.Module.from_file(eng, str(path))), entry)
        for prog in ("e_1", "five"):
            xs: dict = {k: [] for k in mods}
            fs: dict = {k: [] for k in mods}
            for i in range(n + 3):
                for name, (pre, entry) in pres.items():
                    f0 = resource.getrusage(resource.RUSAGE_SELF).ru_minflt
                    s = time.perf_counter()
                    call(eng, pre, entry, PROGRAMS[prog], lib, dev)
                    t = time.perf_counter() - s
                    f1 = resource.getrusage(resource.RUSAGE_SELF).ru_minflt
                    if i >= 3:
                        xs[name].append(t)
                        fs[name].append(f1 - f0)
            for name in mods:
                row = {
                    "ms_median": round(statistics.median(xs[name]) * 1000, 3),
                    "minor_faults_median": statistics.median(fs[name]),
                }
                out[f"{kind} {name} {prog}"] = row
                print(kind, name, prog, row, flush=True)
    Path(dest).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
