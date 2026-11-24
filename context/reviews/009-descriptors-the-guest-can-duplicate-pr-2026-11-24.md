# Review: 009, descriptors the guest can duplicate

Reviewer: an agent (Claude), a fresh sub-agent of https://claude.ai/code/session_01ThzDEUwftdmy8zWBJK1gWa; not the author

- **Branch:** `spec/009-fd-table` at `2f0d5d7`, reviewed as the diff `origin/dev...origin/spec/009-fd-table` against `origin/dev` at `eee84ac` (11 files: the new `recipes/wasi-fd-table.c`, `recipes/wasi-cc`, `scripts/20_target.sh`, `recipes/sandbox-refusals.c`, `tests/build/test_descriptors.py`, `test_refusals.py`, `test_modules.py`, `docs/sandbox.md`, `docs/modules.md`, the spec and the prior-art index).
- **Read:** `AGENTS.md`, `context/constitution.md`, the spec with its landing notes, 003's decision 8 (which this replaces), `recipes/wasi-fd-table.c` in full, and wasi-libc at `2e6fb9d` (`__wasilibc_fd_renumber.c`, `preopens.c`, `ppoll.c`) from the author's prior-art clone.
- **Date:** 2026-11-24.

## The run

Everything below was EXECUTED in a fresh worktree at `2f0d5d7`.

```
$ WASI_PERL_DOWNLOAD_CACHE=/home/user/wasi-perl/downloads WASI_PERL_JOBS=2 scripts/build.sh
00_download: ok / 10_native: ok / 20_target: ok / 30_library: ok (1453 files)
$ sha256sum build/artifacts/perl.wasm
b55f54ff8e1d26222350f0094f41bdaf78b1dad000ba88fa6973c3f7bfa66e97   (9264041 bytes, the landing size;
  the author's own build in 009-fd still hashes d6e358fb…, as its notes say)
$ uv run --no-project --with pytest --with wasmtime==51.0.0 pytest tests/build -q
319 passed in 12.15s
$ WASI_PERL_ARTIFACTS=$PWD/build/artifacts WASI_PERL_ORACLE=$PWD/build/native-install/bin/perl \
    uv run pytest -q tests/runner tests/escapes tests/oneliners
2 failed, 616 passed, 1 skipped, 1 xfailed
```

The two failures are the known `from conftest import` collisions (`test_dev_null_must_be_empty`, `test_without_the_export_a_refused_status_is_wasmtimes_words`), which are fixed elsewhere.

## Coverage of the wrapped calls

