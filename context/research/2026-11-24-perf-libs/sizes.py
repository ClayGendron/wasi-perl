"""What each statically linked extension costs in perl.wasm.

Usage: python sizes.py SDK_BIN PERL_TREE PERL_WASM [EXT ...]

For each extension archive (lib/auto/<Ext>/<base>.a in the built Perl tree), it lists the
functions the archive defines, then sums the sizes the linked perl.wasm gives those functions
(llvm-nm -S reads the module's name section). That is the code each extension adds, exactly,
for names no other object also defines. Its data is the archive's own DATA section
size (llvm-size), which the link keeps whole. Prints one JSON object.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path


def nm(sdk: Path, *args: str) -> list[list[str]]:
    out = subprocess.run([str(sdk / "llvm-nm"), *args], capture_output=True, text=True).stdout
    return [line.split() for line in out.splitlines() if line.strip()]


def main() -> None:
    sdk, tree, wasm = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    exts = sys.argv[4:]
    sizes: dict[str, int] = {}
    dup = Counter()
    for row in nm(sdk, "-S", str(wasm)):
        if len(row) == 4 and row[2] in "tT":
            dup[row[3]] += 1
            sizes[row[3]] = int(row[1], 16)
    total_code = sum(sizes.values())
    res = {"perl_wasm_bytes": wasm.stat().st_size, "named_code_bytes": total_code, "ext": {}}
    for ext in exts:
        base = ext.split("/")[-1]
        archive = tree / "lib" / "auto" / ext / f"{base}.a"
        if not archive.exists():
            res["ext"][ext] = {"missing": str(archive)}
            continue
        defined = {r[-1] for r in nm(sdk, "--defined-only", str(archive)) if len(r) >= 2 and r[-2] in "tT"}
        code = sum(sizes.get(f, 0) for f in defined if dup[f] == 1)
        out = subprocess.run([str(sdk / "llvm-size"), "-A", str(archive)], capture_output=True, text=True).stdout
        data = 0
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "DATA":
                data += int(parts[1])
        res["ext"][ext] = {"functions": len(defined), "code_bytes": code, "data_bytes": data,
                           "archive_bytes": archive.stat().st_size}
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
