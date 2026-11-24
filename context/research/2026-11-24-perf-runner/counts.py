"""Counts per warm run() call: page faults (getrusage minflt) and, under strace, syscalls.

    python counts.py <scenario> <calls>

Runs 5 warm-up calls, writes a marker (a getpid() burst) and then <calls> calls, so an strace
of the whole process can be cut at the marker. Prints minor faults per call.
"""
import os
import resource
import sys
import tempfile
from pathlib import Path

import wasi_perl

scenario, calls = sys.argv[1], int(sys.argv[2])
data = Path(tempfile.mkdtemp())
w = Path(tempfile.mkdtemp())
cache = Path(tempfile.mkdtemp())
cache.chmod(0o700)
G = wasi_perl.Grant
grants = {"bare": [], "ro": [G(str(data), "/data")],
          "rw": [G(str(data), "/data"), G(str(w), "/w", writable=True)]}[scenario]
one = lambda: wasi_perl.run(["-e", 'print "hello\\n"'], grants=grants, cache_dir=cache)
for _ in range(5):
    one()
os.write(2, b"=====MARK=====\n")
r0 = resource.getrusage(resource.RUSAGE_SELF)
for _ in range(calls):
    assert one().exit_code == 0
r1 = resource.getrusage(resource.RUSAGE_SELF)
os.write(2, b"=====END=====\n")
print(f"{scenario}: minflt/call {(r1.ru_minflt - r0.ru_minflt) / calls:.0f}, "
      f"majflt/call {(r1.ru_majflt - r0.ru_majflt) / calls:.1f}")
