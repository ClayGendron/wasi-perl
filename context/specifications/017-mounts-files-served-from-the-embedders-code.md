# 017 — Mounts: files served from the embedder's code

- **Status:** agreed by the owner on 2026-11-24, through AskUserQuestion, to the question "Adapt wasi-python's code, allow cwd inside a mount, keep the four shortcuts out." The answer was "Agree as written (Recommended)". A draft by an agent (Claude Opus 5.5), 2026-11-24, for synthesis item 5 (`../research/2026-11-24-perf-synthesis.md` §3). The owner agreed to build the ten items that day: "You can do all 10 actually, but do not change the API around perl to be different than wasi python, unless wasi python could adopt the same changes." It is listed in the roadmap's table "under 'You can do all 10'", before 022, which builds on it. Amended the same day for the review `../reviews/specs-017-022-pr-2026-11-24.md` (M1, M6, M8, L1–L4).
- **Owner:** Clay Gendron
- **Kind:** the runner only (`src/wasi_perl/`: new `mounts.py`, `_fs_memory.py`, `_fs_table.py`, `_fs_descriptors.py`, `_fs_paths.py`; changes to `_wasmtime_ffi.py`, `_engines.py`, `_grants.py`, `_limits.py`, `_stop.py`, `runner.py`, `__init__.py`), checks in a new `tests/mounts/` and in `tests/escapes/`, `docs/mounts.md`, `docs/sandbox.md`, `README.md`, `CHANGELOG.md`. **No guest change:** `perl.wasm`, `recipes/` and `downloads/SHA256SUMS` are untouched, so nothing is rebuilt.
- **Depends on:** 004 (the runner), 005 (the escape tests), 009 and 010 (the guest's descriptor table and its look before an open), 011 (sleeps in slices), 012 (the runner's hardening). It lands before or after 014 (one `InstancePre` per module); Decision 14 says what each order means.
- **Research it stands on:**
  - study E, `../research/2026-11-24-perf-vfs-io.md`, its counts and `mount-prototype.patch` in `2026-11-24-perf-vfs-io/`;
  - the synthesis, `../research/2026-11-24-perf-synthesis.md` §3, spec 5, and §4's note on E's rank 6;
  - wasi-python at `f9bf678` (0.1.6, `/home/user/claygendron/wasi-python`): `src/wasi_python/mounts.py`, `_fs_memory.py`, `_fs_table.py`, `_fs_descriptors.py`, `_fs_paths.py`, `_wasmtime_ffi.py`, `_call.py`, `_places.py`, `runner.py`; its specifications 032 (read-only mounts), 034 (writable mounts), 061 (a provider's `OSError`), 073, 143 and 145; `docs/how-to/mounts.md`, `docs/explanation/security-model.md`; `tests/public_api.txt`, `tests/unit/test_runner_mounts.py`, `tests/unit/test_runner_grants.py`, `tests/harness/files.py`, `tests/checks/test_v1_4_embedding.py`, `tests/checks/test_v1_2_hostile_guest.py` (all READ);
  - vfs's `context/specifications/active/172-the-wasi-python-runtime/spec.md` and `178-the-ranged-read/spec.md` (READ);
  - wasi-perl's `src/wasi_perl/runner.py`, `_grants.py`, `recipes/wasi-fd-table.c`, `recipes/wasi-stubs.c`, and Perl 5.42.3's `doio.c` (READ).
- **Decided by the owner's rule** (2026-11-24): "Do not change the API around perl to be different than wasi python, unless wasi python could adopt the same changes. We are going to eventually unify the shared components."

Labels: EXECUTED means run and seen. READ means read from a file. INFERRED means reasoned from what was read or run.

## Intent

An embedder can give a guest host folders, and nothing else. vfs needs to serve the guest's files from its own code: rows in a database, under the caller's authority, each access checked.

After this specification, `run(argv, mounts=[Mount(provider, "/work")])` does that. The API, the provider protocol, the limits and the record are wasi-python's, name for name. So an embedder's provider works unchanged with both runtimes, and the two projects can later share one package.

A mount is not slower than a grant today. Study E measured a grep over 500 rows at 97 ms through a mount against 156 ms through a grant (EXECUTED, E §4.2). That holds only while wasmtime-py hands every file call to another thread. Once the runner can set `allow_blocking_current_thread`, a grant answers in 0.4–1.5 µs, and a mount, at 16–19 µs a call, is 10 to 40 times slower (INFERRED: `../research/2026-11-24-wasmtime-py-file-call-cost.md` §4.3). Mounts are for files that live in the embedder's code, not a way to make grants faster.

## Terms

- **Provider:** the embedder's object that answers file operations on normalized paths: `stat`, `list` and `read`; for a writable mount also `write`, `mkdir`, `remove`, `rmdir` and `rename`.
- **Mount:** a provider placed at a guest path, as a grant places a host folder.
- **Shadow:** a host function defined over one of wasmtime's own preview1 functions.
- **Forward:** a shadow handing a call it does not own to wasmtime's own WASI.
- **Trampoline:** a tiny wasm module, instantiated on the guest's memory, through which a forward reaches wasmtime.
- **Placeholder:** an empty, read-only folder the runner owns. Each mount's preopen is made on it, so wasi-libc's preopen scan and the guest's descriptor table find the mount like a grant.
- **The look:** the guest's `path_filestat_get` before every open that is not an exclusive create (spec 010, `recipes/wasi-fd-table.c`'s `look()`). It refuses special files.
- **Staged bytes:** a file's whole content, held by the runner from its open.

## Decisions

### 1. The public surface is wasi-python's

- **The names.** `wasi_perl` exports `Mount`, `FileProvider`, `WritableFileProvider`, `Errno`, `Stat`, `FileFault`, `RefusedWrite` and `FileRecord`, defined in `wasi_perl.mounts`. Each is wasi-python's class with the same fields, defaults, `kw_only` markers, members and values.
- **`run`.** It gains two keyword-only parameters:
  - `mounts: Iterable[Mount] = ()`, placed after `grants`;
  - `max_file_calls: int | None = UNSET`, placed after `artifacts`.
  Their meaning is wasi-python's: providers whose files the guest sees at their guest paths; the most provider calls a call with mounts makes, after which it ends "out of file calls". `None` means no limit. `max_file_calls` without mounts is accepted and has no effect, as in wasi-python.
- **`Defaults`.** It gains `file_calls: int | None = 10_000`, declared exactly as in wasi-python and checked by the same integer rule.
  - wasi-perl's `Defaults` has no `async_workers`, so `file_calls` is its fourth field where it is wasi-python's fifth. The declaration line is the same; only lines wasi-perl lacks are absent. The docs say to pass every field after the first three by keyword, which works with both.
- **`Completed`.** It gains `files: FileRecord | None = None`, after `truncated`: the record for a call with mounts, `None` otherwise. wasi-python's order is `truncated`, `files`, `tools`, `image_sha256` (`tests/public_api.txt:14-17`, READ). wasi-perl has no `tools`, and spec 022, which lands after this, adds `image_sha256` after `files`. So `files` is last until 022, and second to last after it, as in wasi-python.
- **Why:** the owner's rule. An embedder's provider and its call site then move between the two runtimes with no edit.
- **How it is held:** check A1 compares wasi-perl's listing of these names with wasi-python's own `tests/public_api.txt`, line for line.

### 2. The provider protocol is wasi-python's, unchanged

