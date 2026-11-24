"""A fresh process's peak resident memory after its first two run() calls, warm module cache.

    python peak_rss.py <cache dir>

Prints one JSON line: which wasi_perl ran, and the process's peak RSS in MiB (getrusage).
"""
import json
import resource
import sys

import wasi_perl

for _ in range(2):
    done = wasi_perl.run(["-e", "1"], cache_dir=sys.argv[1])
    assert done.exit_code == 0, done
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KiB on Linux
print(json.dumps({"module": wasi_perl.__file__, "peak_rss_mib": round(peak, 1)}))
