"""The fuel and wall time of three module-loading programs, on two artifact sets.

    uv run python measure.py <before set> <after set> [rounds]

Fuel is wasmtime's count of executed instructions for one run, with PERL_HASH_SEED=0 and the
runner's two read-only grants. Wall time is the median of 31 `run()` calls after one warm-up, in
this process, with a warm module cache of each set's own; the rounds alternate the two sets, and
each line gives one round. Run it under the machine's lock.
"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from pathlib import Path

import wasmtime as wt

from wasi_perl import Artifacts, run

PROGRAMS = {
    "-e 1": ["-e", "1"],
    "-MPOSIX -e 1": ["-MPOSIX", "-e", "1"],
    "five modules": ["-e", "use strict; use warnings; use POSIX; use JSON::PP; use List::Util; 1"],
}
CALLS = 31


def fuel(folder: Path, args: list[str]) -> int:
    config = wt.Config()
    config.wasm_exceptions = True
    config.consume_fuel = True
    engine = wt.Engine(config)
    module = wt.Module.from_file(engine, str(folder / "perl.wasm"))
    tmp = Path(tempfile.mkdtemp())
    wasi = wt.WasiConfig()
    wasi.argv = ["perl", *args]
    wasi.env = [("PERL_HASH_SEED", "0")]
    wasi.stdout_file = str(tmp / "out")
    wasi.stderr_file = str(tmp / "err")
    wasi.preopen_dir(str(folder / "lib" / "perl5"), "/usr/local/lib/perl5", False)
    wasi.preopen_dir(str(folder / "dev"), "/dev", False)
    store = wt.Store(engine)
    store.set_wasi(wasi)
    store.set_fuel(10**13)
    linker = wt.Linker(engine)
    linker.define_wasi()
    try:
        linker.instantiate(store, module).exports(store)["_start"](store)
    except wt.ExitTrap as e:
        assert e.code == 0, e.code
    assert (tmp / "err").read_bytes() == b""
    return 10**13 - store.get_fuel()


def main() -> None:
    sets = {"before": Path(sys.argv[1]), "after": Path(sys.argv[2])}
    rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    for label, folder in sets.items():
        for name, args in PROGRAMS.items():
            print(f"fuel\t{label}\t{name}\t{fuel(folder, args)}")
    caches = {label: tempfile.mkdtemp() for label in sets}
    for r in range(rounds):
        for label, folder in sets.items():
            arts = Artifacts.at(folder)
            for name, args in PROGRAMS.items():
                run(args, artifacts=arts, cache_dir=caches[label])
                times = []
                for _ in range(CALLS):
                    t0 = time.perf_counter()
                    done = run(args, artifacts=arts, cache_dir=caches[label])
                    times.append(time.perf_counter() - t0)
                    assert (done.exit_code, done.stderr) == (0, b""), done
                ms = statistics.median(times) * 1000
                print(f"time\tround {r + 1}\t{label}\t{name}\t{ms:.2f} ms")


if __name__ == "__main__":
    main()
