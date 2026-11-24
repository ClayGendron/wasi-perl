"""Where an embedder's image pays: a program that imports pandas, from the shipped snapshot and
from an image that already imported it; and Perl's heaviest common loads, for scale."""
import json, os, statistics, sys, tempfile, time
from pathlib import Path
import wasi_perl, wasi_python
cache = Path(tempfile.mkdtemp()); os.chmod(cache, 0o700)
def med(f, n=10):
    for _ in range(2): f()
    xs = []
    for _ in range(n):
        t = time.perf_counter(); r = f(); xs.append(time.perf_counter() - t); assert r.exit_code == 0, r.stderr[-300:]
    return round(statistics.median(xs) * 1000, 1)
code = "import pandas as pd\nprint(pd.DataFrame({'a': [1, 2, 3]}).a.sum())"
t = time.perf_counter(); img = wasi_python.make_snapshot("import pandas", cache_dir=cache); made = time.perf_counter() - t
res = {
    "python pandas, plain": med(lambda: wasi_python.run(["-c", code], snapshot=False, cache_dir=cache)),
    "python pandas, shipped snapshot": med(lambda: wasi_python.run(["-c", code], cache_dir=cache)),
    "python pandas, image of `import pandas`": med(lambda: wasi_python.run(["-c", code], snapshot=img, cache_dir=cache)),
    "python image made in s": round(made, 2),
    "perl -e 1": med(lambda: wasi_perl.run(["-e", "1"], cache_dir=cache), 30),
    "perl -MPOSIX -MList::Util -MData::Dumper -MJSON::PP -MStorable -MEncode -e 1": med(lambda: wasi_perl.run(
        ["-MPOSIX", "-MList::Util", "-MData::Dumper", "-MJSON::PP", "-MStorable", "-MEncode", "-e", "1"], cache_dir=cache), 30),
}
f = {}
for k, call in [("pandas plain", lambda: wasi_python.run(["-c", code], snapshot=False, cache_dir=cache, fuel=10**13)),
                ("pandas snapshot", lambda: wasi_python.run(["-c", code], cache_dir=cache, fuel=10**13)),
                ("pandas image", lambda: wasi_python.run(["-c", code], snapshot=img, cache_dir=cache, fuel=10**13)),
                ("perl six modules", lambda: wasi_perl.run(["-MPOSIX", "-MList::Util", "-MData::Dumper", "-MJSON::PP", "-MStorable", "-MEncode", "-e", "1"], cache_dir=cache, fuel=10**13))]:
    f[k] = call().fuel_used
res["fuel"] = f
print(json.dumps(res, indent=1)); json.dump(res, open(sys.argv[1], "w"), indent=1)
