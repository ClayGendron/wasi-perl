"""Count the WASI preview1 calls in a wasmtime CLI trace (WASMTIME_LOG=wasmtime_wasi=trace).
Usage: wasicount.py TRACE [--seq]. Prints each function's count, the bytes fd_read and fd_write
moved, and with --seq the order of calls with runs of one function folded into one line."""

import re
import sys
from collections import Counter

LINE = re.compile(r'function="([a-z_0-9]+)"\}: \S+: (.*)$')


def main():
    calls, seq, moved = Counter(), [], Counter()
    args = None
    for line in open(sys.argv[1], errors="replace"):
        m = LINE.search(line)
        if not m:
            continue
        fn, rest = m.groups()
        if not rest.startswith("result="):
            args = rest
            continue
        calls[fn] += 1
        r = re.match(r"result=Ok\((\d+)\)", rest)
        if fn in ("fd_read", "fd_write", "fd_pread", "fd_pwrite") and r:
            moved[fn] += int(r.group(1))
        fd = re.match(r"fd=Fd\((\d+)\)", args or "")
        tag = f"{fn}({fd.group(1)})" if fd else fn
        if fn == "path_open" or fn == "path_filestat_get":
            tag += " " + re.sub(r"\*guest 0x[0-9a-f]+/", "len=", (args or "").split(" path=")[-1].split(" ")[0])
        if not rest.startswith("result=Ok"):
            tag += " -> " + rest[7:40]
        if seq and seq[-1][0] == tag:
            seq[-1][1] += 1
        else:
            seq.append([tag, 1])
    print(f"total {sum(calls.values())}")
    for fn, c in sorted(calls.items(), key=lambda x: -x[1]):
        extra = f" ({moved[fn]} bytes)" if fn in moved else ""
        print(f"{c:7d}  {fn}{extra}")
    if "--seq" in sys.argv:
        for tag, n in seq:
            print(f"   {tag}" + (f" x{n}" if n > 1 else ""))


if __name__ == "__main__":
    main()
