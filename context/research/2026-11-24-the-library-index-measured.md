# The library index, measured

- **Status:** research, 2026-11-24. The before and after numbers that specification 019's acceptance asks for: the host's lookups under the library, the fuel and the wall time of the module-loading programs. Remeasured after round 1 of the review, whose index searches with A to Z as a to z; every number below is round 1's, except the final fuel figures of §1.1 and the figures after `wasm-opt -O3` of §1.2.
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
| five modules, ms per call (median of three rounds) | 27.94 (27.77–30.01) | **23.83** (23.83–26.34) | −4.1 ms, −15% |
| `-MPOSIX -e 1`, ms per call | 17.01 (14.90–18.30) | **14.33** (14.16–15.25) | −2.7 ms, −16% |
| `-e 1`, ms per call | 1.30 (1.19–1.35) | 1.26 (1.13–1.30) | within noise |
| five modules, fuel | 121,707,825 | 123,193,275 | +1,485,450, +1.2% |
| `-MPOSIX -e 1`, fuel | 71,062,047 | 71,991,025 | +928,978, +1.3% |
| `-e 1`, fuel | 1,600,585 | 1,600,492 | −93 |

The five modules are `use strict; use warnings; use POSIX; use JSON::PP; use List::Util; 1`. Every count is EXECUTED (`results/calls.txt`, `results/measure.txt`).

- **The failed lookups are gone, and the found ones are untouched.** Every one of the 118 and the 73 was a name the library does not hold: a `site_perl` folder, or a `.pmc` (the specification's Intent, READ). The guest now answers each itself.
- **Wall time falls by about 2.7 ms and 4.1 ms.** Study D put a failed host stat at 28.7 µs and so predicted about 3.4 ms for the five modules (READ: `2026-11-24-perf-startup.md` §1.3). The measured 4.1 ms is 35 µs per lookup removed, close to that figure. This round's rounds were noisier than round 0's (25.99 → 22.22 ms then), from other agents' work outside the lock. INFERRED: the rest of the call is unchanged, so the difference is the host calls.
- **Fuel rises by about 1.2%.** The guest now does in WebAssembly what the host did outside it: each lookup under the library is a binary search over the index's 1,728 names for the first component, then over the narrowed range of each folder's names, comparing only the component's bytes. About 12,600 instructions per lookup of a missing name (1,485,450 over 118, INFERRED: the found lookups are searched too, so this overstates it). Round 0's search, exact and over the whole array each time, cost 1,879,667 (+1.5%); the case-folding compare costs only on a byte that differs. A host call is not counted as fuel, so fuel cannot show the saving; wall time does.
- **`-e 1` loads no module and makes no lookup under the library**, before or after. Its fuel moves by 93 instructions (INFERRED: the table's start now also compares each grant's name with the library's).

### 1.1 The final fuel figures

019 as merged (`50e0298`), after the review's last round, which also sends Windows device names to the host (READ: specification 019's landing notes, "Windows device names", where they were EXECUTED on that head):

| | before (dev) | 019 as merged | change |
|---|---:|---:|---:|
| five modules, fuel | 121,707,825 | 123,227,113 | +1,519,288, +1.25% |
| `-MPOSIX -e 1`, fuel | 71,062,047 | 72,010,878 | +948,831, +1.33% |
| `-e 1`, fuel | 1,600,585 | 1,600,492 | −93 |

### 1.2 After `wasm-opt -O3`

Specification 021 runs binaryen's `wasm-opt -O3` over the linked module. On that branch's build, with `measure.py` (EXECUTED: `2026-11-24-wasm-opt-measured.md` §7, `2026-11-24-wasm-opt-measured/results/library-index-measure.txt`):

| | before (dev) | linked, before the pass | shipped, after the pass | against dev |
|---|---:|---:|---:|---:|
| five modules, fuel | 121,707,825 | 123,212,541 | **120,184,111** | −1,523,714, −1.25% |
| `-MPOSIX -e 1`, fuel | 71,062,047 | 72,001,857 | **70,195,305** | −866,742, −1.22% |
| `-e 1`, fuel | 1,600,585 | 1,600,492 | **1,557,752** | −42,833, −2.68% |

- The pass removes about 2.5% of the instructions, so the index's fuel is paid back: the five modules and `-MPOSIX` now run fewer instructions than before 019.
- The failed host stats under the library stay at 0 for both programs (EXECUTED: `tests/build/test_library_lookups.py` on that build).
- That branch's index also sends to the host a name holding a byte Windows cannot hold, and a device name with blanks after its stem. Its linked figures are within 0.02% of 019's final ones.

## 2. How it was measured

- **Before** is dev's set at `8897c22`, built in this worktree before any change and copied aside: `perl.wasm` SHA-256 `5124d6b4c5381b13ae2c1dfd322398f589d9c5501174137b782a11dd729b2b4f`, 9,387,903 bytes. **After** is this branch's `build/artifacts/` after round 1: `ade0a15ca702d592355d4c3e58a7856a09329f0d459142cee4dd0eae2f2f616a`, 9,442,629 bytes, 54,726 more, which is the index and its search (EXECUTED: `sha256sum`, `stat -c %s`). The library is the same 1,462 files in both (`MANIFEST`, byte for byte).
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

The relink is one more link of `perl.wasm`. `make perl` in the built tree, with `perl` removed, took 48.5 s of wall time and 5.4 s of user time, and wrote the same bytes as the first link (EXECUTED: `time make perl` on dev's tree, `sha256sum` before and after). Most of that time is make_ext.pl finding each extension up to date, one after another (READ: the log's 400 lines, one link command among them). In round 0, the first full build and two later reruns of `30_library.sh` and `40_link.sh` (after a comment added to `30_library.sh`, and after its revert) gave a `perl.wasm` with the same SHA-256, `55cdde9c…59bc` (EXECUTED: `sha256sum` after the first and the last).
