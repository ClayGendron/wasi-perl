# Review: 014, the runner's fixed cost (pull request)

Reviewer: a fresh agent (Claude Opus 5.5, `claude-opus-5-5`), which did not write this change.

Branch `spec/014-the-runners-fixed-cost`, head `83db344`, reviewed as `git diff dea44fa..HEAD` (the merge of `dea44fa` is specification text). Read: `AGENTS.md`; specification 014 with its landing notes; specifications 004 and 012; the diff of `src/` and `tests/`; the memo `context/research/2026-11-24-the-runners-fixed-cost-measured.md` with its scripts and raw results; wasmtime-py 51.0.0's `_instance_pre.py`, `_linker.py` and `_module.py` in the worktree's `.venv`. No code was edited.

## Verdict

Does not land. H1 must be fixed first: mapping the `.cwasm` (L7) lets a later call's writable grant rewrite or truncate native code the process is running. Before this change the same calls ran cleanly. Everything else is sound. The other levers keep 004's and 012's rules, the shared `InstancePre` isolates calls (threads included), the checks count operations, and the memo's numbers match its raw results.

## Findings, ranked

### H1. A mapped `.cwasm` can be written by a later grant in the same process, so a guest can crash the host or change the code it runs

- **Where.**
  - `src/wasi_perl/_engines.py:235`: `deserialize_file` maps the checked file.
  - `src/wasi_perl/_cache.py:123` (`every`): the folders no grant may reach.
  - `src/wasi_perl/runner.py:300`.
  - The specification's own argument, `context/specifications/014-the-runners-fixed-cost.md:35`.
- **What.**
  - Before this change, a loaded module was a copy in memory.
  - Now it stays mapped from the `.cwasm` for its whole life in `_engines._modules`. That entry is keyed by digest alone and is used by every later call, whatever cache that call names.
  - wasmtime maps the file `MAP_PRIVATE`. Pages that were never written stay the page cache's, so a write to the file shows in the running code, and a truncation raises `SIGBUS`. As far as I know, wasmtime's documentation of `Module::deserialize_file` requires that the file is not changed while it is mapped. That is from memory and was not checked here (INFERRED); the probes below show the effect.
  - The specification argues that the mapped inode stays the checked one because the runner never writes a `.cwasm` in place. That covers the runner, not a guest.
  - The grant rule protects only what `_cache.every` returns: this call's cache, the folder `$WASI_PERL_CACHE` names now, and the platform defaults. A folder an earlier call used as `cache_dir` is not among them. The same holds for a folder `$WASI_PERL_CACHE` named before the variable changed (INFERRED from `every`; not probed).
  - So a later call may grant that folder writable. Its guest can then write the very inode the process has mapped.
- **Evidence.** EXECUTED with perf-base's set, each probe in its own process. The scripts are in the reviewer's scratch folder as `probes/mapped_write.py` and `probes/mapped_rewrite.py`. Each probe runs three calls:
  1. `run([...], cache_dir=X)` compiles the module and writes its `.cwasm`.
  2. `_engines.forget()`, then `run([...], cache_dir=X)` loads it, mapped.
  3. `run(["-e", prog], grants=[Grant(X, "/c", writable=True)], cache_dir=False)`. The grant is accepted, and the guest writes the `.cwasm`.

  | probe | this branch | the base, `git archive dea44fa src` first on `PYTHONPATH` |
  |---|---|---|
  | `prog` truncates the `.cwasm` (`truncate("/c/<name>", 0)`) | the Python process dies with `SIGBUS`, exit 135, inside call 3 | call 3 prints "truncated", call 4 prints "4", exit 0 |
  | `prog` overwrites the `.cwasm` in place with `0xCC` bytes (`open +<`, `print`) | the Python process dies with `SIGSEGV`, exit 139, inside call 3 | call 3 prints "rewritten", call 4 prints "4", exit 0 |

  The second probe shows that the bytes a guest writes reach the host's executable code. That is a primitive for running code outside the sandbox, not only a crash.
- **Which rule it breaks.**
  - 012 M2: "a compiled module is never loaded from a folder a guest could write". A mapped module goes on being read from its folder for as long as the process holds it.
  - `AGENTS.md`: refusals are clear, not crashes.
  - The specification's own line, "Every rule of specs 004 and 012 holds unchanged."
