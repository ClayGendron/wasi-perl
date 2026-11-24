# Review: the speed research and specifications 013–016 (2026-11-24)

Reviewer: an agent (Claude Opus 5.5, `claude-opus-5-5`), a fresh sub-agent of https://claude.ai/code/session_01ThzDEUwftdmy8zWBJK1gWa; not the author of any file in this change.

- **Scope:** `git diff origin/dev...HEAD` on `claude/compassionate-volta-q5apn8` at `1c062ea`: 292 files, +39,958 lines, all under `context/`. I read the four specifications whole, the synthesis whole, its correction commit `0c9a6dd`, the roadmap's new section, and the parts of studies A, B, C and D that the specifications cite. I checked 013 against `recipes/wasi-fd-table.c` and against `util.c`, `perl.c` and `pp.c` in `/home/user/wasi-perl-wt/perf-base/build/target/` (READ). I checked 015 against `src/wasi_perl/_grants.py` and study A's `patches/L5-runtime-identities-per-tree-stamp.patch` (READ).
- **Tests:** `uv run ruff check src tests` and `uv run ruff format --check src tests` are clean. `uv run pytest -m "not slow" -q` gave `101 passed, 345 skipped, 242 deselected` (no `WASI_PERL_ARTIFACTS` set). EXECUTED. The change touches no code, so this only confirms the base is green.
- **perl.wasm's imports today:** I listed them with wasmtime-py's `Module.imports` on perf-base's `build/artifacts/perl.wasm`. There are 32 `wasi_snapshot_preview1` functions, and `random_get` is not one of them. EXECUTED.

## Findings

### High

**H1. 013's virtual device is not fully specified for the descriptor table it lives in.** `013-seeds-from-the-hosts-randomness.md:33-42`.
- In `recipes/wasi-fd-table.c`, every open file holds a host descriptor, `files[].real`.
  - Every `PASS_FD` wrapper hands `.real` to the host (`:157-207`).
  - `release()` closes `.real` on the host unless it is `GONE` (`:139-146`).
  - `poll_oneoff` stats `.real` and copies it into the host's subscriptions (`:533-552`).
- The specification sets the answers of `fd_read`, `fd_pread`, `fd_seek`, `fd_tell`, `fd_write`, the open, close, dup, fcntl and the three stats. It does not say:
  - what `.real` holds for the virtual file;
  - that close must not reach the host;
  - what `select` and `poll_oneoff` report for the descriptor. Natively it is readable at once.
  - what `fd_fdstat_set_flags`, `fd_advise`, `fd_allocate`, `fd_sync`, `fd_datasync`, `fd_filestat_set_size`, `fd_filestat_set_times`, `fd_readdir` and `fd_pwrite` answer;
  - what a `path_*` call or an `openat` answers when it uses the descriptor as its folder. Natively that is `ENOTDIR`.
- The rights that `fd_fdstat_get` reports decide `isatty`. wasi-libc's `isatty` treats a character device whose rights lack `FD_SEEK` and `FD_TELL` as a terminal. With rights like that, `-t $fh` would be true on `/dev/urandom`, and PerlIO would line-buffer it, which native Perl does not do. Spec 016's `isatty` decision depends on 013 and would inherit this.
- **Fix:**
  - Name a sentinel for `.real`, as `GONE` is today.
  - Add one rule for every call not listed, for example `EBADF`, and `ENOTDIR` for a path call that uses the descriptor as its folder.
  - Make `release()` skip the host for the virtual file.
  - Say that `poll_oneoff` answers the descriptor as readable inside the guest.
  - Give the exact `fs_rights_base` that `fd_fdstat_get` reports, chosen so that `isatty` is false, as it is natively.
  - Add checks for `-t`, `select` and `close`.

**H2. 015's argument for the tree record does not hold on Windows.** `015-the-runners-checks-kept-across-calls.md:39`, and check 3(b) at `:52`.
- The specification says: "A user can set a file's mtime but not its ctime."
- On Windows, Python's `st_ctime_ns` is the file's creation time (CPython 3.12 and 3.13 deprecate it there but still return it). `SetFileTime` lets the file's owner set it, together with the mtime.
- So an in-place edit that keeps the size, with both times put back afterwards, keeps the stamp, and the next process trusts the record and does not rehash the file.
- Check 3(b) uses `os.utime`, which sets only atime and mtime, so it cannot catch this.
- The project runs on Windows: CI runs on every platform, and the descriptor table handles Windows renames.
- **Fix:** use the record only where ctime cannot be set by a user: Linux and macOS. On Windows, hash every file as today. Add a check that the record is never read on Windows, and say so in `docs/sandbox.md`.

