"""Fuel of each variant against base, per workload, and whether every output matches base."""

import json
import sys
from pathlib import Path

r = Path(__file__).parent / "results"
b = json.loads((r / "fuel-base.json").read_text())
names = sys.argv[1:] or sorted(p.stem[5:] for p in r.glob("fuel-*.json"))
print("variant".ljust(12), " ".join(k[:8].rjust(8) for k in b), "same-out")
for v in names:
    x = json.loads((r / f"fuel-{v}.json").read_text())
    print(
        v.ljust(12),
        " ".join(f"{x[k]['fuel'] / b[k]['fuel']:8.3f}" for k in b),
        all(x[k]["stdout"] == b[k]["stdout"] for k in b),
    )
