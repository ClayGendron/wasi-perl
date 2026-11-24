"""What a per-process library check costs by its shape: SHA-256 over the library's 1,452 files one
by one, against the same bytes as one file. The page cache is warm for both.

    python3 hashprobe.py LIBDIR SCRATCH OUT.json
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

lib, scratch, dest = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
files = sorted(p for p in lib.rglob("*") if p.is_file())
blob = scratch / "library-as-one-file.bin"
with open(blob, "wb") as f:
    for p in files:
        f.write(p.read_bytes())


def per_file() -> None:
    for p in files:
        hashlib.sha256(p.read_bytes()).hexdigest()


def one_file() -> None:
    hashlib.sha256(blob.read_bytes()).hexdigest()


res = {"files": len(files), "bytes": blob.stat().st_size}
for name, fn in (("per_file", per_file), ("one_file", one_file)):
    fn()
    ts = []
    for _ in range(9):
        t = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t)
    res[name] = {
        "median_ms": round(statistics.median(ts) * 1000, 2),
        "min_ms": round(min(ts) * 1000, 2),
    }
blob.unlink()
print(res)
dest.write_text(json.dumps(res, indent=1))
