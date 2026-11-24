"""Fuel (guest instructions) and wall time of the baseline workloads for one perl.wasm, through
the wasi_perl runner with the same grants as the baseline harness.

    uv run python measure.py fuel PERL.wasm LIBSET DATA OUT.json [WORK...]
    uv run python measure.py time PERL.wasm LIBSET DATA OUT.json [WORK...]
    uv run python measure.py many DATA    (DATA/many/: 200 files of 50 lines of DATA/t.log)

LIBSET is an artifact folder whose lib/ and dev/ are used; PERL.wasm replaces its module.
Fuel is deterministic, so one call each. Time is the median of REPS calls after two warm-ups,
in one process with a warm module cache. Every call's stdout must equal the base build's."""

import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

import wasi_perl
from wasi_perl._artifacts import Artifacts

PERL = {
    "hello": ["-e", 'print "hello\\n"'],
    "sum_csv": ["-F,", "-lane", "$s += $F[2]; END { print $s }", "/data/t.csv"],
    "grep_log": ["-ne", "$n++ if /ERROR \\d+/; END { print $n }", "/data/t.log"],
    "inplace": ["-pi", "-e", "s/ERROR/WARN/g", "/w/t.log"],
    "json": ["-MJSON::PP", "-0777", "-ne", 'print scalar(@{decode_json($_)}), "\\n"', "/data/t.json"],
    "loop": ["-e", 'my $s = 0; $s += $_ * $_ for 1 .. 1_000_000; print "$s\\n"'],
    # Startup and line I/O alone, the floor under every one-liner over a file.
    "e1": ["-e", "1"],
    "cat_log": ["-ne", "print", "/data/t.log"],
    "count_lines": ["-ne", "END { print $. }", "/data/t.log"],
    # An agent's grep over many small files: 200 files of 50 lines (about 2.5 KB each).
    "many_files": ["-ne", "$n++ if /ERROR/; END { print $n }"]
    + [f"/data/many/f{i:03d}.log" for i in range(200)],
}
EXPECT = {
    "hello": "hello",
    "sum_csv": "1000605790",
    "grep_log": "28572",
    "inplace": "28572",
    "json": "20000",
    "loop": "333333833333500000",
    "e1": "",
    "count_lines": "200000",
    "many_files": "1429",
}
REPS = {"hello": 30, "e1": 30, "json": 5, "loop": 7}


def caller(perl: Path, libset: Path, data: Path):
    base = Artifacts.at(libset)
    art = Artifacts(perl, base.lib, base.dev, base.manifest, base.sums)
    w = Path(tempfile.mkdtemp(prefix="w-"))
    cache = Path(tempfile.mkdtemp(prefix="cache-"))
    cache.chmod(0o700)

    def call(work: str, fuel=None):
        shutil.copyfile(data / "t.log", w / "t.log")
        g = [wasi_perl.Grant(str(data), "/data")]
        if work == "inplace":
            g.append(wasi_perl.Grant(str(w), "/w", writable=True))
        t = time.perf_counter()
        # A fixed hash seed makes the fuel count repeatable: hash order changes the work.
        env = {"PERL_HASH_SEED": "0"} if fuel else None
        r = wasi_perl.run(PERL[work], grants=g, artifacts=art, cache_dir=cache, fuel=fuel, env=env)
        s = time.perf_counter() - t
        if r.exit_code != 0:
            raise SystemExit(f"{work}: exit {r.exit_code} {r.trapped} {r.stderr[:300]!r}")
        out = r.stdout.decode().strip()
        if work == "inplace":
            out = str((w / "t.log").read_bytes().count(b"WARN"))
        if work == "cat_log":
            ok = r.stdout == (data / "t.log").read_bytes()
        else:
            ok = out == EXPECT[work]
        if not ok:
            raise SystemExit(f"{work}: wrong output {out[:80]!r}")
        return s, r.fuel_used

    return call


def many(data: Path) -> None:
    lines = (data / "t.log").read_text().splitlines(keepends=True)
    (data / "many").mkdir(exist_ok=True)
    for i in range(200):
        (data / "many" / f"f{i:03d}.log").write_text("".join(lines[i * 50 : (i + 1) * 50]))


def main():
    if sys.argv[1] == "many":
        return many(Path(sys.argv[2]))
    mode, perl, libset, data, out = sys.argv[1:6]
    works = sys.argv[6:] or list(PERL)
    call = caller(Path(perl), Path(libset), Path(data))
    res = {}
    for work in works:
        if mode == "fuel":
            res[work] = call(work, fuel=10**13)[1]
        else:
            for _ in range(2):
                call(work)
            xs = sorted(call(work)[0] for _ in range(REPS.get(work, 10)))
            res[work] = round(statistics.median(xs) * 1000, 2)
        print(work, res[work], flush=True)
    Path(out).write_text(json.dumps({"perl": perl, mode: res}, indent=1) + "\n")


if __name__ == "__main__":
    main()
