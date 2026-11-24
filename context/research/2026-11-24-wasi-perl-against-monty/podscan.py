"""How much of the library is POD: bytes from a line starting `=word` to the next `=cut`, in .pm,
.pl and .pod files, against the library's total bytes. A count, not a timing.

    python3 podscan.py LIBDIR OUT.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

lib, dest = Path(sys.argv[1]), Path(sys.argv[2])
total = pod = 0
for p in lib.rglob("*"):
    if not p.is_file():
        continue
    b = p.read_bytes()
    total += len(b)
    if p.suffix not in (".pm", ".pl", ".pod"):
        continue
    inpod = False
    for line in b.split(b"\n"):
        if line.startswith(b"=cut"):
            inpod = False
            pod += len(line) + 1
            continue
        if line.startswith(b"=") and line[1:2].isalpha():
            inpod = True
        if inpod:
            pod += len(line) + 1
res = {"library_bytes": total, "pod_bytes": pod, "share": round(pod / total, 3)}
print(res)
dest.write_text(json.dumps(res, indent=1))
