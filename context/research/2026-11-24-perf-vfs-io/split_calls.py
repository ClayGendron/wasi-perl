"""How many of each workload's WASI calls a mount serves in Python, how many a shadow forwards
to wasmtime (a descriptor that is not the mount's), and how many never meet a shadow (calls with
no descriptor a mount could hold). Through the prototype's mount, all four switches on.

    python split_calls.py > results/split.md
"""

from __future__ import annotations

from collections import Counter

import harness as h

from wasi_perl._mounts import PROVIDER_BASE, SIGNATURES

MOUNT_PREOPEN = 5  # stdio, the library and /dev come first, and there is no grant


def main() -> None:
    files = h.make_data()
    print("| workload | served by the mount | forwarded by a shadow | not shadowed | total |")
    print("|---|---|---|---|---|")
    for w in h.workloads("/work"):
        mount = h.mount_for(
            files, "/work", w.writable, memo=True, stat_reads=True, defer_close=True
        )
        done, rec = h.observed(w.argv, mounts=[mount])
        assert done.exit_code == 0, done
        split: Counter[str] = Counter()
        for name, fd, *_ in rec.events:
            if name not in SIGNATURES:
                split["plain"] += 1
            elif fd >= PROVIDER_BASE or (fd == MOUNT_PREOPEN and name.startswith("path_")):
                split["served"] += 1
            elif name == "fd_readdir" and fd >= PROVIDER_BASE:
                split["served"] += 1
            else:
                split["forwarded"] += 1
        print(
            f"| {w.name} | {split['served']} | {split['forwarded']} | {split['plain']} |"
            f" {len(rec.events)} |"
        )


if __name__ == "__main__":
    main()
