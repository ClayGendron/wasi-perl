# Study A: the runner's overhead, per call and per process

- **Status:** research, 2026-11-24. Study A of `2026-11-24-perf-research-plan.md`. It changes no code on `dev`: every prototype is a patch in the evidence folder.
- **Researcher:** an agent (Claude Opus 5.5).
- **The question:** what does each call and each process cost on the host side in `src/wasi_perl/`, and how near 1 ms can a bare call get, and a process's first call near 30 ms, without weakening any sandbox check?
- **Evidence:** `2026-11-24-perf-runner/`: the scripts, `results/` (raw output), and `patches/` (one patch per lever, L1 to L8, and `all-levers-cumulative.patch`, which applies to this branch's base, `32dfb73`).

Labels: EXECUTED means run and seen. READ means read from a file. INFERRED means the researcher's reasoning.

## 1. The answer

- **A bare warm call: 3.5–4.4 ms → 1.26–1.49 ms** (EXECUTED, `calls.py`, four sessions). It is now within about 0.3 ms of wasmtime-py's own floor for one perl.wasm call, 0.96–1.15 ms (`floor.py`).
- **A call with a writable grant: 21–24 ms → 2.8–3.3 ms** (EXECUTED, `calls.py`).
- **A fresh process's first call with a warm cache: 133–157 ms → 30–42 ms**, median 34 ms (EXECUTED, `first_call.py`, five fresh processes each, two sessions).
- **No check was removed.** Each lever either caches a result keyed by what would change it, or stops computing an input no rule reads (section 4).
- With the levers, `uv run pytest -m "not slow"` gives **444 passed, 2 skipped** with the native oracle set (EXECUTED, `results/pytest-all-levers.txt`). No test was changed.

Getting below 1 ms needs work in wasmtime, not in the runner (section 5). The largest piece is a cost found on the way: **every WASI filesystem call from the guest costs about 56 µs under wasmtime-py, against 0.85 µs under the wasmtime CLI** (EXECUTED, section 5.1). That matters most to study E.

## 2. How it was measured

- **The runner measured** is `src/wasi_perl/` with the patches applied in order (this branch's `src/` itself is left at its base), in the wheel's layout: the artifact set copied to `src/wasi_perl/artifacts/` (git-ignored), so the library lies under the package and the runtime-file scan sees it twice, as in the released wheel. The base is the same tree at `32dfb73`, put first on `PYTHONPATH` from a pristine `git archive`. The artifacts are perf-base's build (`/home/user/wasi-perl-wt/perf-base/build/artifacts`, perl 5.42.3, 1,453 library files).
- **Host:** CPython 3.13, wasmtime-py 51.0.0, 4 shared vCPUs. Every wall-clock number ran under `flock /home/user/wasi-perl-wt/machine.lock`. Six agents shared the machine, so the same run varies by about ±15% between sessions. Each table says which session.
- `results/phases-base-envlayout-*.json` are the same base profile with the set outside the package (`WASI_PERL_ARTIFACTS`), where the runtime scan sees the library once: 15.0 ms for a writable call instead of 24.5 ms.
- **Counts** (syscalls under `strace -f`, page faults by `getrusage`, guest fuel) are the primary evidence. They do not vary.

The scripts, each run with the worktree's `.venv/bin/python` (base: `PYTHONPATH=<pristine src>`):

| script | what it measures |
|---|---|
| `phases.py <scenario> <calls> <out>` | the median per call of each runner function and wasmtime-py method, each wrapped with a timer |
| `calls.py <label> <calls> <out>` | the median total per call, for six scenarios |
| `floor.py <artifacts> <calls> <out> [variants]` | a perl.wasm call with no runner code, step by step, under engine variants |
| `first_call.py <cache>` | a fresh process: import, first call (its artifact check and module load), second call |
| `load.py <bytes\|file\|procfd> <cwasm> <artifacts>` | one fresh process's load of a `.cwasm` |
| `counts.py <scenario> <calls>` | minor faults per call, and a marker to cut an `strace` |
| `wasi_call_cost.py`, `wasi_fs_call_cost.py <dir>` | the host cost of one WASI call, as the slope over many |
| `fuel_cost.py <calls>` | a 1 M-iteration loop with and without a fuel cap |

## 3. Where a call went before

### 3.1 A warm bare call, by phase

`phases.py bare 100` at the base (EXECUTED, `results/phases-base-bare.json`). Total **4.36 ms**:

| phase | µs | what it is |
|---|---|---|
| `_engines.wasmtime()` (4 calls) | 1,046 | `importlib.metadata.version("wasmtime")`, which searches every distribution on the path, about 250 µs each |
| `_start` and the exit-status read (`Func.__call__`) | 1,022 | the guest |
| `_artifacts.checked` | 645 | SHA256SUMS (1,455 lines) read and parsed on every call |
| `_grants.judge` | 429 | the identity chains of 3 caches and 12 runtime folders, with no grant to judge |
| `runner._runtime_folders` | 361 | `sysconfig`, `site`, `realpath` |
| `wt.Store.close` | 251 | wasmtime frees the store |
| `_set_up` (WasiConfig, preopens, Linker, `define_wasi`) | 130 | |
| `_cache.every` | 89 | reads the password database for this user's home |
| `instantiate` | 69 | |

So 2.6 ms of 4.4 ms was host Python, and most of it computed inputs that no rule read for this call.

Syscalls per call (EXECUTED, `strace -f` over 20 calls cut at a marker, `results/syscalls-base-bare.txt`): **436** (`results/syscalls-per-call.txt`). By kind, in the run with the set outside the package (`results/syscalls-base-bare.txt`): 148 `stat` and 87 `lstat` (identity chains and the metadata search) and 80 `futex`.

### 3.2 A writable grant

`phases.py rw 100` at the base (EXECUTED, `results/phases-base-rw.json`): **24.5 ms**, of it 14.2 ms in `_runtime_files` (`rglob` and `is_file` of 2,995 paths) and 5.1 ms in `judge` (an `lstat` of each). **12,325 syscalls per call** (EXECUTED, `results/syscalls-per-call.txt`).

### 3.3 A fresh process's first call

`first_call.py` at the base (EXECUTED, `results/first_call.jsonl`, label `base`): **138 ms**, of it **80 ms** in the artifact check and **22–29 ms** in the module load. Import of `wasi_perl` itself is a further 20–25 ms and is not counted in the first call.

- The 80 ms check is not mostly hashing. With string paths instead of a `Path` per file, the same full check takes 57–67 ms (EXECUTED, `results/tree-check.txt`). `Path.relative_to` for 1,453 files was most of the difference (READ, a `cProfile` of the first call).
- The module load reads 15 MB into `bytes`, and wasmtime-py's `deserialize` copies them once more (`from_buffer_copy`, READ, `wasmtime/_module.py`).

## 4. The levers

Each was built in `src/`, measured, and saved as a patch. In the order built:

| | lever | gain (EXECUTED) | how it keeps the check |
|---|---|---|---|
| L1 | wasmtime-py's version read once per process | **−1.0 ms per call**: `_engines.wasmtime` 1,046 → 5 µs | The wasmtime-py a process imported cannot change after the import. The tests that fake a version still run, because they patch `wasmtime_version` itself. |
| L2 | SHA256SUMS parsed once per stamp | **−0.5 ms per call**: `checked` 645 → 90–117 µs | It is read again when its inode, size, mtime or ctime moves, which is the rule `digest()` already keeps for perl.wasm. A parse is kept only when the stamp is the same after the read. |
| L3 | each rule's folders found only when a grant can break it | **−0.85 ms on a call with no grant**: `judge` + `_runtime_folders` + `every` 879 → 3 µs; −0.36 ms with read-only grants | The cache rule binds every grant, and the runtime rules bind only a writable one. With no grant, the inputs were computed and never read. |
| L4 | one `InstancePre` per module | **−50 to −60 µs per call** (`floor.py`: Linker and `define_wasi` 56 µs) | `define_wasi` reads each store's own WASI context, so one linking serves every store. |
| L5 | the runtime files' identities scanned once per tree stamp; the writable grants walked on every call | **−19 ms per writable call**: 21.9–23.6 → 2.8–3.3 ms; 12,325 → 657 syscalls | See 4.1. |
| L6 | the tree check kept in the module cache as each file's stamp; string paths in the walk | **−70 ms on a first call**: check 80–88 → 10–15 ms | See 4.2. |
| L7 | the checked `.cwasm` mapped by its descriptor's name | **−22 to −28 ms on a first call**: module 22–29 → 0.8–1.2 ms; peak RSS 70 → 37 MiB (`load.py`) | See 4.3. |
| L8 | wasmtime-py's version read from the one `wasmtime-*.dist-info` beside the module | **about −8 ms on a first call**: first call 37–41 → 30–42 ms, median 34 | It reads the version of the very package an import would load, without importing `importlib.metadata` (11.7 ms, `python -X importtime`). With not exactly one such folder, `importlib.metadata` decides, as today. |

Totals, by session (EXECUTED, `results/calls.jsonl`, ms per call, medians of 60):

| label | bare | read-only | writable | stdin + env | fuel cap | `-ne print` on a granted file |
|---|---|---|---|---|---|---|
| base | 4.24 | 4.41 | 23.57 | 4.48 | 4.38 | 4.97 |
| L1–L4 | 1.57 | 1.44 | 19.01 | 1.84 | 1.28 | 2.06 |
| base, second session | 3.77 | 3.90 | 21.94 | 3.52 | 4.36 | 3.88 |
| L1–L7, second session | **1.26** | 1.44 | **2.79** | 1.60 | 1.50 | 1.97 |
| base, third session | 3.48 | 3.62 | 21.35 | 3.66 | 4.17 | 4.62 |
| L1–L8, third session | 1.49 | 1.67 | 3.30 | 1.80 | 1.67 | 1.80 |

First call of a fresh process, warm cache (EXECUTED, `results/first_call.jsonl`, five processes each):

| label | first call, median | artifact check | module load | second call |
|---|---|---|---|---|
| base (two sessions) | 138, 153 ms | 80–88 ms | 22–29 ms | 3.8–5.0 ms |
| L1–L7 | 38 ms | 10 ms | 0.8–1.0 ms | 1.2–1.5 ms |
| L1–L8 | **34 ms** | 10–15 ms | 0.9–1.2 ms | 1.4–1.7 ms |

Syscalls per bare call: **436 → 138**, of which 84 are `futex` (EXECUTED, `results/syscalls-all-levers.txt`). Section 5.1 says where they come from.

### 4.1 L5: the hard-link rule, kept exact

The rule: no writable grant holds a second name of a runtime file. Before, every writable call listed and `lstat`ed about 3,000 runtime paths, then walked the writable grants if any runtime file had more than one name.

Now:

1. **The scan is kept per process** (`_grants.RuntimeFiles`): the identity `(st_dev, st_ino)` of every regular file under wasmtime-py, `wasi_perl`, the library, plus perl.wasm, MANIFEST, SHA256SUMS and `dev/null`, and the stamp `(dev, ino, mtime_ns, ctime_ns)` of every folder walked (272 in the wheel's layout). Each folder is stamped before it is listed.
2. **It is used only while every folder stamp holds.** A file is replaced, added or removed only through an entry of its folder, and that moves the folder's mtime and ctime. So while the stamps hold, the identities are still the files' own. An in-place write keeps the inode, so it changes nothing here.
3. **The writable grants are walked on every call,** and every file there with more than one name is looked up among the identities. A second name made after the scan changes no folder stamp in the runtime trees, but it is in the grant, so the walk finds it. The test that links wasmtime's library into a grant after other calls still passes.
4. **The old shortcuts keep their meaning.** A grant larger than the runtime has the runtime's files read afresh for a second name; when none has one, the walk stops, since there is nothing to find. Past `MAX_WALK`, it is refused only when a runtime file has a second name now, as before. An unreadable folder in a grant is refused under the same condition, as before.

A guest cannot make the second name itself. `link()` and `symlink()` from the read-only library into a writable grant both fail with "Operation not permitted" (EXECUTED: a `run()` with `link("/usr/local/lib/perl5/5.42/strict.pm", "/w/x")`).

wasi-python's 1.5 ms writable grant has no such rule (READ: `wasi_python/_places.py` `_runtime_folders` and a grep for `nlink`; it has folder rules only). L5 keeps wasi-perl's stronger rule at about 1.7 ms more than a read-only call.

### 4.2 L6: a first-call artifact check that keeps the tamper check

A first call hashed all 1,455 files. Now, after a full check passes, the runner writes `tree-<digest of SHA256SUMS>.json` into the module cache folder. The record lists each file's `(dev, ino, size, mtime_ns, ctime_ns)` under which its hash matched, perl.wasm and MANIFEST included. A later process:

- walks the tree with every rule as before: no link, no file the list omits, no listed file missing, and `dev/` exactly one empty `null`;
- hashes only the files whose stamp is not the record's, and writes a new record if any were hashed.

Why this keeps the protection (INFERRED):

- A write to a file moves its mtime and ctime. A new file at its name moves its inode. No unprivileged process can set a ctime; only the clock can, which takes root. The in-process digest cache already trusts this stamp, and the record carries the same trust across processes.
- The record is trusted exactly as a `.cwasm` beside it is: it is read through `_cache.read`, which takes only a file at 0600 with one name, this user's, in a private folder. Whoever could forge the record could write a `.cwasm`, which is native code.
- With `cache_dir=False`, or a cache folder that is refused, there is no record and the check hashes everything, as today.

Cost: 146 KB of JSON per artifact set.

String paths alone (no record) take the full check from 86 to 64 ms (EXECUTED, `results/tree-check.txt`). That part has no security effect and could land on its own.

### 4.3 L7: map the `.cwasm` and keep the descriptor guarantee

`_cache._open_checked` does today's checks through the open descriptor. Then `Module.deserialize_file` is given `/proc/self/fd/N`, after checking that this name opens that very file. So wasmtime maps the inode that was checked, whatever is at the path by then. Where there is no such name (not Linux, or no `/proc`), the bytes are read through the descriptor as today. This is wasi-python's way (READ: `wasi_python/_engines.py` `_load`, `_platform_posix.py` `open_cached`).

What changes (INFERRED): the code is a private mapping of the file, not a copy. An in-place write to the `.cwasm` by this user could change pages not yet read. The runner never writes one in place: `_cache.write` renames a new file over the name, which leaves a mapped inode as it was. Another process of the same user could also replace the `.cwasm` itself, so this grants nothing new.

`load.py` (EXECUTED, `results/load.jsonl`, five fresh processes each): `bytes` 19.6–23.8 ms, `file` 0.36–0.43 ms, `procfd` 0.38–0.48 ms. The first `_start` after a mapped load is also faster, 1.2–1.5 ms against 2.9–3.8 ms, and peak RSS falls from 70 to 37 MiB.

### 4.4 Tried and dropped

All EXECUTED with `floor.py` (`results/floor.json`, `results/floor-pooling.json`), µs per call:

| variant | total | note |
|---|---|---|
| new Linker per call (today) | 1,147 | |
| shared Linker | 1,097 | |
| `InstancePre` | 962–1,110 | kept as L4 |
| pooling allocator | 1,169 | no gain; close 186 → 168 µs |
| pooling, 16 MiB kept resident | 1,531 | **worse**: close takes 733 µs, which is consistent with wasmtime zeroing the kept memory itself |
| `memory_reservation` 1 GiB | 1,320 | no gain |
| `memory_init_cow` off | 2,589 | instantiate takes 1.5 ms: keep the default on |
| no epoch interruption | 1,213 | no gain beyond noise: epochs cost nothing measurable here |
| fuel metering | 1,013 | no gain or loss on a bare call |

- **Fuel on a computing guest** costs 8–10%. The 1 M-iteration loop took 73.5 → 79.1 ms at the base and 67.3 → 73.9 ms with the levers (EXECUTED, `fuel_cost.py 10`, `results/fuel_cost-*.json`; 589.7 M instructions).
- **Page faults** are not the cost. A bare call takes 84 minor faults (EXECUTED, `counts.py`), and a fault costs about 1 µs here (EXECUTED: touching 1,000 fresh pages took 986 µs). That is under 0.1 ms.

## 5. What is left, and how near 1 ms it can get

After the levers, `phases.py bare 100` (EXECUTED, `results/phases-final-bare.json`) gives **1,245 µs**:

| phase | µs |
|---|---|
| `_start` and the exit-status read | 731 |
| `Store.close` | 186 |
| `_artifacts.checked` (about 8 `stat`s: artifact folder, SHA256SUMS, perl.wasm, MANIFEST) | 90 |
| `_set_up`: WasiConfig, stdout and stderr callbacks, two preopens | 39 |
| `exports`, `Store()`, `_cache.given`, `check_inputs`, the epoch callback, the rest | about 100 |
| the timers' own overhead (about 30 wrappers) | about 100 |

The runner's own Python is now about 0.25 ms. The rest is wasmtime.

### 5.1 The guest's `_start`: mostly WASI filesystem calls

- Perl's startup runs **1.59 M instructions** (EXECUTED, `fuel=` on `-e 1`). The steady guest rate is **8.5 B per second**: 85 M more instructions cost 10 ms more (EXECUTED, loops of 20,000 and 200,000). So the instructions are about 0.19 ms of the 0.7 ms `_start`.
- Perl's startup makes **33 WASI calls** (EXECUTED: `WASMTIME_LOG=wasmtime_wasi=trace` with the wasmtime 51 CLI). Of them, path_open, path_filestat_get, fd_filestat_get and fd_close reach the filesystem, and fd_fdstat_get is called 11 times.
- **One filesystem call costs 56–58 µs under wasmtime-py** (EXECUTED, `wasi_fs_call_cost.py`: `path_filestat_get` on a preopened folder, slope over 2,000 calls, in two runs). `strace -f -c` of that script counts about 17 `futex` calls per WASI call on eight other threads. That is a handoff to a thread pool and back on every call (INFERRED from the counts; the pool is presumably tokio's, which wasmtime-wasi runs file operations on).
- **The same module under the wasmtime 51 CLI costs 0.85 µs per call**: 20,010 calls take 22.1 ms and 10 take 5.1 ms (EXECUTED, `wasmtime run --dir`). So the CLI runs these operations on the calling thread, and the C API does not (INFERRED). wasmtime-py's C API has no setting for it (READ: the `wasi_config_*` functions in `wasmtime/_bindings.py`).
- A WASI call that does not touch the filesystem costs 0.09–0.18 µs (EXECUTED, `wasi_call_cost.py`: `clock_time_get`, `fd_fdstat_get` on stdout).

A handful of such handoffs at 56 µs each would explain most of the 0.5 ms that `_start` spends beyond its instructions (INFERRED). And the `futex` calls are most of a bare call's 138 syscalls.

**This matters more to study E than to a bare call.** Every file a guest opens, stats or reads through a preopen pays it, and so does every module that `use` loads from the library preopen. A provider-backed mount implemented in Python, as wasi-python's `mounts.py` does, replaces these calls with Python ones, but the library stays a preopen.

### 5.2 How near 1 ms

| step | per bare call | status |
|---|---|---|
| today | 3.5–4.4 ms | EXECUTED |
| L1–L8 | 1.26–1.49 ms | EXECUTED |
| wasmtime-py's floor with `InstancePre` | 0.96–1.11 ms | EXECUTED |
| floor, if the C API ran filesystem calls on the calling thread | about 0.5–0.8 ms | INFERRED from 5.1 |
| a store closed off the caller's path | about 0.2 ms less latency, not less work | INFERRED from 186 µs |

So, without weakening a check: **about 1.3 ms in the runner alone; about 1 ms or a little under with an upstream wasmtime change.**

The first call of a process: **34 ms**, of it about 21 ms importing wasmtime-py (`python -X importtime`: `wasmtime._bindings` alone 9.5 ms) and 10–15 ms of tree walk. A further 20–25 ms of `import wasi_perl` comes before it (`dataclasses` and `_artifacts` most, `python -X importtime`). About 30 ms is reachable, but not much below it without a lighter wasmtime-py import.

## 6. Ranking

Gain is per call unless it says first call. Cost to build: S under a day, M a few days. "vfs" is relevance to vfs's pattern of many short calls, each a fresh instance.

| rank | lever | gain | cost | risk | security effect | vfs |
|---|---|---|---|---|---|---|
| 1 | L1 version once | −1.0 ms | S | none | none | high: every call |
| 2 | L3 rules only when a grant can break them | −0.85 ms (no grant), −0.36 ms (read-only) | S | none | none: unread inputs only | high: vfs calls grant mounts, not folders |
| 3 | L2 SHA256SUMS per stamp | −0.5 ms | S | low | none: the stamp rule `digest()` keeps | high |
| 4 | L5 runtime identities per tree stamp | −19 ms on a writable call | M | medium: a subtle rule, needs its own tests (a second name made after the scan; a runtime file replaced) | equal: section 4.1 | medium: only where a host folder is writable |
| 5 | L7 map the checked `.cwasm` | −22 to −28 ms first call; −33 MiB RSS | S | low: wasi-python ships it | equal: the descriptor's own name | medium: once per process |
| 6 | L6 tree record and string walk | −70 ms first call | M | medium: a new on-disk file to keep consistent | equal with a cache; none without one (section 4.2) | medium: once per process |
| 7 | upstream: filesystem WASI calls on the calling thread | about −0.3 to −0.5 ms per bare call; 56 → about 1 µs per guest file operation | M upstream, then a wasmtime-py release | an upstream change | none | **high, for study E**: every file a guest touches |
| 8 | L8 version beside the module | about −8 ms first call | S | low: falls back to `importlib.metadata` | none | low |
| 9 | L4 one `InstancePre` | −0.05 ms | S | low | none | low |
| 10 | close the store off the caller's path | about −0.2 ms latency | M: the grants must stay held until it closes | medium | none if the hold is kept | low |
| — | pooling allocator, memory reservation, `memory_init_cow` off | none or a loss | | | | dropped |
| — | turning off fuel or epochs | none on a bare call; fuel costs 8–10% on a computing guest | | | removes a limit | not a lever |

Recommendation (INFERRED, for the synthesis): one specification for L1, L2, L3, L4, L7 and L8, all small and with no change to any rule, and one for L5 and L6, which keep a rule exactly but by a new argument that a reviewer must check. The filesystem-call cost goes to study E and upstream.

## 7. Limits

- One x86-64 Linux VM, shared with five other studies. Sessions differ by about ±15%. Every comparison here is within one session.
- L7's `/proc/self/fd` name is Linux only. macOS would need wasi-python's `/dev/fd` way, and Windows the path with a stamp check. Neither was built or timed here.
- The cause of the 56 µs filesystem call is INFERRED from `futex` counts and the CLI's contrast. The wasmtime source was not read.
- `phases.py` adds about 1 µs per wrapped call, about 0.1 ms per call in all.
- The prototypes passed the existing fast tests. L5 and L6 have no tests of their own yet.

To reproduce, from the worktree, with the artifact set at `src/wasi_perl/artifacts/` and `WASI_PERL_ARTIFACTS` unset: apply `patches/all-levers-cumulative.patch`, then run each script in section 2 under `flock /home/user/wasi-perl-wt/machine.lock`.
