"""Runs `-e 1` N times through wasi_perl.run on a given artifact set, for `strace -f -c`.

    python e1_calls.py <artifact dir> <n>
"""

import sys
from wasi_perl import Artifacts, run

a = Artifacts.at(sys.argv[1])
for _ in range(int(sys.argv[2])):
    d = run(["-e", "1"], artifacts=a, cache_dir=False)
    assert d.exit_code == 0, d
