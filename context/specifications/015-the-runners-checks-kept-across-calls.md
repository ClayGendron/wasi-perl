# 015 — The runner's checks, kept across calls

- **Status:** agreed by the owner 2026-11-24 ("Agree all four (Recommended)"). Written by the orchestrator; amended the same day for the review `../reviews/perf-research-and-specs-013-016-pr-2026-11-24.md` (H2, M3, M4, L10).
- **Owner:** Clay Gendron
- **Kind:** `src/wasi_perl/` (`_grants.py`, `_artifacts.py`, `_cache.py`, `runner.py`), checks in `tests/runner/` and `tests/escapes/`.
- **Depends on:** 014.
- **Research it stands on:** `../research/2026-11-24-perf-runner.md` (study A), its levers L5 and L6, its §4.1 and §4.2, and the patches in its evidence folder.

## Intent

Two checks of specification 012 redo the same work on every call. Each was right to do so until there was a way to know the work's answer had not changed.

- **The hard-link rule.** No writable grant may hold a second name of a runtime file. It lists and `lstat`s about 3,000 runtime files on every call with a writable grant. That is 19 ms, which takes a writable call to 21–24 ms (EXECUTED in study A, `results/calls.jsonl`, the wheel's layout).
- **The artifact check.** It hashes all 1,455 library files in every new process, about 70 ms of a first call (EXECUTED in study A, `results/first_call.jsonl`).

After this specification, both keep exactly the guarantee they give today:
- a writable call costs about 3 ms (EXECUTED in study A: 2.8–3.3 ms with L1–L8);
- a fresh process's first call costs about 34 ms with a cache, and as today without one (EXECUTED in study A: L1–L8, five processes).

These levers keep a rule by a new argument. That is why they are a specification of their own, with checks for each way the argument could fail.

## Decisions

1. **The runtime files' identities are scanned once and reused while every folder stamp holds (L5).**
   - **What is kept, per process:** the set of `(st_dev, st_ino)` of every regular file under wasmtime-py, `wasi_perl` and the library, and the files `perl.wasm`, `MANIFEST`, `SHA256SUMS` and `dev/null`. Beside it, the stamp `(st_dev, st_ino, st_mtime_ns, st_ctime_ns)` of every folder walked. Each folder is stamped before it is listed.
   - **When it is reused:** only if every folder stamp still holds. Any change rescans.
   - **The argument** (study A §4.1):
     - A file is added, removed or replaced only through an entry of its folder, which moves that folder's mtime and ctime.
     - A write into an existing file keeps its inode, so its identity is still right.
     - Making a second name of a runtime file *inside a writable grant* changes no runtime folder. That is why decision 2 exists.
2. **The writable grants are still walked on every call.**
   - Every file there with more than one name is looked up among the identities.
   - `MAX_WALK`, the refusal of an unreadable folder, and the shortcut when no runtime file has a second name all keep their meaning.
   - Those three decisions need to know whether any runtime file has a second name *now*. A link made inside a grant raises that count without changing any runtime folder, so the cached scan cannot answer it. So each of them `lstat`s every runtime file afresh, at the point it is needed, as study A's prototype does (`RuntimeFiles.linked_now`):
     - when the walk passes the runtime's own size;
     - on an unreadable folder;
     - past `MAX_WALK`.
   - A file that a grant's file matched is read afresh too.
3. **A first call trusts a record of what it hashed, kept in the module cache (L6).**
   - **Writing the record.** After a full check passes, the runner writes `tree-<SHA-256 of SHA256SUMS>-<st_dev>-<st_ino of the artifact root>.json` into the module cache folder, with `_cache.write`'s rules: 0600, renamed into place. It lists each file's `(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)` under which its hash matched, `perl.wasm` and `MANIFEST` included.
   - **Using it.** A later process walks the tree with every rule as today: no link, no file the list omits, no listed file missing, and `dev/` exactly one empty `null`. It hashes only the files whose stamp differs from the record, and writes a new record if any were hashed.
   - **Reading it.** The record is read through `_cache.read`, so it is trusted exactly as a `.cwasm` beside it is. Whoever could forge it could already write a `.cwasm`, which is native code.
   - **Without it.** With `cache_dir=False`, or a cache folder that is refused, there is no record and every file is hashed, as today.
   - **Why stamps are enough, and where.** On Linux and macOS a user can set a file's mtime but not its ctime: only the clock can, and changing the clock takes root.
   - **Not on Windows.** There `st_ctime` is the creation time, which the file's owner can set with `SetFileTime`. So on Windows no record is written or read, and every file is hashed in every new process, as today.
   - **The record's name.** It is keyed by the artifact root's identity `(st_dev, st_ino)` as well as by the SHA-256 of `SHA256SUMS`, so two installs of the same set do not keep replacing each other's record.
4. **Coarse file times are allowed for**, as git does for its index:
   - A file is recorded only if its stat before the hash and its stat after the hash agree.
   - A stamp whose mtime or ctime lies within 2 s of the start of the scan or check that read it is not trusted. The folder is scanned again on the next call, or the file is hashed again in the next process.
   - This holds for decision 1's folder stamps and for decision 3's file stamps.
5. **Decision 1's reuse of a scan is also Linux and macOS only.** On Windows every writable call scans, as today, for the reason decision 3 gives.
6. **Plain string paths in the tree walk.** This takes a full check from 86 ms to 64 ms with no change of rule (study A §4.2). It comes with decision 3.

## Checks (written first, seen failing; they count operations, never time)

1. **Scans.** Ten writable calls in a row scan the runtime trees once, counted by a patch on the walk. Touching a file in the library (a new file, a removal, a rename over it) makes the next call scan again.
2. **The rule, after a scan.**
   - (a) A hard link to wasmtime's shared library made inside a writable grant *after* several calls is refused on the next call.
   - (b) The same with a link to a library `.pm` file.
   - (c) A runtime file replaced by rename, then linked into a grant, is refused.
   - (d) A grant past `MAX_WALK` is refused exactly when a runtime file has a second name, as today.
3. **Records.**
   - (a) A second process with a warm cache hashes zero library files: counted.
   - (b) A library file changed in place, with its mtime then restored by `os.utime`, is hashed, because its ctime moved, and is refused for a wrong digest.
   - (c) A record edited by hand (mode 0644, or a stamp changed) is not used, and every file is hashed.
   - (d) With `cache_dir=False`, every file is hashed.
   - (e) A record from another `SHA256SUMS` is never read.
4. **The shortcut in a copy-mode install.** The check 2(a) is run again in a layout where no runtime file has a second name when the scan is made, such as an install with `UV_LINK_MODE=copy`. The link made later is still refused.
5. **Coarse times.**
   - A file rewritten within the same second as its stamp, keeping its size, is hashed again in the next process.
   - A folder changed within 2 s of its stamp is scanned again.
6. **Windows.** On Windows no record is read or written, and every writable call scans. Patched platform checks verify both on any system, and CI runs them on Windows.
7. Every check of spec 012 and every escape test passes unchanged.

## Acceptance

1. The checks pass, and every existing suite stays green (EXECUTED).
2. The memo of spec 014 is extended with the writable-call and first-call timings.
3. `docs/sandbox.md` says that the artifact check trusts a record in the module cache on Linux and macOS, and when it does not.
4. `CHANGELOG.md` under "Unreleased".
5. A fresh agent's review, which checks the arguments of decisions 1 and 3 against the code, lands.