- **Does the README cover it?** The README (line 23) says never to grant a cache folder "in any process". The runner itself enforces only the other order, a grant first and a cache after. Before this change, breaking that advice inside one process was harmless. Now it is an escape.
- **Fix.**
  - Keep a process-wide registry of every folder a module has been loaded from by name (or every folder `_cache.folder` accepted), kept by identity and chain like `_grants._ever_writable`, for the process's life.
  - Add that registry to what `_cache.every` returns, or to what `_grants.judge` checks. Then a grant at or inside such a folder, and a writable grant above one, is refused like a grant of today's cache.
  - Write a check first and see it fail: call 1 with `cache_dir=X`, `forget()`, call 2 with `cache_dir=X` (mapped), then call 3 granting X writable with `cache_dir=False` raises `ValueError` "reaches the module cache". Add a second case where `$WASI_PERL_CACHE` changes between the calls.
  - The alternative is to map only from folders that stay protected for the process's life. Either way, correct the specification's argument at line 35 in its landing notes.

### L1. The README should say that a loaded module stays tied to its file

- **Where.** `README.md:23`.
- **Why.** After H1's fix, a grant inside this process is refused. Another process cannot be refused, and its writable grant of this process's cache folder now crashes or changes this process while it runs, not only the next process that loads from the folder.
- **Fix.** One sentence: "A module loaded from the cache stays mapped from its file while the process runs."

### L2. Calls started at once each link the module, and the isolation check runs one call at a time

- **Where.** `src/wasi_perl/_engines.py:149-164` (`linked`); `tests/runner/test_fixed_cost.py:257`.
- **What.**
  - Sixteen threads making their first call at once made 3 Linkers (EXECUTED, `probes/threads.py`). One `InstancePre` is kept ("a thread that linked first wins"), so this is harmless.
  - The specification's check 4, "two calls … each see only their own", is run in sequence only.
  - The probe ran 96 calls at once on one `InstancePre`. Each call had its own grant (a third of them writable), its own `WHO` environment, and its own `memory_bytes` (32 MiB for odd threads, 1 GiB for even ones). Each call read its own file and saw its own environment. Each odd call failed its 40 MB allocation and each even one made it: 0 wrong of 96 (EXECUTED).
  - That agrees with wasmtime-py: `InstancePre.instantiate` passes only `self.ptr()` and the store's own context (READ, `wasmtime/_instance_pre.py`).
- **Fix.**
  - Put a threaded version of `test_calls_from_one_linking_see_only_their_own_grants_and_environment` in the suite, a few threads with the stand-in, checking outputs only.
  - Either link under the per-key lock `module` already uses for compiling, or say in `linked`'s docstring that a race may link more than once and keeps one.

### L3. Not this change's: the tree-check stamp is read after `SHA256SUMS` is parsed

- **Where.** `src/wasi_perl/_artifacts.py:220`.
- **What.** `checked` parses the list, then stamps the file again to record that the tree passed. A list replaced between the two is recorded as passed under the new stamp, against the old list's tree check.
- **Is it new?** No. The same window was there before this change (READ: the base `_artifacts.py`). `_read_sums` now holds its own parse to `before == after`, which does not close this window.
- **Fix.** Key `_trees` by the same `before` stamp `_read_sums` used (for example, return it with the list). This can wait for specification 015, which reworks these checks.

### Note. The memo's warm-up runs outside the lock

- **Where.** `context/research/2026-11-24-the-runners-fixed-cost-measured/measure.sh:18-19`.
- **What.** The two warm-up `first_call.py` runs are not under `flock`. They are not timed, so no number depends on them. Wrap them for consistency with the memo's "every timed script ran under the lock".

## What holds (checked and found sound)

1. **004 and 012's rules, apart from H1.**
   - **L3, computing a rule's inputs only when needed.**
     - The runtime rules in `_grants.judge` apply only to `g.writable`, and `_refuse_second_names` returns when nothing is writable. So skipping `_runtime_folders` and `_runtime_files` without a writable grant changes no decision.
     - `_cache.every` is pure, and the cache rule needs a grant to break it. So skipping it with no grant changes nothing.
     - The registry (`hold`), the descriptor budget and `_cache.folder`'s `granted_writable` check still run on every path (READ: `runner.run`, `_grants.py`).
   - **L2, parsing `SHA256SUMS` once per stamp.**
     - The key is the path plus `(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)`, so another set by `WASI_PERL_ARTIFACTS` or another path parses again.
     - `artifacts=` skips `checked()` as before.
     - A stale list can only refuse a changed `perl.wasm`, never admit one, since `digest` is stamped on its own.
   - **L1 and L8, the version read once.** It is safe across forks and threads; at worst a race reads it twice with the same result. The version gate (<51 and ≥52 refused) still runs on every call, now from the kept value.