- **wasip1.h.** `include/wasm32-wasip1/wasi/wasip1.h` in the pinned SDK declares 46 `__wasi_*` functions (READ, by grep). The 36 that take or return a descriptor are every `fd_*` (21), every `path_*` (10), `poll_oneoff` and the four `sock_*`. All 36 are in `wasi-cc`'s list and have a wrapper. None is missed. The other 10 (`args_*`, `environ_*`, `clock_*`, `proc_exit`, `random_get`, `sched_yield`, `thread_spawn`) take no descriptor.
- **No bypass.** `llvm-nm -A` over every `lib/wasm32-wasip1/*.a` finds no `__imported_wasi_*` reference outside `__wasilibc_real.c.obj` (EXECUTED).
- **The linked module.** I mapped each `call` in `llvm-objdump -d build/target/perl` to its callee, using the import count from wasmtime-py (EXECUTED). The module has 32 imports, all from `wasi_snapshot_preview1`. Each is called only from its `__wasi_*` definition. Each `__wasi_*` that takes a descriptor is called only from its `__wrap_` and from the table's helpers. `fcntl` is called only from `__wrap_fcntl`. `dup`, `dup2` and `__wasi_fd_renumber` are not in the module at all, so `fd_renumber` is never imported now. This confirms the landing table.
- **Two-descriptor calls.** `path_link` and `path_rename` translate both descriptors, and `path_symlink` translates its second argument. By hand: `rename("/w1/a","/w2/b")`, `link`, `symlink`, `readlink`, `mkdir`, `rmdir`, `unlink` and `utime` across two writable grants all work. After `dup2` swaps the numbers of the two grants, the same rename looks in the swapped folders, as the table says it should.
- **`fd_readdir`.** It translates its descriptor. After `close STDIN`, `opendir` gets number 0. `readdir` and `rewinddir` work through it, and the next `open` takes 0 again.
- **`poll_oneoff`.** Only fd subscriptions are copied. The `userdata` is left as it is (wasi-libc's `ppoll` stores the `pollfd` pointer there). A preview1 event has no descriptor field, so nothing needs translating on the way back. `IO::Poll` on a file and on STDOUT gives 2 ready, as natively. A closed number: see L1.
- **`fd_renumber`.** It is a table move. The count is right when `to` holds another number for the same file (`release` drops one reference, the move adds none). It refuses `to >= 1024`. For a closed `to`, see L2.
- **Reference counts.** Each slot that points at a file holds exactly one of its references: `bind` adds one, `release` drops one, and `renumber` moves one. A failed `path_open` binds nothing. I ran 3,000 iterations of three kinds of failing open: a missing file, a write into the read-only library, and a `sysopen` of a folder for writing. Afterwards, 1,017 dups of STDOUT still fit, the same as with no failed opens (EXECUTED). Closing an alias keeps the file open, and closing its last number frees the number for the next `open`, with no leak (1,016 dups after that).
- **`dup2(x, x)`.** It returns `x` when `x` is open and EBADF when `x` is closed, as POSIX says.
- **Out of range.** `is_open` casts to `int` and checks `0 <= v < 1024` before it indexes the table. `TO_REAL`, `dup`, `dup2`, `close` and `F_DUPFD` answer EBADF (or EINVAL for `F_DUPFD`'s argument). POSIX.xs turns negative numbers into EBADF before they reach C, and `2**32` becomes 0 in Perl's IV-to-int conversion. 1023 works and 1024 is EBADF (EXECUTED).
- **EMFILE.** 1,017 dups with five grants, then `$!` is "No file descriptors available". The 1,024 limit holds, and so does reuse after a close.
- **The 003 checks that changed.** `test_dup_of_a_handle_works` and `test_fdopen_of_a_shared_descriptor_matches_native` now require the guest's stdout, stderr, status and files to equal native's. That is stricter than the "Not supported" assertions they replace. Dropping Test::Simple and Term::Table from `FAILS_IN_THE_SANDBOX` makes them subject to the load-as-natively check, which is also stricter. No exclusion was added.
- **Spec numbers in code comments.** I grepped the added lines under `recipes/`, `scripts/` and `tests/`. No comment names a specification or review number.
- **Thread safety.** The table is global and has no lock. That is fine: the build is single-threaded wasip1 (constitution 4.3). A `wasm32-wasip1-threads` build would need a lock around every wrapper. Noted only.

## Hostile programs (real perl.wasm, real Perl)

The grants were the library and `/dev` read-only, `/work` writable, and `/ro` read-only with a file `secret` in it. A file `outside` sat beside them, ungranted. Scratch `probe/run.py`, EXECUTED.

| Program | What happened |
|---|---|
| `open(my $fh, "<&=", $n)` for 0..20 | 0–6 open (3–6 are the grants, `stat` gives a folder), 7–20 EBADF. `sysread` on 3–6 gives EBADF. No number reaches anything that was not granted. |
| `POSIX::dup2(fileno $f, 3)`, then relative and absolute opens | `dup2` returns 3. Relative paths still resolve through the cwd grant. `/usr/local/lib/perl5/strict.pm` gives ENOTDIR because the library prefix now names a file. `/ro/secret` still reads. Only the program's own view is affected. |
| `POSIX::close` on 3..8, then `open` | 3–6 close, 7–8 EBADF. Every later path open is EBADF. The same happens before the table. |
| `POSIX::dup2(6, 5)` (read-only grant onto the writable one) | Reads of `/work/secret` return `/ro/secret`. Writes, `rename` and `link` give EPERM because the read-only rights travel with the real descriptor. Nothing is written. |
| 1,100 `POSIX::dup(1)` | 1,017 succeed, the highest is 1023, then EMFILE. After closing them all, `dup` gives 7 and `open` gives 8. |
| 4-argument `select` on descriptor masks | Returns -1 or 0 with a stale `$!`. This is the known `fd_set` layout mismatch the landing notes give to specification 010, and it is the same before the table. |
| `dup2` and `close` at -1, -2^31, 1023, 1024, 65535, 2^31, 2^32-1 and 2^32 | EBADF throughout except 1023, which is valid, and 2^32, which Perl reads as 0. No trap. |
| A `{ open my $fh, "<&=", 5 }` block, then `opendir "/w2"`, then `open ">", "/w1/result"` | **The write lands in `/w2/result`.** Before the table (003's module), the open fails with EBADF. See M1. |

**The table never invents access.** Real numbers enter it from three places only: the host's own descriptors found by the start-up scan, the results of `path_open`, and the results of `sock_accept`. The start-up scan stops at the first gap after 2, so a host descriptor past a gap is now out of reach, where before the raw number reached it. Every escape attempt above stayed inside its grants.

## Findings

| # | Rank | Where | Finding | Suggested fix |
|---|---|---|---|---|
| M1 | Medium | `recipes/wasi-fd-table.c:93-100` (`lowest_free`), with `:103-111` (`release`) | **A grant's number can be reused, and then paths under that grant open in another folder.** wasi-libc caches each grant's number once (`preopens.c`, READ) and never forgets it when the number is closed. The table hands a freed number to the next `open`. So once a program closes a grant's number, the next file or folder it opens takes that number. Every later path under that grant's prefix then resolves against the new descriptor. Demonstrated (EXECUTED): `{ open my $fh, "<&=", 5 }` (the handle closes 5 when its block ends), then `opendir "/w2"`, then `open ">", "/w1/result"` writes `/w2/result`. Before the table, the open fails with EBADF. Natively, closing a number never moves paths. The write stays inside the grants, because the real descriptor's rights still apply, so this is not an escape. It is a new, silent misdirection of file contents between grants. | When `start()` finds a grant, mark its number reserved, and have `lowest_free` skip reserved numbers, so `open`, `dup` and `F_DUPFD` never hand one out. Explicit `dup2`, `dup3` and `renumber` onto a reserved number stay allowed: the program asked for that number. A lookup through a closed grant then answers EBADF, as it did before. Add a check that runs the program above and expects EBADF and an empty `/w2`. |
| M2 | Medium | `tests/build/test_descriptors.py` (no such check) | **Several translation paths have no check.** No test puts a descriptor into `poll_oneoff`: the only `select` check (`test_refusals.py:178`) is clock-only, and it takes the fast path at `wasi-fd-table.c:226-227`. Also unchecked: `path_link` and `path_rename` with two different descriptors, `fd_readdir` at a remapped number, `dup2` onto a grant's number, and `close` of a grant's number. Each works when run by hand (see above), but a regression in any of them would reach the host unseen, and this file is the sandbox boundary. | Add these to `SAME_AS_NATIVE` or a guest-only test. `IO::Poll` with POLLOUT on a file and on STDOUT (compare `events` with native). `rename` and `link` between two writable grants. `close STDIN; opendir; readdir`. `POSIX::dup2($fd, <grant>)` followed by an open under that grant, which should give ENOTDIR or EBADF, never success. Most of these programs are in this review's table. |
| L1 | Low | `recipes/wasi-fd-table.c:215-216, 237` | The comment says a closed number becomes `UINT32_MAX` "so the host reports EBADF for that subscription". wasmtime 51 fails the whole `poll_oneoff` with EBADF instead. `IO::Poll::_poll(0, 50, POLLOUT, 1, POLLOUT)` returns -1 EBADF in the guest. Natively it returns 2, with POLLNVAL on 50 (EXECUTED). 003's module does the same as the guest, so this is not a regression. | Correct the comment. Better: drop closed-number subscriptions from the copy and add a local event with `error = __WASI_ERRNO_BADF` and the same `userdata` to `out`. wasi-libc's `ppoll` then sets POLLNVAL, as natively. |
| L2 | Low | `recipes/wasi-fd-table.c:298-311` | `fd_renumber` onto a closed `to` succeeds. WASI preview1 (and wasmtime) answers EBADF. Its only caller, wasi-libc's `freopen`, always renumbers onto an open descriptor, so nothing reaches this today. | Return `__WASI_ERRNO_BADF` when `slot[to] == 0`, or say in the comment why a move onto a closed number is allowed. |
| L3 | Low | `recipes/wasi-fd-table.c:72-84`, `docs/sandbox.md:65-66` | A host that opens 1,024 or more descriptors fills the table at start-up, and then every `open` fails with EMFILE. Descriptors past 1023 are unreachable. The documents say neither. | One line in `docs/sandbox.md`: grants count against the 1,024. Optionally, have the runner refuse more than about 1,000 grants. |
| L4 | Low | spec decision 4; `recipes/wasi-fd-table.c:33` | "1,024 … is the runner's default open-file limit". The runner sets no such limit: `src/wasi_perl` has none (READ, grep), and neither does wasmtime-py's `WasiConfig`. The number matches the usual native `ulimit -n`, which is what the EMFILE check compares against. | Say "the usual native `ulimit -n`" in both places. |
| L5 | Low | `tests/build/test_descriptors.py:134` | The `posix-dup-dup2` check prints `lowest` whenever `POSIX::dup(20)` returns any number at or below 20. That cannot tell lowest-free from merely low. | Print `$d - fileno($f)` (1 natively and in the guest). |
| L6 | Low | `docs/sandbox.md:49` | The `&=` forms are listed as working "as on POSIX". But `open my $fh, "<&=", 3` succeeds on a grant's number, where natively it gives EBADF, and closing that handle closes the grant for the rest of the run. This predates the table. It is how M1 is reached without `POSIX`. | One sentence: numbers 3 and up name the grants, and closing one ends access to that folder. The M1 fix removes the redirection part. |

There is no High finding. No descriptor outside the grants was reached, no wrapper indexes out of the table, every error path keeps the counts right, and the spec's checks pass.

## Verdict

Lands. The table is complete against wasip1.h and the linked module, it never invents access, and its checks compare with native perl. M1, a reused grant number that sends paths under that grant into another folder, and M2, the missing checks on the poll, two-descriptor, readdir and grant-number paths, should be fixed or answered in writing in the review round before the merge.
