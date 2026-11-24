"""The fd_read calls a 5 MB read makes by request size, through a grant (wasmtime returns at most
64 KiB per call) and through the prototype's mount (the request whole). Deterministic.

    python readsize_counts.py > results/readsize-counts.md
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import bench_readsize as b
import harness as h

from wasi_perl import Grant
from wasi_perl._mounts import MemoryProvider, Mount


def main() -> None:
    files = h.make_data()
    host = Path(tempfile.mkdtemp())
    h.write_tree(files, host)
    print("| request size | fd_read calls, grant | fd_read calls, mount | largest answer, grant |")
    print("|---|---|---|---|")
    for size in b.SIZES:
        argv = ("-e", b.PROGRAM, "/work/data/sales.csv", str(size))
        _, g = h.observed(argv, grants=[Grant(host, "/work")])
        _, m = h.observed(argv, mounts=[Mount(MemoryProvider(files), "/work")])
        biggest = max(e[4] for e in g.events if e[0] == "fd_read")
        print(f"| {size:,} | {g.counts()['fd_read']} | {m.counts()['fd_read']} | {biggest:,} |")


if __name__ == "__main__":
    main()