### Medium

**M1. 013 says the device behaves "as native /dev/urandom does", but its seek and write answers do not.** `013:35-39`, checks at `:57-60`.
- On Linux, `/dev/urandom` uses `noop_llseek`, so `sysseek` succeeds with "0 but true". The device is mode 0666, so an open for writing succeeds, and so does a write.
- The specification answers `ESPIPE` and `EACCES`, and check 3 asserts that `sysseek` fails. Neither is given as a difference from native, and no reason is given for `ESPIPE`.
- It also does not say how "an open for writing" is recognised: `FD_WRITE` in the base rights, `CREAT`, `TRUNC`, `EXCL`, or `APPEND`.
- **Fix:** either answer seek and tell with 0, as Linux does, or keep `ESPIPE` and `EACCES` and list both, with their reasons, as differences in `docs/sandbox.md` next to the `/dev` listing. Also name the flags that make an open "for writing".

**M2. 013 adds an import, `random_get`, and does not say so.** `013:35`.
- perl.wasm does not import `random_get` today (EXECUTED, above).
- The table's own header says "the module imports nothing new" (`recipes/wasi-fd-table.c:22`).
- The synthesis's mount design (spec 5) is "trimmed to the calls perl.wasm imports".
- **Fix:**
  - Add a decision that the module gains the preview1 import `random_get`, with the reason it is allowed: it is WASI preview1 and every host defines it.
  - Correct the header comment.
  - Add a build check that the import list includes `random_get` and nothing outside preview1.

**M3. 015 leaves out an input of the shortcut that its prototype has.** `015:31-33`.
- The specification says only: "'Has a second name' is read afresh for the runtime files that a grant's file matched."
- The shortcut "when no runtime file has a second name" needs every runtime file's current `st_nlink`. A link made inside a grant raises that count without changing any runtime folder's stamp.
- So an implementation that takes the shortcut from the cached scan would skip the walk in an install with no hard links (`UV_LINK_MODE=copy`). It would then miss a link made after the scan.
- Study A's patch reads every runtime file's link count afresh (`RuntimeFiles.linked_now`) at three points: once the walk passes the runtime's size, on an unreadable folder, and past `MAX_WALK`.
- **Fix:** write that rule into decision 2, and run check 2(a) in a copy-mode layout, where no runtime file has a second name when the scan is made.

**M4. 015's stamps do not allow for how coarse file times are.** `015:24-39`.
- Linux takes inode times from a coarse clock, about one tick. HFS+, FAT and SMB are coarser.
- **L6:** a file can be written after its hash in the same tick as the stamp. The record then vouches for changed bytes.
- **L5:** a folder can change in the same tick right after its stamp. The change is missed.
- This is git's "racy index" problem.
- **Fix:**
  - Stat each file before and after its hash, and record it only if both stats agree, as decision 2 of 014 already does for `SHA256SUMS`.
  - Do not record, or reuse, a stamp whose mtime or ctime is within about 2 s of the scan's own start. Hash it next time instead.
  - Add a check for each.