2. **The checked descriptor.**
   - `_open_checked` keeps the old `read`'s checks.
   - `_fd_name` holds the descriptor open across wasmtime's open. It compares `(st_dev, st_ino)`, falls back to bytes when the name is missing or opens a decoy, and is `None` off Linux and macOS.
   - The swap checks show that a file renamed over the path after the check is not what loads, on both the mapped and the read way.
   - The descriptor guarantee holds. H1 is about the inode's contents after the load, not about which inode is loaded.
3. **The shared `Linker` and `InstancePre`.**
   - `define_wasi` reads each store's own WASI context.
   - Limits, fuel and the epoch deadline are set per store.
   - `_linked` holds the module, so its `id` stays unique. It is cleared by `forget()` and at exit, before `_modules`.
   - The concurrent probe in L2 found no leak.
4. **The checks count operations.**
   - Counted: metadata calls, `read_text` of `SHA256SUMS`, input functions, `Linker` constructions, `deserialize_file` against `deserialize` and the name passed, and the engine settings.
   - Each fails on regression: 19 of 21 were seen failing at `1e4a5e1`, per the landing notes.
   - The swap checks would fail if the path were reopened.
5. **The memo.**
   - Every figure carries EXECUTED, READ or INFERRED, and the commands are given.
   - I recomputed from `results/*.jsonl`:
     - the warm medians, for example bare 4.495/1.399/3.853/1.778 ms;
     - the first-call medians, 134.5/106.4/144.9/106.2 ms;
     - peak RSS, 74.8–74.9 against 43.9–44.0 MiB.

     All match the tables.
   - It reports honestly where the specification's numbers are missed: 1.78 ms against "1.3–1.5", and 31 against 33 MiB.

## Tests run (EXECUTED, in the worktree, under `flock /home/user/wasi-perl-wt/machine.lock`)

| command | result |
|---|---|
| `WASI_PERL_ARTIFACTS=…/perf-base/build/artifacts WASI_PERL_ORACLE=…/perf-base/build/native-install/bin/perl uv run pytest -m "not slow" -q` | 465 passed, 3 skipped, 242 deselected, 4 warnings (log: `build/logs/014-review-fast.txt`) |
| (same variables) `uv run pytest -m slow -q tests/runner tests/escapes` | 32 passed, 190 deselected (log: `build/logs/014-review-slow.txt`) |
| `uv run ruff check` | All checks passed! |
| `uv run ruff format --check` | 39 files already formatted |
| probes `mapped_write.py`, `mapped_rewrite.py` (this branch, then base `dea44fa` src) | branch: exit 135 (`SIGBUS`), 139 (`SIGSEGV`); base: exit 0 both (H1) |
| probe `threads.py` (16 threads × 6 calls, one `InstancePre`) | 0 of 96 calls saw another's grant, environment or memory limit; 3 Linkers made (L2) |

The macOS check, `test_on_macos_a_cwasm_is_mapped_by_its_dev_fd_name`, skips here. It is meant to run in CI's macOS wheel job, which runs `tests/runner` with `WASI_PERL_ARTIFACTS` set to the wheel's set. I did not see it run.

## Round 2

Reviewed `git diff 83db344..1444cf6`, by the same reviewer: a fresh agent (Claude Opus 5.5, `claude-opus-5-5`), which did not write the change.

### Verdict, round 2

Does not land. The round-1 findings are fixed as described, and the probe that showed H1 now ends in a refusal. But the new registry protects the path a module was mapped from, not the file that is mapped. Three ways remain for a later call's writable grant to reach that file, and each still kills the host with `SIGBUS`:

- the host renames the cache folder;
- a hard link to the file is made;
- two calls run at once.

The README sentence this round added, "the runner refuses any later grant that reaches a folder it loaded one from", is not yet true. R2-M1 and R2-M2 must be fixed. R2-L1 may be fixed, or answered in writing with the README narrowed.

### Round-1 findings, rechecked

