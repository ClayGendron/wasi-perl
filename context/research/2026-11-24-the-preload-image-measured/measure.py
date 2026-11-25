"""The preload image as built, measured through the runner's own API. Run under the lock:

  flock /home/user/wasi-perl-wt/machine.lock uv run python \
      context/research/2026-11-24-the-preload-image-measured/measure.py OUT.json [calls]

1. The making of the default package image, five times, with no disk cache: its whole time, and
   inside it the reactor's prepare, the capture, the writer and the compiles; the prelude's
   fuel; the image's size, segments and data; each .cwasm's size.
2. Whole calls through `wasi_perl.run`, perl.wasm against the image, alternated call by call,
   `calls` of each after two warm-up calls: median wall time and the process's median minor page
   faults per call. Once in process (no disk cache), once from a cache folder after the
   process's modules are forgotten, so both sides load .cwasm files through the sealed memfd.
3. Fuel: each program once, metered, from each.
4. Instantiation alone, median of 30, from the runner's own InstancePre: time, minor faults and
   what /proc/self/maps gains while the instance lives, for perl.wasm and the image module, each
   compiled in process and loaded from a .cwasm.
"""

from __future__ import annotations

import json
import os
import resource
import statistics
import sys
import tempfile
import time
from pathlib import Path

import wasmtime as wt

from wasi_perl import _engines, _image_module, make_package_snapshot, run, runner
from wasi_perl._artifacts import Artifacts

ROOT = Path(__file__).resolve().parents[3]
ART = Artifacts.at(ROOT / "build" / "artifacts")
FIVE = "use strict; use warnings; use POSIX; use JSON::PP; use List::Util; 1"
PROGRAMS = {"five": ["-e", FIVE], "posix": ["-MPOSIX", "-e", "1"], "e1": ["-e", "1"]}


def minflt() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_minflt


def med(xs):
    return statistics.median(xs)


class Timed:
    """Wraps a function, keeping the seconds of each call."""

    def __init__(self, owner, name):
        self.owner, self.name, self.real, self.seconds = owner, name, getattr(owner, name), []
        setattr(owner, name, self)

    def __call__(self, *a, **k):
        t = time.perf_counter()
        try:
            return self.real(*a, **k)
        finally:
            self.seconds.append(time.perf_counter() - t)

    def undo(self):
        setattr(self.owner, self.name, self.real)


def making(n: int = 5):
    rows, snap = [], None
    first_reactor_compile = None
    for i in range(n):
        if i == 0:
            _engines.forget()
        prepare = Timed(runner, "_prepare")
        capture = Timed(runner, "_capture")
        writer = Timed(_image_module, "write")
        module = Timed(wt, "Module")
        t = time.perf_counter()
        snap = make_package_snapshot(artifacts=ART, cache_dir=False)
        total = time.perf_counter() - t
        for w in (prepare, capture, writer, module):
            w.undo()
        compiles = module.seconds  # the reactor's (first making only), then the image's
        if i == 0 and len(compiles) == 2:
            first_reactor_compile = compiles[0]
        _engines.forget()  # so the next making compiles its image module again
        rows.append(
            {
                "total_s": total,
                "prepare_s": prepare.seconds[0],
                "capture_s": capture.seconds[0],
                "write_s": writer.seconds[0],
                "image_compile_s": compiles[-1],
            }
        )
    summary = {k: med([r[k] for r in rows]) for k in rows[0]}
    summary["reactor_compile_s_cold"] = first_reactor_compile
    fueled = Timed(wt, "Module")
    metered = make_package_snapshot(artifacts=ART, cache_dir=False, fuel=10**12)
    fueled.undo()
    summary["prelude_fuel"] = metered.origin.prelude_fuel
    summary["image_compile_metered_s"] = fueled.seconds[-1]
    data, stats = _image_module.write(ART.reactor.read_bytes(), snap._image)
    summary.update(stats)
    summary["image_bytes"] = snap.origin.image_bytes
    for flavour in (False, True):
        blob = wt.Module(_engines.engine(flavour), data).serialize()
        summary[f"cwasm_bytes_{'fuel' if flavour else 'plain'}"] = len(blob)
    blob = wt.Module(_engines.engine(False), ART.perl.read_bytes()).serialize()
    summary["perl_wasm_cwasm_bytes"] = len(blob)
    return {"runs": rows, "median": summary}, snap


