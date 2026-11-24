# 014 — The runner's fixed cost

- **Status:** agreed by the owner 2026-11-24 ("Agree all four (Recommended)"). Written by the orchestrator; amended the same day for the review `../reviews/perf-research-and-specs-013-016-pr-2026-11-24.md` (M5, L1, L2, L3).
- **Owner:** Clay Gendron
- **Kind:** `src/wasi_perl/` (`_engines.py`, `_artifacts.py`, `_cache.py`, `_grants.py`, `runner.py`), checks in `tests/runner/`.
- **Depends on:** 004, 012.
- **Research it stands on:** `../research/2026-11-24-perf-runner.md` (study A), its levers L1, L2, L3, L4, L7 and L8, and the patches in its evidence folder. Also `../research/2026-11-24-perf-codegen.md` §8 (study B's engine pins).

## Intent

A bare call in a warm process costs 3.5–4.4 ms, and Perl's own share of it is about 0.2 ms. The rest is Python work that does not need to happen on every call. After this specification, a bare call costs about 1.3–1.5 ms (EXECUTED in study A, `results/calls.jsonl`, levers L1–L4 and L7–L8), and a fresh process's first call about 100 ms, against 138–153 ms (INFERRED: study A's base, less L7's 22–28 ms and L8's 8 ms). The library check, 80–88 ms of that first call, stays until specification 015. Every rule of specs 004 and 012 holds unchanged.

The two levers that keep a rule by a new argument, L5 and L6, are specification 015.

## Decisions

1. **wasmtime-py's version is read once per process (L1).** Today `importlib.metadata` runs four times per call, about 1.0 ms. The wasmtime-py a process imported cannot change after the import. The tests that fake a version patch `wasmtime_version` itself, so they still apply.
2. **`SHA256SUMS` is parsed once per stamp (L2).** It is parsed again when its `(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)` changes, the same rule `_artifacts.digest()` already keeps for `perl.wasm`. A parse is kept only if the stamp is the same after the read as before it. This saves 0.5 ms.
3. **Each rule's inputs are computed only when a grant can break the rule (L3).**
   - `_runtime_folders`, `_runtime_files` and `_cache.every` run only when the call has a grant that the rule binds:
     - the cache rule binds any grant;
     - the runtime rules bind a writable grant.
   - This saves 0.85 ms on a call with no grant, and 0.36 ms with read-only grants.
   - Who may grant what is unchanged.
4. **One `InstancePre` per compiled module (L4).**
   - One `Linker` with `define_wasi` is made per module, and every call instantiates from it.
   - It is safe because `define_wasi` reads each store's own WASI context, so one linking serves every store.
   - The saving is 50–60 µs.
5. **The checked `.cwasm` is mapped, not read (L7).**
   - `_cache` keeps its checks on the open descriptor: owner, mode, one name, a private folder.
   - It then gives `Module.deserialize_file` that descriptor's own name, after checking that the name opens the same inode:
     - `/proc/self/fd/N` on Linux;
     - `/dev/fd/N` on macOS, as wasi-python does (READ: `wasi_python/_platform_posix.py`, `_FD_NAMES`). Study A measured Linux only, so macOS has its own check, run in CI.
   - Elsewhere, Windows included, the bytes are read through the descriptor as today.
   - The runner never writes a `.cwasm` in place: `_cache.write` renames a new file over the name. So the mapped inode is the checked one for the module's whole life.
   - A first call saves 22–28 ms and 33 MiB of peak memory.
6. **wasmtime-py's version comes from the one `wasmtime-*.dist-info` folder beside the imported module (L8).**
   - Only that folder's `METADATA` file is read.
   - With no such folder, or more than one, `importlib.metadata` decides, as today.
   - A first call saves about 8 ms.
7. **The engine states the four wasmtime defaults that the speed depends on** (study B §8): `cranelift_opt_level = "speed"`, `parallel_compilation = True`, `memory_init_cow = True` and `signals_based_traps = True`.
   - Each is wasmtime 51's default today, so nothing changes now.
   - Without each one, study B measured, in order:
     - optimised code: calls 1.13–1.46× slower;
     - parallel compilation: a 3.3 s compile instead of 0.9 s;
     - copy-on-write memory: a 7.2 ms bare call instead of 3.0 ms;
     - signals-based traps: text one-liners 1.17–1.32× slower.
   - The comment says why each is set.

## Checks (written first, seen failing; they count operations, never time)

1. **Version reads.** A bare call after the first makes zero calls into `importlib.metadata`: patched to count. With two `wasmtime-*.dist-info` folders, the version still comes from `importlib.metadata`.
2. **`SHA256SUMS` reads.** Ten calls with an unchanged file parse it once. A rewrite of `SHA256SUMS` between two calls makes the second call parse it again, and refuse a wrong digest, as today.
3. **Rule inputs.** A call with no grant runs none of the three input functions, counted by a patch. A writable grant runs all three. Every grant check of `tests/runner` and `tests/escapes` still refuses what it refuses today.
4. **Linking.** Ten calls on one module make one `Linker`. Two calls with different preopens and environments each see only their own.
5. **The mapping.**
   - On Linux, the module is loaded by `deserialize_file` with a `/proc/self/fd/` name.
   - A `.cwasm` swapped at its path between the check and the load still loads the checked inode: a probe renames another file over the path from a patched hook.
   - With `/proc` unavailable (patched), the bytes are read as today.
   - On macOS, in CI, the module is loaded by `deserialize_file` with a `/dev/fd/` name.
6. **The engine.** A fake `Config` records the seven settings `engine()` makes, the four new ones included.

## Acceptance

1. The checks pass, and every existing suite stays green (EXECUTED).
2. A research memo beside study A gives the before and after timings on this machine: a bare call, read-only and writable grants, a fresh process's first call, and peak memory. Its commands are given beside it. Wall time stays out of the tests, as `AGENTS.md` requires.
3. `CHANGELOG.md` under "Unreleased".
4. A fresh agent's review lands.

## Landing notes

Built 2026-11-24 by the implementer (Claude Opus 5.5) on `spec/014-the-runners-fixed-cost`, from `1c062ea`, with the amendment `dea44fa` merged. Linux x86-64, CPython 3.13, wasmtime-py 51.0.0, as root, with perf-base's set (`/home/user/wasi-perl-wt/perf-base/build/artifacts`, perl 5.42.3). **Not yet reviewed.** Each claim carries its label.

### What was built

- **Decisions 1 and 6** (`_engines.wasmtime_version`, `_wasmtime_site`, `_version_beside`): the version is read once per process and kept. It is the `Version:` of the one `wasmtime-*.dist-info` in the folder that holds the `wasmtime` package an import loads (found by `importlib.util.find_spec`, without importing it), when that METADATA's `Name:` is wasmtime. With no such folder, more than one, or another name, `importlib.metadata.version` decides (EXECUTED: the checks below).
- **Decision 2** (`_artifacts._read_sums`): a parse is kept under the stamp `_stamp()` gives, which is the path with `(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)`, and only when the stamp after the read equals the one before. One parse is kept at a time (READ: the code).
- **Decision 3** (`runner.run`): `_cache.every` runs only when the call has a grant; `_runtime_folders` and `_runtime_files` only when one is writable. `_grants.py` did not need to change: `judge` already applied the runtime rules to writable grants only (READ: `_grants.judge`).
- **Decision 4** (`_engines.linked`): one `Linker` with `define_wasi` per compiled module, kept as an `InstancePre` beside the module and dropped by `forget()`; `_drive` calls `pre.instantiate(store)` (READ: the code).
- **Decision 5** (`_cache._open_checked`, `_fd_name`, `load`): the checks on the open descriptor are those `read` made before. `_fd_name` opens `/proc/self/fd/N` on Linux or `/dev/fd/N` on macOS and compares `(st_dev, st_ino)` with the descriptor's own; on a match `Module.deserialize_file` is given that name, else the bytes are read through the descriptor and handed to `Module.deserialize`. The name's folder is `_cache._FD_FOLDER`, `None` elsewhere (Windows included). `read` is gone: nothing else called it (READ: `grep -rn "_cache.read" src tests`). Adapted from wasi-python's `_platform_posix.py` `open_cached` and `_fd_name`, as `_cache.py`'s docstring says.
- **Decision 7** (`_engines.engine`): the four settings, with a comment giving the reason for each in words.

### The checks

`tests/runner/test_fixed_cost.py`, 22 checks. 19 of the first 21 failed before the change (EXECUTED: commit `1e4a5e1`, `uv run pytest tests/runner/test_fixed_cost.py`, "19 failed, 2 passed"); the two that passed hold rules the change keeps (a writable grant computes all three inputs; a `SHA256SUMS` replaced while it is read is not kept). The macOS check came with the amendment and was added after (`53e3a8b`).

1. Version reads: a call after the first makes no call into `importlib.metadata` (counted: `version`, `metadata`, `distribution`, `distributions`, `packages_distributions`, `Distribution.from_name`); before, it made 12 (EXECUTED). The real install's version comes from its dist-info with none; a fake site with one dist-info gives its METADATA's version with none; with none, with two, or with one naming another package, `importlib.metadata.version` decides.
2. `SHA256SUMS`: ten calls read it once (before: ten, EXECUTED). A rewrite, by `os.replace` or in place with its mtime moved, makes the next call parse again and refuse "perl.wasm does not match". A file replaced while it is read is not kept under the new stamp, so the next call refuses it.
3. Rule inputs: no grant runs none of the three; a read-only grant runs only `_cache.every`; a writable grant runs all three. Every grant check of `tests/runner` and `tests/escapes` passes unchanged (EXECUTED, below).
4. Linking: ten calls after `forget()` make one `Linker` (before: ten, EXECUTED). Calls alternating between two grants, two environments and a writable and a read-only grant of one folder each see only their own, from one `Linker`.
5. The mapping: on Linux the load is one `deserialize_file` with a `/proc/self/fd/` name and no `deserialize`; on macOS one with a `/dev/fd/` name (skipped here; it uses the real set so that CI's macOS wheel job, which builds no stand-in, runs it). With `_FD_FOLDER` pointed at a missing folder, as with no `/proc`, the bytes are read. With every name in it opening a decoy, the name is refused and the bytes read. A decoy `.cwasm` renamed over the path from a patched `_fd_name`, between the check and the load, is not what loads, on both the mapped and the read way: the call prints "hello", counts one load and no compile, and the path holds the decoy after.
6. The engine: a fake `Config` records the seven settings `engine()` makes, for each flavour, and a third call makes no third engine.

```
$ WASI_PERL_ARTIFACTS=/home/user/wasi-perl-wt/perf-base/build/artifacts \
  WASI_PERL_ORACLE=/home/user/wasi-perl-wt/perf-base/build/native-install/bin/perl \
  uv run pytest -m "not slow" -q
465 passed, 3 skipped, 242 deselected, 4 warnings
$ (same variables) uv run pytest -m slow -q tests/runner tests/escapes
32 passed, 190 deselected
$ uv run ruff check && uv run ruff format --check
All checks passed!  39 files already formatted
```

All EXECUTED. Before the change the fast suite gave 444 passed, 2 skipped with the same variables (EXECUTED); the 21 new checks pass and the macOS one skips. No existing check was changed.

### The specification's numbers, against what was found

The timings are `../research/2026-11-24-the-runners-fixed-cost-measured.md`, from `measure.sh` and study A's `phases.py`, two sessions, under the machine lock.

| the specification says | found | label and command |
|---|---|---|
| a bare call 3.5–4.4 ms before | 3.85–4.50 ms | EXECUTED, `calls.py` in `measure.sh` |
| a bare call about 1.3–1.5 ms after | 1.40 and 1.78 ms (two sessions) | EXECUTED, `calls.py`; the second session ran slower before as well (4.50 → 3.85 ms) |
| Perl's own share about 0.2 ms | not measured here | READ, study A §5.1 (1.59 M instructions at 8.5 B per second) |
| a fresh process's first call about 100 ms, against 138–153 | 106.4 and 106.2 ms (medians), against 134.5 and 144.9 | EXECUTED, `first_call.py`, five processes per tree and session |
| the library check 80–88 ms stays | 77.4–85.4 ms after, 77.5–88.5 before | EXECUTED, `first_call.py` |
| L1: four `importlib.metadata` reads per call, about 1.0 ms | four `version()` calls; `_engines.wasmtime` 1,033 → 5.9 µs | EXECUTED, the check before the change, and `phases.py bare 100` |
| L2: 0.5 ms | `_artifacts.checked` 618 → 110 µs | EXECUTED, `phases.py bare 100` |
| L3: 0.85 ms with no grant, 0.36 ms with read-only grants | 919 → 3 µs with none; 864 → 147 µs read-only (0.72 ms) | EXECUTED, `phases.py bare 100` and `ro 100` (`judge` + `_runtime_folders` + `_cache.every`) |
| L4: 50–60 µs | `_set_up` 136 → 54 µs; `Linker.instantiate`'s 71 µs replaced by `InstancePre.instantiate`, which was not timed apart | EXECUTED, `phases.py bare 100` |
| L7: a first call saves 22–28 ms and 33 MiB | module load 21.0–30.2 → 1.0–1.2 ms; peak RSS 74.8–74.9 → 43.9–44.0 MiB (31 MiB) | EXECUTED, `first_call.py` and `peak_rss.py` |
| L8: about 8 ms of a first call | not separated: it is inside the 20 ms beside the check and the load | INFERRED; study A's figure is READ, its §4 |
| study B's figures for the four engine settings | not re-measured: each setting is wasmtime 51's default, so nothing changed | READ, study B §8; the settings are EXECUTED by the engine check |

### Left open

- The spec's macOS check runs only in CI (the macOS wheel job). It was not seen run here.
- A writable call is still about 20 ms (EXECUTED, `calls.py`): the runtime-file scan is L5, specification 015, as decided.

### After the review (round 1)

The review `../reviews/014-the-runners-fixed-cost-pr-2026-11-24.md` found that decision 5's argument was incomplete (H1). "The runner never writes a `.cwasm` in place" covers the runner, not a guest. A module stays mapped for the process's life, and every later call runs it whatever cache that call names. So a later call could grant an earlier call's cache folder writable, and its guest could truncate the mapped file (the host died with `SIGBUS`) or rewrite it (`SIGSEGV`; the bytes reached the host's code). EXECUTED by the reviewer.

The fix, with its checks seen failing first (EXECUTED, `uv run pytest tests/runner/test_fixed_cost.py`, with the fix stashed):
- **The rule.** `_cache` keeps every folder a module was mapped from, for the process's life. `_cache.every` returns them with this call's caches. So a grant at or inside one, or a writable grant above one, is refused like a grant of today's cache, whatever `cache_dir` or `$WASI_PERL_CACHE` the later call names. Check: `test_no_later_grant_reaches_a_folder_a_module_was_mapped_from`, for `cache_dir` and for the variable changed between the calls. Without the fix it failed with "DID NOT RAISE".
- **A bound.** At most 1,000 folders are kept. Past that, a module is read into memory through its checked descriptor, which ties it to no file. Check: `test_past_its_bound_a_module_is_read_not_mapped`.
- **L2.** Linking is under one lock, so calls started at once link once. Check: `test_calls_started_at_once_link_once_and_see_only_their_own` (12 threads). Without the lock it failed in 1 of 3 runs.
- **L3** was there before this change. The tree check is now recorded under the stamp the list was read under, and not recorded when the list changed while it was read. Check: `test_a_tree_checked_against_one_list_is_not_recorded_for_its_successor`. Without the fix it failed with "DID NOT RAISE".
- **L1.** The README says that a loaded module stays mapped from its file while the process runs.
- **The note.** The memo's warm-up runs are now under the lock too. No number depended on them.
