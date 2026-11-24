# The runner's fixed cost, measured

- **Status:** research, 2026-11-24. The before and after timings that specification 014's acceptance asks for. It stands beside study A (`2026-11-24-perf-runner.md`) and reuses its scripts.
- **Researcher:** the implementer of 014 (Claude Opus 5.5), on `spec/014-the-runners-fixed-cost`.
- **Evidence:** `2026-11-24-the-runners-fixed-cost-measured/`: `measure.sh` (the whole run), `peak_rss.py` (peak memory), and `results/` (raw JSON lines).

Labels: EXECUTED means run and seen. READ means read from a file. INFERRED means the implementer's reasoning.

## 1. The answer

**The code as it lands is section 7's**, where L7 maps a sealed copy in memory instead of the cache's file, after the review's round 2. Its session s3 gives:
- a bare call of 1.22 ms against 3.69 ms;
- a first call of 113.9 ms against 134.6 ms;
- a peak of 38.7 MiB against 75.0 MiB.

The table below is sessions s1 and s2, with L7 mapping the cache's own file.

With levers L1, L2, L3, L4, L7 and L8 built, on this machine, in two sessions:

| | before | after | change |
|---|---|---|---|
| a bare warm call | 3.85–4.50 ms | **1.40–1.78 ms** | −2.1 to −3.1 ms |
| a call with a read-only grant | 3.79–4.54 ms | **1.64–1.67 ms** | −2.1 to −2.9 ms |
| a call with a writable grant | 21.7–23.5 ms | 19.6–19.9 ms | −1.7 to −3.8 ms |
| a fresh process's first call, warm cache (median of five) | 134.5, 144.9 ms | **106.4, 106.2 ms** | −28 to −39 ms |
| of it, the module load | 21.0–30.2 ms | **1.0–1.2 ms** | |
| of it, the library check | 77.5–88.5 ms | 77.4–85.4 ms | unchanged, as decided |
| a fresh process's peak RSS after two calls | 74.8–74.9 MiB | **43.9–44.0 MiB** | −31 MiB |

All EXECUTED (`measure.sh`, section 2).

- A bare call meets the intent's 1.3–1.5 ms in the first session and is a little over it in the second (1.78 ms). The sessions differ by about ±15% on this shared machine, as study A found; the before figure moved the same way (4.50 → 3.85 ms).
- The first call is about 106 ms, as the amended specification says: the 80–88 ms library check stays until specification 015 (L6).
- A writable call stays near 20 ms: its cost is the runtime-file scan, which is L5, also specification 015.

## 2. How it was measured

- **Machine:** one x86-64 Linux VM, 4 vCPUs shared with other agents. CPython 3.13, wasmtime-py 51.0.0. Every timed script ran under `flock /home/user/wasi-perl-wt/machine.lock`, one script per hold (EXECUTED).
- **The artifact set:** perf-base's build (`/home/user/wasi-perl-wt/perf-base/build/artifacts`, perl 5.42.3), assembled into the package as the wheel carries it (EXECUTED: `scripts/assemble_artifacts.py --source …/perf-base/build/artifacts --target …/perf-base/build/target --downloads …/perf-base/downloads`: "assembled 1455 files, 32193546 bytes"). `WASI_PERL_ARTIFACTS` was unset, so each tree found its own `src/wasi_perl/artifacts/`. This is study A's layout.
- **Before** is this branch's base `1c062ea` (its `src/` is the runner as it was), from `git archive 1c062ea src`, with the same set copied into it, put first on `PYTHONPATH`. **After** is the worktree's `src/` at the head of the implementation, through its `.venv`.
- **Both trees share one warm module cache.** The `.cwasm` key is the same for both (the same `perl.wasm` digest and wasmtime-py version), so "first call" means a fresh process with a warm disk cache, as in study A.

The commands, from the worktree:

```
S=<scratch>; mkdir -p $S/base && git archive 1c062ea src | tar -x -C $S/base
cp -a src/wasi_perl/artifacts $S/base/src/wasi_perl/
context/research/2026-11-24-the-runners-fixed-cost-measured/measure.sh $S/base/src s1
context/research/2026-11-24-the-runners-fixed-cost-measured/measure.sh $S/base/src s2
P=context/research/2026-11-24-perf-runner/phases.py
R=context/research/2026-11-24-the-runners-fixed-cost-measured/results
for sc in bare ro; do
  flock /home/user/wasi-perl-wt/machine.lock env PYTHONPATH=$S/base/src .venv/bin/python $P $sc 100 $R/phases-before-$sc.json
  flock /home/user/wasi-perl-wt/machine.lock .venv/bin/python $P $sc 100 $R/phases-after-$sc.json
done
```

`measure.sh` runs, for each tree, under the lock:

| script | from | what it gives |
|---|---|---|
| `calls.py <label> 60 results/calls.jsonl` | study A | the median of 60 warm calls, after 3 warm-ups, for six scenarios |
| `first_call.py <cache>`, five fresh processes | study A | import, the first call, its artifact check and module load, the second call |
| `peak_rss.py <cache>`, three fresh processes | here | peak RSS (`getrusage`) after two calls |

## 3. Warm calls

`results/calls.jsonl`, ms per call, medians of 60 (EXECUTED):

| label | bare | read-only | writable | stdin + env | fuel cap | `-ne print` on a granted file |
|---|---|---|---|---|---|---|
| before, s1 | 4.50 | 4.54 | 23.47 | 4.50 | 4.53 | 4.83 |
| after, s1 | **1.40** | 1.64 | 19.63 | 1.71 | 1.70 | 2.22 |
| before, s2 | 3.85 | 3.79 | 21.68 | 4.11 | 3.85 | 4.22 |
| after, s2 | 1.78 | 1.67 | 19.94 | 1.62 | 1.52 | 2.09 |

These match study A's L1–L4 row (1.57 ms bare, 19.01 ms writable) within the session spread (READ: `2026-11-24-perf-runner/results/calls.jsonl`).

What each call no longer does is counted, not timed, by `tests/runner/test_fixed_cost.py` (EXECUTED, all pass; the "was" counts are from the same checks run before the change, commit `1e4a5e1`):

- no call into `importlib.metadata` after the first call (was 12 counted calls: four `version("wasmtime")`, each with the `distribution` and `Distribution.from_name` it makes);
- one parse of an unchanged `SHA256SUMS` in ten calls (was ten);
- with no grant, none of `_runtime_folders`, `_runtime_files` and `_cache.every`; with read-only grants, only `_cache.every`; with a writable grant, all three;
- one `Linker` in ten calls on one module (was ten).

### 3.1 By phase

Study A's `phases.py bare 100` and `phases.py ro 100`, one run each per tree under the lock, median µs per call of each wrapped function (EXECUTED, `results/phases-{before,after}-{bare,ro}.json`). The wrappers add about 0.1 ms per call (study A, READ), so the totals are above `calls.py`'s.

| phase | lever | bare before | bare after | read-only before | read-only after |
|---|---|---|---|---|---|
| `_engines.wasmtime` | L1 | 1,033 | 5.9 | 836 | 4.9 |
| `_engines.engine` and `_engines.module`, which call it (nested in the row above) | L1 | 346 + 513 | 3.0 + 6.6 | 330 + 411 | 2.4 + 5.2 |
| `_artifacts.checked` | L2 | 618 | 110 | 564 | 86 |
| `_grants.judge` + `_runtime_folders` + `_cache.every` | L3 | 477 + 352 + 90 | 3.0 + 0 + 0 | 545 + 253 + 66 | 86 + 0 + 61 |
| `_set_up` (it made the Linker and ran `define_wasi`, 75 µs of its 136) | L4 | 136 | 54 | 104 | 44 |
| `Linker.instantiate` (now `InstancePre.instantiate`, not wrapped) | L4 | 71 | — | 53 | — |
| `Func.__call__` (the guest) | — | 993 | 959 | 819 | 776 |
| `Store.close` | — | 243 | 245 | 208 | 202 |
| **total `run()`** | | **4,327** | **1,675** | **3,684** | **1,509** |

- The version reads were about 1.0 ms of a bare call: four calls of `wasmtime()`, from `run`, `engine` (twice) and `module` (READ: `_engines.py` and `runner.py` at `1c062ea`). The `engine` and `module` rows are mostly those nested calls, so they are not added to it.
- With read-only grants the cache rule still runs, as decided: `_cache.every` 61 µs and `judge` 86 µs remain.
- What is left is wasmtime's: the guest's `_start` and `Store.close`, about 1.2 ms of 1.5–1.7 ms.

## 4. A fresh process's first call

`results/first_call.jsonl`, five fresh processes each, warm cache, ms (EXECUTED):

| label | first call, range (median) | artifact check | module load | second call | import of `wasi_perl` |
|---|---|---|---|---|---|
| before, s1 | 127.7–156.8 (134.5) | 77.5–88.5 | 21.0–30.2 | 3.7–5.1 | 21.0–26.1 |
| after, s1 | 105.5–106.5 (**106.4**) | 83.2–84.1 | **1.0–1.1** | 1.6 | 25.0–25.5 |
| before, s2 | 134.3–150.0 (144.9) | 79.4–86.3 | 22.8–28.6 | 4.0–6.3 | 22.9–25.8 |
| after, s2 | 99.2–109.0 (**106.2**) | 77.4–85.4 | **1.0–1.2** | 1.4–1.8 | 23.3–26.8 |

