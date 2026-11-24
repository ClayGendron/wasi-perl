"""Wall results as a table: compile, cwasm, then each workload's median ms and its ratio to a
reference result. Usage: table.py REF.json OTHER.json..."""

import json
import sys
from pathlib import Path

ref = json.loads(Path(sys.argv[1]).read_text())
works = [k for k in ref if k not in ("engine", "compile")]
print("result".ljust(34), "compile_s cwasm_MB deser_ms", " ".join(w[:8].rjust(8) for w in works))
for p in sys.argv[1:]:
    x = json.loads(Path(p).read_text())
    c = x["compile"]
    cells = []
    for w in works:
        if w not in x:
            cells.append("-".rjust(8))
            continue
        assert x[w]["stdout"] == ref[w]["stdout"], (p, w)
        cells.append(f"{x[w]['median_ms'] / ref[w]['median_ms']:8.3f}")
    print(
        Path(p).stem[5:].ljust(34),
        f"{c['compile_s']:9.3f} {c['cwasm_bytes'] / 1e6:8.2f} {c['deserialize_ms']:8.2f}",
        " ".join(cells),
    )