| finding | state | evidence |
|---|---|---|
| H1 | the reported path is fixed | `_cache._map_from` records `cwasm.parent` before a name is given to wasmtime, and `every` returns the folders recorded. Both round-1 probes now raise `ValueError` "reaches the module cache", and the host survives (EXECUTED: `mapped_write.py` and `mapped_rewrite.py` exit 1 with that error). The new check covers `cache_dir` and a changed `$WASI_PERL_CACHE`, a read-only grant, and a writable grant above the folder. |
| L1 | fixed | The README sentence is there. Its claim is too broad until R2-M1 and R2-M2 are fixed. |
| L2 | fixed | `linked` links under `_linking`, checking again inside the lock. The threaded check passed in 5 of 5 runs here (EXECUTED). |
| L3 | fixed | `_read_sums` returns the stamp its parse is held to, or `None`, and `checked` records the tree under that stamp only. The new check goes with it. |
| Note | fixed | `measure.sh`'s warm-ups run under `flock`. |

### Findings, ranked

The probes for these findings are in `r2_ways.py`, in the reviewer's scratch folder. Each probe first compiles the module and writes its `.cwasm` to X, which is not mapped. It calls `forget()`, then runs the case below with perf-base's set. All were EXECUTED on `1444cf6`.

| case | what the probe does | result |
|---|---|---|
| `rename` | a call maps the module from X; the host runs `os.rename(X, Y)`; a call grants Y writable and its guest truncates the `.cwasm` | the grant is accepted, and the process dies with `SIGBUS`, exit 135 |
| `link` | a call maps the module from X; the host runs `os.link(X/<name>, G/x.cwasm)`; a call grants G writable and its guest truncates `x.cwasm` | the grant is accepted, and the process dies with `SIGBUS`, exit 135 |
| `race` | thread A calls with X granted writable and `cache_dir=False`, and pauses between `judge` and `hold` (a patched `reserve_descriptors` widens that window, nothing else); meanwhile call B runs with `cache_dir=X` and maps the module; then A goes on, and its guest truncates the `.cwasm` | B maps (`loaded` 1), A's grant is accepted, and the process dies with `SIGBUS`, exit 135 |

#### R2-M1. Recording a mapped folder and holding a writable grant are separate steps, so calls run at once can both pass

- **Where.**
  - `src/wasi_perl/_cache.py:58` (`_map_from`) and `:333` record the folder.
  - `src/wasi_perl/runner.py:297` checks grants against `every` in `judge`.
  - `src/wasi_perl/runner.py:310` and `src/wasi_perl/_grants.py:224` register the grant in `hold`.
- **What.**
  - A call's grants are checked against `every()`, with no lock, before the call registers them.
  - A call that maps runs `_cache.folder`'s `granted_writable` check before it records the folder, also with no lock.
  - So a call that is granting X writable and a call that is mapping from X can each pass the other's check, as the `race` probe shows.
  - 012 M2 promises that concurrent calls are covered, because a call registers its grants before its guest runs. That promise does not reach the new registry.
  - Before this change the same interleaving was harmless. B copied the `.cwasm` before A's guest ran, and kept nothing tied to the file.
- **Fix.**
  - Make "may this folder be mapped" and "may this grant be held" one step under `_grants._live_lock`:
    - `_map_from` records the folder only if no live or past writable grant reaches it (`granted_writable`, under the same lock), else the bytes are read;
    - `hold` refuses a placed grant that reaches a recorded folder, by the cache rule, so the check is made again at registration time.
  - Write a check first and see it fail: the probe's interleaving, forced by patching `reserve_descriptors`, must end with A refused, or with B reading the bytes, and the host alive.

#### R2-M2. The registry is keyed by path, so a host rename, or a retargeted link on the way, moves the mapped file out of its reach

- **Where.** `src/wasi_perl/_cache.py:58` (`_mapped_from: dict[Path, None]`) and `:149` (`every`).
- **What.**
  - `judge` turns each recorded path into identities when the call is made (`chain(c)`, `_exists(c)`).
  - After `os.rename(X, Y)`, the recorded X names nothing, or another folder, and a grant of Y passes (the `rename` probe).
  - The same happens when a link on the way to X is pointed elsewhere.
  - The guest cannot do either itself: any grant that could reach X is refused. But the host moving a work folder is ordinary.
  - The README now says such a grant is refused.
  - 012's `_ever_writable` is kept by identity and chain for the same reason.
- **Fix.**
  - Protect the file wasmtime maps, wherever its name now is:
    1. Keep each mapped module's checked descriptor open for the module's life. Such modules are few, one per digest and flavour; count the descriptors in the budget or bound them.
    2. When a call is judged, find each file's current name from its descriptor: `os.readlink("/proc/self/fd/N")` on Linux, `fcntl(F_GETPATH)` on macOS.
    3. Apply the cache rule to that name's folder.
  - A smaller fix that is not enough: keep the folder's identity and chain. That catches a grant at or inside the renamed folder, but not a writable grant of a new parent the folder was moved under.
  - Write a check first: map from X, rename X to Y, then a grant of Y (writable or read-only) and a writable grant of Y's new parent are refused.

