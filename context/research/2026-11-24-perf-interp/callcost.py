"""The host's cost of one WASI call of each kind, through the wasi_perl runner: a perl loop that
makes N calls of one kind, against the same loop without the call, both timed whole.

    uv run python callcost.py PERL.wasm LIBSET DATA OUT.json

Each pair differs by one operation per iteration; the difference over N is that call's cost
(the guest's side of the call is included, and is small: compare their fuel)."""

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

import wasi_perl
from wasi_perl._artifacts import Artifacts

N = 20_000
PAIRS = {
    # sysread of 1 byte from a big file: one fd_read each
    "fd_read_1B": (
        f'open my $f, "<", "/data/t.log" or die; my $b; sysread($f, $b, 1) for 1 .. {N}',
        f'open my $f, "<", "/data/t.log" or die; my $b; substr("abc", 0, 1) for 1 .. {N}',
    ),
    # sysread of 64 KiB: one fd_read moving 64 KiB each
    "fd_read_64K": (
        f'open my $f, "<", "/data/t.log" or die; my $b; for (1 .. {N // 10}) {{ sysseek($f, 0, 0); sysread($f, $b, 65536) }}',
        f'open my $f, "<", "/data/t.log" or die; my $b; for (1 .. {N // 10}) {{ sysseek($f, 0, 0); }}',
    ),
    # stat of a file by name: one path_filestat_get each
    "path_filestat_get": (
        f'my $n = 0; for (1 .. {N}) {{ $n++ if -e "/data/t.log" }}',
        f'my $n = 0; for (1 .. {N}) {{ $n++ if "/data/t.log" }}',
    ),
    # -t on an open file: isatty, one fd_fdstat_get each
    "fd_fdstat_get": (
        f'open my $f, "<", "/data/t.log" or die; my $n = 0; for (1 .. {N}) {{ $n++ if -t $f }}',
        f'open my $f, "<", "/data/t.log" or die; my $n = 0; for (1 .. {N}) {{ $n++ if $f }}',
    ),
    # sysseek: one fd_seek each
    "fd_seek": (
        f'open my $f, "<", "/data/t.log" or die; sysseek($f, 0, 0) for 1 .. {N}',
        f'open my $f, "<", "/data/t.log" or die; substr("abc", 0, 1) for 1 .. {N}',
    ),
    # open and close of a small file: wasi-libc's, ours and Perl's calls together (7 per pair)
    "open_close": (
        f'for (1 .. {N // 10}) {{ open my $f, "<", "/data/many/f000.log" or die; close $f }}',
        f'for (1 .. {N // 10}) {{ my $f = "/data/many/f000.log"; }}',
    ),
}


def main():
    perl, libset, data, out = sys.argv[1:5]
    base = Artifacts.at(libset)
    art = Artifacts(Path(perl), base.lib, base.dev, base.manifest, base.sums)
    cache = Path(tempfile.mkdtemp(prefix="cache-"))
    cache.chmod(0o700)
    g = [wasi_perl.Grant(data, "/data")]

    def t(prog):
        xs = []
        for i in range(9):
            s = time.perf_counter()
            r = wasi_perl.run(["-e", prog], grants=g, artifacts=art, cache_dir=cache)
            xs.append(time.perf_counter() - s)
            assert r.exit_code == 0, r.stderr
        return statistics.median(xs[2:])

    def fuel(prog):
        r = wasi_perl.run(["-e", prog], grants=g, artifacts=art, cache_dir=cache, fuel=10**12)
        return r.fuel_used

    res = {}
    for name, (with_call, without) in PAIRS.items():
        n = N // 10 if name in ("fd_read_64K", "open_close") else N
        a, b = t(with_call), t(without)
        fa, fb = fuel(with_call), fuel(without)
        res[name] = {
            "calls": n,
            "us_per_call": round((a - b) / n * 1e6, 2),
            "guest_instructions_per_call": round((fa - fb) / n),
        }
        print(name, res[name], flush=True)
    Path(out).write_text(json.dumps(res, indent=1) + "\n")


if __name__ == "__main__":
    main()
