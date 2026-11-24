"""Print the memo's tables from the result files, and check the answers agree.

Usage: python report.py RESULTS_DIR

Reads fuel-base.json, fuel-A.json (and fuel-B.json if present), wall-A.json (and wall-B.json),
native.json and python.json. A wasi-perl answer that differs from native perl's is flagged
"DIFFERS"; Python answers are shown only where they differ, since formats may.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load(d: Path, name: str) -> dict:
    p = d / name
    return json.loads(p.read_text()) if p.exists() else {}


def mfuel(x: dict | None) -> str:
    return f"{x['fuel'] / 1e6:,.0f}" if x and x.get("exit", 0) == 0 and x.get("fuel") else "–"


def ms(x: dict | None) -> str:
    return f"{x['median_ms']:,.0f}" if x and "median_ms" in x else "–"


def main() -> None:
    d = Path(sys.argv[1])
    fb, fa, fB = load(d, "fuel-base.json"), load(d, "fuel-A.json"), load(d, "fuel-B.json")
    wa, wB = load(d, "wall-A.json"), load(d, "wall-B.json")
    nat, py = load(d, "native.json"), load(d, "python.json")
    print("| program | fuel M, base | fuel M, prototype | wasi-perl ms | native perl ms | guest/native | wasi-python ms | python fuel M | answers |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for k in fa | fB:
        f = fB.get(k) if k in fB and fB[k].get("exit") == 0 else fa.get(k)
        w = wB.get(k) if k in wB else wa.get(k)
        n = nat.get(k)
        ratio = f"{w['median_ms'] / n['median_ms']:.1f}×" if w and n and "median_ms" in n else "–"
        agree = "–"
        if f and f.get("exit") == 0 and n and "answer" in n:
            agree = "same" if f["answer"] == n["answer"] else "DIFFERS"
        p = py.get(k)
        if p and f and p["answer"] != f.get("answer"):
            agree += "; py differs"
        print(f"| {k} | {mfuel(fb.get(k))} | {mfuel(f)} | {ms(w)} | {ms(n)} | {ratio} | {ms(p)} | {mfuel(p)} | {agree} |")


if __name__ == "__main__":
    main()