def calls(snap, n: int, cache) -> dict:
    out = {}
    for name, argv in PROGRAMS.items():
        t_p, t_i, f_p, f_i = [], [], [], []
        for i in range(n + 2):
            for which in ("plain", "image"):
                f0, t0 = minflt(), time.perf_counter()
                c = run(argv, snapshot=snap if which == "image" else False, artifacts=ART,
                        cache_dir=cache)  # fmt: skip
                dt, df = time.perf_counter() - t0, minflt() - f0
                assert c.exit_code == 0, (which, name, c)
                if i >= 2:
                    (t_p if which == "plain" else t_i).append(dt)
                    (f_p if which == "plain" else f_i).append(df)
        out[name] = {
            "plain_ms": med(t_p) * 1e3,
            "image_ms": med(t_i) * 1e3,
            "speedup": med(t_p) / med(t_i),
            "plain_minflt": med(f_p),
            "image_minflt": med(f_i),
        }
    return out


def fuel(snap) -> dict:
    out = {}
    for name, argv in PROGRAMS.items():
        p = run(argv, fuel=10**12, artifacts=ART, cache_dir=False)
        i = run(argv, snapshot=snap, fuel=10**12, artifacts=ART, cache_dir=False)
        out[name] = {"plain": p.fuel_used, "image": i.fuel_used}
    return out


def maps_summary() -> dict[str, int]:
    seen: dict[str, int] = {}
    with open("/proc/self/maps") as f:
        for line in f:
            parts = line.split(maxsplit=5)
            if len(parts) == 6 and ("memfd" in parts[5] or parts[5].strip().endswith(".cwasm")):
                a, b = (int(x, 16) for x in parts[0].split("-"))
                seen[parts[5].strip()] = seen.get(parts[5].strip(), 0) + b - a
    return seen


def instantiation(label: str, mod, n: int = 30) -> dict:
    pre = _engines.linked(False, mod)
    eng = _engines.engine(False)
    ts, fs, live = [], [], None
    for i in range(n + 2):
        store = wt.Store(eng)
        store.set_wasi(wt.WasiConfig())
        store.set_epoch_deadline(1 << 62)
        before = maps_summary() if i == 0 else None
        f0, t0 = minflt(), time.perf_counter()
        inst = pre.instantiate(store)
        dt, df = time.perf_counter() - t0, minflt() - f0
        if i == 0:
            after = maps_summary()
            live = {k: v - before.get(k, 0) for k, v in after.items() if v != before.get(k, 0)}
            live["memory_bytes"] = inst.exports(store)["memory"].data_len(store)
        store.close()
        if i >= 2:
            ts.append(dt)
            fs.append(df)
    return {"label": label, "ms": med(ts) * 1e3, "minflt": med(fs), "mapped": live}


def main(out: Path, n: int) -> None:
    result: dict = {}
    result["making"], snap = making()
    result["calls_in_process"] = calls(snap, n, False)
    result["fuel"] = fuel(snap)
    reactor_digest = snap.origin.binary_sha256
    perl_digest = __import__("hashlib").sha256(ART.perl.read_bytes()).hexdigest()
    inst = [
        instantiation("perl.wasm, in process", _engines.module(False, ART.perl, perl_digest, None)),
        instantiation(
            "image, in process",
            _engines.image_module(False, ART.reactor, reactor_digest, snap._image,
                                  snap.origin.image_sha256, None),  # fmt: skip
        ),
    ]
    with tempfile.TemporaryDirectory(dir=ROOT / "build") as tmp:
        os.chmod(tmp, 0o700)
        cache = Path(tmp) / "cache"
        _engines.forget()  # so the two calls below write both .cwasm files
        run(["-e", "1"], artifacts=ART, cache_dir=cache)
        run(["-e", "1"], snapshot=snap, artifacts=ART, cache_dir=cache)
        _engines.forget()
        before = _engines.counts()
        result["calls_cwasm"] = calls(snap, n, cache)
        after = _engines.counts()
        result["cwasm_counts"] = {k: after[k] - before[k] for k in after}
        _engines.forget()
        inst.append(instantiation("perl.wasm, from .cwasm",
                                  _engines.module(False, ART.perl, perl_digest, cache)))  # fmt: skip
        inst.append(
            instantiation(
                "image, from .cwasm",
                _engines.image_module(False, ART.reactor, reactor_digest, snap._image,
                                      snap.origin.image_sha256, cache),  # fmt: skip
            )
        )
    result["instantiation"] = inst
    out.write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 60)