- **Paths.** Every path a provider sees is absolute within the mount, `/` being the mount itself, and normalized by the runner. A component is never empty, `.` or `..`, and never holds `/`, `\`, `:` or NUL.
- **Answers.** Each method returns a value or an `Errno`:
  - `stat` a `Stat` (kind `"file"` or `"dir"`; `stat("/")` must be a folder);
  - `list` an iterable of `(name, kind)` pairs, each name checked before the guest sees it;
  - `read` the whole file as `bytes`, `bytearray` or `memoryview`, or `EISDIR` for a folder;
  - each write-half method `None` for success.
- **A raised `OSError`** is the answer of its errno, matched by name on this host, or with no errno by its class (`FileNotFoundError` is `ENOENT`, and so on for the five other classes). An errno outside `Errno` is a fault.
- **A fault** (any other `Exception`, or an answer outside the protocol) gives the guest `EIO` and adds a `FileFault`. The run goes on. A coroutine answer is closed, never awaited.
- **A `BaseException` that is not an `Exception`** stops the call and is raised from `run`.
- **A writable mount's bytes** are staged and handed over whole: a create or a truncation at open as `write(path, b"")`, the content at close, at `fsync` and at the call's ending.
- **Why:** an embedder's provider must mean the same thing to both runtimes. This is wasi-python's 032 Decisions 3–5, 034 Decision 2 and 061, as they stand at `f9bf678`.

### 3. The code is wasi-python's, adapted and trimmed

Each module below is adapted from the wasi-python file of the same name at `f9bf678`. Its docstring says so, names the commit, and lists what was trimmed. AGENTS.md allows this for the sister project.

| wasi-perl module | from wasi-python | kept | trimmed |
|---|---|---|---|
| `mounts.py` | `mounts.py` | every public type; `_Mounted`, `_Open`, `_CallContext` (all nine methods), `_raised`, the errno table by name | nothing |
| `_fs_memory.py` | `_fs_memory.py` | all 29 signatures in `SHADOWED`, `FORWARD_SAFE`, every constant, the trampoline's WAT, the ABI checks, `_Memory` and its readers | `COUNTED`, which serves the open-file cap |
| `_fs_table.py` | `_fs_table.py` | `_Files` whole: the gate, the fault boundary, the read, stat and list answers, numbers and staging, write-back, owed buffers, `_follow`, `_unlink`, the walk, `release` | the session branches (`session`, `new_turn`, `walk`'s `ending`); the open-file cap (`open_files`, `held`, `open_now`, `_at_cap`, `_hold`, `_unhold`, `_renumbered`); `kind()`, which serves wasi-python's host-side `poll_oneoff` |
| `_fs_descriptors.py` | `_fs_descriptors.py` | every handler | the held-set updates in `fd_close` and `fd_renumber` |
| `_fs_paths.py` | `_fs_paths.py` | every handler, `_resolve`, the open table, the namespace calls, `_across`; the mount-side `path_open`'s bound `files.opened >= MAX_MOUNT_FILES` | `_forwarded_open`'s cap and host-side look: wasi-perl's look is the guest's (Decision 12), so a forwarded `path_open` goes straight to wasmtime. The mount-side `path_open`'s `or files._at_cap()` clause (`_fs_paths.py:146`, READ), which serves the open-file cap |
| `_wasmtime_ffi.py` (grows) | `_wasmtime_ffi.py` | `context_of`, `context_address`, `set_epoch_deadline`, `get_fuel`, `set_fuel`, `raw_func`, `PreparedCall`; and the import-time check: `PIN`, `NEEDED`, `version`, `missing`, `check` and the call of `check` (`_wasmtime_ffi.py:131-233`, READ) | the rest, which wasi-perl does not call. `NEEDED` is cut to the private wasmtime-py names that wasi-perl's `_wasmtime_ffi.py` reaches, today's `set_stdin_bytes`, `error` and `on_epoch` included, so an unknown wasmtime-py is refused with one `ImportError` naming every missing name |

- **Where the glue goes.** wasi-python's `_call.py` holds the shared host function, `_Call` and `_Context`; here they go in `runner.py`, which is already "adapted from `runner.py` and `_call.py`". The mount linker and the trampoline module go in `_engines.py`, beside the engines they are cached for. `_check_mounts` and `_siphash13` go in `_grants.py`, wasi-perl's counterpart of `_places.py`.
- **Why trim only these:** each trimmed branch serves a feature wasi-perl does not have (sessions, `max_open_files`, a host-side poll). Everything wasi-perl does have stays line for line, so a later shared package is a merge of two near-equal files, not a rewrite. A unification record (Acceptance 2) lists every remaining difference.
- **Why not grow study E's prototype:** it has no ABI checks, budgets, fault boundary or thread rule (E §3). wasi-python's code has all four, hardened by its reviews.

### 4. How a call with mounts is served

- **The shadows.** A call with at least one mount instantiates from the mount linker: wasmtime's WASI plus a shadow over each of the 29 calls in `SHADOWED`. perl.wasm imports 22 of them (READ: the import section of perl.wasm). The other seven are shadowed too, so no later binary can reach a provider number unseen.
- **Serving or forwarding.** A shadowed call that names a number in the call's mount table is served by the table. Any other is forwarded to wasmtime's own WASI through the trampoline, by a prepared `wasmtime_func_call` on the caller's context. A forward is fuel-neutral, as in wasi-python.
- **The shared function.** Each shadow is defined once per engine. It finds its call on a per-thread stack of calls and serves it only when the caller's store context is that call's. Otherwise it answers `EBADF`, and once its call has stopped or ended, `EIO`. It never raises into wasmtime-py. This is wasi-python's `_shared`.
- **Numbers.** Each mount is preopened after the library, `/dev` and the grants, in the order given. A file or folder opened in a mount gets a number from `2**30` up, which wasmtime never reaches.
- **The forward-safe calls** (`fd_prestat_get`, `fd_prestat_dir_name`, `poll_oneoff` and the socket calls) are not shadowed. On a mount's preopen, wasmtime answers them for the placeholder, as for a grant.
- **A call without mounts is unchanged.** It uses today's linker and runs no shadow. wasi-python's counted linker, which serves its open-file cap, has no counterpart here.
- **Why:** this is wasi-python's mechanism (032 Decision 1). The guest still imports only `wasi_snapshot_preview1`; no custom import is added.

### 5. The placeholder

- One placeholder per call: a folder `mount` inside a fresh `tempfile.mkdtemp(prefix="wasi-perl-")` folder (mode 0700, this user's), left empty and set read-only (0500) before the preopens. Every mount of the call is preopened on it, read-only.
- **Clean-up is two `rmdir`s, never a tree removal.** After the store is closed, however the call ended, the runner removes the placeholder, then its folder, each with `os.rmdir`. If either fails, it is left, and the call's result is unchanged.
  - Why: the folder lies in the system temporary folder, which a writable grant may include. wasi-python's lies under a folder no grant may reach (`_call.py:492-497`, READ). Here a guest with such a grant could rename the folder, or put entries where it was. An `rmdir` removes only an empty folder, so the runner never deletes what the guest put there.
  - Nothing reads the placeholder's contents (below), so such a change cannot reach a provider.
- **Nothing reads its contents.** Every path call on a mount's preopen (`path_open`, `path_filestat_get`, `fd_readdir` and the rest) is served by the table. Only `fd_fdstat_get`, `fd_fdstat_set_flags` and the forward-safe calls on the preopen reach wasmtime, and none of them reads an entry. So a placeholder that another process changed or replaced is never served.
- **Why per call, not per process:** a per-process folder could be removed by a temp cleaner between calls, and its absence would need its own check. A per-call folder costs one `mkdtemp` and one `rmdir`. wasi-python also makes one per call.

### 6. `mounts` is checked before any work

`_grants.check_mounts` runs with the other argument checks, before any file, engine or store is touched. In order, for each mount, naming its position and guest path:

1. `mounts` is an iterable and not a `str`, `bytes`, `bytearray` or a lone `Mount` (`TypeError`).
2. Each item is a `Mount` (`TypeError`).
3. The guest path is `/`, or passes wasi-perl's grant rule (`_grants.guest_path`): a `str`, valid UTF-8, absolute, normalized, with no `\` (`TypeError` or `ValueError`).
4. It is not at or under `/usr/local/lib/perl5` or `/dev`, the runtime's own folders (`ValueError`).
5. Its `writable` is `True` or `False` (`TypeError`).
6. The provider has a callable `stat`, `list` and `read`, and, when writable, `write`, `mkdir`, `remove`, `rmdir` and `rename`. None is a coroutine function (`TypeError`: "a provider answers synchronously").
7. No grant and no other mount has the same guest path (`ValueError`).

This is wasi-python's order (`_places._check_mounts`, `_places.py:232-270`, READ): the type, the guest path with its reserved folders, `writable`, the methods, then a duplicate. So a mount with two faults raises the same error in both runtimes. wasi-python's last rule, a mount at `/` beside `root=`, has no counterpart, since wasi-perl has no `root`.

What is allowed, and why:

- **A mount at `/`.** The provider is then the guest's whole filesystem, except what the library, `/dev`, a grant or a more specific mount owns. Relative paths reach it when `cwd` is `None`. wasi-python allows it when there is no `root=`, and wasi-perl has no root.
- **A mount above the runtime's folders** (`/`, `/usr`, `/usr/local`), though a grant there is refused. The grant rule exists because a host folder the caller controls would lie on the way to the library (spec 004). A mount puts no host folder there. Every path at or under a runtime folder spelled through the mount is `EPERM` by the table's nested-preopen rule, and wasi-libc sends the runtime's own paths to their own preopens by the longest prefix.
- **Mounts and grants nested either way.** A path spelled through an outer mount into an inner grant or mount is `EPERM` with no provider call.
- **No host-side rule applies to a mount.** It has no host folder. So it is not walked, not compared with the module cache or the runtime's folders, not entered in the registry of live grants, and not remembered as written-to for the cache rule (spec 012, M2). A writable mount binds none of spec 014's rule inputs: a call whose only writable place is a mount computes none of them.

### 7. `cwd` may be inside a mount

- `cwd` is a wasi-perl parameter that wasi-python does not have. Today it must lie inside a grant. After this specification it may also name a guest path at or under a mount's guest path. The longest matching guest path among the grants and mounts decides which one serves it, as wasi-libc decides.
- **The runner makes no provider call for it before the guest runs.** Provider code runs only while the guest runs, on the guest's behalf, inside the gate and the record. The check is by text.
- **The guest's start-up `chdir`** (`recipes/wasi-stubs.c`) then makes one provider `stat`, counted in the record: wasi-libc's `chdir` stats its target to check that it is a folder (INFERRED from wasi-libc's `chdir.c`, not read here; check D17 confirms it). If the provider does not answer a folder, the guest stays at `/`, as that stub does today when `chdir` fails. The docs say so.
- **Why:** vfs gives agents a working folder in their own tree. If wasi-python gains a `cwd`, the same rule fits it.

### 8. The provider's thread, Ctrl-C and stops

- **The provider runs synchronously on the thread that called `run`.** That is wasi-python's rule. In wasi-python the guest runs on a thread of its own and hands each provider call back to the calling thread. In wasi-perl the guest already runs on the calling thread, so `_CallContext.on_home` calls the provider directly: wasi-python's own path for a call with no home. What a provider observes is the same.
- **Ctrl-C** during a provider call is deferred: wasi-perl's handler only notes it (`_stop.watching_ctrl_c`). Once the provider returns, the shared function stops the guest, nothing is written back, and `run` raises `KeyboardInterrupt` after the store is closed.
- **A provider's own `BaseException`** (not an `Exception`) is recorded as the call's stop. The guest stops at its next function entry, nothing is written back, and `run` raises it.
- **The deadline** is checked before every provider call: once the ticker has passed it, the call answers `ETIMEDOUT`, no provider code runs, and the guest stops with "deadline". A provider's own time is outside the deadline and cannot be interrupted, as in wasi-python.
- **`_CallContext`'s answers here:** `ino_of` is SipHash-1-3 of the guest path; `trampoline` the engine's module; `exiting` is `sys.is_finalizing()`; `ctrl_c_pending` is the call's noted Ctrl-C; `stop_now` sets the caller's epoch deadline to 0; `cancelled` is always `False`; `deadline_passed` compares the ticker with the call's stop; `name_end` keeps the first name; `on_home` calls directly.
- **Why `cancelled` stays:** the gate's order (cancel, deadline, limit) is wasi-python's. Keeping the method keeps `_fs_table.py` unchanged, and a later `cancel=` plugs in with no change there.

### 9. Limits

- **Provider calls.** Every provider call counts against `max_file_calls`, whatever it answers. The call that would pass the limit is not made. The run ends with `trapped == "out of file calls"`, `exit_code` and `fuel_used` both `None`. The epoch callback learns the name from the call and returns it as the stop's error, so `_stop.word` recognises a third word.
- **Staged bytes.** The bytes of every open mount file and kept listing are at most `memory_bytes`, or 4 GiB when `memory_bytes` is `None`. A `read` or a growth past the bound answers `EFBIG`.
- **Open files.** The guest's own table holds at most 256 numbers (spec 009). It refuses an open past that with `EMFILE` before its look, so before any provider call. The table's own bound, `MAX_MOUNT_FILES = 1024`, stays as a second wall for a guest that bypasses the guest table, such as the raw WAT modules of the checks. wasi-perl has no `max_open_files` yet.
- **Host descriptors.** A call with mounts is still counted at 256 host descriptors (`_limits.reserve_descriptors`). A mount's preopen holds one; a mount's open file holds none.
- **Why:** these are wasi-python's limits and their defaults, minus `max_open_files`, which wasi-perl does not have.

### 10. Writable mounts

Wasi-python's spec 034 holds whole:

- nothing is written without `writable=True`, and a read-only mount answers every write-shaped call `EPERM` before any provider call;
- the open table, with a create or a truncation at open as `write(path, b"")`;
- the buffer, written whole at close, at `fsync` and at every ending that returns a result, in ascending provider number;
- an owed write-back when the gate stops a close;
- open descriptors following the guest's renames and removals;
- `link` and `symlink` `ENOTSUP`; times accepted and ignored once the path is found;
- `RefusedWrite` for every write-half call that did not succeed and every write-back owed but not made, each with its `at` and `cause`;
- nothing written back when `run` raises.

wasi-perl adds one fact: **a writable mount writes no host file.** Every write goes to the runner's buffer or to the provider.

### 11. The record

- `done.files` is a `FileRecord`: `calls`, all eight methods with zeros; the first 1,000 `faults`; the first 1,000 `refused`; and `refused_total`. It holds no times and no exception objects.
- It is made after the walk at the call's ending, so the walk's write-backs are in it.
- **Why:** wasi-python's record, so an embedder reads both runtimes' results with one piece of code.

### 12. The guest is unchanged, and how its calls look to a provider

No recipe changes. The guest's descriptor table makes calls a Python guest does not. Each reaches a provider through the unchanged protocol, so the API stays wasi-python's, but the counts a provider sees are Perl's:

- **Every open but an exclusive create costs a `stat` before its `read`.** That `stat` is the look (spec 010). On a mount the provider's `stat` answers it, and a mount holds no special file, so the look always lets the open through.
  - The look stays on mounts. Through preview1 the guest cannot tell a mount's preopen from a grant's, and every channel that could tell it is forbidden or visible: a custom import, a reserved environment variable the program would see, or a flag value wasmtime's ABI refuses.
  - Proposal P1 makes the extra call free in both projects.
- **`opendir` costs two `stat`s and one `list`.** The look stats the folder, then wasi-python's folder open stats it before it lists (032).
- **`unlink` costs a `stat` before the `remove`.** Perl `lstat`s each name first to refuse folders (READ: `doio.c`, `OP_UNLINK`).
- **`-i` opens the working folder.** With `ARGV_USE_ATFUNCTIONS`, which wasi-perl's `config.sh` enables, Perl calls `opendir(".")` once for each file it edits (READ: `doio.c`). When the working folder is in a mount, that is one more `stat` (two below the mount's own path) and one `list` of the whole folder, for each file.
- **A refused rename over a file the guest holds open is tried twice.** On `EACCES` or `EPERM`, the guest's `path_rename` wrapper retries once after closing and reopening the held file (spec 010, Decision 5). So the provider sees a `stat`, a `read`, a second `rename` and a second `RefusedWrite`. On a mount that the provider accepts, a rename over a held file succeeds the first time, so this happens only when the provider refuses.
- **`select` works on a mount file.** The guest answers a regular file's subscription itself, as always ready (spec 010). A subscription on a mount folder reaches wasmtime's `poll_oneoff` with a number it does not hold, and the poll answers `EBADF`.
- **`fileno` is small.** It is the guest's own number, under 256, not the provider number above `2**30`.
- **Why no guest change:** each of these is correct behaviour with a provider-visible cost, not a fault. A guest change needs a rebuild and a whole `perl_t` run. The counts are pinned by check D1, so a later change shows.

### 13. Study E's four switches

Study E's prototype had four switches beyond wasi-python's design. Each is decided against the owner's rule: an internal change is in only if it keeps everything a provider or a caller can observe exactly as wasi-python has it.

| switch | what it does | what a provider or caller would observe | decision |
|---|---|---|---|
| `stat_reads` | answers a `stat` with one `read`, and keeps the body for the open that follows | a `read` where the guest asked for a `stat`; a body fetched for `-s` or `-e` on a large file that is never opened | **out.** Proposal P1 gives the same saving with the provider choosing when |
| `memo` | keeps each `stat` answer and staged body by path for the call | fewer calls than the guest's own (`glob`'s `lstat`, then the look's `stat`, become one); an answer that misses another writer's change during the call | **out.** wasi-python's rule is "nothing is cached between opens; your provider may cache". The provider-side cache vfs plans (E rank 1) gives the saving under the provider's control. Proposal P2 records it |
| `defer_create` | a created file reaches the provider once, at its close | no empty first version at open; a refused create fails the guest's `close`, not its `open`; other readers of the provider do not see the new name during the call | **out.** It changes "a file exists from its open". Proposal P3 |
| `defer_close` | a closed new file is written at the call's end, under the name it has then | `-pi` reaches the provider as one write of the row, not a write of the temporary file and a rename; a refused write is reported `at="exit"`, and Perl's `close` no longer returns the provider's errno | **out.** Proposal P4 |

What wasi-python already does: none of the four. It asks the provider on every `stat`, `list` and `read` the guest causes, and it writes `b""` at a create (READ: `_fs_paths.py`, `_open_writing`, `_create`). It already serves `fd_filestat_get` from the staged bytes with no provider call, which the prototype also did.

### 14. Internal speed, with no observable change

- **What is in:** wasi-python's own: one mount linker and one trampoline module per engine; a prepared forward per call name; staged bytes copied into guest memory in one write. Study E's prototype had nothing beyond these and the four switches.
- **Spec 014, either order.**
  - If 014 lands first, the mount linker gets its own `InstancePre` per compiled module, made on the first call with mounts, as 014 makes one for today's linker.
  - If this lands first, the mount linker is instantiated per call, as today's is, and 014 adds the second `InstancePre` when it lands.
  - Either way a call without mounts keeps 014's path.
- **What is not in:** anything that changes a provider's calls or a result's fields. Those are the proposals below.

### 15. Docs

- `docs/mounts.md`, adapted from wasi-python's `docs/how-to/mounts.md`:
  - the protocol, with a dict provider and its record;
  - raising an `OSError`; a path is a name, not a host path;
  - where to mount, including `/`;
  - the limits;
  - how a mount differs from a grant;
  - time and threads;
  - the record;
  - writable mounts.
  It adds a section *What a Perl program costs a provider*, with D1's table, and says that a module the guest loads from a mount (`-I`, `use lib`, `do`) is the provider's code.
- `docs/sandbox.md`:
  - a mount opens no host path but the empty placeholder;
  - a writable mount writes no host file;
  - the provider is the embedder's code, called with normalized paths only;
  - staged bytes are host memory beside the guest's: budget three times `memory_bytes` for a guest with a writable mount, as wasi-python says.
- `README.md`: one *Mounts* paragraph and a link.
- `CHANGELOG.md`: an *Added* line under "Unreleased".

## Where wasi-perl differs from wasi-python

Every difference, its reason, and how wasi-python could adopt it.

| # | wasi-python | wasi-perl | why | how wasi-python could adopt it |
|---|---|---|---|---|
| 1 | `cancel=`, `run_async`, `Session`, `max_open_files` and `Defaults.open_files`, `root=`, `import_path=`, tools | absent; the table's branches for them are trimmed | wasi-perl does not have these features. Nothing wasi-perl has behaves differently | nothing to adopt; the branches return with the features |
| 2 | a mount's guest path may hold `\` | refused, as for a grant | wasi-perl's grant rule (spec 004) refuses `\`, and one rule serves both kinds | refuse `\` in `_places._guest_path`. The table already refuses a `\` in a component (`EINVAL`), so such a mount serves nothing useful |
| 3 | reserved path `/usr/local/lib` | reserved paths `/usr/local/lib/perl5` and `/dev` | each runtime's own folders | the same rule, each with its own folders |
| 4 | no `cwd` | `cwd` may lie in a mount, checked by text (Decision 7) | wasi-perl has `cwd`; vfs gives agents a working folder | the same rule if it gains `cwd` |
| 5 | the guest runs on its own thread; provider calls are handed to the calling thread | the guest runs on the calling thread; provider calls are direct | wasi-perl's guest has always run there | nothing: a provider observes the same thread |
| 6 | `trapped` may be "cancelled" | not: there is no `cancel` | as row 1 | nothing |
| 7 | `poll_oneoff` is the runner's own and answers mount numbers | wasmtime's; the guest answers mount files, and a mount folder's subscription is `EBADF` | wasi-perl has no host-side poll; the guest's table already answers regular files | nothing; a guest-side answer for folders would be a guest change |
| 8 | an open costs one `read` | an open costs a `stat` and a `read`; `opendir` two `stat`s and a `list`; `unlink` a `stat` and a `remove`; `-i` an `opendir(".")` (Decision 12) | Perl's guest makes these calls, and the API is unchanged | P1 and P5 shrink them in both |
| 9 | `Defaults.file_calls` is the fifth field | the fourth | no `async_workers` | nothing: by keyword, both are the same |

## Proposals for both projects (not built)

Each changes what a provider or a caller observes. So each needs a specification in both projects, agreed at once by their one owner, and none is built here. Each shape below is a sketch for those specifications.

- **P1. A `stat` that may carry the body.** A provider may answer `stat` with a `Stat` that also holds the file's bytes. The runner keeps them for the next open of that path in the same call, and serves it with no `read`, unless a write-half call touched the path in between. A provider that never fills the field sees exactly today's calls. The provider chooses when, so it can give the body only when it is small: the large-file cost the synthesis names under `stat_reads` (E §6, rank 3) is then the provider's decision.
  - In wasi-perl it halves the provider calls of every reading workload: a 500-row grep goes from 1,000 calls to 500 (EXECUTED, E §4.1, `stat_reads` column).
  - In wasi-python it helps `os.path.exists` followed by `open`, and pandas' checks before a read.
  - The shape (a new field on the frozen `Stat`, last, with a default) keeps positional use.
- **P2. A per-call memo**, as an opt-in `Mount` keyword. Not recommended while a provider-side cache can do the same under the provider's control (E rank 1; vfs spec 172 §3's staged-buffer cache). Recorded so the choice is not made twice.
- **P3. A create made at close**, as a per-mount choice. A new file reaches the provider once, at its close or the ending. vfs makes one version instead of two (READ: vfs's 2026-10-23 memo, finding (b)). The docs' "a file exists from its open" becomes the default, not the only rule.
- **P4. Write-back at the ending**, as a per-mount choice. `-pi` becomes one read and one write of the row (6 provider calls to 3, EXECUTED, E §4.1). A refused write is then reported `at="exit"`, which vfs already folds into a stage's result (READ: E §6, rank 4).
- **P5. A folder's listing made at its first `fd_readdir`.** The `stat` stays at the open, so `ENOTDIR` is still answered there. Perl's `-i` uses `opendir(".")` only for its descriptor, so its `list` would vanish. Python's `os.scandir` reads at once, so wasi-python's counts would not change.
- **P6. A ranged read.** vfs spec 178 waits on "a ranged provider call in wasi_python's mount protocol, which is that project's decision". It is that decision for both projects; whole-file staging stays until then.

## Checks (written first, seen failing; they count operations, never time)

Every check counts provider calls, WASI calls, bytes and errnos. None reads a clock. A check that needs the deadline advances the ticker by count, through the seam spec 004's deadline checks use.

The ports keep wasi-python's test names, so the two suites can be diffed. Each port says in its docstring which wasi-python test it ports, and at which commit.

### A. The API and the arguments (`tests/mounts/test_mount_api.py`; fast)

1. **`test_the_mount_api_is_wasi_pythons`.** A listing of wasi-perl's public names is made in wasi-python's `public_api.txt` format, by the same introspection, ported from wasi-python's `tests/harness/api.py` (which `tests/checks/test_v1_4_public_api.py` drives). It is compared with `tests/fixtures/wasi_python_public_api.txt`, a copy of wasi-python's file at `f9bf678` whose first line names that commit.
   - The blocks for `Errno`, `FileFault`, `FileProvider`, `FileRecord`, `Mount`, `RefusedWrite`, `Stat` and `WritableFileProvider` are equal, line for line.
   - `run`'s `mounts` and `max_file_calls` lines are equal to wasi-python's `run`'s.
   - `Completed`'s `files` line is equal, and stands right after `truncated`. Spec 022 extends this check to its own names, and to `image_sha256` after `files`.
   - `Defaults`'s block is wasi-python's with whole lines absent and none changed.
2. **`test_a_bad_mount_is_refused_before_any_work`** (port). One case per rule of Decision 6, plus:
   - which error wins when a mount has two faults, as wasi-python's order gives it: a relative guest path with `writable="yes"` raises the guest path's `ValueError`; a mount at `/dev` whose provider lacks `stat` raises the reserved folder's `ValueError`; `writable="yes"` with a provider that lacks `stat` raises `writable`'s `TypeError`; a duplicate guest path whose provider lacks `read` raises the method's `TypeError`;
   - a mount at the same guest path as a grant;
   - mounts at `/usr/local/lib/perl5`, `/usr/local/lib/perl5/x`, `/dev` and `/dev/x`;
   - `max_file_calls` of `True`, `-1`, `2**63` and `"10"`;
   - `Defaults(..., file_calls=-1)`.
   Each raises the named error, while the provider's call count stays 0 and `_engines.module` is never called (a counting patch).
3. **`test_nesting_is_allowed_and_the_mounts_are_plain`** (port). Mounts at `/`, `/usr` and `/work` inside it, beside a grant at `/work/g`, are accepted. The checked mounts are plain `Mount`s.
4. **`test_the_errno_values_are_preview1s`** and **`test_the_host_errno_table_is_by_name`** (ports).
5. **`test_a_call_without_mounts_is_unchanged`** (port, trimmed). A call without mounts never makes the mount linker or the trampoline, and invokes no shadow (counting patches). It computes no spec 014 rule input that it did not compute before.
6. **`test_a_writable_mount_binds_no_host_rule`.** A call whose only writable place is a mount runs none of `_runtime_folders`, `_runtime_files` and `_cache.every`. It enters nothing in the live-grant registry, and nothing in the set of folders once granted writable (counting patches).
7. **The wasmtime-py check** (ports of wasi-python's `tests/unit/test_wasmtime_ffi.py`: `test_missing_names_each_absent_name_in_order_and_all_lists_the_table`, `test_check_raises_one_import_error_naming_the_version_the_names_and_the_pin`, `test_the_runner_lets_the_adapters_import_error_through` and `test_the_bindings_the_runner_calls_exist`). `missing` names each absent name of `NEEDED`, in order. `check` raises one `ImportError` naming the version, every missing name and the pin. `run` lets it through. Every name in `NEEDED` exists in the pinned wasmtime-py.

### B. The table, through raw preview1 (`tests/mounts/test_mount_table.py`; fast)

The harness is wasi-python's `tests/harness/files.py`, ported to `tests/mounts/raw.py`: an in-memory `MemoryProvider` that records its calls, the same tree on disk, and a table-driven WAT module that calls each preview1 function directly. These checks run no Perl, so they run on every CI platform.

Every test of wasi-python's `tests/unit/test_runner_mounts.py` at `f9bf678` is ported, with its assertions, except:

- `test_a_counted_call_leaves_the_other_calls_native`, `test_a_call_without_mounts_is_unchanged_but_for_the_two_fixes`, `test_open_files_are_counted_on_a_call_without_mounts` and `test_mount_files_count_toward_the_open_file_cap`. They test the counted linker and `max_open_files`, which wasi-perl does not have. A5 and D7 stand in for them.
- In `test_a_cancel_or_a_passed_deadline_stops_the_provider_call`, `test_a_close_stopped_by_a_cancel_is_owed` and `test_the_write_half_is_gated`, the cancel cases. The deadline and limit cases are kept, and the owed-close case is driven by the deadline.

The ported tests include, by name:

- the guards: guest arguments never reach the provider; a path is normalized before the provider sees it (`..` above the descriptor's folder, an absolute path, a NUL, a long component and a more specific preopen's path are each refused with no call); a backslash or a colon in a component is `EINVAL`;
- the gate: the deadline stops the provider call; the file-call limit names the end, and the name holds under the epoch callback; the write half is gated;
- forwards cost no fuel and keep traps; an ABI error keeps the grant's fuel;
- every shadowed call has one handler; the mount linker shadows every call; renumber and close keep the table true; numbers are reused lowest first;
- the staging bound and the open limit; a listing is judged as it is gathered, counted as what it holds, kept and listed again from cookie 0; what another thread keeps is no part of a listing's count; dirent padding is zero;
- a folder `stat` comes from the mount; a folder open of a file is refused before any listing; `pread` writes no more than it judged; the set-times rows;
- faults: the record keeps the first thousand faults; a generator listing that raises answers its errno; a mapped `OSError` leaves nothing of itself;
- writes: a writable mount is asked for, and checked before any work; writes are bounded; an unlinked folder holds nothing; the runner answers the namespace calls it can; the walk is bounded and keeps its record; a refused rename or removal moves no descriptor;
- a finished call holds nothing.

### C. Parity with a grant, through raw preview1 (`tests/mounts/test_mount_parity.py`; fast)

1. **`test_raw_calls_answer_as_a_grant_does`** (port of wasi-python's `tests/unit/test_runner_grants.py`). Each of the 29 shadowed calls is made on a provider file, a provider folder and a mount's preopen, and on a read-only grant's, with the same arguments. The cases include unknown flag bits, an overflowing seek, whence 3, an empty path, a leading `/`, a path that is not UTF-8, a NUL, a two-`iovec` read and `SYNC`. The errno and every byte written are equal, with `dev`, `ino`, times and numbers masked, except wasi-python's listed deliberate differences.
2. **`test_raw_write_calls_answer_as_a_writable_grant_does`** and **`test_the_write_parity_asserts_each_systems_grant`** (ports). The same for every call that can write, with the per-system rows wasi-python asserts for Linux, macOS and Windows.

### D. Perl guests (`tests/mounts/test_mount_perl.py`; `slow`)

1. **`test_each_perl_operation_makes_its_provider_calls`.** Each row runs alone in a mount at `/work` with `cwd=None`. `done.files.calls` equals the provider's own counts and equals the row, method by method; every method not named is 0.

   | Perl | provider calls | source |
   |---|---|---|
   | `-e 1` | none | INFERRED |
   | `open "<"`, read to the end, `close` | `stat` 1, `read` 1 | EXECUTED (E §4.1, `ne_50k`) |
   | `-e`, `-f`, `-s`, `stat`, `lstat` of a file | `stat` 1 | INFERRED |
   | `opendir`, `readdir` to the end, `closedir` | `stat` 2, `list` 1 | EXECUTED (E §4.1, `grep_opendir_500`) |
   | `glob("/work/*")` over N files | `stat` 2 + N, `list` 1 | EXECUTED (E §4.1, `grep_glob_500`) |
   | `open ">"` of a new file, `print`, `close` | `stat` 1, `write` 2 (`b""`, then the bytes) | INFERRED |
   | `open ">>"` of a file, `print`, `close` | `stat` 1, `read` 1, `write` 1 | INFERRED |
   | `open "+<"`, no write, `close` | `stat` 1, `read` 1 | INFERRED |
   | `sysopen` with `O_CREAT \| O_EXCL` | `stat` 1, `write` 1 (`b""`) | INFERRED: no look on an exclusive create; wasi-python's `"x"` row |
   | `mkdir`; `rmdir`; `rename` | that method, 1 each | INFERRED |
   | `unlink` | `stat` 1, `remove` 1 | READ (`doio.c`) |
   | `-pi -e` over one file | `stat` 2, `read` 1, `write` 2, `rename` 1 | EXECUTED (E §4.1, `pi_50k`) |
   | `-pi -e` over one file, `cwd` at the mount's own path | the row above, plus `stat` 1 and `list` 1 | READ (`doio.c`, `opendir(".")`) |
   | `-pi -e` over one file, `cwd` below it | the row above, plus `stat` 2 and `list` 1 | READ, as above |

   A row marked INFERRED or READ is confirmed by its first run. If a run gives another count, the review sees it with the guest's trace (B's harness logs each shadowed call) and the reason, before the expectation is changed. That is a correction of an inference, not a weakened check.
2. **`test_each_workload_makes_its_provider_calls`.** Study E's workloads, rebuilt as fixtures: `ne_50k`, `lane_5m`, `slurp_5m`, `pi_50k`, `grep_args_500`, `grep_glob_500`, `grep_opendir_500` and `write_new`. Each makes exactly study E's column (a) provider calls (EXECUTED, E §4.1): 2, 2, 2, 6, 1,000, 1,503, 1,003 and 4. The output and the files left are byte-equal to the same program over a grant of the same tree.
3. **`test_a_mount_and_a_grant_see_the_same_wasi_calls`.** For each workload of D2, two runs:
   - the tree as a grant at `/work`, with an unused empty mount at `/unused`;
   - the tree as a mount at `/work`, with an unused empty grant at `/unused`.
   Both instantiate from the mount linker. A patch on the table's dispatch logs each shadowed call's name, served or forwarded. The two sequences of names are equal, as study E found (EXECUTED, E §1).
4. **`test_a_mount_serves_reads`** (port). At `/work`:
   - the bytes read equal the provider's;
   - `fileno` is under 256;
   - `select` on a mount file reports it ready;
   - a grant at `/data` in the same call reads as before;
   - `done.files.calls` equals the provider's own counts.
5. **`test_a_mount_answers_as_a_grant_does`** (port). The same tree as an in-memory `Mount` and as a read-only `Grant`. Each Perl row gives the same result, or the same `$!` text:
   - a missing file; a folder opened as a file; a file `opendir`ed;
   - a missing parent; `..` that escapes; `..` that climbs and returns;
   - opens to write, append, create and truncate; `mkdir`, `unlink`, `rmdir`, `rename`, `link`, `symlink`, `utime`, `truncate`; `readlink` of a file;
   - `seek` and `tell` past the end; a trailing `/` and `/.` on a file; a component of 256 bytes;
   - `stat`'s kind, and a file's size; a sorted listing.
   Every write row makes no provider call. wasi-python's deliberate differences are asserted as its table says: no times from `stat` of an open file, `readlink` `EINVAL`.
6. **`test_a_mount_at_the_root`** (port). A mount at `/`:
   - `/a.txt`, and `a.txt` with `cwd=None`, read the provider's file;
   - `use POSIX; use List::Util; use Data::Dumper;` load from the library, and the provider sees no path under `/usr/local/lib/perl5` or `/dev`;
   - an `open` of `/usr/./local/lib/perl5/strict.pm` is `EPERM` with no provider call;
   - a provider that serves its own `/usr/local/lib/perl5/strict.pm` is never asked for it, and `use strict` loads the library's;
   - `-e 1` makes at most a bound of provider calls. The bound is twice the count the builder measures, with the measured count in the landing notes, as wasi-python's check does.
7. **`test_mount_descriptors_are_bounded`** (port, adapted). A guest holds 300 mount files open at once. The first open past the guest table's cap fails with `EMFILE` ("Too many open files"). No provider call is made for it or for any later open in the loop. `read` is called exactly once for each file opened. The run exits 0. The placeholder is empty during the run, seen by the provider listing its host path, and gone after it.
8. **`test_the_file_call_limit_stops_the_run`** (port). With `max_file_calls=5`, a guest that runs `-e` in a loop ends with `trapped == "out of file calls"` and `exit_code` and `fuel_used` both `None`. Exactly 5 provider calls are made, and the line after the loop is not printed. `defaults().file_calls == 10_000`, and a default call that stats 10,001 times stops after exactly 10,000.
9. **`test_a_mount_keeps_its_deadline`** (port of the deadline half). A provider that advances the ticker past the deadline, by count, in its third call: the run ends "deadline" after exactly 3 provider calls. A guest printing in a loop while the ticker passes the deadline ends "deadline", never with a raw error.
10. **`test_a_mounted_run_out_of_fuel_counts_the_cap`** (port). With `fuel=` set, a guest that spins over a mount ends "out of fuel" with `fuel_used == fuel`.
11. **`test_the_errno_values_are_the_guests`** (port). A provider answering each `Errno` member to an `open` makes Perl's `$!+0` equal the member's value. `$!{NAME}` is true for each name Perl's `Errno` module defines.
12. **`test_a_raised_oserror_is_the_answer_of_its_errno`**, **`test_an_oserrors_class_answers_when_its_errno_cannot`** and **`test_a_write_halfs_raised_oserror_is_a_refusal`** (ports, Perl guests).
13. **`test_a_provider_fault_never_escapes`** (port). The faults: a provider that raises `ValueError`; that returns `None`, text, an object or a coroutine; whose listing's iterator raises; whose buffer export raises; that lists a bad name (`/`, NUL, `..`, a lone surrogate, 256 bytes, a duplicate, the kind `"link"`); that answers `size=True`; that answers `stat("/")` with a file; that returns more than the staging bound. For each, the guest gets `EIO` (`EFBIG` for the last), prints its `$!` and exits 0. `run` raises nothing. `done.files.faults` names each fault and no more, and no "never awaited" warning is emitted.
14. **`test_a_providers_base_exception_is_raised_from_run`** and **`test_an_oserror_that_names_no_member_stays_a_fault`** (ports).
15. **`test_calls_with_mounts_share_nothing`.** Two calls on two threads, each with its own provider, each see only their own paths and get their own records. A provider that itself calls `run` with a mount of its own gets its result, and the outer call's record holds only the outer provider's calls.
16. **Writable mounts** (ports of wasi-python's v1.4 checks, with Perl guests):
    - `test_a_writable_mount_serves_writes`: D1's write rows, `-pi`, `-pi.bak` (Perl's `linkat` answers `ENOTSUP` and it falls back to `rename`; READ: `doio.c`), and `done.files.refused` empty;
    - `test_refused_writes_are_recorded_on_exit_0`: a refused content write makes Perl's `close` false with `$!{EACCES}`; the guest exits 0; `refused` is exactly `RefusedWrite("/work", "write", ("/denied.txt",), Errno.EACCES, "close", "refused")`. The same for a refused create (`at == "open"`), `mkdir` and `rename` (`at == "call"`);
    - `test_open_files_follow_the_guests_renames_and_removals`: a file renamed while open is written back to its new name only; a file held open while another is renamed over it is never written back; `File::Temp`'s `tempfile` works; an open folder removed then remade answers `ENOENT` through the old handle;
    - `test_writes_land_at_every_ending`: a file opened with `open`, written with `syswrite` and not closed, then the guest ends by `exit 0`, `POSIX::_exit(3)`, an uncaught `die`, its fuel cap or its deadline. In each the provider holds the bytes after `run` returns, from exactly one content `write`. Ended by the file-call limit, no content `write` is made, and `refused` names the file once, `at == "exit"`, `cause == "limit"`;
    - `test_a_failed_write_back_at_the_ending_is_recorded`;
    - `test_unlinked_files_are_not_written_back_at_the_ending`;
    - `test_a_writable_mount_answers_as_a_writable_grant_does`: wasi-python's rows in Perl (create; an exclusive create of an existing name; create under a missing parent and under a file; open a folder to write; append; truncate; a write past the end leaves zeros; a write on a read-only handle; each `mkdir`, `rmdir`, `unlink` and `rename` case; `rename` and `link` to a read-only grant `EPERM`; `File::Copy`'s `copy` and `move`; `File::Path`'s `remove_tree`; `-s` after a write; a sorted listing after each change), each leaving the same tree as a writable grant;
    - `test_links_and_times_in_a_writable_mount`;
    - `test_a_call_that_raises_writes_nothing_back`: a provider whose call raises `KeyboardInterrupt`, and a Ctrl-C the test sends itself, each with a dirty file open: `run` raises, and no content `write` is made.
17. **`test_cwd_in_a_mount`.** `cwd="/work/d"` with a mount at `/work`: `open "<", "a.txt"` reads `/d/a.txt`, after exactly one start-up `stat("/d")`. `cwd` at a path the provider answers `ENOENT` for: the guest starts at `/`, and the `open` fails "No such file or directory". `cwd` at a path no grant or mount serves is still a `ValueError`.

### E. Escapes (`tests/escapes/test_mount_escapes.py`; `slow`)

They are written as `test_escapes.py`'s are: each guest prints `NAME ok` or `NAME refused <errno text>` for each attempt. Each also checks the host's files before and after, and checks the provider's log: every path it was given is normalized and within the mount.

1. **`test_dot_dot_cannot_climb_out_of_a_mount`.** Each is "Operation not permitted", as on a grant, with no provider call:
   - `/work/../etc/passwd`, `/work/d/../../x`;
   - `../x` with `cwd="/work"`;
   - `..` past the mount from an `opendir` handle's folder;
   - each in a read-only and in a writable mount.
2. **`test_an_absolute_path_never_reaches_a_provider`.** The guest's absolute paths reach the provider only as normalized paths within its mount. A raw `path_open` of `/a.txt` on a mount's descriptor is `EPERM`, with no provider call.
3. **`test_a_mount_over_a_grant`.**
   - A mount at the same guest path as a grant is a `ValueError` before any work.
   - A mount at `/data`, with a read-only grant at `/data/g`. Paths into the grant spelled through the mount (`/data/./g/x`, `/data/d/../g/x`) are `EPERM` with no provider call. `/data/g/x` reads the grant's host file. The provider never sees a path at or under `/g`.
   - A writable mount over a read-only grant cannot write into the grant: `/data/./g/new` and `/data/g/new` are each "Operation not permitted". A rename from the mount into the grant is `EPERM`, and into a writable grant `EXDEV`, each with no provider call.
   - The grant's host folder is unchanged.
4. **`test_a_writable_mount_writes_no_host_file`.** A writable mount at `/w`, and in a second run at `/`. The guest creates, writes, appends, truncates, renames, makes and removes folders, and runs `-pi`. It also tries:
   - `../` climbs;
   - the placeholder's own host path, and the test's `tmp_path`, spelled as guest paths;
   - `link` and `symlink` out of the mount (`ENOTSUP`).
   After the call:
   - every byte the guest wrote is in the provider;
   - the placeholder was empty during the call and is gone;
   - the parent of the placeholder's folder and `tmp_path` hold no new entry;
   - the library's and `/dev`'s files hash as before.

   A third run gives the guest a writable grant over the placeholder folder's parent, with `tempfile.tempdir` pointed at a folder of the test's. The guest renames the call's `wasi-perl-*` folder and makes a file of the same name in its place. After the call, `run` has returned normally, and both the renamed folder and the guest's file are still there: the clean-up removed nothing that was not an empty folder (Decision 5).
5. **`test_a_writable_root_mount_keeps_the_runtime_and_other_grants`** (port). A writable mount at `/`, with a read-only grant at `/data` and a writable mount at `/work/out` inside it:
   - writing into `/usr/local/lib/perl5/x`, `/usr/./local/lib/perl5/x`, `/dev/x` and `/data/x`, making a folder there, or removing or renaming into it, is `EPERM` with no provider call;
   - `rmdir` of `/usr` or `/usr/local`, and renaming `/usr` or `/work`, are `EBUSY` with no call;
   - renaming `/a.txt` to `/work/out/a.txt` is `EXDEV`, and to `/data/a.txt` `EPERM`, with no call;
   - `use POSIX` still loads from the library.
6. **`test_a_mount_refuses_a_name_a_host_could_reinterpret`**, **`test_a_drive_prefixed_name_stays_in_its_folder`** (the mounted case), **`test_a_trailing_slash_symlink_rename_cannot_escape`** and **`test_a_trailing_slash_rename_rule`** (ports, mount cases, Perl guests).
7. **`test_budgets_bound_a_hostile_guest`.** Each bound holds, each with a count:
   - **file calls:** a guest that stats in a loop stops at exactly `max_file_calls` provider calls;
   - **staging:**
     - a provider file of `memory_bytes + 1` bytes fails its `open` with "File too large";
     - a guest that appends to a mount file until it passes the bound gets "File too large", and the table's staged-byte count (a counting patch) never exceeds the bound;
     - a listing past the bound is `EFBIG`;
   - **open files:** D7's 300 opens;
   - **the walk:** with `max_file_calls` set so that the ending's write-backs pass it, the provider's total call count equals the limit exactly, and each unmade write-back is one `RefusedWrite` with `cause == "limit"`.

## Acceptance

1. The checks pass, and every existing suite stays green: `uv run pytest`, slow included, `ruff check` and `ruff format --check` (EXECUTED). The one-liner oracle, the escape tests and `perl_t` run unchanged, since the guest is unchanged.
2. **A unification record**, `context/research/<date>-mounts-beside-wasi-python.md`. For each adapted module it gives the `diff -u` line counts against wasi-python at `f9bf678`, with the command beside them (EXECUTED), and one line of reason for each hunk: a trimmed feature, a package name, or wasi-perl's guest-path rule. No hunk is left unexplained.
3. The docs of Decision 15, and `CHANGELOG.md` under "Unreleased".
4. A fresh agent's review lands. It is also a security review of the mount boundary, as wasi-python's 032 review was.

## Out of scope

- Study E's four switches, and proposals P1 to P6 (above), in either project.
- vfs's provider cache, warmed by argv and by `list` (E rank 1). It is vfs's to build, and this runner must not assume it (synthesis §3).
- `cancel=`, `run_async`, sessions, `max_open_files`, `root=` and `import_path=`. Each would come with the wasi-python code this specification trims.
- Any guest change: the look on mounts, the rename retry, a lazy `opendir`, a guest answer for a mount folder's `poll_oneoff`, and E's rank 6 (a cheaper descriptor-table start).
- Mounts on the command line. Neither project's command line has them: a provider is Python code.
- Async providers. They are refused, as in wasi-python ("a later specification").
- Moving the guest onto a thread of its own, as wasi-python's spec 078 did.
- Ranged reads and writes (vfs spec 178), and links in the protocol.

## Landing notes

Built on `spec/017-mounts` from dev at `f44e0fd`, with wasi-python at `f9bf678` (`/home/user/claygendron/wasi-python`). The artifact set is a fresh build of dev with `scripts/build.sh` in the worktree, under the machine lock: `perl.wasm` SHA-256 `71b362c0a61491e67e76ac161459daf3a3ad254b3b242e3cca7e1c42adc54307` (EXECUTED: `sha256sum build/artifacts/perl.wasm`). `perl.wasm`, `recipes/` and `downloads/SHA256SUMS` are unchanged.

### How the checks were seen failing

- The adapted modules were copied and wired into the runner before the checks were written, against AGENTS.md's order. To see each check fail all the same, every new check file was run against dev's own code: `git archive f44e0fd src` unpacked in a scratch folder and put first on `PYTHONPATH` (EXECUTED). `test_mount_api.py`, `test_mount_table.py`, `test_mount_perl.py` and `test_mount_escapes.py` each fail at collection there (`ImportError: cannot import name 'Errno' from 'wasi_perl'`). `test_mount_parity.py` fails 4 of 7 there; the 3 that pass are `test_the_write_parity_asserts_each_systems_grant`, which checks wasi-python's per-system table and no runner code.
- `tests/runner/test_arguments.py::test_the_public_api` failed on the new code until the eight names were added to its list (EXECUTED). The list grew; nothing was removed from it.
- `tests/runner/test_kept_checks.py::test_in_a_copy_mode_install_a_link_made_after_the_scan_is_still_refused` failed on the new code: 2 scans of the library where it holds 1 (EXECUTED). The engine seam is now imported only when `run` first needs wasmtime-py (so that `import wasi_perl` still loads no wasmtime), and its first import wrote a `.pyc` into the package's folder after the kept scan, which then no longer matched. Fixed in the code, not the check: `_runtime_folders` and `_runtime_files` import wasmtime-py through `_engines.wasmtime()`, which imports the seam too, before the scan.

### Choices left to the builder

- **The glue's home** (READ: Decision 3). `_Call`, `_shared`, `_Context` and the thread's stack of calls are in `runner.py`; the plain linker, the mount linker and the trampoline module are in `_engines.py`, cached per engine; `check_mounts` and `_siphash13` are in `_grants.py`.
- **Spec 014 landed first** (Decision 14). `_engines.linked(metered, module, mounts=True)` makes the mount linker's own `InstancePre` per compiled module, in a cache of its own, on the first call with mounts. The plain linker is now one per engine, shared by `linked` and the trampoline, and `_engines.forget()` drops it with the modules, so "ten calls on one module make one linker" still holds (EXECUTED: `tests/runner/test_fixed_cost.py`).
- **The stop's state** (INFERRED, then EXECUTED by B and D). A call's recorded stop and its named end live on its `_stop.Stop`, since the epoch callback is `_stop`'s. The callback stops the guest when either is set, and names the end itself when it passes the deadline, as wasi-python's callback sets `call.ended = "deadline"`. So `_shared`'s rule for a wasmtime error met after an end is named is wasi-python's unchanged.
- **The seam's import** (EXECUTED: `test_importing_wasi_perl_loads_no_wasmtime`, `test_the_runner_lets_the_adapters_import_error_through`). `_wasmtime_ffi` imports wasmtime-py at its top, as wasi-python's does, so `runner.py` and `_stop.py` import it only inside functions, and `_engines.wasmtime()` imports it after wasmtime-py. `version()` imports `importlib.metadata` inside itself: it runs only for a refusal, and that import is one of the slowest of a first call (spec 014). The unification record lists it as one wasi-python could adopt.
- **The placeholder** (READ: Decision 5). It is made 0700 by `os.mkdir`, then set 0500. On Windows the runner clears its read-only bit before the `rmdir`, since Windows will not remove a read-only folder (INFERRED; not run here). The clean-up of a folder a guest replaced is EXECUTED (`test_the_clean_up_removes_only_an_empty_folder`).
- **D6's bound.** `-e 1` with a mount at `/` makes 0 provider calls (EXECUTED), so the check's bound, twice the measured count, is 0.
- **D7's count.** The guest holds 250 mount files open before its table refuses the next (EXECUTED). wasi-libc's text for `EMFILE` is "No file descriptors available", not "Too many open files", so the check reads `$! + 0 == 33` and `$!{EMFILE}`. Likewise wasi-libc's texts are "I/O error" for `EIO`, "Cross-device link" for `EXDEV` and "Resource busy" for `EBUSY` (EXECUTED).
- **D11's `EISDIR`.** A `read` answered `EISDIR` is a folder, which Perl's `open "<"` takes as a folder handle, so that one member is answered to a `stat` and read by Perl's `stat` (EXECUTED).
- **D16's writable rows** run with `cwd` at the mount and the grant: File::Path's `remove_tree` stats its working folder. Its `chmod` is not in WASI, so both sides print the same warning; the check compares the two sides' stdout, stderr and tree (EXECUTED).
- **Ports with Perl guests.** A Python guest that left raw descriptors open at its end leaves Perl handles that Perl closes at its own exit, so the walk's checks end with `POSIX::_exit`, which closes nothing. Each open there costs the guest's look, a `stat`, so the walk's limit is 10 (4 looks, 4 creates, 2 write-backs) where wasi-python's is 6 (EXECUTED).
- **Cancel cases.** `test_a_cancel_or_a_passed_deadline_stops_the_provider_call`, `test_a_close_stopped_by_a_cancel_is_owed` and `test_the_write_half_is_gated` keep their names; their cancel halves are driven by the deadline, a provider moving `_engines.TICKER.count` past the call's stop.
- **CI.** The wheel job now runs `tests/mounts` beside `tests/runner`, `tests/escapes` and `tests/oneliners`, against the set `WASI_PERL_ARTIFACTS` names, and its gate counts every check of `tests/mounts` and `tests/escapes/test_mount_escapes.py`: a skip of any fails it. The raw-preview1 checks (B, C) need no Perl, so the lint job runs them too.
- **No speed memo.** The specification asks for the unification record, `../research/2026-11-24-mounts-beside-wasi-python.md`, and no timing; the provider-call counts are D1's and D2's, in `docs/mounts.md` and the checks.

### Found by CI: `cwd` is checked by text before the artifact set is found

CI's lint job runs the fast suite with no artifact set. There `test_a_bad_mount_is_refused_before_any_work[kw29-ValueError-mount]`, a `cwd` outside every grant and mount, raised `ArtifactsMissing` before its `ValueError`: the check lay in `judge`, after `_artifacts.checked()`. Locally it had passed only because a set was built. Seen failing the same way locally, in a clean export with no `build/` and `WASI_PERL_ARTIFACTS` unset (EXECUTED: 1 failed, 205 passed). `_grants.check_cwd` now takes the grants and mounts and refuses such a `cwd` by its text, with the other argument checks, before any work; `judge` still checks a grant's host folder for it. After the fix that run passes whole (EXECUTED: 206 passed, 390 skipped).

The refusals still made after the set is found are host rules, not argument checks: a grant's host folder that is missing or not a folder, and a `cwd` whose folder in a grant is missing (EXECUTED: each gives `ArtifactsMissing` when there is no set). wasi-python orders them the same way: its `_run_checked` finds the set, then walks the grants (READ: `_call.py`, `_run_checked`).

### Found by CI: a Windows grant answers as Windows does

On windows-2025 (CI run 45462992471, job 138692425791, EXECUTED there) four checks compared a mount with a grant and met Windows' own answers. The mount answered Linux's in each; each check now asserts the mount's answer on every host and the Windows grant's own row on Windows, each row naming its source:

- `test_a_mount_and_a_grant_see_the_same_wasi_calls[pi_50k]`: the grant adds the guest's rename retry, 12 calls after the first `path_rename`. Windows refuses a rename over a file the guest holds open, since wasmtime opens a Windows grant's files without FILE_SHARE_DELETE, and the guest retries (`CHANGELOG.md` 0.1.0; wasi-python's `tests/harness/files.py` note, READ).
- `test_a_mount_answers_as_a_grant_does`: `a.txt/.` opens the file and a 256-byte component is ENOENT on a Windows grant. wasi-python's grant parity leaves both rows to the host (`HOST_ROWS`, READ).
- `test_a_writable_mount_answers_as_a_writable_grant_does`: a Windows grant renames a folder over a file, which is wasi-python's per-system row "rename a folder over a file" (READ). It also answers EINVAL to an open of a folder for writing. wasi-python's `docs/how-to/mounts.md`, *On Windows*, says the same of opening a folder.
- `test_the_clean_up_removes_only_an_empty_folder`: a guest on Windows cannot rename the placeholder's folder while the call holds the placeholder open as a preopen, so the swap is EACCES there. The clean-up then removes both folders, and the check asserts that. The mount code is unchanged: on Windows the swap the check guards against cannot be made.

### The review, `../reviews/017-mounts-pr-2026-11-24.md`

- **H1, Windows.** Ported as wasi-python ports its write parity (`WRITE_MOUNT`, `WRITE_BY_SYSTEM`). For each row a Windows grant answers its own way, the check asserts the mount's answer on every host and the grant's answer per system, keyed by `sys.platform`; a system the table does not name is a `KeyError`. Every other row is parity. The same is done for D5's two host rows, D16's three rows and tree, D3's `pi_50k` (`RENAME_RETRY_BY_SYSTEM`) and the clean-up's move (`MOVE_BY_SYSTEM`). No skip is added. CI run 45465526868, on `606227d`, was green on every job, Windows included (EXECUTED: `gh api`).
- **H2, the clean-up followed a link the guest made.** I chose the descriptor, not wasi-python's private folder. wasi-perl has no folder that its grant rules keep out of every grant, and adding one would refuse a writable grant over the temporary folder, which spec 012's rules allow. `runner._Placeholder` opens the call's folder with `O_DIRECTORY | O_NOFOLLOW` right after `mkdtemp`, checks it is the folder `mkdtemp` made, makes `mount` (mode 0500) and removes it with `dir_fd=`, and removes the folder by name only while its name still gives that identity. Every removal is an `rmdir`. wasi-python could adopt it unchanged. On Windows, which has neither, the folder cannot be renamed while the store holds the placeholder (seen in CI), and the identity is checked by name.
  - The checks came first: E4's third run became five cases. The link case failed on the old code with `victim/mount` removed (EXECUTED). The two "moved" cases failed too, since the old clean-up never found its own placeholder after a move.
  - All five pass now. Each mutant of the new clean-up is caught (EXECUTED: the reviewer's `mutate.py` with four mutants added, in `scratchpad/mine/`): `rmtree` of `mount`, `rmtree` of the folder, removal by name, and no identity check.
  - The reviewer's own `placeholder-rmtree` mutant no longer applies: the `os.rmdir(path)` it edits is gone. The two `rmtree` mutants above stand in for it.
  - **Found by CI on `22e88f0`** (run 45471580229: the wheel jobs on ubuntu-24.04, ubuntu-24.04-arm and macos-15, which do not run as root). In the case "a file in the moved placeholder", the guest's write into the placeholder was refused, "Permission denied", where it had succeeded here. The cause: this machine runs the checks as root, and the placeholder is 0500, so only the superuser can write into it. I reproduced it here by running the check as `nobody` (EXECUTED: `setpriv --reuid=65534`, 1 failed). The check now expects the mode's answer by privilege, and the guest makes its own empty folder before the write, so both answers are still asserted:
    - as root, the guest's file is in the placeholder and is left;
    - otherwise the write is refused, and the empty placeholder is removed.

    Both pass, as root and as `nobody` (EXECUTED). As `nobody`, the guest cannot put anything in the placeholder, so the `rmtree` of `mount` has nothing extra to remove, and only a root run catches that mutant. The other mutants are caught either way.
    - As `nobody`, the mount, escape and runner checks give 456 passed and 2 errors (EXECUTED). The 2 errors are two existing checks that make folders inside the root-owned checkout, which `nobody` may not write.
  - **Review round 2.** A sixth case covers a tree removal as any user: inside the call's own folder, the guest renames `mount` to `aside`, then makes a `mount` of its own holding a file. The guest's file, `aside` and the folder are all left. With `rmdir("mount", dir_fd=)` mutated to a tree removal, this case fails as root and as `nobody` (EXECUTED: a copy in `/tmp` run through `setpriv --reuid=nobody`; the mutant fails 2 checks as root, 1 as `nobody`). On the real code it passes as both (EXECUTED). On Windows the inner rename is expected to be refused, like the move; that row is INFERRED until the first Windows run.
- **M1.** `no_work` in `test_mount_api.py` also fails on `_artifacts.checked` and `_cache.every`. Run against `590b8ca`'s code with a set built, it fails `kw29` as CI's lint job did (EXECUTED).
- **L1.** `_engines.wasmtime()` imports `_fs_table`, and so `_fs_paths`, `_fs_descriptors` and `mounts`, beside the seam. The copy-mode check's child now makes four calls, the second and third with a mount. It failed with 2 scans before the import moved, and passes with 1 (EXECUTED).
- **L2.** D17 adds `cwd="/work/g/missing"`, with a grant at `/work/g` inside a mount at `/work`, which is the grant's `ValueError`. It catches the `cwd-longest` mutant (EXECUTED).
- **L3.** Decided: a `cwd` at or under `/usr/local/lib/perl5` or `/dev` is refused before any work, even with a mount at `/` or `/usr` above it. wasi-python would refuse the same by its reserved-folder rule if it had `cwd`. A `cwd` naming a provider file is not refused: knowing it is a file needs a provider call before the guest runs, which Decision 7 forbids. `docs/mounts.md` says that `PWD` keeps the given text when the guest falls back to `/`. A2 has the 6 new cases; they failed before the rule and pass after (EXECUTED).
- **L4.** `test_a_shadow_serves_only_its_own_calls_store` calls a shadow from a second store while a call is on the stack. It answers `EBADF` and asks neither provider. It catches the `shared-context` mutant (EXECUTED).
- **L5.** `forget()` also clears the mount linkers and the trampolines, checked by `test_forget_drops_every_linker_and_trampoline`.

### Merged with dev at `50e0298` (specs 018, 019, 020)

- Conflicts in `CHANGELOG.md` and the wheel job's no-skip gate in `ci.yml`. Both sides are kept: the gate counts the module cases, `test_fast_perl.py`'s `test_guest_*`, `test_library_case.py`, the kept-scan checks, both escape files and every check of `tests/mounts`. The build job's gate for the CPAN modules and the library index is dev's, unchanged.
- `docs/fast-perl.md`'s `File::Find` section now says "a folder inside a grant or a mount", and that only a mount at `/` serves a call with no `cwd`, as spec 020's memo (§5) asks of whichever of 017 and 020 lands second. Checked by running both: `find` over `.` with `cwd` in a mount, and with a mount at `/` (EXECUTED).
- Rebuilt under the lock with 019's order (steps 20, 30, 40): `perl.wasm` SHA-256 `824d06c1…94258c0`. No mount check meets the library index. The index answers on the library's own preopen, and every path into the library spelled through a mount is still `EPERM` from the table (D6 and E5 pass, EXECUTED).

### Counts that differ from the specification's table

Each is a correction of an inference or of a prototype's count, with the guest's own calls as the reason; the review should see them (EXECUTED: `uv run pytest -m slow tests/mounts/test_mount_perl.py`).

- **A glob or an `opendir` of the mount's own folder costs one `stat` fewer.** The mount's own folder is listed with no `stat` (wasi-python's `_open_folder`, READ), so `glob("/work/*")` over N files is `stat` 1 + N and `list` 1. The table's 2 + N is a folder below the mount, as study E's `grep_glob_500` globbed `notes/`. D1 has both rows.
- **A `cwd` in a mount adds the start-up `chdir`'s `stat`.** `-pi` with `cwd` at the mount's own path is the bare row plus `stat` 2 and `list` 1, and with `cwd` below it plus `stat` 3 and `list` 1: the table left out the one start-up `stat` that Decision 7 names. D17 confirms that `stat` comes first, and once.
- **`write_new` makes 3 provider calls, not study E's 4.** E's prototype stats before a create that truncates (READ: `2026-11-24-perf-vfs-io/mount-prototype.patch`, `_open_writing`); wasi-python's open, adapted here line for line, makes one `write(path, b"")` (READ: `_fs_paths.py`, `elif creat and trunc`). So E's column (a) was one call above wasi-python's own design for this workload. The other seven workloads make E's counts exactly.
- **A refused rename** is tried twice only over a file the guest holds open: a second `rename` and a second `RefusedWrite`, as Decision 12 says (EXECUTED in `test_refused_writes_are_recorded_on_exit_0`).

### Results

- `flock …/machine.lock uv run pytest -m "not slow"`: 593 passed, 3 skipped (EXECUTED; the 3 skips are the suite's own, from before this change).
- `flock …/machine.lock uv run pytest -m slow tests/runner tests/escapes tests/mounts`: 131 passed (EXECUTED).
- `uv run ruff check` and `uv run ruff format --check`: clean (EXECUTED).
- `flock …/machine.lock uv run pytest -m slow tests/oneliners` with `WASI_PERL_ORACLE` set to perf-base's native perl: 209 passed (EXECUTED).
- The API check, `tests/mounts/test_mount_api.py::test_the_mount_api_is_wasi_pythons`, passes (EXECUTED).