#### R2-L1. A hard link to a mapped `.cwasm`, made after the load, is not looked for

- **Where.** `src/wasi_perl/runner.py` `_runtime_files`, and `src/wasi_perl/_grants.py:311` (`_refuse_second_names`).
- **What.**
  - `_open_checked` checks for one name when it opens the file.
  - A link made later puts the mapped inode inside a grant the cache rule does not see (the `link` probe).
  - Only the host can make that link, for example by a backup tool, `cp -al` or a deduplicator. But 012 M1 exists for exactly this kind of second name.
- **Fix.**
  - Add each mapped file, through its held descriptor (R2-M2), to the files `_refuse_second_names` protects.
  - Its walk already runs only when a protected file has more than one name, so a call pays nothing unless a link exists.
  - Check: link the mapped file into G, and a writable grant of G is refused "a hard link to the runtime's …".
  - Or answer in writing: the owner decides that host-made links to the cache are out of scope, and the README says so.

#### Note. The route for each choice

All three findings come from one design choice: mapping a file whose names the host can change. If the owner would rather not protect the mapped file wherever it goes, the other route is a specification change, agreed by the owner. It would say that host renames and links of a cache folder, and the same folder used as cache and grant at once, are the embedder's to avoid. The README sentence would then be narrowed to match. R2-M1 still needs a fix on either route, since 012 M2 promises that concurrent calls are covered.

### What else was checked and holds

- `_map_from` records the folder before `_fd_name`. A failed name, or a wasmtime error, leaves the folder protected: it errs toward refusing.
- Past `MAPPED_FROM_MAX`, the module is copied into memory and ties itself to no file. That is safe.
- The registry is not cleared by `_engines.forget()`. That is correct, since a module may still be held by a live call.
- The tests' many temporary caches stay apart from later tests' grants. The suite is green with the registry growing through the session.

### Tests run, round 2 (EXECUTED, in the worktree, under the machine lock)

| command | result |
|---|---|
| `WASI_PERL_ARTIFACTS=…/perf-base/build/artifacts WASI_PERL_ORACLE=…/perf-base/build/native-install/bin/perl uv run pytest -m "not slow" -q` | 470 passed, 3 skipped, 242 deselected, 4 warnings (`build/logs/014-review-r2-fast.txt`) |
| (same variables) `uv run pytest -m slow -q tests/runner tests/escapes` | 32 passed, 195 deselected (`build/logs/014-review-r2-slow.txt`) |
| `uv run pytest -q tests/runner/test_fixed_cost.py -k "at_once or mapped_from or bound"`, five times | 4 passed, each time |
| `uv run ruff check`; `uv run ruff format --check` | All checks passed!; 39 files already formatted |
| round-1 probes `mapped_write.py`, `mapped_rewrite.py` | `ValueError` "reaches the module cache"; the host survives |
| round-2 probe `r2_ways.py rename`, `link`, `race` | exit 135 (`SIGBUS`) for each |

## Round 3

Reviewed `git diff 1444cf6..72738d2`, by the same reviewer: a fresh agent (Claude Opus 5.5, `claude-opus-5-5`), which did not write the change.

### Verdict, round 3

Lands on the code, once the owner agrees the amended Decision 5 and the specification's Decisions and Checks say what was built (R3-M1).

The sealed copy removes the class of fault the first two rounds found. It does not only patch the cases those rounds showed:

- All five earlier probes now run to the end, and the host survives.
- With root's `/proc/self/map_files`, the mapped copy refuses every write.
- wasmtime refusing the copy falls back to the bytes.

R3-L1 to R3-L3 may be fixed in one commit, or answered in writing. R3-M1 is the owner's to decide, and it must be settled before the merge.

### The four questions

