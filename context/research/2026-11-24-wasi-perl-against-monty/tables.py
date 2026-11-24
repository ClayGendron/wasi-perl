"""Print the memo's tables from results/. Usage: python3 tables.py"""

from __future__ import annotations

import json
from pathlib import Path

R = Path(__file__).resolve().parent / "results"
SESSIONS = ("s1", "s2")


def load(name: str) -> dict:
    return json.loads((R / name).read_text())


def rng(vals: list[float]) -> str:
    lo, hi = min(vals), max(vals)
    f = (
        (lambda v: f"{v:,.0f}")
        if hi >= 100
        else (lambda v: f"{v:.1f}")
        if hi >= 1
        else (lambda v: f"{v:.2f}")
    )
    return f(lo) if f(lo) == f(hi) else f"{f(lo)}–{f(hi)}"


def calls_table() -> None:
    cols = [("wasi-perl", "perl-calls-{}.json", None)]
    cols += [
        (f"Monty {m}", f"monty-calls-{m}-{{}}.json", None) for m in ("checkout", "session", "fresh")
    ]
    cols += [
        ("native perl", "native-times-{}.json", "perl"),
        ("monty CLI", "native-times-{}.json", "monty_cli"),
    ]
    data = {
        c: [load(f.format(s)) for s in SESSIONS if (R / f.format(s)).exists()] for c, f, _ in cols
    }
    keys = {c: k for c, _, k in cols}
    works = list(data["wasi-perl"][0])
    print("| workload | " + " | ".join(c for c, _, _ in cols) + " |")
    print("|---|" + "---:|" * len(cols))
    for w in works:
        row = []
        for c, _, _ in cols:
            ds = [d[keys[c]] if keys[c] else d for d in data[c]]
            row.append(rng([d[w]["median_ms"] for d in ds]))
        print(f"| {w} | " + " | ".join(row) + " |")


def counts_table() -> None:
    fuel, ins = load("perl-fuel.json"), load("native-instr.json")
    pm, mm = load("perl-memory.json"), load("monty-memory.json")
    print(
        "| workload | wasi-perl fuel | native perl Ir | monty CLI Ir | perl linear memory MiB | Monty worker VmHWM MiB |"
    )
    print("|---|---:|---:|---:|---:|---:|")
    for w in fuel:
        m = ins["monty_cli"][w]
        m = f"{m / 1e6:,.0f} M" if isinstance(m, int) else "skipped"
        print(
            f"| {w} | {fuel[w] / 1e6:,.0f} M | {ins['perl'][w] / 1e6:,.0f} M | {m} | {pm[w]['mib']} |"
            f" {rng(mm[w]['vm_hwm_mib'])} |"
        )


def firsts() -> None:
    for side in ("perl", "monty"):
        rows = [
            json.loads(x)
            for s in SESSIONS
            for x in (R / f"{side}-first-{s}.jsonl").read_text().split("\n")
            if x
        ]
        keys = [k for k in rows[0]]
        print(side, {k: rng([r[k] for r in rows]) for k in keys}, f"n={len(rows)}")


if __name__ == "__main__":
    calls_table()
    print()
    counts_table()
    print()
    firsts()
    for f in sorted(R.glob("*idle*")) + sorted(R.glob("*stdin*")) + sorted(R.glob("*hostdata*")):
        print(f.name, json.loads(f.read_text()))
