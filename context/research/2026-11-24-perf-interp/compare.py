"""Fuel of each variant against the base relink, as a markdown table of changes in percent.
Usage: compare.py RESULTS_DIR VARIANT..."""

import json
import sys
from pathlib import Path

d = Path(sys.argv[1])
base = json.loads((d / "fuel-relink.json").read_text())["fuel"]
works = ["hello", "e1", "sum_csv", "grep_log", "inplace", "count_lines", "cat_log", "many_files", "json", "loop"]
print("| variant | " + " | ".join(works) + " |")
print("|---|" + "---:|" * len(works))
print("| base (instructions) | " + " | ".join(f"{base[w] / 1e6:,.1f} M" for w in works) + " |")
for v in sys.argv[2:]:
    f = json.loads((d / f"fuel-{v}.json").read_text())["fuel"]
    cells = [f"{100 * (f[w] - base[w]) / base[w]:+.1f}%" if w in f else "" for w in works]
    print(f"| {v} | " + " | ".join(cells) + " |")