1. **Can anything still change the bytes wasmtime runs? No way remains after the load.**
   - I loaded the module, then opened the mapped copy read-write through `/proc/self/map_files/<range>`. That is a way only root has, and it gets past any folder or descriptor rule.
   - Every change was refused with `EPERM`: `pwrite`, `ftruncate` down and up, `fallocate` with `PUNCH_HOLE|KEEP_SIZE`, a shared writable `mmap`, and `F_ADD_SEALS` (EXECUTED: probe `r3_seals.py`).
   - `posix_fallocate` within the size was allowed. It changes no byte.
   - The next call ran: `print 2` gave `2`.
   - **During the copy** there is a window, and it was there before this change. `sendfile` reads the checked inode, so anything that can write that inode in place while it is copied changes the copy. That means a process of the same user, or the guest of a concurrent call whose writable grant was judged before this call's `_cache.folder` check and registered after it. The 0.1.0 read path has the same window, and the copy now takes 7 ms where the read took 22 ms. It is not this change's (P1 below).
2. **Does wasmtime's open of the copy's `/proc/self/fd/N` name work as intended? Yes.**
   - `_fd_name(copy)` compares the name's inode with the copy's.
   - At the moment wasmtime opens the name, the check `test_on_linux_a_cwasm_is_mapped_from_a_sealed_copy` sees a link count of 0 and all four seals.
   - `/proc/self/maps` shows the copy mapped `r--p` as `/memfd:wasi-perl-module (deleted)` (EXECUTED).
   - wasmtime keeps its own read-only descriptor of the copy for the module's life (fd 7 in the probe). That is harmless: the seals are on the inode, so they bind every descriptor.
   - **If the kernel refuses an executable memfd**, the fallback is what saves the call. That happens with `vm.memfd_noexec=2`, where `memfd_create` without `MFD_EXEC` fails, or under a security module that denies execute on shared-memory files, where wasmtime's mapping fails. Both are INFERRED: here `vm.memfd_noexec` is 0, so neither was run.
3. **Is swallowing an exception from `by_name` safe? Yes.**
   - The fallback deserializes the same checked bytes, read again from offset 0 through the checked descriptor.
   - An error from that path is raised. A stale `.cwasm` therefore still ends in `_miss`'s recompile, after one extra attempt.
   - `KeyboardInterrupt` is not an `Exception`, so it is not swallowed.
   - Both descriptors are closed on every path.
   - EXECUTED: with `deserialize_file` patched to raise, a call printed `3` with `{'compiled': 0, 'loaded': 1, 'written': 0}`.
   - The cost of swallowing: a lasting failure would hide a lost saving, not a safety fault. The Linux check that the name is used catches that on CI's kernels. The fallback itself has no check in the suite (R3-L2).
4. **Is removing the registry tests a weakening? No, it is a fair replacement.**
   - Those tests checked that a refusal was given. The refusal is gone on purpose: a grant of an earlier cache folder is treated as in 0.1.0.
   - The guarantee they stood for, that nothing done to the cache file after the load reaches the running module, is now checked directly. `test_a_cache_file_changed_after_the_load_does_not_reach_the_running_module` covers a host truncation, a host rewrite with `0xCC`, and a guest rewrite through a later grant, each in a child process.
   - That covers round 2's rename, link and race too. Each of them ends in writing the same inode.
   - I ran those checks against `83db344`'s `_cache.py`, with the rest of `72738d2`'s `src/` first on `PYTHONPATH`. They failed: each child died with no output, and the sealed-copy check failed. With the branch's own code they pass (EXECUTED).
   - One weakening of an existing check remains, which this commit caused (R3-L1).

### Decision 5, narrowed: does it still meet the intent?

- **What was agreed.**
  - Decision 5 maps the checked file on Linux and macOS.
  - Check 5 has a macOS `/dev/fd/` check.
  - The intent gives a first call of about 100 ms, and L7 a saving of 22–28 ms and 33 MiB.
- **What is built.**
  - Linux maps a sealed copy: a first call of 113.9 against 134.6 ms, saving 20.7 ms, of which the load is 15.4 ms.
  - Peak memory is 38.7 against 75.0 MiB, saving 36.3 MiB.
  - macOS and Windows read the bytes as in 0.1.0, so macOS gains nothing from L7.
  - I recomputed every s3 median from `results/*.jsonl`, and each matches the memo.
- **My judgement.** The intent's main promise, that every rule of 004 and 012 holds, is now met. Mapping the file was shown to break it. The speed promise is met in part: the warm call is fully met (1.22 ms), the first call partly. The memory saving is exceeded. So the narrowing serves the intent.
- **Why it must go to the owner.** It changes an agreed decision, an agreed check and a number in the intent. `AGENTS.md` says the owner agrees a specification before it is built. Landing notes are not the place to change a decision.

### Findings, ranked

#### R3-M1. The specification's Decisions and Checks still describe the mapping this commit removed

