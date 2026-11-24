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

(Written when built.)
