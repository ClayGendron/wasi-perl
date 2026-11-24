"""Research prototype, not shipped: what choice (c) costs and saves, on Linux. Run under the lock:

  flock /home/user/wasi-perl-wt/machine.lock uv run python \
      context/research/2026-11-24-preload-image-prototype/item3_speed.py <out.json> [calls]

1. The making, five times: the prelude's run, the capture, the module's writing, its compile
   for each flavour, its serialisation, and the .cwasm's size.
2. Whole calls through `wasi_perl.run`, plain perl.wasm against the image module, alternated
   call by call, `calls` of each (default 60) after two warm-up calls: median wall time and
   median minor page faults of the process per call. Once from an image module compiled in
   this process, once from one loaded from a .cwasm through `_cache.load`'s sealed memfd.
3. Fuel: each program once, metered, from each.
4. Instantiation alone, median of 30: wall time, minor faults, and what /proc/self/maps shows
   mapped while the instance lives, for perl.wasm and the image module; compiled in the process,
   loaded from a .cwasm, and, as the control, compiled on an engine with copy-on-write off.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as h  # noqa: E402
import wasmtime as wt  # noqa: E402

from wasi_perl import _cache, _engines  # noqa: E402

PROGRAMS = {"five": ["-e", h.FIVE], "posix": ["-MPOSIX", "-e", "1"], "e1": ["-e", "1"]}


def minflt() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_minflt


def med(xs: list[float]) -> float:
    return statistics.median(xs)


def making(n: int = 5) -> tuple[dict, h.Made, bytes]:
    rows = []
    made = data = None
    for _ in range(n):
        made = h.make(h.PRELUDE)
        data, stats, write_s = h.write_module(made)
        _, plain_s = h.compile_module(data, False)
        mod_m, fuel_s = h.compile_module(data, True)
        mod_p, _ = h.compile_module(data, False)
        t = time.perf_counter()
        blob = mod_p.serialize()
        ser_s = time.perf_counter() - t
        rows.append(
            {
                "prelude_s": made.seconds["prelude"],
                "capture_s": made.seconds["capture"],
                "write_s": write_s,
                "compile_plain_s": plain_s,
                "compile_fuel_s": fuel_s,
                "serialize_s": ser_s,
                "cwasm_bytes": len(blob),
                **stats,
            }
        )
    metered = h.make(h.PRELUDE, metered=True)
    keys = rows[0].keys()
    summary = {k: med([r[k] for r in rows]) for k in keys}
    summary["prelude_fuel"] = metered.fuel
    summary["image_bytes"] = len(made.image)
    summary["perl_wasm_cwasm_bytes"] = len(
        wt.Module(_engines.engine(False), (h.ART / "perl.wasm").read_bytes()).serialize()
    )
    return {"runs": rows, "median": summary}, made, data


def cwasm_module(data: bytes, metered: bool, folder: Path, name: str) -> object:
    path = folder / f"{name}-{'fuel' if metered else 'plain'}.cwasm"
    if not path.exists():
        _cache.write(h.compile_module(data, metered)[0].serialize(), path)
    eng = _engines.engine(metered)
    mod, _ = _cache.load(
        path, lambda n: wt.Module.deserialize_file(eng, n), lambda b: wt.Module.deserialize(eng, b)
    )
    assert mod is not None, f"{path} was refused by the cache's checks"
    return mod


def calls(mods_image: dict, n: int, plain_cache: object) -> dict:
    out = {}
    for name, argv in PROGRAMS.items():
        t_p, t_i, f_p, f_i = [], [], [], []
        for i in range(n + 2):
            for which in ("plain", "image"):
                f0, t0 = minflt(), time.perf_counter()
                if which == "plain":
                    c = h.run_plain(argv, cache_dir=plain_cache)
                else:
                    c = h.run_image(argv, mods_image, cache_dir=False)
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


def fuel(mods_image: dict) -> dict:
    out = {}
    for name, argv in PROGRAMS.items():
        p = h.run_plain(argv, fuel=10**12, cache_dir=False)
        i = h.run_image(argv, mods_image, fuel=10**12, cache_dir=False)
        out[name] = {"plain": p.fuel_used, "image": i.fuel_used}
    return out


def maps_summary() -> dict[str, int]:
    """Bytes mapped per memfd or file name, for the names that can hold a memory image."""
    seen: dict[str, int] = {}
    with open("/proc/self/maps") as f:
        for line in f:
            parts = line.split(maxsplit=5)
            if len(parts) < 6:
                continue
            nm = parts[5].strip()
            if "memfd" in nm or nm.endswith(".cwasm"):
                a, b = (int(x, 16) for x in parts[0].split("-"))
                seen[nm] = seen.get(nm, 0) + (b - a)
    return seen


def instantiation(label: str, eng: object, mod: object, n: int = 30) -> dict:
    linker = wt.Linker(eng)
    linker.define_wasi()
    pre = linker.instantiate_pre(mod)
    ts, fs = [], []
    maps_live = None
    for i in range(n + 2):
        store = wt.Store(eng)
        store.set_wasi(wt.WasiConfig())
        store.set_epoch_deadline(1 << 62)
        try:
            store.set_fuel(1 << 62)
        except wt.WasmtimeError:
            pass
        before = maps_summary() if i == 0 else None
        f0, t0 = minflt(), time.perf_counter()
        inst = pre.instantiate(store)
        dt, df = time.perf_counter() - t0, minflt() - f0
        if i == 0:
            after = maps_summary()
            maps_live = {
                k: v - (before or {}).get(k, 0)
                for k, v in after.items()
                if v != (before or {}).get(k, 0)
            }
            mem = inst.exports(store)["memory"]
            maps_live["memory_bytes"] = mem.data_len(store)
        store.close()
        if i >= 2:
            ts.append(dt)
            fs.append(df)
    return {
        "label": label,
        "ms": med(ts) * 1e3,
        "minflt": med(fs),
        "maps_added_by_instance": maps_live,
    }


def main(out: Path, n: int) -> None:
    result: dict = {"pid": os.getpid()}
    result["making"], made, data = making()
    mods_inproc = {False: h.compile_module(data, False)[0], True: h.compile_module(data, True)[0]}
    # The cache's checks refuse a folder under a way others can write, such as /tmp.
    with tempfile.TemporaryDirectory(dir=h.PROTO) as tmp:
        os.chmod(tmp, 0o700)
        folder = Path(tmp) / "cache"
        folder.mkdir(mode=0o700)
        mods_cwasm = {m: cwasm_module(data, m, folder, "image") for m in (False, True)}
        result["calls_inproc"] = calls(mods_inproc, n, False)
        # The plain side loads perl.wasm's .cwasm from the same kind of cache, as run() does.
        plain_cache = Path(tmp) / "plain-cache"
        plain_cache.mkdir(mode=0o700)
        h.run_plain(["-e", "1"], cache_dir=plain_cache)  # writes perl.wasm's .cwasm
        _engines.forget()  # so the next call loads it, through the sealed memfd
        result["calls_cwasm"] = calls(mods_cwasm, n, plain_cache)
        result["fuel"] = fuel(mods_inproc)
        perl = (h.ART / "perl.wasm").read_bytes()
        eng = _engines.engine(False)
        cfg = wt.Config()
        cfg.wasm_exceptions = True
        cfg.epoch_interruption = True
        cfg.memory_init_cow = False
        eng_nocow = wt.Engine(cfg)
        perl_cwasm = cwasm_module(perl, False, folder, "perl")
        image_cwasm = mods_cwasm[False]
        result["instantiate"] = [
            instantiation("perl.wasm, compiled in process", eng, wt.Module(eng, perl)),
            instantiation("perl.wasm, from .cwasm (sealed memfd)", eng, perl_cwasm),
            instantiation("image, compiled in process", eng, mods_inproc[False]),
            instantiation("image, from .cwasm (sealed memfd)", eng, image_cwasm),
            instantiation("perl.wasm, copy-on-write off", eng_nocow, wt.Module(eng_nocow, perl)),
            instantiation("image, copy-on-write off", eng_nocow, wt.Module(eng_nocow, data)),
        ]
    out.write_text(json.dumps(result, indent=1) + "\n")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k != "making"}
            | {"making": result["making"]["median"]},
            indent=1,
        )
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 60)