- **Where.** `context/specifications/014-the-runners-fixed-cost.md`:
  - line 11, the intent's "about 100 ms";
  - lines 29–35, Decision 5: `/dev/fd/N` on macOS, and "the mapped inode is the checked one for the module's whole life", which round 1 disproved;
  - line 60, Check 5's macOS bullet.
- **What.** Only "After the review (round 2)" (lines 145–177) says what was built. A reader of the Decisions gets the design that failed.
- **Fix.** The orchestrator asks the owner with AskUserQuestion. The options, the recommended one first:
  1. Agree Decision 5 as built: a sealed `memfd` copy on Linux, bytes elsewhere, a first call of about 114 ms.
  2. Drop L7: bytes everywhere, about 135 ms.
  3. Keep mapping the file on macOS, which rounds 1 and 2 showed unsafe. Not recommended.

  Then amend the intent's number, Decision 5 and Check 5 in place, with the status line naming the amendment.

#### R3-L1. The swap check's mapped case now swaps after the copy, so it no longer tests a swap between the check and the copy

- **Where.** `tests/runner/test_fixed_cost.py:463` (`swap_then_name` wraps `_cache._fd_name`).
- **What.**
  - `_fd_name` is now called on the copy, after `_sealed_copy` has run, so the swap comes after the bytes are taken.
  - If `_sealed_copy` were changed to reopen the path instead of reading the checked descriptor, the mapped case would still pass.
  - The read case still swaps before the bytes are read.
- **Fix.** In the mapped case, wrap `_cache._sealed_copy`: swap, then call the real one. Keep `_fd_name` as the hook for the read case.

#### R3-L2. The fallbacks of `_sealed_copy` and `load` have no check

- **Where.** `src/wasi_perl/_cache.py:299-324` (`_sealed_copy`) and `:345-353` (`load`).
- **What.** Each of these falls back to the bytes:
  - `memfd_create` refused;
  - a short `sendfile`;
  - seals refused, or `F_GET_SEALS` short of the four;
  - `by_name` raising.

  None of them is in the suite. My probe ran the last one by hand.
- **Fix.** One parametrised check that patches each step to fail and asserts `{'compiled': 0, 'loaded': 1, 'written': 0}` through `by_bytes`, with no copy left open. Count the `memfd` descriptors in `/proc/self/fd` before and after.
- **Nit.** The `# noqa: BLE001` at line 349 names a rule ruff does not select (`pyproject.toml:61`). Drop the code, or keep only the reason.

#### R3-L3. The memo says the copy "lives in the kernel's shared memory … either way", but a mapped file was in the page cache

- **Where.** `context/research/2026-11-24-the-runners-fixed-cost-measured.md`, §7's last bullet (line 172).
- **What.**
  - A mapped `.cwasm` was page cache: shared by every process that mapped it, and reclaimable.
  - The copy is shared memory owned by one process: about 15 MB per process for the module's life, swap-backed and not shared.
  - So K worker processes now hold K × 15 MB where mapping held one copy. That is still less than 0.1.0's two copies per process.
- **Fix.** Say so, with the label INFERRED. Also move §7's table into §1, so the memo's answer is the code that lands.

#### P1. There before this change, and not this change's: an in-place write of the checked inode during the load reaches the module

- **Where.** `src/wasi_perl/_cache.py` `folder` (`granted_writable`), against `runner.py`'s `judge` → `hold` steps.
- **What.** Round 2's race, shifted to before the copy. Call A's writable grant of X is judged, not yet held. Call B passes `folder(X)`. A's guest then writes the `.cwasm` in place while B copies or reads it.
- **Is it new?** No. 0.1.0's read path has the same window, so this does not block 014.
- **Fix.** Specification 015, or a follow-up: make `_cache.folder`'s check and `hold`'s registration one step under `_grants._live_lock`, and refuse a grant that reaches a cache a live call is loading from.

### Tests run, round 3 (EXECUTED, in the worktree, under the machine lock)

