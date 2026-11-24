"""Tables of results/: fuel, WASI calls and the three timed rounds, before against after.

    python3 summarize.py > results/summary.md
"""

import json
import statistics
from pathlib import Path

R = Path(__file__).resolve().parent / "results"


def load(name):
    return json.loads((R / name).read_text())


def main():
    fb, fa, f0 = (load(f"fuel-{v}.json")["fuel"] for v in ("before", "after", "v010"))
    print("| workload | 0.1.0 | before (dev) | after | after against 0.1.0 | after against dev |")
    print("|---|---:|---:|---:|---:|---:|")
    for w in fb:
        print(
            f"| {w} | {f0[w]:,} | {fb[w]:,} | {fa[w]:,} | {fa[w] / f0[w] - 1:+.1%} "
            f"| {fa[w] / fb[w] - 1:+.1%} |"
        )
    print()
    cb, ca = (load(f"calls-{v}.json")["calls"] for v in ("before", "after"))
    print("| workload | WASI calls before | after | fd_read | fd_write | fd_fdstat_get |")
    print("|---|---:|---:|---:|---:|---:|")
    for w in cb:
        b, a = cb[w], ca[w]
        cells = " | ".join(f"{b.get(k, 0)} → {a.get(k, 0)}" for k in ("fd_read", "fd_write", "fd_fdstat_get"))
        print(f"| {w} | {b['total']} | {a['total']} | {cells} |")
    print()
    rounds = {v: [load(f"time-r{r}-{v}.json")["time"] for r in (1, 2, 3)] for v in ("before", "after")}
    print("| ms per call | before | after | change |")
    print("|---|---:|---:|---:|")
    for w in rounds["before"][0]:
        cell = {}
        for v, rs in rounds.items():
            xs = [r[w] for r in rs]
            cell[v] = (statistics.median(xs), min(xs), max(xs))
        b, a = cell["before"], cell["after"]
        print(
            f"| {w} | {b[0]:.1f} ({b[1]:.1f}–{b[2]:.1f}) | {a[0]:.1f} ({a[1]:.1f}–{a[2]:.1f}) "
            f"| {a[0] / b[0] - 1:+.0%} |"
        )


if __name__ == "__main__":
    main()
