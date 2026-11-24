# The library index, measured

- **Status:** research, 2026-11-24. The before and after numbers that specification 019's acceptance asks for: the host's lookups under the library, the fuel and the wall time of the module-loading programs.
- **Researcher:** the implementer of 019 (Claude Opus 5.5), on `spec/019-the-library-index`.
- **Evidence:** `2026-11-24-the-library-index-measured/`: `count_calls.sh` (the host's `path_filestat_get` and `path_open` on the library's grant, from the wasmtime command line's trace), `measure.py` (fuel and wall time of two sets), and `results/` (`calls.txt`, `measure.txt`, the raw output of both).

Labels: EXECUTED means run and seen. READ means read from a file. INFERRED means the implementer's reasoning. "Fuel" is wasmtime's count of executed WebAssembly instructions: a count, the same on any machine.

## 1. The answer

| | before (dev) | after | change |
|---|---:|---:|---:|
| five modules: failed `path_filestat_get` under the library | 118 | **0** | −118 |
| five modules: successful `path_filestat_get` / `path_open` | 36 / 18 | 36 / 18 | none |
| `-MPOSIX -e 1`: failed `path_filestat_get` under the library | 73 | **0** | −73 |
| `-MPOSIX -e 1`: successful `path_filestat_get` / `path_open` | 22 / 11 | 22 / 11 | none |
| five modules, ms per call (median of three rounds) | 25.99 (25.16–26.53) | **22.22** (21.52–23.33) | −3.8 ms, −15% |
| `-MPOSIX -e 1`, ms per call | 16.17 (14.81–16.33) | **13.85** (13.32–15.16) | −2.3 ms, −14% |
| `-e 1`, ms per call | 1.14 (0.97–1.27) | 1.05 (1.03–1.28) | within noise |
| five modules, fuel | 121,707,825 | 123,587,492 | +1,879,667, +1.5% |
| `-MPOSIX -e 1`, fuel | 71,062,047 | 72,168,972 | +1,106,925, +1.6% |
| `-e 1`, fuel | 1,600,585 | 1,600,523 | −62 |

The five modules are `use strict; use warnings; use POSIX; use JSON::PP; use List::Util; 1`. Every count is EXECUTED (`results/calls.txt`, `results/measure.txt`).

- **The failed lookups are gone, and the found ones are untouched.** Every one of the 118 and the 73 was a name the library does not hold: a `site_perl` folder, or a `.pmc` (the specification's Intent, READ). The guest now answers each itself.
- **Wall time falls by about 2.3 ms and 3.8 ms.** Study D put a failed host stat at 28.7 µs and so predicted about 3.4 ms for the five modules (READ: `2026-11-24-perf-startup.md` §1.3). The measured 3.8 ms is 32 µs per lookup removed, close to that figure. INFERRED: the rest of the call is unchanged, so the difference is the host calls.
- **Fuel rises by about 1.5%.** The guest now does in WebAssembly what the host did outside it: each lookup under the library is a binary search over the index's 1,728 names, once per folder on the path and once more for the name. About 16,000 instructions per lookup of a missing name (1,879,667 over 118, INFERRED: the found lookups are searched too, so this overstates it). A host call is not counted as fuel, so fuel cannot show the saving; wall time does.
- **`-e 1` loads no module and makes no lookup under the library**, before or after. Its fuel moves by 62 instructions (INFERRED: the table's start now also compares each grant's name with the library's).

## 2. How it was measured

- **Before** is dev's set at `8897c22`, built in this worktree before any change and copied aside: `perl.wasm` SHA-256 `5124d6b4c5381b13ae2c1dfd322398f589d9c5501174137b782a11dd729b2b4f`, 9,387,903 bytes. **After** is this branch's `build/artifacts/`: `55cdde9cebf6f04fa34ff41622ad1a64e45cabc0bdfd1209dbdab3a3eb4459bc`, 9,441,787 bytes, 53,884 more, which is the index (EXECUTED: `sha256sum`, `stat -c %s`). The library is the same 1,462 files in both (`MANIFEST`, byte for byte).
- **Lookups** are counted from the wasmtime 51 command line's trace of every WASI call (`WASMTIME_LOG=wasmtime_wasi=trace`), with the library granted first, so it is descriptor 3, and `/dev` second. The counting check `tests/build/test_library_lookups.py` counts the same way.
- **Fuel** is one run per program under wasmtime-py 51 with the runner's two read-only grants and `PERL_HASH_SEED=0`.
- **Wall time** is the median of 31 `run()` calls after one warm-up, in one process, with a warm module cache of each set's own; three rounds, before and after alternating, under `flock /home/user/wasi-perl-wt/machine.lock`. Each cell is the median of the three rounds' medians, with the lowest and highest round in brackets.
- Linux x86-64, 4 vCPUs, shared with other agents' work outside the lock; CPython 3.13, wasmtime-py 51.0.0.

```
sh context/research/2026-11-24-the-library-index-measured/count_calls.sh <set> -MPOSIX -e 1
sh context/research/2026-11-24-the-library-index-measured/count_calls.sh <set> -e '<the five modules>'
flock /home/user/wasi-perl-wt/machine.lock uv run python \
    context/research/2026-11-24-the-library-index-measured/measure.py <before set> build/artifacts 3
```

## 3. The build's cost

The relink is one more link of `perl.wasm`. `make perl` in the built tree, with `perl` removed, took 48.5 s of wall time and 5.4 s of user time, and wrote the same bytes as the first link (EXECUTED: `time make perl` on dev's tree, `sha256sum` before and after). Most of that time is make_ext.pl finding each extension up to date, one after another (READ: the log's 400 lines, one link command among them). The first full build and two later reruns of `30_library.sh` and `40_link.sh` (after a comment added to `30_library.sh`, and after its revert) gave a `perl.wasm` with the same SHA-256, `55cdde9c…59bc` (EXECUTED: `sha256sum` after the first and the last).
