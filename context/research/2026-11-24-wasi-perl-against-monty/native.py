"""Process-per-call references: native perl 5.42.3 and Monty's `monty` CLI, each workload as a
subprocess. Also counts native x86-64 instructions of both under valgrind (callgrind's Ir).

Run with the Monty venv's python, `-I`.

  times PERL MONTY DATA OUT.json [REPS]   median, min, max wall time per workload
  instr PERL MONTY DATA OUT.json          instructions per workload, one valgrind run each
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workloads import W  # noqa: E402


def argv(tool: str, exe: str, data: Path, name: str) -> list[str]:
    if tool == "perl":
        return [exe] + [a.replace("/data", str(data)) for a in W[name][0]]
    return [exe, "-m", f"{data}::/data", "-c", W[name][1]]


def times(perl: str, monty: str, data: Path, dest: Path, reps: int = 10) -> None:
    res: dict = {"perl": {}, "monty_cli": {}}
    for tool, exe, key in (("perl", perl, "perl"), ("monty", monty, "monty_cli")):
        for n in W:
            a = argv(tool, exe, data, n)
            ts, out, k, warm = [], b"", reps, 2
            i = 0
            while i < k + warm:
                t = time.perf_counter()
                p = subprocess.run(a, capture_output=True, env={})
                s = time.perf_counter() - t
                if i == 0 and s > 2.0:  # a cliff: one warm-up and three timed calls
                    k, warm = 3, 1
                elif i >= warm:
                    ts.append(s)
                i += 1
                if p.returncode != 0:
                    raise SystemExit(f"{key} {n}: {p.returncode} {p.stderr[:300]!r}")
                out = p.stdout
            ts.sort()
            res[key][n] = {
                "n": len(ts),
                "median_ms": round(statistics.median(ts) * 1000, 3),
                "min_ms": round(ts[0] * 1000, 3),
                "max_ms": round(ts[-1] * 1000, 3),
                "stdout": out.decode(),
            }
            print(key, n, res[key][n], flush=True)
    dest.write_text(json.dumps(res, indent=1))


def instr(perl: str, monty: str, data: Path, dest: Path) -> None:
    res: dict = {"perl": {}, "monty_cli": {}}
    for tool, exe, key in (("perl", perl, "perl"), ("monty", monty, "monty_cli")):
        for n in W:
            if key == "monty_cli" and n in ("strcat", "sub_log"):
                res[key][n] = "skipped: 6-24 s natively, hours under valgrind"
                continue
            with tempfile.NamedTemporaryFile() as f:
                p = subprocess.run(
                    ["valgrind", "--tool=callgrind", f"--callgrind-out-file={f.name}"]
                    + argv(tool, exe, data, n),
                    capture_output=True,
                    env={},
                )
            m = re.search(rb"Collected : (\d+)", p.stderr)
            res[key][n] = int(m.group(1)) if m and p.returncode == 0 else f"failed {p.returncode}"
            print(key, n, res[key][n], flush=True)
    dest.write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    cmd, perl, monty, data, dest, *rest = sys.argv[1:]
    if cmd == "times":
        times(perl, monty, Path(data), Path(dest), int(rest[0]) if rest else 10)
    else:
        instr(perl, monty, Path(data), Path(dest))
