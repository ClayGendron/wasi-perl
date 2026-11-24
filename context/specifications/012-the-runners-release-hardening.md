# 012 — The runner's release hardening

- **Status:** written by the orchestrator 2026-11-24, under the owner's pre-agreement of the work toward 0.1.0; it answers the runner's findings of the 0.1.0 security review (`../reviews/0.1.0-security-and-structural-review-2026-11-24.md`, M1, M2, M3 and the Lows).
- **Owner:** Clay Gendron
- **Kind:** `src/wasi_perl/`, checks in `tests/runner/` and `tests/escapes/`, the README.
- **Depends on:** 004, 005.
- **Research it stands on:** the security review (EXECUTED probes in its appendix), and wasi-python's runner (READ): its process-wide grant registry, its cache checks, its rule against granting the runtime's folders, and its version floor on wasmtime-py.

## Decisions

1. **A writable grant may not reach the runtime by a hard link (M1).** Before a call, each writable grant is checked against the runtime's protected files (wasmtime-py's shared library, the package's own files and the artifact set) by `(st_dev, st_ino)`, not only by path: a protected file with more than one link has its other names found by walking only the writable grants, bounded at 100,000 entries per call; a grant too large to walk while any protected file has extra links is refused with a message that says why. A protected file with one link needs no walk.
2. **A compiled module is never loaded from a folder a guest could write (M2).** The process remembers every folder it has granted writable, for its whole life, not only while the call runs; a `cache_dir` at, inside or over one of them is not used (a warning, modules kept in memory). The README says plainly: never point `cache_dir` at a folder you grant, in any process.
3. **Host descriptors are counted per process (M3).** The runner keeps one count of the host descriptors its live calls may hold (each call reserves the guest table's 256), and refuses a call that would pass a budget of half the process's soft `RLIMIT_NOFILE` (POSIX; on Windows, a fixed 4,096), with a documented `RuntimeError` subclass raised before any work. Any `wasmtime` error raised while starting a guest becomes `trapped` with wasmtime's words, never an undocumented exception from `run()`.
4. **The Lows.** `run()` refuses `wasmtime` older than 51.0.0 at import of the runner, naming the version. The base interpreter's own folders (`sys.base_prefix`'s stdlib and its `bin`/`Scripts`) join the folders that may not be granted writable. Ctrl-C reaches a computing guest at once (the runner's cancel already does; check it). The Windows cache check documents that it trusts the folder's ACL.
5. **Checks** for each, seen failing first, following the review's probes.

## Acceptance

1. The checks pass, EXECUTED; the runner, escape and one-liner suites stay green.
2. A fresh agent's review lands.

## Landing notes

Built 2026-11-24 by the implementer (Claude Opus 5.5) on `spec/012-the-runners-release-hardening`, from `0645504`, Linux x86-64, CPython 3.13, wasmtime-py 51.0.0, as root, with spec 002's sealed set (`../wasi-perl-wt/002-xs/build/artifacts`, `sha256sum -c` passes). **Not yet reviewed.** Every claim below was run (EXECUTED) unless it says otherwise.

### What was built

- **M1** (`_grants._refuse_second_names`, `runner._runtime_files`): when a call has a writable grant, the runtime's files (every file of wasmtime-py and of `wasi_perl`, and the set's `perl.wasm`, `MANIFEST`, `SHA256SUMS`, `dev/null` and library tree) are `lstat`ed; those with more than one name are looked for, by `(st_dev, st_ino)`, in the writable grants, walked without following links, at most `MAX_WALK` = 100,000 entries per call. A match is refused ("a hard link to the runtime's …"), and so is a grant too large to walk while such a file exists. Here the venv's `_libwasmtime.so` has 24 names (uv's hard links), so every writable grant is walked.
- **M2** (`_grants._ever_writable`, `granted_writable`; `_cache.folder`): every folder a call grants writable is remembered, by identity and chain, for the process's life; a cache at, inside or above one is not used (a warning, modules kept in memory). Concurrent calls are covered by the same set, since a call registers its grants before its guest runs.
- **M3** (`_limits.reserve_descriptors`, `TooManyCalls`, exported): each live call counts 256 host descriptors; a call that would pass half the soft `RLIMIT_NOFILE` (4,096 on Windows), read at each call, raises `TooManyCalls(RuntimeError)` before any work. The store's set-up (`preopen_dir`, `set_wasi`, the linker) is `_set_up`; a `WasmtimeError` there is the call's `trapped`, never raised.
- **L1, Ctrl-C** (`_stop.py`): every call now has an epoch callback (wasmtime's `wasmtime_store_epoch_deadline_callback`, through wasmtime-py's `_bindings`) that runs every 5 ticks on the guest's thread and ends the call at its deadline or at Ctrl-C. On the main thread with Python's default SIGINT handler, a call puts in a handler that only notes the signal; `run` then raises `KeyboardInterrupt` once the store is closed, and the CLI exits 130. The specification said "the runner's cancel already does": it did not (no cancel existed; the review's L1 EXECUTED it), so this was built. The ticker now runs for every call, deadline or not.
- **L2**: `_engines.wasmtime()` reads wasmtime-py's version from its metadata before importing it and refuses one below 51 with an `ImportError` naming the found version and GHSA-f2pq-268w-886c. It is at the first `run`, not at the import of `wasi_perl.runner`, since `import wasi_perl` imports the runner and must never load or refuse wasmtime-py.
- **L4**: the real executable's folder and `sys.base_prefix`'s `bin` (`Scripts`) join the folders no writable grant may reach.
- **L5**: `_cache.py`'s docstring and the README say the Windows check trusts the folder's access list.
- The README: the cache warning, uv's hard links and `UV_LINK_MODE=copy`, the descriptor count, Windows' cache, what `SHA256SUMS` proves (L8), Ctrl-C.
- The suite's two failures on `dev` (`from conftest import …` reached `tests/oneliners/conftest.py` when the whole tree ran) are fixed by moving `write_set` to `tests/standin_sets.py`.

### The checks

`tests/runner/test_hardening.py`, 12 checks, all seen failing before the change (`build/logs/012-seen-failing.txt`: 12 failed; the two Ctrl-C checks at their 60 s fail-safe): a hard link to wasmtime's library and one to a library file in a writable grant refused (read-only allowed); a grant too large to walk refused while a runtime file has two names (`MAX_WALK` lowered to 10 by monkeypatch); a folder once granted writable never used as a cache (at, inside, above), and the review's planted `.cwasm` never loaded; the descriptor budget (patched to 300) refuses a second live call, and the budget is half the soft limit; a grant folder removed between the check and wasmtime's open is `trapped` "failed to add preopen dir"; wasmtime 50.0.0 refused; the base interpreter's folders protected; Ctrl-C ends `run` with `KeyboardInterrupt` and the command with 130, each in a child process.

```
$ WASI_PERL_ORACLE=../wasi-perl-wt/002-xs/build/native-install/bin/perl \
  WASI_PERL_ARTIFACTS=../wasi-perl-wt/002-xs/build/artifacts uv run pytest -q
XFAIL tests/escapes/test_escapes.py::test_a_fifo_in_a_grant_is_refused - …
630 passed, 1 skipped, 1 xfailed, 4 warnings in 49.35s
$ uv run ruff check && uv run ruff format --check
All checks passed!
33 files already formatted
```

The one skip is the check that cannot run as root. The runner, escape and one-liner suites are all in the 630.

### The review's probes, re-run

- `probes/cache.py` (umask 077): the planted `.cwasm` is not used: "not using the module cache at …/cache: it is at, inside or above a folder this process granted writable to a guest", and `loaded` stays 0 (`{'compiled': 1, 'loaded': 0, 'written': 1}` → `{'compiled': 2, 'loaded': 0, 'written': 1}`).
- `probes/fds2.py` under `ulimit -n 1024`: the three concurrent calls return `Completed(exit_code=0, stdout=b'B\n')`, where the review saw `WasmtimeError` raised; the host's own open works. The guest of the first call still held 1,013 descriptors: this set's `perl.wasm` has no descriptor-table cap (that is specs 009 and 010's), so the 256 a call is counted at is the runner's budget, not yet a limit on the guest. The README says so.
- `probes/hardlink.py` was not re-run (it builds a scratch venv); its route is the hard-link check above, with the venv's real `_libwasmtime.so`.

### Left over

- The guest-side descriptor cap of 256 (specs 009 and 010), which makes M3's count a true bound.
- H1 (a guest `sleep` past the deadline) is not this specification's: the epoch callback cannot run while the guest waits inside a host call, so it needs the guest's wait cut into slices or a shadowed `poll_oneoff`.
- The review's structural notes (one process object, one platform module) are unchanged; this change adds `_stop.py` and two registries, which a process object would own.
