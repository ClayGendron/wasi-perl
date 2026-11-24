# Review: 010, special files refused at open, and select over Perl's bit vectors

- **Branch:** `spec/010-special-files-and-select`, head 6f355aa
- **Diff reviewed:** `git diff origin/spec/009-fd-table...origin/spec/010-special-files-and-select` (8 files, +747 −49)
- **Reviewer:** an agent (Claude), a fresh sub-agent of https://claude.ai/code/session_01ThzDEUwftdmy8zWBJK1gWa; not the author
- **Date:** 2026-11-24

## What I ran

    WASI_PERL_DOWNLOAD_CACHE=/home/user/wasi-perl/downloads WASI_PERL_JOBS=2 scripts/build.sh    EXECUTED
    # 20_target: ok / 30_library: ok (1453 files)
    sha256sum build/artifacts/perl.wasm; stat -c %s build/artifacts/perl.wasm                     EXECUTED
    # 65132082ff45e61a18b5044d83854335a308fa9447fb7496b6635edfb90976c2, 9258122 bytes
    uv run --no-project --with pytest --with wasmtime==51.0.0 pytest tests/build -q               EXECUTED
    # 377 passed in 13.01s
    WASI_PERL_ARTIFACTS=$PWD/build/artifacts uv run pytest -q tests/escapes tests/runner          EXECUTED
    # 172 passed, 1 skipped, 4 warnings in 14.07s

Probes by hand, under `build/tools/wasmtime-v51.0.0-x86_64-linux/wasmtime run -W exceptions=y`, with a writable folder holding a FIFO `pipe` granted at `/g` (EXECUTED unless marked):

| Program | Guest | Native |
|---|---|---|
| `open(my $f,"<","pipe")` | `No such device or address` | blocks |
| `truncate("pipe",0)` | ENXIO | (EINVAL) |
| `opendir(my $d,"pipe")` | `Not a directory` | the same |
| `stat("pipe")`, `-e` | 13 fields, true | the same |
| `utime(undef,undef,"pipe")` | **hangs: killed by `timeout 8`, status 124** | `ok` at once |
| `utime(1,1,"pipe")` | **hangs, status 124** | `ok` |
| `symlink("pipe","l"); utime(undef,undef,"l")` | **hangs, status 124** | `ok` |
| `utime(undef,undef,"pipe")` through `run()` with `deadline_s=2`, `fuel=2e9` | **never returns; the child was killed at 15 s** | |
| `$v="\xff"; select($v,undef,undef,0)` | `-1 [Bad file descriptor] 11111111`, no trap | EBADF too (3 to 7 are closed) |
| `"\xff" x 200` in all three sets | `-1 EBADF`, length 200 kept, no trap | |
| a file and STDIN in the read set, timeout `undef` | `1`, the file's bit | |
| `select(undef,undef,$e,0)` with the file's bit in `$e` | **`-1 [Function not implemented]`, `$e` kept** | `0`, `$e` cleared |
| `select(undef,undef,undef,0.3)` | sleeps, returns | the same |

Sources read: wasmtime-wasi 51.0.0 (`src/filesystem.rs` `set_times_at` line 890 and `from_raw_os_error` lines 446-450, `src/p1.rs` `poll_oneoff` lines 2308-2500, `src/filesystem/windows.rs` `metadata_hash_at`) and cap-primitives 3.4.4 (`src/rustix/fs/set_times_impl.rs`, `src/rustix/linux/fs/set_times_impl.rs`, `src/fs/via_parent/set_times_nofollow.rs`, `src/fs/manually/open.rs` `stat`), both from static.crates.io into the scratchpad. READ. Perl's `pp_sselect` (`pp_sys.c` 1348-1500) and `recipes/config.sh` (`selectminbits='32'`). READ.

## Findings

