"""Designs (b) and (c) without a rebuild: a Perl program that reads the 5 MB row with `sysread`
of N bytes makes ceil(5 MB / N) + 1 fd_read calls of N bytes, as PerlIO would with an N-byte
buffer (b); with N at least the file's size it makes one call that hands the whole file over,
which is what a custom import would do (c), but through preview1's own fd_read.

    flock /home/user/wasi-perl-wt/machine.lock python bench_readsize.py results/readsize.json
"""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

import harness as h

from wasi_perl import Grant, run
from wasi_perl._mounts import MemoryProvider, Mount

PROGRAM = (
    'open my $f, "<", $ARGV[0] or die $!; my ($n, $c) = (0, 0);'
    ' while (my $r = sysread $f, my $b, $ARGV[1]) { $n += $r; $c++ } print "$n $c\\n"'
)
SIZES = (8192, 65536, 1 << 20, 8 << 20)


def main() -> None:
    out = Path(sys.argv[1])
    files = h.make_data()
    host = Path(tempfile.mkdtemp())
    h.write_tree(files, host)
    results: dict[str, dict[str, object]] = {}
    for size in SIZES:
        argv = ("-e", PROGRAM, "/work/data/sales.csv", str(size))
        row: dict[str, object] = {}
        for label in ("grant", "mount"):

            def once() -> float:
                kw: dict = {}
                if label == "grant":
                    kw["grants"] = [Grant(host, "/work")]
                else:
                    kw["mounts"] = [Mount(MemoryProvider(files), "/work", stat_reads=True)]
                t = time.perf_counter()
                done = run(argv, **kw)
                elapsed = time.perf_counter() - t
                assert done.exit_code == 0, done
                row["stdout"] = done.stdout.decode().strip()
                return elapsed

            once()
            row[label + "_ms"] = statistics.median(once() for _ in range(7)) * 1000
        _, rec = h.observed(argv, mounts=[Mount(MemoryProvider(files), "/work")])
        row["fd_read_calls"] = rec.counts()["fd_read"]
        results[str(size)] = row
        print(size, row, flush=True)
    out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