| command | result |
|---|---|
| `WASI_PERL_ARTIFACTS=…/perf-base/build/artifacts WASI_PERL_ORACLE=…/perf-base/build/native-install/bin/perl uv run pytest -m "not slow" -q` | 470 passed, 3 skipped, 242 deselected, 4 warnings (`build/logs/014-review-r3-fast.txt`) |
| (same variables) `uv run pytest -m slow -q tests/runner tests/escapes` | 32 passed, 195 deselected (`build/logs/014-review-r3-slow.txt`) |
| `uv run pytest -q tests/runner/test_fixed_cost.py -k "sealed or after_the_load or swapped or without_proc or another_file"` | 8 passed |
| the same `sealed or after_the_load` checks with `83db344`'s `_cache.py` (scratch copy of `72738d2`'s `src/` first on `PYTHONPATH`) | failed: the children died with no output; the sealed-copy check failed |
| `uv run ruff check`; `uv run ruff format --check` | All checks passed!; 39 files already formatted |
| probes `mapped_write.py`, `mapped_rewrite.py`, `r2_ways.py rename`, `link` and `race` | each exits 0: the guest's truncation succeeds, and the next call prints its output |
| probe `r3_seals.py` (root, `/proc/self/map_files`) | write, truncate, grow, punch hole, shared writable map and unseal all refused with `EPERM`; the next call ran; `deserialize_file` patched to raise fell back to the bytes, with `loaded` 1 |

## Round 4

Reviewed `git diff 72738d2..1e11304`, by the same reviewer: a fresh agent (Claude Opus 5.5, `claude-opus-5-5`), which did not write the change. The owner agreed the narrowed Decision 5 on 2026-11-24 ("Sealed copy (Recommended)"), as relayed by the orchestrator.

### The round-3 findings

| finding | state | evidence (READ: the diff; EXECUTED: the runs below) |
|---|---|---|
| R3-M1 | fixed | The Status names the owner's answer to the narrowed decision. The Intent gives about 114 ms against about 135 ms, citing memo §7. Decision 5 is rewritten in place: the copy, the name, why a copy, the fallback, other systems, and the saving with the memory cost per process. Check 5 now names the sealed copy, the three after-the-load cases, the swap before the copy, the three fallbacks, and macOS reading the bytes. Nothing in the Decisions or Checks still describes the dropped mapping. |
| R3-L1 | fixed | The swap now happens in a hook on `_sealed_copy`, after the check and before any read, on both the mapped and the read way. A `_sealed_copy` that reopened the path would copy the decoy, and the check would fail (INFERRED from the test). |
| R3-L2 | fixed in the main | `test_without_a_sealed_copy_the_bytes_are_read` refuses `memfd_create`. `test_a_sealed_copy_wasmtime_refuses_falls_back_to_the_bytes` makes `deserialize_file` raise `WasmtimeError`. Both assert that the bytes load and nothing is compiled. The `noqa` is gone. Not covered: a short `sendfile`, refused seals, and a count of copy descriptors left open. Each of these takes the same `os.close(copy)` / return-`None` path the first check runs (READ: `_sealed_copy`), so this is acceptable. |
| R3-L3 | fixed | §7 now says the copy is about 15 MB per process, charged to the process, where a mapped file was shared page cache. The claim is labelled INFERRED. |
| P1 | left, as there before this change | Agreed: it is in 0.1.0's read path too. It belongs to specification 015 or a follow-up. |

### New in this round

- **Nit.** `src/wasi_perl/_cache.py:349-351`: the formatter wrapped the comment as `except (\n Exception\n ):  # …`. Fix: put the reason on its own line above `except Exception:`. It is cosmetic and blocks nothing.

### Tests run, round 4 (EXECUTED, in the worktree, under `flock /home/user/wasi-perl-wt/machine.lock`)

| command | result |
|---|---|
| `WASI_PERL_ARTIFACTS=…/perf-base/build/artifacts WASI_PERL_ORACLE=…/perf-base/build/native-install/bin/perl uv run pytest -m "not slow" -q` | 472 passed, 3 skipped, 242 deselected, 4 warnings (`build/logs/014-review-r4-fast.txt`) |
| (same variables) `uv run pytest -m slow -q tests/runner tests/escapes` | 32 passed, 197 deselected (`build/logs/014-review-r4-slow.txt`) |
| `uv run pytest -q tests/runner/test_fixed_cost.py -k "sealed or swapped or after_the_load"` | 8 passed |
| `uv run ruff check`; `uv run ruff format --check` | All checks passed!; 39 files already formatted |

### Final verdict

Lands.

- Every finding of rounds 1 to 3 is fixed, or answered in writing.
- The specification now states what was built, and the owner agreed it.
- The rules of 004 and 012 hold. A cached module runs from a sealed copy that no grant, rename, link or concurrent call can change. Elsewhere it runs from bytes read through the checked descriptor.
- The checks count operations, and each was seen failing against the design it guards.
- The suites are green.

P1 and the formatting nit are left for later; neither blocks the merge.