**M5. 014 gives the first-call time that needs 015 as well.** `014-the-runners-fixed-cost.md:11`.
- "A fresh process's first call about 40 ms" needs L6, which is in 015.
- Study A's 38 ms (L1–L7) and 34 ms (L1–L8) both include L6's −70 ms (A §4).
- With 014 alone, the 80–88 ms artifact check stays. That leaves about 100–105 ms (INFERRED: A's 138 ms base, minus 22–28 ms for L7 and about 8 ms for L8).
- **Fix:** correct the number, and put "about 34 ms" on 015 only, where it already is.

**M6. The synthesis's `-pi` row takes L5's saving from the wrong layout.** `2026-11-24-perf-synthesis.md:63`.
- The −19 ms is A's figure in the wheel's layout, where the scan walks the library twice (A §3.2).
- Study C ran with `artifacts=` pointing outside the package (`2026-11-24-perf-interp/measure.py:53-54`). A measured that layout's writable call at 15.0 ms, not 24.5 ms (A §2, `phases-base-envlayout-rw.json`). So L5 saves about 12 ms there.
- The row's "about 90 ms" is nearer 97 ms (INFERRED).
- The other figures that the correction commit fixed now match their studies:
  - 133–157 ms;
  - 68/114 and 66/112 ms;
  - 3.7–5.8×;
  - the four engine pins;
  - the INFERRED labels;
  - the dispatch rule.
- **Fix:** restate the row for C's layout, or say which layout it assumes.

**M7. The specifications' status lines contradict the brief.** `013:3`, `014:3`, `015:3`, `016:3`, and `roadmap.md` ("drafts for the owner to agree before they are built").
- The brief says the owner has agreed 013–016. Each file says it is a draft for the owner to agree.
- `AGENTS.md` lets nothing be built before the owner agrees.
- **Fix:** either record the agreement, with its date and words, in each Status line and in the roadmap, or leave them as drafts and do not build them yet.

### Low

**L1.** `014:60` says "the eight settings `engine()` makes". It sets three today (`src/wasi_perl/_engines.py:87-89`), and three plus four is seven. **Fix:** "seven".

**L2.** `014:31-33` adds macOS `/dev/fd/N`. Study A prototyped and measured Linux only, and it cites only wasi-python's posix path (A §4.3). The checks at `:56-59` test only Linux. **Fix:** cite the wasi-python file that uses `/dev/fd`, and add a macOS case to check 5 that runs in CI. Or limit the decision to Linux.

**L3.** The numbers in `014:11-47`, `015:13-18` and `016:75-82` carry no EXECUTED, READ or INFERRED label and no command. `AGENTS.md` asks for both. **Fix:** label each and name its study's command, as 013 does.

**L4.** `016:111` says "200 fewer `fd_fdstat_get`". Study C counted 1,838 → 1,637 calls in all, which is 201 (C §4.3). **Fix:** "one fewer per file opened" or "at least 200 fewer".

**L5.** `016:108` does not say where `memchr-test.c` will live. It is now under `context/research/2026-11-24-perf-interp/proto/`. It also does not say which wasmtime runs the test. **Fix:** name `tests/build/` and the runner.

**L6.** `013:63` says "`/dev/../dev/urandom` written through a grant", which is unclear. **Fix:** name the exact paths and their answers:
- `/dev/./urandom` is virtual;
- `<grant>/../dev/urandom` is refused, as today;
- `urandom/` answers `ENOTDIR`.

**L7.** The synthesis is now out of step with the specifications:
- `:75` still proposes wrapping `Perl_seed`. 013 rightly found that a wrap cannot reach the call at `util.c:4839`, which is in the same object.
- `:76` mentions a "stamp check for Windows" for L7. 014 reads the bytes on Windows instead.
- `:3` says "none is agreed yet".

**Fix:** add one line saying that 013–016 supersede rows 1–4.

**L8.** `roadmap.md` names `research/2026-11-24-wasmtime-py-file-call-cost.md` as "in progress". That file is not in the tree. **Fix:** say "to be written", without the path, or add the memo.

**L9.** The evidence folders are clean:
- no `*.wasm`, `*.cwasm` or archive, and no binary file at all (`file` on every path);
- 956 KB of text in all;
- no secrets;
- no zeroperl code;
- the mount prototype says in its docstring that it is adapted from wasi-python;
- no specification or review number in the prototype patches.

Two small points remain:
- `2026-11-24-perf-codegen/measure.sh:7` defaults `BENCH_DATA` to this session's scratchpad. Thirty-nine `perf-interp/results/*.json` files and four `recipes-diffs/config-*.diff` record scratchpad paths. Neither is a secret, but they cannot be reproduced. **Fix:** make the data folder a required argument or a path relative to the repository.
- `perf-startup/results/phases.json` is 18,051 lines (148 KB). That is acceptable, though a summary would do.

**L10.** `015:35` names the record by `SHA256SUMS` alone. Two installs with the same `SHA256SUMS` in different places share one record and keep replacing each other's. That is correct, but each process rehashes. **Fix:** key the record by the artifact root's identity as well.

Aside, not part of this diff: the working tree holds four untracked drafts, `context/specifications/018-*` to `021-*`. Keep them out of this branch's commits.

## Verdict

Does not land. The research, the evidence folders and the corrected synthesis are sound, apart from M6 and the stale rows in L7. But two of the four specifications cannot be built as written without the builder deciding something:
- 013's device does not fit the descriptor table's design (H1), and differs from native without saying so (M1, M2);
- 015's tree record argument fails on Windows (H2), and its stamps and shortcut have the gaps in M3 and M4.

All of these are text changes to the specifications. One commit that fixes H1, H2 and M1 to M7, with the Lows fixed or answered, should be enough for a second round to land.
