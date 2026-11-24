# The guest's reads and writes, measured

- **Status:** research, 2026-11-24. The before and after numbers that specification 016's acceptance asks for. It stands beside study C (`2026-11-24-perf-interp.md`) and reuses its workloads.
- **Researcher:** the implementer of 016 (Claude Opus 5.5), on `spec/016-the-guests-reads-and-writes`.
- **Evidence:** `2026-11-24-the-guests-reads-and-writes-measured/`: `measure.sh` (the whole run), `measure.py` (fuel, wall time and WASI calls of one artifact set), `summarize.py` (the tables), `check_fuel.py` and `check_counts.py` (what the checks themselves count, on any module), and `results/` (raw JSON, `summary.md`, `time.out`, `checks.out`).

Labels: EXECUTED means run and seen. READ means read from a file. INFERRED means the implementer's reasoning. "Fuel" is wasmtime's count of executed WebAssembly instructions: a count, the same on any machine.

## 1. The answer

The three changes of 016 together, against dev's build, on this machine (EXECUTED: `measure.sh`, `results/summary.md`):

| | before (dev) | after | change |
|---|---:|---:|---:|
| `grep_log`, ms per call | 113.6 (103.8–116.3) | **61.3** (61.2–64.8) | −46% |
| `inplace` (`-pi`), ms per call | 202.3 (194.9–220.0) | **122.4** (119.2–126.2) | −40% |
| `many_files` (200 small files), ms per call | 57.7 (56.8–63.4) | **45.4** (44.9–49.8) | −21% |
| `count_lines` over the 10 MB log, ms per call | 72.8 (71.3–80.3) | **32.2** (31.3–35.4) | −56% |
| `grep_log`'s fuel | 643,902,442 | **574,055,286** | −10.8% |
| `grep_log`'s WASI calls | 1,300 | **207** | 1,248 → 157 `fd_read` |

