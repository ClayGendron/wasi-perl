"""A fresh process's first perl call with a warm cache, split: the artifact check, the module load, the call."""
import json, sys, time
from pathlib import Path
cache = sys.argv[1]
t = time.perf_counter(); import wasi_perl; from wasi_perl import _artifacts, _engines; imp = time.perf_counter() - t
t = time.perf_counter(); found, want = _artifacts.checked(); chk = time.perf_counter() - t
t = time.perf_counter(); _engines.module(False, found.perl, want, Path(cache)); load = time.perf_counter() - t
t = time.perf_counter(); wasi_perl.run(["-e", "1"], cache_dir=cache); call = time.perf_counter() - t
print(json.dumps({"import_ms": imp*1e3, "artifact_check_ms": chk*1e3, "module_load_ms": load*1e3, "first_call_after_ms": call*1e3}))
