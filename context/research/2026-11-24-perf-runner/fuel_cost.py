"""What fuel metering costs on a computing guest: the same loop with and without a fuel cap
(a fuel cap selects the metered engine, whose code counts every instruction).

    python fuel_cost.py <calls>
"""
import json
import statistics
import sys
import time

import wasi_perl

LOOP = ["-e", '$s += $_ * $_ for 1 .. 1_000_000; print "$s\\n"']
res = {}
for name, kw in (("plain", {}), ("fuel", {"fuel": 10**12})):
    for _ in range(2):
        wasi_perl.run(LOOP, **kw)
    xs = []
    for _ in range(int(sys.argv[1])):
        t = time.perf_counter()
        r = wasi_perl.run(LOOP, **kw)
        xs.append(time.perf_counter() - t)
    res[name] = {"ms": round(statistics.median(xs) * 1e3, 1), "fuel_used": r.fuel_used}
print(json.dumps(res))