- **The module load** fell from 21–30 ms to 1.0–1.2 ms: wasmtime maps the checked `.cwasm` by `/proc/self/fd/N` instead of being handed a 15 MB copy (L7). Study A's prototype gave 0.8–1.2 ms (READ: its section 4).
- **The rest of the first call**, about 20 ms beside the check and the load, includes wasmtime-py's import (study A: about 21 ms, `python -X importtime`, READ). L8's saving of about 8 ms in study A is the `importlib.metadata` import it spares; it is inside this remainder and not separated here (INFERRED: `first_call.py` times the check and the load only).
- **The artifact check is unchanged**, 77–88 ms, as the amended specification decides. With L6 (specification 015) study A measured 10–15 ms, which would bring the first call to about 35 ms (READ: its section 4).
- The import of `wasi_perl` itself, 21–27 ms, is outside the first call and outside this specification.

## 5. Peak memory

`results/peak_rss.jsonl`, three fresh processes each, after two calls (EXECUTED):

| label | peak RSS |
|---|---|
| before, s1 and s2 | 74.8–74.9 MiB |
| after, s1 and s2 | **43.9–44.0 MiB** |

The 31 MiB is the `.cwasm` read into `bytes` and the copy wasmtime-py makes of it for `deserialize` (INFERRED from study A's section 3.3, which READ `wasmtime/_module.py`: `from_buffer_copy`). A mapped file's pages are the page cache's, shared and not counted twice.

## 6. Limits

- One machine, two sessions, shared. Every comparison is within one session.
- macOS and Windows read the bytes through the descriptor, as before (section 7), and were not timed. CI's macOS wheel job runs `test_on_macos_a_cwasm_is_read_into_memory`.
- Wall time is here only. The tests count operations, as `AGENTS.md` requires.

## 7. After the review: a sealed copy in memory (session s3)

The review's rounds 1 and 2 (`../reviews/014-the-runners-fixed-cost-pr-2026-11-24.md`) showed why mapping the cache's own file is unsafe. wasmtime reads a mapped file for the module's whole life. Anything that later truncates or rewrites that inode stops or changes the host's code:
- a later grant;
- a host rename;
- a hard link;
- two calls started at once.

So on Linux the runner now copies the checked bytes, through the checked descriptor, into a `memfd`. It seals the copy against writes, shrinking, growing and new seals, and gives wasmtime that copy's `/proc/self/fd/N` name. No folder names the copy, and the seals hold against any descriptor (READ: `memfd_create(2)`, `fcntl(2)` on file seals). macOS has no `memfd`, so there, as on Windows, the bytes are read into memory.

**A prototype first** (EXECUTED: `memfd_proto.py <bytes|map|memfd-sf> <cwasm>` from the evidence folder, three runs each, under the lock, on the 14.9 MB plain `.cwasm`). It timed the load of a module, and the process's peak RSS after one call:

| way | load | peak RSS |
|---|---|---|
| read into `bytes`, `deserialize` | 22.8–24.9 ms | 70 MiB |
| map the cache's file | 0.5 ms | 41 MiB |
| copy into a sealed `memfd` by `sendfile`, map that | **5.4–7.0 ms** | **35 MiB** |

`copy_file_range` refuses a copy from a disk file to a `memfd` with `EXDEV`, so `sendfile` is used (EXECUTED).

**Session s3** ran `measure.sh $S/base/src s3`, as sections 2–5, against the same base `1c062ea` (EXECUTED; the raw lines carry the label `s3`). Medians:

| | before, s3 | after, s3 |
|---|---|---|
| a bare warm call | 3.69 ms | **1.22 ms** |
| a call with a read-only grant | 3.66 ms | **1.44 ms** |
| a call with a writable grant | 21.2 ms | 20.2 ms |
| a fresh process's first call (median of five) | 134.6 ms | **113.9 ms** |
| of it, the module load | 22.6 ms | **7.2 ms** |
| of it, the artifact check | 81.0 ms | 83.9 ms |
| peak RSS after two calls (median of three) | 75.0 MiB | **38.7 MiB** |

- The sealed copy costs about 6 ms of a first call against mapping the file (7.2 against 1.0–1.2 ms). It keeps about three quarters of L7's saving.
- Peak RSS is lower than with the mapped file. `ru_maxrss` counts the copy's pages only as they are touched through the mapping, as it does for a mapped file (INFERRED). The copy is about 15 MB of memory per process for the module's life, charged to the process. A mapped file's pages were the page cache's, which processes share. So several host processes each hold their own copy (INFERRED, from `memfd_create(2)`: a memfd is anonymous memory). The round-3 review noted this.
