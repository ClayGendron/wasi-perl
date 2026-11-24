"""The host's cost of the calls a require's @INC search makes: a failed stat, a found stat, an
open and close, each timed in a loop of 20,000 inside one perl.wasm call, less the same loop
empty. Medians of 5 calls, in one warm process, with the runner's preopens.

    python statcost.py <artifacts folder> OUT.json
"""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

import wasmtime as wt

N = 20000
LOOPS = {
    "empty": f"for my $i (1..{N}) {{ }}",
    "stat_missing": f'for my $i (1..{N}) {{ stat "/usr/local/lib/perl5/5.42/site_perl/strict.pm" }}',
    "stat_found": f'for my $i (1..{N}) {{ stat "/usr/local/lib/perl5/5.42/strict.pm" }}',
    "open_close": f'for my $i (1..{N}) {{ open my $f, "<", "/usr/local/lib/perl5/5.42/strict.pm"; close $f }}',
}


def main() -> None:
    art, dest = Path(sys.argv[1]), sys.argv[2]
    cfg = wt.Config()
    cfg.wasm_exceptions = True
    eng = wt.Engine(cfg)
    m = wt.Module.from_file(eng, str(art / "perl.wasm"))
    linker = wt.Linker(eng)
    linker.define_wasi()
    pre = linker.instantiate_pre(m)
    res = {}
    for name, code in LOOPS.items():
        xs = []
        for _ in range(6):
            with tempfile.TemporaryDirectory() as t:
                w = wt.WasiConfig()
                w.argv = ["perl", "-e", code]
                w.stdout_file = str(Path(t, "o"))
                w.stderr_file = str(Path(t, "e"))
                w.preopen_dir(str(art / "lib" / "perl5"), "/usr/local/lib/perl5", False)
                w.preopen_dir(str(art / "dev"), "/dev", False)
                store = wt.Store(eng)
                store.set_wasi(w)
                s = time.perf_counter()
                inst = pre.instantiate(store)
                try:
                    inst.exports(store)["_start"](store)
                except wt.ExitTrap:
                    pass
                xs.append(time.perf_counter() - s)
        res[name] = statistics.median(xs[1:]) * 1000
    base = res["empty"]
    per = {k: round((v - base) / N * 1000, 3) for k, v in res.items() if k != "empty"}
    out = {"ms_per_call_of_N": res, "N": N, "us_per_op_less_empty": per}
    print(out)
    Path(dest).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
