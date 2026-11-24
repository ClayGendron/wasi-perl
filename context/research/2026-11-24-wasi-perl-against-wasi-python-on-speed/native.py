"""The same workloads under native perl 5.42.3 and native CPython, as subprocesses: a baseline."""
import json, shutil, statistics, subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); import bench
d = Path(sys.argv[1]).resolve(); w = Path(tempfile.mkdtemp()); perl = sys.argv[2]
def fix(a): return [x.replace("/data/", f"{d}/").replace("/w/", f"{w}/") for x in a]
res = {}
for name, cmd in [("native perl", lambda k: [perl, *fix(bench.PERL[k])]),
                  ("native python", lambda k: [sys.executable, "-I", "-S", "-c", fix([bench.PY[k]])[0]])]:
    res[name] = {}
    for k in bench.PERL:
        xs = []
        for _ in range(7):
            shutil.copyfile(d / "t.log", w / "t.log")
            t = time.perf_counter(); subprocess.run(cmd(k), check=True, capture_output=True); xs.append(time.perf_counter() - t)
        res[name][k] = round(statistics.median(xs) * 1000, 2)
    print(name, res[name])
json.dump(res, open(sys.argv[3], "w"), indent=1)
