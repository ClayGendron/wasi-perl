"""Wall time over interleaved rounds: per variant and workload, the median of the rounds'
medians, with the lowest and highest round. Usage: timesum.py RESULTS_DIR PREFIX VARIANT..."""

import json
import statistics
import sys
from pathlib import Path

d, prefix, variants = Path(sys.argv[1]), sys.argv[2], sys.argv[3:]
works = ["hello", "sum_csv", "grep_log", "inplace", "count_lines", "many_files", "loop"]
print("| variant | " + " | ".join(works) + " |")
print("|---|" + "---:|" * len(works))
for v in variants:
    rounds = [json.loads(p.read_text())["time"] for p in sorted(d.glob(f"{prefix}-r*-{v}.json"))]
    cells = []
    for w in works:
        xs = sorted(r[w] for r in rounds if w in r)
        cells.append(f"{statistics.median(xs):.1f} ({xs[0]:.0f}–{xs[-1]:.0f})" if xs else "")
    print(f"| {v} ({len(rounds)} rounds) | " + " | ".join(cells) + " |")
