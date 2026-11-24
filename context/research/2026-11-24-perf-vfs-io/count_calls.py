"""Step 1: the WASI calls each vfs-shaped program makes, by name, with bytes, through a grant
(wasmtime's own WASI) and through the prototype's mount, and the provider calls the mount makes
under each design switch. Deterministic: no clock is read.

    python count_calls.py results/counts.json > results/counts.md
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

import harness as h

from wasi_perl import Grant

# The design switches of the prototype's mount (`Mount` fields), each a column of provider calls.
DESIGNS = {
    "a_wasi_python": dict(defer_create=False, memo=False, stat_reads=False),
    "memo": dict(defer_create=False, memo=True, stat_reads=False),
    "memo_defer": dict(defer_create=True, memo=True, stat_reads=False),
    "memo_defer_statread": dict(defer_create=True, memo=True, stat_reads=True),
    "all_four": dict(defer_create=True, memo=True, stat_reads=True, defer_close=True),
}


def attribute(events: list[tuple]) -> Counter[str]:
    """Who asked for each call, by its place in the sequence: the guest's descriptor table at
    its start (its scan of fd_fdstat_get and fd_prestat_get, which ends at the first number the
    host does not know) and before each open (the path_filestat_get just before a path_open of
    the same path); everything else is Perl's or wasi-libc's."""
    out: Counter[str] = Counter()
    scan = True
    for i, e in enumerate(events):
        name = e[0]
        if scan and name in ("fd_fdstat_get", "fd_prestat_get") and i >= 4:
            out["fd-table start"] += 1
            if name == "fd_fdstat_get" and e[5] == 8:
                scan = False
            continue
        if i >= 4:
            scan = False
        nxt = events[i + 1] if i + 1 < len(events) else None
        if name == "path_filestat_get" and nxt and nxt[0] == "path_open" and nxt[2] == e[2]:
            out["fd-table look"] += 1
            continue
        out["perl + wasi-libc"] += 1
    return out


def main() -> None:
    out_path = Path(sys.argv[1])
    files = h.make_data()
    results: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for w in h.workloads("/work"):
            row: dict = {}
            host = Path(tmp) / w.name
            h.write_tree(files, host)
            done, rec = h.observed(w.argv, grants=[Grant(host, "/work", writable=w.writable)])
            assert done.exit_code == 0, (w.name, done)
            row["grant"] = {
                "calls": dict(rec.counts()),
                "bytes": {k: v for k, v in rec.moved().items() if v},
                "total": len(rec.events),
                "who": dict(attribute(rec.events)),
                "fd_read_sizes": dict(Counter(e[3] for e in rec.events if e[0] == "fd_read")),
                "stdout": len(done.stdout),
            }
            grant_out = done.stdout
            for design, switches in DESIGNS.items():
                mount = h.mount_for(files, "/work", w.writable, **switches)
                done, rec = h.observed(w.argv, mounts=[mount])
                assert done.exit_code == 0, (w.name, design, done)
                assert done.stdout == grant_out, (w.name, design)
                if w.name.startswith("pi_") or w.name == "write_new":
                    host_files = {
                        "/" + str(p.relative_to(host)): p.read_bytes()
                        for p in host.rglob("*")
                        if p.is_file()
                    }
                    assert mount.provider.files == host_files, (w.name, design)
                row[design] = {
                    "provider": {k: v for k, v in (done.files or {}).items() if v},
                    "provider_total": sum((done.files or {}).values()),
                    "wasi_total": len(rec.events),
                    "calls": dict(rec.counts()),
                }
            # Design (e): the provider-side cache, in database round trips, under the
            # prototype's protocol-level switches off (wasi-python's design) and on.
            for design in ("a_wasi_python", "all_four"):
                provider = h.RoundTrips(files)
                provider.prefetch(
                    [a[len("/work") :] for a in w.argv if a.startswith("/work/") and " " not in a]
                )
                mount = h.Mount(provider, "/work", writable=w.writable, **DESIGNS[design])
                done, _ = h.observed(w.argv, mounts=[mount])
                assert done.exit_code == 0 and done.stdout == grant_out, (w.name, design)
                row[f"e_cache+{design}"] = {"trips": provider.trips, "protocol": done.files}
            results[w.name] = row
    out_path.write_text(json.dumps(results, indent=1, sort_keys=True))
    print(
        "| workload | WASI calls (grant) | of them: fd-table start / look | fd_read calls, bytes |"
        " fd_write calls | path_open | path_filestat_get | fd_readdir |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for name, row in results.items():
        g = row["grant"]
        c, b, who = g["calls"], g["bytes"], g["who"]
        print(
            f"| {name} | {g['total']} | {who.get('fd-table start', 0)} / "
            f"{who.get('fd-table look', 0)} | {c.get('fd_read', 0)}, {b.get('fd_read', 0):,} |"
            f" {c.get('fd_write', 0)} | {c.get('path_open', 0)} | "
            f"{c.get('path_filestat_get', 0)} | {c.get('fd_readdir', 0)} |"
        )
    print()
    print("| workload | " + " | ".join(DESIGNS) + " |")
    print("|---|" + "---|" * len(DESIGNS))
    for name, row in results.items():
        cells = []
        for d in DESIGNS:
            p = row[d]["provider"]
            cells.append(
                f"{row[d]['provider_total']} ("
                + ", ".join(f"{k} {v}" for k, v in sorted(p.items()))
                + ")"
            )
        print(f"| {name} | " + " | ".join(cells) + " |")
    print()
    print("| workload | database round trips, (e) cache + (a) | (e) cache + all four switches |")
    print("|---|---|---|")
    for name, row in results.items():
        a, b = row["e_cache+a_wasi_python"], row["e_cache+all_four"]
        print(f"| {name} | {a['trips']} | {b['trips']} |")


if __name__ == "__main__":
    main()
