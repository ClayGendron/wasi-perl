"""The fuel of the check of tests/build/test_reads_and_writes.py, for each perl.wasm named, twice.
Run from the worktree root: uv run python check_fuel.py PERL.wasm..."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, "tests/build")
import test_reads_and_writes as t
tmp = Path(tempfile.mkdtemp())
(tmp/"data").mkdir(); t._write_log(tmp/"data"/"t.log")
for m in sys.argv[1:]:
    for _ in range(2):
        print(m, t._fuel(Path(m), tmp/"data", tmp, *t.GREP_LOG), flush=True)
