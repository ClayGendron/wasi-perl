"""The counts the call-count checks of tests/build/test_reads_and_writes.py see, for each perl.wasm
named. Run from the worktree root: uv run python check_counts.py PERL.wasm..."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, "tests/build")
import test_reads_and_writes as t
W = Path("build/tools/wasmtime-v51.0.0-x86_64-linux/wasmtime")
for m in sys.argv[1:]:
    m = Path(m)
    tmp = Path(tempfile.mkdtemp()); g = tmp / "g"; g.mkdir()
    (g / "big.txt").write_bytes(b"".join(t.LINES))
    p, tr = t._traced(W, m, g, "-ne", "print if /x/", "/g/big.txt")
    q, tq = t._traced(W, m, g, "-pi", "-e", "s/a/b/g", "/g/big.txt")
    tmp2 = Path(tempfile.mkdtemp()); h = tmp2 / "g"; h.mkdir()
    for k in range(200):
        (h / f"f{k:03d}.log").write_text("".join(f"w{i} {'ERROR' if (i + k) % 7 == 0 else 'INFO'}\n" for i in range(50)))
    names = [f"/g/f{k:03d}.log" for k in range(200)]
    a, ta = t._traced(W, m, h, "-ne", t.MANY, *names)
    b, tb = t._traced(W, m, h, "-ne", t.MANY, names[0])
    print(m, "ne fd_read", tr.count("fd_read"), "| pi fd_write", tq.count("fd_write"), "fd_read", tq.count("fd_read"),
          "| 200 files fdstat", ta.count("fd_fdstat_get"), "1 file", tb.count("fd_fdstat_get"), "on opened", ta.on_opened("fd_fdstat_get"))
