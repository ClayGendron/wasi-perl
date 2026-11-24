"""Guest instructions per startup phase, from the probe build (probes.c, link.sh).

    python phases.py <probe.wasm> <library folder> <dev folder> OUT.json

Runs each program once on a fuel-metered engine with the runner's preopens (the library at
/usr/local/lib/perl5, the dev folder at /dev), argv and an empty environment, and records the
fuel spent at every mark the probe build makes. Writes the marks and a phase table per program.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import wasmtime as wt

NAMES = {
    1: "main",
    2: "perl_alloc",
    3: "perl_construct",
    4: "perl_parse",
    5: "perl_run",
    6: "perl_destruct",
    7: "perl_free",
    8: "get_hash_seed",
    9: "init_i18nl10n",
    10: "seed",
    11: "yyparse",
    12: "boot_core_PerlIO",
    13: "pp_require",
    14: "boot_core_UNIVERSAL",
}
FUEL = 10**13

PROGRAMS = {
    "e_1": ["-e", "1"],
    "ne_devnull": ["-ne", "print if /x/", "/dev/null"],
    "pi_lane": ["-lane", "print $F[0]", "/dev/null"],
    "Mstrict": ["-Mstrict", "-e", "1"],
    "Mwarnings": ["-Mwarnings", "-e", "1"],
    "MPOSIX": ["-MPOSIX", "-e", "1"],
    "MJSON_PP": ["-MJSON::PP", "-e", "1"],
    "MList_Util": ["-MList::Util", "-e", "1"],
    "MData_Dumper": ["-MData::Dumper", "-e", "1"],
    "MStorable": ["-MStorable", "-e", "1"],
    "MEncode": ["-MEncode", "-e", "1"],
    "five": ["-e", "use strict; use warnings; use POSIX; use JSON::PP; use List::Util; 1"],
    "six": [
        "-MPOSIX",
        "-MList::Util",
        "-MData::Dumper",
        "-MJSON::PP",
        "-MStorable",
        "-MEncode",
        "-e",
        "1",
    ],
}


def run(engine: wt.Engine, module: wt.Module, lib: str, dev: str, argv: list[str]) -> dict:
    store = wt.Store(engine)
    marks: list[tuple[int, int]] = []

    def mark(i: int) -> None:
        marks.append((i, FUEL - store.get_fuel()))

    linker = wt.Linker(engine)
    linker.define_wasi()
    linker.define_func("wasi_perl_research", "mark", wt.FuncType([wt.ValType.i32()], []), mark)
    with tempfile.TemporaryDirectory() as t:
        wasi = wt.WasiConfig()
        wasi.argv = ["perl", *argv]
        wasi.env = []
        wasi.stdout_file = str(Path(t, "out"))
        wasi.stderr_file = str(Path(t, "err"))
        wasi.preopen_dir(lib, "/usr/local/lib/perl5", False)
        wasi.preopen_dir(dev, "/dev", False)
        store.set_wasi(wasi)
        store.set_fuel(FUEL)
        inst = linker.instantiate(store, module)
        code = 0
        try:
            inst.exports(store)["_start"](store)
        except wt.ExitTrap as e:
            code = e.code
        total = FUEL - store.get_fuel()
        err = Path(t, "err").read_text()
    return {"argv": argv, "exit": code, "stderr": err[:300], "total": total, "marks": marks}


def phases(r: dict) -> dict:
    """Top-level phases, and the inclusive cost of the outermost requires and yyparses."""
    m = r["marks"]
    at = {}
    for i, f in m:
        at.setdefault(i, f)  # the first time each mark is seen
    end = {}
    for i, f in m:
        end[i] = f  # the last time
    out = {}
    out["before main (ctors, libc start)"] = at[2]
    out["PERL_SYS_INIT3 + perl_alloc"] = at[6] - at[2]
    out["perl_construct"] = at[7] - at[6]
    out["  of it: get_hash_seed (seed + state)"] = at[17] - at[16]
    out["  of it: init_i18nl10n (locale)"] = at[19] - at[18]
    out["perl_parse"] = at[9] - at[8]
    out["  of it: start to seed(): main stash, switches"] = at[20] - at[8]
    out["  of it: seed() to boot_core_PerlIO: @INC setup, script open, lexer start"] = (
        at[24] - at[21]
    )
    out["  of it: boot_core_* and xs_init to compile start"] = at[22] - at[24] if 22 in at else 0
    depth_y = depth_r = 0
    ystart = rstart = 0
    ytotal = rtotal = 0
    nreq = 0
    for i, f in m:
        if i == 22:
            if depth_y == 0:
                ystart = f
            depth_y += 1
        elif i == 23:
            depth_y -= 1
            if depth_y == 0:
                ytotal += f - ystart
        elif i == 26:
            nreq += 1
            if depth_r == 0:
                rstart = f
            depth_r += 1
        elif i == 27:
            depth_r -= 1
            if depth_r == 0:
                rtotal += f - rstart
    out["  of it: the main program's compile (outermost yyparse, requires included)"] = ytotal
    out["  of it: requires, outermost, inclusive"] = rtotal
    out["requires executed (all depths)"] = nreq
    out["perl_run"] = end[11] - at[10]
    out["perl_destruct + perl_free"] = end[15] - at[12]
    out["after perl_free (PERL_SYS_TERM, exit, atexit, flush)"] = r["total"] - end[15]
    out["total"] = r["total"]
    return out


def main() -> None:
    wasm, lib, dev, dest = sys.argv[1:5]
    cfg = wt.Config()
    cfg.wasm_exceptions = True
    cfg.consume_fuel = True
    engine = wt.Engine(cfg)
    module = wt.Module.from_file(engine, wasm)
    res = {}
    for name, argv in PROGRAMS.items():
        r = run(engine, module, lib, dev, argv)
        r["phases"] = phases(r)
        res[name] = r
        print(name, r["exit"], r["total"], r["stderr"][:80].strip())
    Path(dest).write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
