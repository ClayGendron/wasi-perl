# 014 — The runner's fixed cost

- **Status:** a draft by the orchestrator, 2026-11-24, for the owner to agree. The owner chose specifications 013 to 016 to come first (2026-11-24).
- **Owner:** Clay Gendron
- **Kind:** `src/wasi_perl/` (`_engines.py`, `_artifacts.py`, `_cache.py`, `_grants.py`, `runner.py`), checks in `tests/runner/`.
- **Depends on:** 004, 012.
- **Research it stands on:** `../research/2026-11-24-perf-runner.md` (study A), its levers L1, L2, L3, L4, L7 and L8, and the patches in its evidence folder. Also `../research/2026-11-24-perf-codegen.md` §8 (study B's engine pins).

## Intent

A bare call in a warm process costs 3.5–4.4 ms, and Perl's own share of it is about 0.2 ms. The rest is Python work that does not need to happen on every call. After this specification, a bare call costs about 1.3–1.5 ms, and a fresh process's first call about 40 ms. Every rule of specs 004 and 012 holds unchanged.

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
     - `/dev/fd/N` on macOS, as wasi-python does.
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
6. **The engine.** A fake `Config` records the eight settings `engine()` makes, the four new ones included.

## Acceptance

1. The checks pass, and every existing suite stays green (EXECUTED).
2. A research memo beside study A gives the before and after timings on this machine: a bare call, read-only and writable grants, a fresh process's first call, and peak memory. Its commands are given beside it. Wall time stays out of the tests, as `AGENTS.md` requires.
3. `CHANGELOG.md` under "Unreleased".
4. A fresh agent's review lands.
