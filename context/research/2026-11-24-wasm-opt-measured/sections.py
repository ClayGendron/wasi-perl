"""Each section of a wasm module, its size in bytes, and the number of entries of the counted
ones (functions, data segments).

    uv run python sections.py <module.wasm>...
"""

from __future__ import annotations

import sys

NAMES = {1: "type", 2: "import", 3: "function", 4: "table", 5: "memory", 6: "global",
         7: "export", 9: "elem", 10: "code", 11: "data", 12: "datacount", 13: "tag"}


def leb(b: bytes, i: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        byte = b[i]
        i += 1
        n |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return n, i


for path in sys.argv[1:]:
    wasm = open(path, "rb").read()
    print(f"{path}: {len(wasm)} bytes")
    i = 8
    while i < len(wasm):
        sid, i = wasm[i], i + 1
        size, i = leb(wasm, i)
        if sid == 0:
            n, j = leb(wasm, i)
            name = wasm[j : j + n].decode()
        else:
            name = NAMES.get(sid, str(sid))
        extra = ""
        if sid in (3, 10, 11):
            extra = f"\t{leb(wasm, i)[0]} entries"
        print(f"  {name}\t{size}{extra}")
        i += size
