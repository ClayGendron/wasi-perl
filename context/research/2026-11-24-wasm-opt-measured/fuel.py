"""Guest instructions (fuel) of study B's ten workloads on two perl.wasm files of one set.

    uv run python fuel.py <set> <before.wasm> <after.wasm>

<set> gives the library and /dev; each module runs each workload once, with PERL_HASH_SEED=0,
on inputs made here (the same generator as tests/build/test_wasm_opt.py, larger). Prints one
line per workload: before, after and the change.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import wasmtime as wt

PERL = {
    "hello": ["-e", 'print "hello\\n"'],
    "sum_csv": ["-F,", "-lane", "$s += $F[2]; END { print $s }", "/data/t.csv"],
    "grep_log": ["-ne", "$n++ if /ERROR \\d+/; END { print $n }", "/data/t.log"],
    "inplace": ["-pi", "-e", "s/ERROR/WARN/g", "/w/t.log"],
    "json": ["-MJSON::PP", "-0777", "-ne", 'print scalar(@{decode_json($_)}), "\\n"', "/data/t.json"],
    "loop": ["-e", 'my $s = 0; $s += $_ * $_ for 1 .. 1_000_000; print "$s\\n"'],
    "subcall": ["-e", 'sub f { return $_[0] + 1 } my $s = 0; $s = f($s) for 1 .. 300_000; print "$s\\n"'],
    "eval_die": ["-e", 'my $n = 0; for (1 .. 50_000) { eval { die "x\\n" }; $n++ if $@ } print "$n\\n"'],
    "destroy": ["-e", 'package X; sub DESTROY { $main::d++ } package main; for (1 .. 100_000) { my $o = bless {}, "X" } print "$main::d\\n"'],
    "sort": ["-e", 'my @a = map { ($_ * 7919) % 10007 } 1 .. 200_000; my @b = sort { $a <=> $b } @a; print "$b[-1]\\n"'],
}


def inputs(data: Path) -> None:
    levels = ("INFO", "WARN", "ERROR", "DEBUG")
    (data / "t.csv").write_text("".join(f"row{i},{i % 97},{(i * 7919) % 10007}\n" for i in range(200_000)))
    (data / "t.log").write_text(
        "".join(f"2026-11-24 12:00:{i % 60:02d} {levels[i % 4]} {i} request served\n" for i in range(200_000))
    )
    (data / "t.json").write_text("[" + ",".join(f'{{"id":{i},"name":"n{i}","ok":true}}' for i in range(20_000)) + "]")


def fuel(engine, module, art: Path, data: Path, args: list[str]) -> tuple[int, bytes]:
    tmp = Path(tempfile.mkdtemp())
    work = tmp / "w"
    work.mkdir()
    shutil.copy(data / "t.log", work / "t.log")
    wasi = wt.WasiConfig()
    wasi.argv = ["perl", *args]
    wasi.env = [("PERL_HASH_SEED", "0")]
    wasi.stdout_file = str(tmp / "out")
    wasi.stderr_file = str(tmp / "err")
    wasi.preopen_dir(str(art / "lib" / "perl5"), "/usr/local/lib/perl5", False)
    wasi.preopen_dir(str(art / "dev"), "/dev", False)
    wasi.preopen_dir(str(data), "/data", False)
    wasi.preopen_dir(str(work), "/w", True)
    store = wt.Store(engine)
    store.set_wasi(wasi)
    store.set_fuel(10**13)
    linker = wt.Linker(engine)
    linker.define_wasi()
    try:
        linker.instantiate(store, module).exports(store)["_start"](store)
    except wt.ExitTrap as e:
        assert e.code == 0, e.code
    assert (tmp / "err").read_bytes() == b"", (tmp / "err").read_bytes()
    out = (tmp / "out").read_bytes() + (work / "t.log").read_bytes()[:64]
    shutil.rmtree(tmp)
    return 10**13 - store.get_fuel(), out


def main() -> None:
    art, before, after = (Path(a) for a in sys.argv[1:4])
    config = wt.Config()
    config.wasm_exceptions = True
    config.consume_fuel = True
    engine = wt.Engine(config)
    mods = {p: wt.Module.from_file(engine, str(p)) for p in (before, after)}
    data = Path(tempfile.mkdtemp())
    inputs(data)
    for name, args in PERL.items():
        b, ob = fuel(engine, mods[before], art, data, args)
        a, oa = fuel(engine, mods[after], art, data, args)
        assert ob == oa, name
        print(f"{name}\t{b}\t{a}\t{a / b - 1:+.2%}", flush=True)


if __name__ == "__main__":
    main()
