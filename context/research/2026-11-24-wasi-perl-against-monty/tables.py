"""Print the memo's tables from results/. Usage: python3 tables.py

A cell "a–b" is the lowest and highest of the per-session medians (s1, s2) of that harness alone;
one value means both sessions gave it. Ratios are taken within a session, then ranged.
"""

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


def med(name: str, s: str, w: str, key: str | None = None) -> float:
    d = load(name.format(s))
    return (d[key] if key else d)[w]["median_ms"]


def host_table() -> None:
    """Section 4.6: each column one harness, ranged over its two sessions."""
    cols = [
        ("wasi-perl, grant", "perl-calls-{}.json", None),
        ("flag off", "perl-flag-off-{}.json", None),
        ("flag on", "perl-flag-on-{}.json", None),
        ("Monty checkout, mount", "monty-calls-checkout-{}.json", None),
        ("monty CLI, mount, one process per call", "native-times-{}.json", "monty_cli"),
        ("native perl", "native-times-{}.json", "perl"),
    ]
    print("| task | " + " | ".join(c for c, _, _ in cols) + " |")
    print("|---|" + "---:|" * len(cols))
    for w in ("read_big", "read_small", "grep_log", "sum_csv", "split_join", "hello"):
        row = [rng([med(f, s, w, k) for s in SESSIONS]) for _, f, k in cols]
        print(f"| `{w}` | " + " | ".join(row) + " |")
    st = [load(f"perl-stdin-{s}.json")["read_big_stdin"]["median_ms"] for s in SESSIONS]
    hd = {
        k: rng([load(f"monty-hostdata-{s}.json")[k]["median_ms"] for s in SESSIONS])
        for k in load("monty-hostdata-s1.json")
    }
    print(f"read_big through stdin: {rng(st)}; Monty without a mount: {hd}")


GROUPS = [
    ("(b) bare call", ["hello"]),
    ("(c) CPU", ["fib25", "loop", "strjoin"]),
    ("(c) string building", ["strcat"]),
    ("(d) text", ["sum_csv", "grep_log", "split_join"]),
    ("(d) big regex substitution", ["sub_log"]),
    ("(e) dict and sort", ["wordfreq", "sort_ints"]),
    ("(e) JSON", ["json_decode", "json_encode"]),
    ("(f) host files", ["read_big", "read_small"]),
]


def ratio_table() -> None:
    """Section 5: wasi-perl's median over Monty checkout's, within each session."""
    print("| group | tasks | against Monty checkout | flag on | against the monty CLI | flag on |")
    print("|---|---|---:|---:|---:|---:|")
    for g, ws in GROUPS:
        cells = []
        for other, key in (
            ("monty-calls-checkout-{}.json", None),
            ("native-times-{}.json", "monty_cli"),
        ):
            for mine in ("perl-calls-{}.json", "perl-flag-on-{}.json"):
                rs = []
                for s in SESSIONS:
                    m = load(mine.format(s))
                    for w in ws:
                        if w in m:
                            rs.append(m[w]["median_ms"] / med(other, s, w, key))
                if not rs:
                    cells.append("")
                    continue
                lo, hi = f"{min(rs):.3g}", f"{max(rs):.3g}"
                cells.append((lo if lo == hi else f"{lo}–{hi}") + "×")
        print(f"| {g} | {', '.join(ws)} | " + " | ".join(cells) + " |")


def paced_table() -> None:
    """Section 4.2: hello after 0, 50 ms and 1 s of sleep."""
    files = [
        ("wasi-perl", "perl-paced-{}.json"),
        ("Monty fresh", "monty-paced-fresh-{}.json"),
        ("Monty checkout", "monty-paced-checkout-{}.json"),
    ]
    print("| side | back to back | 50 ms between calls | 1 s between calls |")
    print("|---|---:|---:|---:|")
    for name, f in files:
        row = []
        for g in ("gap_0.0s", "gap_0.05s", "gap_1.0s"):
            row.append(rng([load(f.format(s))[g]["median_ms"] for s in SESSIONS]))
        print(f"| {name} | " + " | ".join(row) + " |")


def check() -> None:
    """The same bytes everywhere: wasi-perl, Monty, native perl, the monty CLI, both flag arms."""
    ref = load("perl-stdout.json")
    same = ref == load("monty-stdout.json")
    for s in SESSIONS:
        nt = load(f"native-times-{s}.json")
        same &= all(nt[k][w]["stdout"] == ref[w] for k in ("perl", "monty_cli") for w in ref)
        for arm in ("off", "on"):
            fl = load(f"perl-flag-{arm}-{s}.json")
            same &= all(fl[w]["stdout"] == ref[w] for w in fl)
    print("same stdout on every runtime and arm:", same)


if __name__ == "__main__":
    check()
    paced_table()
    print()
    host_table()
    print()
    ratio_table()
    print()
    calls_table()
    print()
    counts_table()
    print()
    firsts()
    for f in sorted(R.glob("*idle*")) + sorted(R.glob("*stdin*")) + sorted(R.glob("*hostdata*")):
        print(f.name, json.loads(f.read_text()))
