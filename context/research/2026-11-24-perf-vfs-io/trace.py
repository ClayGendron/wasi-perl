"""The WASI call sequence of `-pi` and of the first two files of the 500-row grep, through a
grant: (name, descriptor, path, bytes asked, bytes moved, errno), one call a line.

    python trace.py > results/traces.txt
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import harness as h

from wasi_perl import Grant


def main() -> None:
    files = h.make_data()
    host = Path(tempfile.mkdtemp())
    h.write_tree(files, host)
    for w in h.workloads("/work"):
        if w.name not in ("pi_50k", "grep_args_500"):
            continue
        _, rec = h.observed(w.argv, grants=[Grant(host, "/work", writable=w.writable)])
        print(f"== {w.name}: {len(rec.events)} calls; from the 33rd")
        events = rec.events[32:] if w.name == "pi_50k" else rec.events[32:52]
        for e in events:
            print(e)


if __name__ == "__main__":
    main()
