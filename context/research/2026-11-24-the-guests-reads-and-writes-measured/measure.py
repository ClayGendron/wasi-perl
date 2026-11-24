"""Fuel, wall time and WASI calls of study C's workloads for one artifact set, before and after
the guest's reads and writes changed. Adapted from study C's measure.py and wasicount.py
(../2026-11-24-perf-interp/), trimmed to what this memo needs.

    uv run python measure.py data DATA
    uv run python measure.py fuel SET DATA OUT.json
    uv run python measure.py time SET DATA OUT.json [WORK...]
    uv run python measure.py calls SET DATA OUT.json WASMTIME

SET is an artifact folder as the build writes it. DATA is made by `data`: the baseline's inputs
(bench.py's t.csv, t.log and t.json) and many/, 200 files of 50 lines each of t.log. Fuel is a
count, so one call each, with the hash seed fixed. Time is the median of REPS calls after two
warm-ups, in one process with a warm module cache. Every call's output is checked. Calls are
counted from the wasmtime command line's trace, with the runner's preopens.
"""

import hashlib
import json
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

import wasi_perl
from wasi_perl._artifacts import Artifacts

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent / "2026-11-24-wasi-perl-against-wasi-python-on-speed" / "bench.py"

PERL = {
    "hello": ["-e", 'print "hello\\n"'],
    "sum_csv": ["-F,", "-lane", "$s += $F[2]; END { print $s }", "/data/t.csv"],
    "grep_log": ["-ne", "$n++ if /ERROR \\d+/; END { print $n }", "/data/t.log"],
    "inplace": ["-pi", "-e", "s/ERROR/WARN/g", "/w/t.log"],
    "json": ["-MJSON::PP", "-0777", "-ne", 'print scalar(@{decode_json($_)}), "\\n"', "/data/t.json"],
    "loop": ["-e", 'my $s = 0; $s += $_ * $_ for 1 .. 1_000_000; print "$s\\n"'],
    "count_lines": ["-ne", "END { print $. }", "/data/t.log"],
    "cat_log": ["-ne", "print", "/data/t.log"],
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
    "count_lines": "200000",
    "many_files": "1429",
}
REPS = {"hello": 30, "json": 5, "loop": 7}


def data(d: Path) -> None:
    sys.path.insert(0, str(BENCH.parent))
    import bench

    bench.make_data(d)
    lines = (d / "t.log").read_text().splitlines(keepends=True)
    (d / "many").mkdir(exist_ok=True)
    for i in range(200):
        (d / "many" / f"f{i:03d}.log").write_text("".join(lines[i * 50 : (i + 1) * 50]))


def caller(art: Artifacts, d: Path):
    w = Path(tempfile.mkdtemp(prefix="w-"))
    cache = Path(tempfile.mkdtemp(prefix="cache-"))
    cache.chmod(0o700)

    def call(work: str, fuel=None):
        shutil.copyfile(d / "t.log", w / "t.log")
        g = [wasi_perl.Grant(str(d), "/data")]
        if work == "inplace":
            g.append(wasi_perl.Grant(str(w), "/w", writable=True))
        env = {"PERL_HASH_SEED": "0"} if fuel else None
        t = time.perf_counter()
        r = wasi_perl.run(PERL[work], grants=g, artifacts=art, cache_dir=cache, fuel=fuel, env=env)
        s = time.perf_counter() - t
        if r.exit_code != 0:
            raise SystemExit(f"{work}: exit {r.exit_code} {r.trapped} {r.stderr[:300]!r}")
        out = r.stdout.decode().strip()
        if work == "inplace":
            out = str((w / "t.log").read_bytes().count(b"WARN"))
        ok = r.stdout == (d / "t.log").read_bytes() if work == "cat_log" else out == EXPECT[work]
        if not ok:
            raise SystemExit(f"{work}: wrong output {out[:80]!r}")
        return s, r.fuel_used

    return call


TRACE = re.compile(r'function="([a-z_0-9]+)"\}: \S+: (.*)$')


def calls(art: Artifacts, d: Path, wasmtime: str, work: str) -> dict[str, int]:
    w = Path(tempfile.mkdtemp(prefix="w-"))
    shutil.copyfile(d / "t.log", w / "t.log")
    cmd = [wasmtime, "run", "-W", "exceptions=y"]
    for host, guest in ((art.lib, "/usr/local/lib/perl5"), (art.dev, "/dev"), (d, "/data"), (w, "/w")):
        cmd += ["--dir", f"{host}::{guest}"]
    cmd += ["--argv0", "perl", str(art.perl), *PERL[work]]
    env = {"WASMTIME_LOG": "wasmtime_wasi=trace", "RUST_BACKTRACE": "0", "NO_COLOR": "1"}
    p = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, capture_output=True, check=False)
    if p.returncode != 0:
        raise SystemExit(f"{work}: exit {p.returncode}")
    n = Counter()
    for line in p.stderr.decode("utf-8", "replace").splitlines():
        m = TRACE.search(line)
        if m and m.group(2).startswith("result="):
            n[m.group(1)] += 1
    out = {"total": sum(n.values())}
    out.update(sorted(n.items(), key=lambda x: -x[1]))
    return out


def main():
    mode = sys.argv[1]
    if mode == "data":
        return data(Path(sys.argv[2]))
    art, d, out = Artifacts.at(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    res = {}
    if mode == "calls":
        for work in ("hello", "grep_log", "inplace", "count_lines", "many_files", "sum_csv"):
            res[work] = calls(art, d, sys.argv[5], work)
            print(work, res[work], flush=True)
    else:
        call = caller(art, d)
        works = sys.argv[5:] or list(PERL)
        for work in works:
            if mode == "fuel":
                res[work] = call(work, fuel=10**13)[1]
            else:
                for _ in range(2):
                    call(work)
                xs = sorted(call(work)[0] for _ in range(REPS.get(work, 10)))
                res[work] = round(statistics.median(xs) * 1000, 2)
            print(work, res[work], flush=True)
    digest = hashlib.sha256(art.perl.read_bytes()).hexdigest()
    head = {"set": str(Path(sys.argv[2]).resolve()), "perl_sha256": digest}
    out.write_text(json.dumps({**head, mode: res}, indent=1) + "\n")


if __name__ == "__main__":
    main()
