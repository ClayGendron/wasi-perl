"""Print the before and after table from results/: fuel in millions of instructions, the median
wall time of one warm call in ms, and whether each answer equals native perl's.
Usage: uv run python -I table.py results"""

import json
import sys
from pathlib import Path

r = Path(sys.argv[1])
fb, fa = (json.loads((r / f"fuel-{s}.json").read_text()) for s in ("before", "after"))
wb, wa = (json.loads((r / f"wall-{s}.json").read_text()) for s in ("before", "after"))
nat = json.loads((r / "native.json").read_text())
print("| program | fuel M, before | fuel M, after | ms, before | ms, after | native ms | answer |")
print("|---|---:|---:|---:|---:|---:|---|")
for name in dict.fromkeys([*fb, *fa]):
    def f(d):
        return f"{d[name]['fuel'] / 1e6:,.0f}" if name in d and d[name]["exit"] == 0 else ""

    def w(d):
        return f"{d[name]['median_ms']:,.1f}" if name in d else ""

    answers = {d[name]["answer"] for d in (fb, fa) if name in d}
    same = "same" if answers == {nat[name]["answer"]} else f"DIFFERS {answers} {nat[name]['answer']}"
    if name.startswith("load_"):
        same = "same" if answers == {""} else "?"
    print(f"| {name} | {f(fb)} | {f(fa)} | {w(wb)} | {w(wa)} | {nat[name]['median_ms']:,.1f} | {same} |")