| # | Rank | Where | Finding | Suggested fix |
|---|---|---|---|---|
| H1 | High | `recipes/wasi-fd-table.c:189-192` (`path_filestat_set_times` is a bare `PASS_FD`) | **`utime` on a FIFO, or on a link to one, opens the FIFO on the host and holds it past every limit.** Perl's `utime` reaches `__wasi_path_filestat_set_times` with `SYMLINK_FOLLOW`. wasmtime's `set_times_at` (filesystem.rs:900-903) then calls cap-primitives' `set_times`, which on Linux and macOS *opens the path for writing, then for reading* to call `futimens` (`set_times_impl.rs`), with no `O_NONBLOCK`. A FIFO's open waits for a reader. EXECUTED: the wasmtime CLI hangs (status 124 at 8 s) for `utime(undef,undef,"pipe")`, `utime(1,1,"pipe")` and `utime` through a guest-made link; `run()` with `deadline_s=2` and a fuel cap never returns. Native perl answers `ok` at once. This is exactly the hold decision 1 exists to stop, through a second route, and it needs only a writable grant (`set_times_at` refuses a read-only one first, filesystem.rs:897). On a device node the same open can have side effects. Spec 010's escape check states the call returns; for this route it does not. | Wrap `path_filestat_set_times`. When the flags have `SYMLINK_FOLLOW`, look with `path_filestat_get` and those flags. If the target is a directory or a regular file, forward unchanged. If it is anything else and the name itself (looked at with flags 0) is that special file, not a link, forward with flags 0: cap-primitives' no-follow path is `utimensat` on the parent (`via_parent/set_times_nofollow.rs`) and opens nothing, so `utime` on a FIFO then works as natively. If the name is a link to a special file, answer ENXIO (or resolve the link in the guest and set the times on its target with flags 0). Add checks: `utime` on a FIFO (native answer), through a link (ENXIO or native), both inside `run()`'s fuel limit, beside the escape check's opens. The same look-before-host rule should be stated for every host call that might open, in the comment above `look()`. |
| M1 | Medium | `recipes/wasi-fd-table.c:380-396` | **The host can write one event past the caller's array.** When some subscription is answered locally (`nlocal > 0`) and the caller had no clock (an infinite wait: `select($r,undef,undef,undef)`, `IO::Select->can_read()` with no timeout, `poll(..., -1)`), the wrapper sends the unanswered descriptors plus a zero clock, `nsent` = (nsubscriptions − nlocal) + 1, and gives the host `out + nlocal`, which holds only nsubscriptions − nlocal events. wasmtime writes every ready subscription up to the count it was given (p1.rs: `events.as_array(nsubscriptions)` zipped with the ready set). If all of them are ready, the last 32-byte event lands past the end of wasi-libc's `__wasi_event_t events[nsubscriptions]` on the guest's stack. Guest memory only, not an escape. Not observed in 2,000 calls of a C probe (a regular file plus a write subscription on STDERR, a canary after the caller's two events) on Linux, EXECUTED: wasmtime did not report the zero clock alongside the ready descriptor. So it rests on host timing, not on the code. The path came with 009's closed-number events; 010 makes it reachable from every regular file in a set. | Give the host its own event buffer: `malloc(nsent * sizeof *out)`, call into it, then copy into `out + nlocal` dropping the zero clock (which leaves at most nsubscriptions − nlocal events). Or allocate `copy` and a scratch event array together with nsubscriptions + 1 entries. Add a check that runs an infinite-wait `select` on a file and a ready descriptor, under a canary probe or an assertion in a debug build. |
| L1 | Low | `recipes/wasi-fd-table.c:651` | **A select with only the error set gives ENOSYS.** wasi-libc's `pselect` answers ENOSYS when the error set is not empty and there is nothing else to watch. EXECUTED: guest `-1 [Function not implemented]`, `$e` kept; native `0`, `$e` cleared, after the timeout. Spec decision 4 compares with native where native is defined; this is defined. | Never pass the error set to wasi-libc (it reports no exceptional conditions anyway): pass NULL and, on success, clear the error vector's bits below `n`. A read-or-write-empty call with a timeout then becomes the timeout form. Add the case to `test_select.py`. |
| L2 | Low | `recipes/wasi-fd-table.c:307-316` | **The reopen after a held rename skips the look and does not check what it opened.** It calls the real `path_open` directly. Lookup flags are 0, so a final link is not followed (cap-std fails with ELOOP), intermediate components stay inside the grant, and the rights are those first asked for, which the destination's grant must allow again. So it is not an escape. But a host process that swaps the destination for a FIFO between the retry and the reopen holds the host, and one that swaps in a directory leaves a directory under a file's number. The guest cannot cause either: it is single-threaded and nothing of Perl runs between the two. It is decision 1's documented race on a second path, and the comment at 223-233 does not say so. | Call `look(new_fd, 0, new_path, oflags)` before the reopen, and after it check with `fd_filestat_get` that the result is a regular file; otherwise close it and leave the number EBADF. Name this race in the comment. |
| L3 | Low | `recipes/wasi-fd-table.c:462-467`, `307-308` | **The reopen uses the flags and rights recorded at open, not the current ones.** A later `fcntl(F_SETFL)` (`fd_fdstat_set_flags`, say adding `O_APPEND` or `O_NONBLOCK`) is lost by the reopen. Rights cannot rise above the first request, so this is only a fidelity gap. | Read `fd_fdstat_get` on each held descriptor before closing it; reopen with its current `fs_flags`, and rights that are the recorded ones masked by the current ones. |
| L4 | Low | `recipes/wasi-fd-table.c:285-318` | **On Linux a failing rename now closes and reopens held descriptors.** "Nothing runs where the first rename succeeds" is true, but on Linux an EACCES or EPERM rename over a held file (the read-only library grant, as the test shows) runs the close-and-reopen too. Usually invisible. With overlapping grants of different permissions, a file held read-write through one and renamed over through a read-only one cannot be reopened with its rights, and its handle reads EBADF from then on, where natively it survives. Untested; READ. | Before closing, probe: open the destination with the recorded arguments, then close the probe at once; skip the retry if the probe fails. Or keep a list of the descriptors that failed to reopen and reopen those at their original directory and path. |
| L5 | Low | `recipes/wasi-fd-table.c:297`, `128-136`, `374` | **A descriptor whose reopen failed keeps `real = UINT32_MAX`.** Its later `close` returns EBADF (Perl's `close` is false and may warn), and a `poll_oneoff` that names it sends UINT32_MAX to the host, which fails the whole call with EBADF instead of that entry alone. | Mark such an entry closed-on-the-host: `release` then skips the host close and returns success, and `poll_oneoff` answers it locally with EBADF like a closed number. |
| L6 | Low | `context/specifications/010-special-files-and-select.md`, landing notes "Commands and the artifact" | **The recorded hash does not match a fresh build of the head.** The notes give `7d8c48a7…`, 9258122 bytes; my build of 6f355aa gives `65132082…`, the same size, EXECUTED. No build path is embedded in the module (`grep -a` finds none, EXECUTED). Either the hash predates the last code change (6f355aa edits `wasi-fd-table.c`) or the build is not byte-reproducible. | Rebuild from the head and record its hash, or say in the notes which commit the hash is from. |

## The questions asked

**1. The look before open.**
- Every `path_open` goes through `look()` (`wasi-fd-table.c:459`), after the descriptor is translated and before the host is asked. Lookup flags are passed exactly as the open will use them. That covers `<`, `>` (O_CREAT on an existing FIFO is looked at, since only `O_CREAT|O_EXCL` skips the look), `>>`, `+<`, `truncate`, `opendir` (ENOTDIR), `require` and every other open in wasi-libc. Links that the open follows are looked at through, and a hard link is the FIFO itself.
- The `O_CREAT|O_EXCL` skip is sound: Linux and the manual resolver both refuse an existing last component before opening it.
- A failed `path_filestat_get` forwards the open. I found no case where the stat fails and the open of a special file would succeed.
- `stat`, `-e`, `lstat`, `readlink`, `unlink`, `rename` and `link` open nothing on the host (cap-primitives' manual `stat` does a no-follow `stat_unchecked` on the last component). READ, and `stat`/`-e` EXECUTED.
- **Not covered: `utime` (H1).**
- The race is documented in the code (`look()`'s comment) and in the spec, but not for the reopen (L2).

**2. select.**
- Bounds hold. `n = min(nfds, 1024)`. Perl gives each non-empty vector at least `growsize = maxlen + (4 − maxlen % 4)` bytes (`selectminbits=32`), more than `maxlen ≥ n/8`. So every read and write is inside Perl's buffer. Empty and undef vectors are NULL. The statics are wasi-libc `fd_set`s of 1,024 entries, and `FD_SET` stops there.
- The old trap is gone (`"\xff"` and 200-byte vectors, EXECUTED).
- Returned vectors are rewritten only below `n`. Vectors are left alone on an error.
- The timeout form is unchanged: `n = 0`, three NULLs.
- POSIX says a regular file is always ready to read and to write. That holds whatever the open mode, and the code checks the file type before the rights, as it should.
- Gaps: the error-only form (L1) and the event-buffer size (M1).

**3. The held rename.**
- It closes only the guest's own descriptors. `reopenable` is set only by `path_open`, and stdio, grants and `sock_accept` descriptors are excluded. `find_held` matches regular files only, by `(dev, ino)`. wasmtime computes both sides the same way on Windows too (`metadata_hash_at` opens a metadata handle, `windows.rs:97-103`, READ).
- It reopens with the recorded arguments, less CREAT, EXCL and TRUNC, with lookup flags 0 and the rights first asked for. That is never more than before, and the destination's grant must allow them again. It does not check the type afterwards (L2).
- The offset is clamped to the new size, and the virtual numbers are kept. On failure it puts back what it can (L5).
- **It is not an escape.** The guest is single-threaded, so it cannot plant a link between the retry and the reopen. A link planted by a host process is not followed (flags 0). And `path_open` stays wasmtime's sandboxed resolver on the guest's own grant descriptor.
- Linux is unchanged when the rename succeeds (L4 for when it fails). The shipped module reads the old file as natively (`test_rename_over_open.py`, EXECUTED).
- The test switch is sound. `-DWASI_PERL_TEST_HELD_RENAME` appears only in `tests/build/test_rename_over_open.py:34`. `scripts/20_target.sh:39` compiles the shipped table without it, and `recipes/wasi-cc` adds nothing from the environment.
- The Windows claim (EACCES from `ERROR_ACCESS_DENIED` and `ERROR_SHARING_VIOLATION`) matches `filesystem.rs:448-449`, READ. Its proof stays with 008's Windows CI, as the spec says.

**4. Tests and comments.**
- No check was weakened. 005's strict xfail is now a real check, with a regular-file control, exact lines, `fuel_used` reported and a 20 s fail-safe.
- No exclusion was added.
- No comment under `recipes/` or `tests/` names a specification or review number (grep of the added lines, EXECUTED).

## Verdict

Does not land: H1 is the hang this specification exists to remove, reached through `utime` instead of `open`, and it holds `run()` past its deadline and fuel (EXECUTED). Wrapping `path_filestat_set_times` with the same look, with a check beside the FIFO escape check, should be one round. M1 should be fixed in the same round. The Lows may be fixed or answered in writing.

## Round 2

- **Fix commit:** 7bcaf73. It fast-forwards on 5b68427.
- **Reviewer:** the same agent as round 1 (Claude), a fresh sub-agent of https://claude.ai/code/session_01ThzDEUwftdmy8zWBJK1gWa; not the author.
- **Build:** a fresh worktree, `WASI_PERL_JOBS=2 scripts/build.sh`: ok, 1453 library files, EXECUTED.
- **Tests:**
  - `uv run --no-project --with pytest --with wasmtime==51.0.0 pytest tests/build -q`: 382 passed, EXECUTED.
  - `WASI_PERL_ARTIFACTS=$PWD/build/artifacts uv run pytest -q tests/escapes`: 13 passed, EXECUTED.
  - No check was removed or loosened: the diff's test changes are additions only.

### Reproductions

Each program ran in the guest (the wasmtime CLI, a writable `/g` with a FIFO `pipe` and a folder `d` holding a FIFO `p2` and a file `f`) and in native perl on the same tree. All EXECUTED.

| Program | Guest | Native |
|---|---|---|
| `utime(undef,undef,"pipe")` | `ok` | `ok` |
| `utime(1,1,"pipe")`, then mtime | `ok 1` | `ok 1` |
| `utime` through a guest-made link to the FIFO | `No such device or address`, returns at once | `ok` |
| `utime(1,1,…)` through a link to a regular file | `ok 1` | `ok 1` |
| `-e "pipe"`, `-p "pipe"`, `-e "d/p2"` | `101` | `111` |
| `opendir "d"; readdir` | `f,p2` | `f,p2` |
| `open "<", "d/p2"` | ENXIO | blocks (killed at 3 s) |
| both `utime` forms through `run()` (`deadline_s=2`, fuel cap) | returns, exit 0 | |

- The ENXIO through a link is the documented refusal, the same as an open through a link.
- `-p` false is the documented WASI limit.

### The audit, checked against the sources

I spot-checked the author's claim against wasmtime-wasi 51.0.0 and cap-primitives 3.4.4.

- READ:
  - `link_at` refuses `SYMLINK_FOLLOW` with EINVAL (`filesystem.rs:914-930`). Without following, the link is a `hard_link` through the parent.
  - On Linux, `stat` with following is `open_beneath` with `O_PATH` (`rustix/linux/fs/stat_impl.rs`). Without following, it is `stat_unchecked` on a single component.
  - The macOS manual `stat` does a no-follow `stat_unchecked` on the last component (`fs/manually/open.rs:458-492`).
  - Components in the middle of a path use `dir_options()`, which is read plus `dir_required`, so `O_DIRECTORY`, plus `O_PATH` on Linux (`rustix/fs/dir_utils.rs:79`, `rustix/fs/oflags.rs`).
  - The rename, unlink, readlink, mkdir, rmdir and symlink helpers all live under `fs/via_parent/`.
  - The two `primitives::open` calls outside `open_at` and `set_times` (`filesystem.rs:540`, `637`) are `sync` and `sync_data` on a descriptor already open, not on a guest path.
- INFERRED: that macOS refuses `O_DIRECTORY` on a FIFO before the FIFO's open waits. It is not run here.

### The other fixes

- **M1, the host's event buffer.**
  - The host now writes into a `malloc` of `nsent` events.
  - The copy into `out` is bounded by `nsubscriptions`, and the zero clock is dropped only when the wrapper added it.
  - `tests/build/test_poll_buffer.py` passes. Its fake host answers every subscription it is sent.
- **L1, the error set.** It is never passed to wasi-libc, and its bits come back clear on success.
- **L2, the reopen.** It is now looked at first, and must open a regular file.
- **L3 and L4, the probe before closing.**
  - A probe open runs before any held descriptor is closed. It uses the current `fs_flags`, with rights masked by the current ones.
  - If the probe fails, nothing is closed and the first error is returned.
  - This settles both findings.
- **L5, a lost descriptor.** `GONE` closes cleanly and is answered locally in poll.
- **L6, the hash.** Answered: `perl -V` embeds `__DATE__` and `__TIME__`. This round's build hashed `d0e484c5…`, different again, which fits that answer.

### Left as notes, not findings

- With an error-set-only select and no timeout, wasi-libc now sees three NULL sets and no clock. That should give ENOTSUP where native waits forever. It is not run, and it is harmless.
- The probe open at the destination skips `look()`. Only a host process could race it, the same documented race as before.

### Verdict

Lands. H1 and M1 are fixed and checked by rerunning the reproductions (EXECUTED). L1 to L5 are fixed. L6 is answered in writing. tests/build and tests/escapes pass.
