"""Median ms of a warm `wasi_perl.run(["-e", "1"])`, stock or with the blocking flag on.

    python bare_calls.py <src dir holding wasi_perl> <calls> [--lib DIR [--blocking]]
"""

import ctypes
import statistics
import sys
import tempfile
import time
from pathlib import Path

args = sys.argv[1:]
if "--lib" in args:
    sys.path.insert(0, args[args.index("--lib") + 1])
sys.path.insert(0, args[0])
import wasmtime as wt  # noqa: E402

if "--blocking" in args:
    fn = wt._ffi.dll.wasi_config_allow_blocking_current_thread
    fn.argtypes = [ctypes.c_void_p, ctypes.c_bool]
    original = wt.WasiConfig.__init__

    def init(self, *a, **k):
        original(self, *a, **k)
        fn(self.ptr(), True)

    wt.WasiConfig.__init__ = init
import wasi_perl  # noqa: E402

cache = Path(tempfile.mkdtemp())
cache.chmod(0o700)
for _ in range(5):
    wasi_perl.run(["-e", "1"], cache_dir=cache)
xs = []
for _ in range(int(args[1])):
    t = time.perf_counter()
    wasi_perl.run(["-e", "1"], cache_dir=cache)
    xs.append((time.perf_counter() - t) * 1000)
arm = "blocking" if "--blocking" in args else "stock"
print(f"bare -e 1 {arm}: median {statistics.median(xs):.3f} ms, min {min(xs):.3f} ms over {len(xs)}")