Study C predicted `grep_log` 111 → 66 ms, `-pi` 203 → 112 ms and `many_files` 67 → 50 ms (READ: `2026-11-24-perf-interp/results/time3-summary.md`). The measured `grep_log` and `many_files` land at or below the prediction. `-pi` lands at 122 ms, 10 ms above it; this run's before is the same as study C's, so the difference is in the after (INFERRED: one shared machine, other agents' work outside the lock).

## 2. How it was measured

- **Before** is dev's `perl.wasm` at `236dddc`, built in this worktree before any change and copied to `build/before/artifacts/` (SHA-256 `1a566f80ffd503361d0f52ff3dda708a2225abed43976ace3ecf7936e497b343`, EXECUTED: `sha256sum`). **After** is this branch's `build/artifacts/` (SHA-256 `06ea464ba43c62fc2a3967326c9cea247c6f95bfe1e73c1bf21d64dc1c21a53d`). **0.1.0** is the module built from 0.1.0's recipes in the `perf-base` worktree (`38c80f9c…ab30`); `git diff aac5d4d a5fd83a -- recipes scripts` is empty, so its recipes are the release's (EXECUTED).
- **The workloads** are study C's, through the `wasi_perl` runner with the baseline's grants: read-only `/data`, and a writable `/w` for `inplace` only. The data is the baseline's `bench.py data` (a 10.2 MB log of 200,000 lines, a 4.6 MB CSV, a 1.4 MB JSON array), and `many/`, 200 files of 50 lines of the log.
- **Fuel** is one call per workload with `fuel=10**13` and `PERL_HASH_SEED=0`. Two calls of one module gave the same count to the instruction (EXECUTED: the test helper below, twice per module).
- **Wall time** is the median of 10 calls (30 for `hello`, 7 for `loop`) after two warm-ups, in one process with a warm module cache, under `flock /home/user/wasi-perl-wt/machine.lock`. Three rounds, before and after interleaved. Each cell is the median of the three rounds' medians, with the lowest and highest round in brackets. Every call's output is checked against the expected answer.
- **The run** is `measure.sh`'s steps, run one by one with the same commands: fuel and calls outside the lock (they are counts), the three timed rounds under it.
- **WASI calls** are counted from the wasmtime 51 command line's trace (`WASMTIME_LOG=wasmtime_wasi=trace`), with the runner's preopens.
- Linux x86-64, 4 vCPUs, shared with other agents' work; CPython 3.13, wasmtime-py 51.0.0.

```
sh context/research/2026-11-24-the-guests-reads-and-writes-measured/measure.sh <scratch data folder>
uv run python context/research/2026-11-24-the-guests-reads-and-writes-measured/check_fuel.py <perl.wasm>...
uv run python context/research/2026-11-24-the-guests-reads-and-writes-measured/check_counts.py <perl.wasm>...
python3 context/research/2026-11-24-the-guests-reads-and-writes-measured/summarize.py
```

## 3. Fuel

All EXECUTED (`results/fuel-*.json`):

| workload | 0.1.0 | before (dev) | after | after against 0.1.0 |
|---|---:|---:|---:|---:|
| hello | 1,597,187 | 1,598,119 | 1,598,088 | +0.1% |
| sum_csv | 1,269,362,241 | 1,269,392,050 | 1,238,065,130 | −2.5% |
| grep_log | 643,837,545 | 643,902,442 | 574,055,286 | −10.8% |
| inplace | 843,383,769 | 843,509,876 | 733,613,678 | −13.0% |
| json | 11,135,655,987 | 11,135,679,828 | 11,134,655,584 | −0.0% |
| loop | 603,664,131 | 603,665,063 | 603,664,725 | +0.0% |
| count_lines | 304,903,500 | 304,968,397 | 244,318,643 | −19.9% |
| cat_log | 426,921,916 | 427,047,918 | 365,962,061 | −14.3% |
| many_files | 31,943,244 | 32,027,976 | 26,838,179 | −16.0% |

- The gain is the SIMD `memchr` on line work: 10 to 20% of the instructions, as study C measured (its `fuel-buf65536-memchr-isatty.json`). The buffer and `isatty` change fuel by under 0.4% (study C §4.1, §4.3).
- dev's module spends about 65,000 instructions more than 0.1.0's on every workload, `hello` included. That is spec 013's start, which reads `random_get` for its seeds (INFERRED: a constant per call, the size of the difference on `hello`).
- **The check's own count.** `tests/build/test_reads_and_writes.py` measures `grep_log` with its own wasmtime-py store, not the runner, so its count is a little lower: 643,780,719 for the 0.1.0 module, 643,845,616 for dev's, 574,055,110 after, the same on two calls each (EXECUTED: `uv run python check_fuel.py <perl.wasm>...`, which imports the check's module and runs its `_fuel` on `GREP_LOG` over `_write_log`'s file; `results/checks.out`). The check's constant `FUEL_0_1_0` is that first number. After is 89.2% of it, against the check's bound of 92%.

## 4. WASI calls

All EXECUTED (`results/calls-*.json`):

| workload | WASI calls before | after | `fd_read` | `fd_write` | `fd_fdstat_get` |
|---|---:|---:|---:|---:|---:|
| hello | 45 | 44 | 0 → 0 | 1 → 1 | 15 → 14 |
| grep_log | 1,300 | 207 | 1,248 → 157 | 1 → 1 | 17 → 15 |
| inplace | 2,551 | 370 | 1,248 → 157 | 1,244 → 156 | 19 → 17 |
| count_lines | 1,300 | 207 | 1,248 → 157 | 1 → 1 | 17 → 15 |
| many_files | 1,845 | 1,644 | 400 → 400 | 1 → 1 | 415 → 214 |
| sum_csv | 612 | 121 | 560 → 71 | 1 → 1 | 17 → 15 |

- The 64 KiB buffer cuts `fd_read` and `fd_write` by 8× on every file larger than it. A file under it still costs two reads, the data and the end of file, so `many_files` is unchanged there.
- `isatty` costs no host call on a regular file the table looked at: `many_files` makes 201 fewer `fd_fdstat_get`, one per file opened and one for `/dev/null`.
- **Perl opens `/dev/null` at every start**, before the program runs (EXECUTED: the trace shows a 4-byte path opened under the `/dev` grant; under the wasmtime command line without a `/dev` grant, `perl.wasm -e 'print 1'` stops with "Can't open /dev/null"). So every program saves one `fd_fdstat_get`, `hello` included. Why Perl opens it was not chased.

## 5. Wall time

All EXECUTED (`results/time-r*.json`, `results/time.out`), ms per call:

| workload | before | after | change |
|---|---:|---:|---:|
| hello | 1.5 (1.5–2.0) | 1.7 (1.5–1.9) | within the noise |
| sum_csv | 135.3 (134.2–146.2) | 109.8 (107.2–110.8) | −19% |
| grep_log | 113.6 (103.8–116.3) | 61.3 (61.2–64.8) | −46% |
| inplace | 202.3 (194.9–220.0) | 122.4 (119.2–126.2) | −40% |
| count_lines | 72.8 (71.3–80.3) | 32.2 (31.3–35.4) | −56% |
| many_files | 57.7 (56.8–63.4) | 45.4 (44.9–49.8) | −21% |
| loop | 72.7 (68.9–72.8) | 70.0 (69.0–72.2) | within the noise |

- Every round of the after is below every round of the before on the five file workloads.
- `hello` and `loop` read no file. Their ranges overlap, so the change is noise (INFERRED).
- Native perl's times for the first three, from the baseline, are 58, 26 and 41 ms (READ: study C §1). So the guest goes from about 2.3×, 4.4× and 4.9× native to 1.9×, 2.4× and 3.0× (INFERRED: the ratio of measurements made on the same machine under different load).

## 6. What this does not measure

- **A host other than wasmtime.** The module now needs SIMD128. wasmtime 51 refuses to compile it with SIMD off: `wasmtime run -W exceptions=y,simd=n,relaxed-simd=n perl.wasm` stops with "failed to compile: wasm[0]::function[3518]::memchr", and dev's module runs (EXECUTED). No other engine was run.
- **vfs mounts.** The provider-backed mount is specification 017's. Study E measured the 64 KiB read size there (`2026-11-24-perf-vfs-io.md` §4.3).
