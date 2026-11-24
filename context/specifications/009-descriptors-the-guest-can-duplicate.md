# 009 — Descriptors the guest can duplicate

- **Status:** written by the orchestrator 2026-11-24, under the owner's pre-agreement of the roadmap's work toward 0.1.0 (it closes a gap specifications 002, 003 and 007 found).
- **Owner:** Clay Gendron
- **Kind:** the build recipe (a new `recipes/wasi-fd-table.c`, `--wrap` flags in `recipes/wasi-cc`), replacing specification 003's `dup2` and `close` wrappers; checks in `tests/build/`.
- **Depends on:** 002, 003.
- **Research it stands on:**
  - Specification 002's landing notes (EXECUTED): with no `dup()`, `open my $fh, ">&", STDOUT` fails "Not supported", so Test::More (through Test2), CPAN and Term::Table do not load.
  - Specification 007's first run (EXECUTED): `io/dup.t`, `op/die_exit.t` and `op/write.t` stop on `>&STDOUT`, and the platform exclusions name "descriptors are not lowest-free".
  - Specification 003's decision 8 (READ): `dup2` moves a descriptor with `fd_renumber`. That is right only when the caller closes the old one at once.
  - WASI preview1 (READ) has `fd_renumber` but no way to make a second descriptor for an open file.

## The problem

POSIX gives a process many numbers for one open file: `dup`, `dup2` and `fcntl(F_DUPFD)`. Perl leans on them to save and restore STDOUT and STDERR, for `open … ">&"`, and for `local *STDOUT`. Test::More does it on load. WASI preview1 has one number per open file. So the guest has to keep the many-to-one mapping itself.

## Decisions

1. **The guest numbers its own descriptors.** `recipes/wasi-fd-table.c` keeps a table from the numbers the program sees (virtual) to WASI's numbers (real), each real one with a reference count. At start-up the table is the identity for every real descriptor WASI has open (stdio and the preopens, found the way wasi-libc finds preopens). Every wasi-libc call that passes a descriptor to the host (`__wasi_fd_*`, the directory argument of every `__wasi_path_*`, the subscriptions of `__wasi_poll_oneoff`, and the socket calls) is wrapped with `-Wl,--wrap=` to translate virtual to real. Every call that returns a new descriptor (`__wasi_path_open`, `__wasi_sock_accept`) records it under the lowest free virtual number, as POSIX requires.
2. **`dup`, `dup2`, `dup3` and `fcntl(F_DUPFD, F_DUPFD_CLOEXEC)`** make a new virtual number for the same real descriptor and raise its count. `close` of a virtual number lowers the count, and closes the real descriptor only at zero. So the old and the new numbers share one file offset, as on POSIX. `dup2(old, new)` closes `new` first, as POSIX says, and is a no-op when they are equal. `fd_renumber` from inside the guest is a table operation, not a host call.
3. **It replaces specification 003's decision 8.** The `dup2`-as-move and `close` wrappers are removed. Every check that decision added must still pass.
4. **Limits.** The table holds at most 1,024 virtual numbers, which is the runner's default open-file limit; past it, `dup` answers `EMFILE`. A real descriptor a program never closes is closed by the host at the end of the call, as now.
5. **No new import, and no change for the host.** The module still imports only `wasi_snapshot_preview1`. An embedder sees the same real descriptors as before.
6. **Checks** (`tests/build/test_descriptors.py`), each compared with the native oracle:
   - `open my $save, ">&", STDOUT`, then `open STDOUT, ">", "f"`, print, `open STDOUT, ">&", $save`, print. Both outputs land where they do natively.
   - `open STDERR, ">&STDOUT"`, `open(my $dup, "<&=", $fh)`, `fileno` values after open, close and reopen (lowest-free numbering).
   - Test::More loads, and a three-test script prints the same TAP as natively.
   - `local *STDOUT` and `select` round trips.
   - `dup` past the limit gives `EMFILE` without a trap.
   - Specification 003's reopen checks and specification 007's `io/dup.t`, `op/die_exit.t` and `op/write.t` are rerun. A file that now passes leaves the exclusion list in this change.

## Acceptance

1. Every check of decision 6 passes, EXECUTED, output in the landing notes.
2. 001's, 002's and 003's checks still pass.
3. A fresh agent's review lands.

## Landing notes

Built 2026-11-24 on branch `spec/009-fd-table`, in this container (Linux x86-64, 4 shared cores, `WASI_PERL_JOBS=3`). The branch merges spec/003-refusals (033ed5b, then its review round 54d9bac), spec/002-xs-modules (c1e7745, then its review answer 0e17674) and dev (efd66cd, then eee84ac). Nothing was rebased.

### Commands and the artifact

    WASI_PERL_DOWNLOAD_CACHE=/home/user/wasi-perl/downloads WASI_PERL_JOBS=3 scripts/build.sh
    # 00_download: ok / 10_native: up to date / 20_target: ok / 30_library: ok; real 0m47.0s   EXECUTED
    sha256sum build/artifacts/perl.wasm; stat -c %s build/artifacts/perl.wasm
    # d6e358fbc6d35c87daddc32905c23bb47c3ebadc74a0efdb4be3b600d380da14, 9264041 bytes          EXECUTED

    uv run --no-project --with pytest --with wasmtime==51.0.0 pytest tests/build -q            EXECUTED
    319 passed in 10.76s
    uv run pytest -m "not slow" -q                                                               EXECUTED
    2 failed, 385 passed, 1 skipped, 232 deselected
    uv run pytest -m "not slow" -q tests/runner                                                 EXECUTED
    149 passed, 1 skipped, 10 deselected

The two failures of the whole-tree run are `tests/runner/test_artifacts.py::test_dev_null_must_be_empty` and `tests/runner/test_limits.py::test_without_the_export_a_refused_status_is_wasmtimes_words`: each does a bare `from conftest import …`, which finds `tests/build/conftest.py` once that has been imported under the name `conftest` (`ImportError: cannot import name 'REPO'`). Both pass when `tests/runner` runs alone. The same files and imports are on origin/dev (`git grep "from conftest import" origin/dev -- tests/runner`), so this is dev's, not this change's. EXECUTED. `ruff check` and `ruff format --check` on `tests/` are clean. EXECUTED.

### The design

`recipes/wasi-fd-table.c`, compiled by `scripts/20_target.sh` into `build/stubs/libwasi-perl-fd-table.a` before any link, and linked by `recipes/wasi-cc` into every program it links.

- **The table.** `slot[v]` gives each virtual number 0 to 1023 an open-file entry or none; each entry holds the real WASI number and a count of the virtual numbers on it. A per-number `FD_CLOEXEC` bit reads back what `F_SETFD` stored. Nothing can be executed, so it means nothing else.
- **Start-up.** The table fills itself on the first wrapped call. Every descriptor the host opened gets its own number (identity). The scan is `fd_fdstat_get` from 0, skipping a missing 0, 1 or 2, and ending at the first EBADF from 3, which is how wasi-libc finds preopens (`preopens.c`, READ). No constructor runs. So wasi-libc's lazy preopen scan (`__wasilibc_populate_preopens`, called before `close`, `__wasilibc_fd_renumber` and the first path lookup, READ) and the PWD `chdir` constructor in `wasi-stubs.c` find the table ready whichever runs first. Both still work: `test_cwd_under_a_granted_pwd` and every check that runs `-e` pass. EXECUTED.
- **Translation.** Each wrapper maps the virtual number to the real one, or answers EBADF, and calls `__real_`. `poll_oneoff` copies its subscriptions only when one names a descriptor. A closed number becomes `UINT32_MAX`, so the host reports EBADF for that subscription.
- **New descriptors.** `path_open` and `sock_accept` look for the lowest free number first. If there is none they answer EMFILE without opening anything.
- **Table operations.** `fd_close` drops one number and closes the real descriptor when its count reaches 0. `fd_renumber` (wasi-libc's `freopen`) moves the entry and closes what was at `to`. `dup` and `F_DUPFD` take the lowest free number from 0 or from the argument. `dup2(old, new)` validates `old`, does nothing when they are equal, and otherwise closes `new` quietly, as POSIX says. `dup3` refuses equal numbers and any flag (wasi-libc's `O_CLOEXEC` is 0). `fcntl` reads its third argument only for `F_DUPFD`, `F_DUPFD_CLOEXEC`, `F_SETFD` and `F_SETFL`. It answers `F_GETFD`/`F_SETFD` from the table, with EBADF for a closed number; wasi-libc's own answers `FD_CLOEXEC` for any number (READ). It passes everything else to wasi-libc.
- **Removed:** 003's `__wrap_dup2` (move) and `__wrap_close`, their `moved_fd` slot, the `PL_perlio_fd_refcnt` gate the 003 review added, and `dup2 close` in `wasi-cc`'s list. `sandbox-refusals.c` no longer includes `<fcntl.h>` or `<wasi/libc.h>`.

### The wrapped symbols

`llvm-nm -A share/wasi-sysroot/lib/wasm32-wasip1/libc.a` (EXECUTED): every `__wasi_*` function is defined (`T`) in `__wasilibc_real.c.obj` alone and referenced (`U`) only from other objects. So `--wrap` catches every reference. `dup`, `dup2` and `dup3` (weak) are defined in `dup.c.obj`, `fcntl` in `fcntl.c.obj`. `fcntl` is also referenced from wasi-libc's `__fdopen`, `__fopen_rb_ca` and `freopen`, and those references are wrapped too. `close` is defined in `__wasilibc_fd_renumber.c.obj` and calls `__wasi_fd_close`, so `close` itself needs no wrap. `__wasilibc_fd_renumber` (only `freopen` calls it) calls `__wasi_fd_renumber`, which is wrapped. So it is not wrapped itself.

| Wrapped | What the wrapper does | wasi-libc callers (`llvm-nm -u`) |
|---|---|---|
| `__wasi_fd_advise`, `fd_allocate`, `fd_datasync`, `fd_fdstat_get`, `fd_fdstat_set_flags`, `fd_fdstat_set_rights`, `fd_filestat_get`, `fd_filestat_set_size`, `fd_filestat_set_times`, `fd_pread`, `fd_prestat_dir_name`, `fd_prestat_get`, `fd_pwrite`, `fd_read`, `fd_readdir`, `fd_seek`, `fd_sync`, `fd_tell`, `fd_write` | translate the descriptor | posix_fadvise, posix_fallocate, fdatasync, faccessat/fcntl/getsockopt/ioctl/isatty/openat/pread/pwrite, fcntl/ioctl, fstat, ftruncate, futimens, pread/preadv, preopens, pwrite/pwritev, read/readv, fdopendir/readdir, lseek, fsync, `__wasilibc_tell`, write/writev |
| `__wasi_path_create_directory`, `path_filestat_get`, `path_filestat_set_times`, `path_readlink`, `path_remove_directory`, `path_unlink_file` | translate the directory | mkdirat, faccessat/fstatat, utimensat, readlinkat, `__wasilibc_rmdirat`, `__wasilibc_unlinkat` |
| `__wasi_path_link`, `path_rename` | translate both directories | linkat, renameat |
| `__wasi_path_symlink` | translate its second argument | symlinkat |
| `__wasi_poll_oneoff` | translate the fd subscriptions | clock_nanosleep, ioctl, ppoll |
| `__wasi_sock_recv`, `sock_send`, `sock_shutdown` | translate | recv, send, shutdown |
| `__wasi_path_open`, `__wasi_sock_accept` | translate, record the new one at the lowest free number | openat, accept |
| `__wasi_fd_close` | table: drop a number, close the real one at count 0 | close |
| `__wasi_fd_renumber` | table: move | `__wasilibc_fd_renumber` |
| `dup`, `dup2`, `dup3`, `fcntl` | table (fcntl: the dup and FD commands) | Perl's doio.o, perlio.o, POSIX; wasi-libc's stdio for fcntl |

That makes 36 `__wasi_*` wraps and 4 libc wraps. In the linked module, every real `__wasi_fd_*`, `__wasi_path_*` and `__wasi_poll_oneoff` that remains is called only from its wrapper or from the table's own helpers, and so is `fcntl`. `dup`, `dup2` and `dup3` of wasi-libc are not in the module at all. The wrappers with no caller in Perl (`fd_advise`, `fd_allocate`, `fd_datasync`, `fd_pread`, `fd_pwrite`, `fd_fdstat_set_rights`, the `sock_*`) are dropped by the linker. Counted by mapping every `call` in `llvm-objdump -d build/target/perl` to its callee (a short script over the name section), EXECUTED. The module still imports only `wasi_snapshot_preview1` (`test_still_imports_only_wasi`, EXECUTED).

### The checks

`tests/build/test_descriptors.py`, 30 checks, all EXECUTED and passing. Each compares stdout, stderr, the exit status and the files written with native perl run in a scratch folder.

- `test_same_as_native` (23): save and restore STDOUT (decision 6's first case) and STDERR; `>&STDOUT` and `>&STDERR`; a dup of STDIN; `<&=` on the same descriptor; the orchestrator's `&=` cases (`open STDERR, ">&=STDOUT"`, `open STDOUT, ">&=", $h`, `open my $fh, "<&=", fileno(STDIN)`, and `>&=` followed by the close of either handle, three ways); a shared offset (`syswrite` on two numbers gives `123`, `sysseek` reads 2); closing one of two numbers; lowest-free numbering after open, close and reopen, relative to the first descriptor opened, because each granted folder holds one (the guest's first open is 6, native's 3); a reopen keeps 0, 1 and 2; `POSIX::dup2` onto 20, `dup2(20, 20)`, `POSIX::dup` lowest free; EBADF from `POSIX::dup`, `dup2` and `close` of a closed number; `fcntl(F_DUPFD, 30)` twice (30, 31) and EINVAL for -1; `local *STDOUT` with and without a copied handle; `select` round trips; a `die` after STDERR is reopened (status 25, as natively).
- `test_test_more_tap_matches_native` (3): the three-test script prints `1..3`, `ok 1 - one`, `ok 2 - two`, `ok 3 - three`, byte for byte as natively. The same script with one failing `is` gives the same TAP, the same diagnostics on stderr and status 1. `done_testing` with `note` and `diag` matches too.
- `test_modules_that_copy_stdout_load` (4): Test::More, Test::Simple, Test2::V0 and Term::Table load.
- `test_dup_past_the_limit_is_emfile`: dups of STDOUT until one fails. Natively (under `ulimit -n 1024`) and in the guest there are more than 1000, then `$!{EMFILE}`. An `open` then fails with EMFILE too (natively the fd is allocated before the path is looked up). After one handle closes, the next dup succeeds. No trap. The guest reaches 1019 (1024 minus stdio and two preopens), and `$!` reads "No file descriptors available", musl's text for EMFILE. EXECUTED (wasmtime CLI).

003's checks: `test_reopen_standard_handle_matches_native` (6) passes unchanged. `test_dup_of_a_handle_fails_clearly` is now `test_dup_of_a_handle_works`: the guest's stdout, stderr and status equal native's. The 003 review's `test_fdopen_of_a_shared_descriptor_fails_cleanly` (4) expected "Not supported". It is now `test_fdopen_of_a_shared_descriptor_matches_native`, with the same four programs compared with native. 002's `FAILS_IN_THE_SANDBOX` drops Test::Simple and Term::Table, which now load. All EXECUTED.

**Seen failing first (EXECUTED):** against the merged tree with no table (1ef9ab0), 22 of the first 24 descriptor checks failed. The two that passed were `<&=` on one descriptor and `reopen-keeps-number`, which need no copy. The six `&=` checks were written after that run and were not run against that module. `test_every_listed_extension_loads_as_natively` and `test_dup_of_a_handle_fails_clearly` failed on the first build with the table, because Test::Simple, Term::Table and the dup now work. Those two checks were then changed as above.

### Perl's own t/ (007's harness)

007's `run_suite.py` and `exclusions.toml` were copied unchanged from `/home/user/wasi-perl-wt/007-perl-t` (c726874) into a scratch folder, so 007's worktree was only read. They were run with `WASI_PERL_ARTIFACTS`, `WASI_PERL_TARGET` and `WASI_PERL_TARBALL` pointing here, under `uv run --no-project --with-editable ../004-runner python tests/perl_t/run_suite.py --jobs 3`. The baseline is the merged tree with no table (1ef9ab0, built in a scratch worktree). EXECUTED.

    baseline (no table): perl t/: 617 run, 291 passed, 175 excluded, 9 failed, 142 stale exclusions
    with the table:      perl t/: 617 run, 290 passed, 169 excluded, 10 failed, 148 stale exclusions

Most stale exclusions come from 002 and 003, which 007's list predates. The difference the table makes:

- **Now pass:** `op/fh.t`, `op/hash-clear-placeholders.t`, `op/hash-rt85026.t`, `op/numify_chkflags.t` ("Can't save STDERR"), `op/require_37033.t` (the "not lowest-free" platform exclusion), `test_pl/can_isa_ok.t`. Each should leave the exclusion list. That list is on 007's branch, so 007's builder makes the edit. This change does not.
- **`io/dup.t`:** exit 95 (ENOTSUP) before, 255 now, with only `ok 1` on stdout. Its STDOUT and STDERR are redirected to a scratch file before test 2, so the stop is not visible in the log. By the source, the next step after the copies is line 35, `` print `$cmd` ``, which the sandbox refuses (READ). Its reason is now `subprocess` (INFERRED).
- **`op/die_exit.t`:** unchanged by the table (ran 0 of 17, exit 255, in both runs). Its stderr goes to a scratch file at line 61, which 003's move already allowed, and its tests are each a `system` of a child perl (line 68, READ), which the sandbox refuses. Its reason is now `subprocess` (INFERRED).
- **`op/write.t`:** from 6 of 636 tests to 477. It stops at `fresh_perl` (backticks in `test.pl`). Its reason is now `subprocess`.
- **`io/perlio.t`:** from 28 of 48 to 46. It stops at `fresh_perl`.
- **`op/sselect.t` now fails, tests 19 and 20** (`select` with a write mask on a dup of STDOUT gives -1). The test used to skip that part because `POSIX::dup` failed. The cause is older than the table: Perl hands its bit-string mask to `select` as an `fd_set *` (`pp_sselect`, `pp_sys.c`, READ), but wasi-libc's `fd_set` is a count and a list of numbers (`pselect.c`, READ). So any mask past fd 2 is misread. The baseline module gives the same -1 and EBADF for a plain opened file at fd 6 (EXECUTED, wasmtime CLI). 4-argument `select` on descriptors is a separate fix, not this specification's.
- `benchmark/gh7094-…` differs between runs because it measures wall-clock time, as 007 records.

### Deviations, and what was found while building

1. **Start-up scans with `fd_fdstat_get`, not `fd_prestat_get`.** It stops at the first EBADF from 3, as wasi-libc does. A non-directory descriptor a host passes at 3 or above (a socket) is kept, not taken for the end.
2. **No new check that the embedder sees the same real descriptors** (decision 5). It follows from the design: no real descriptor is opened, moved or renumbered on the host except by the program's own opens and closes. INFERRED.
3. **003's dup check and its review's fdopen checks were inverted, not kept.** Decision 3 says every check of 003's decision 8 "must still pass". Those five checked the failure this specification removes, as 003's own landing note says of the dup check. They now compare with native.
4. **Merge interaction, fixed first (1ef9ab0):** with 003's refusals in, CPAN stops at its `$SIG{WINCH}` store before it reaches the dup, and Math::Complex, which loaded under 002 alone, stops at its load-time `local $SIG{FPE}`. Both are 003's decision 3. 002's review answer reached the same list. Math::Complex (and Math::Trig through it) not loading is worth a decision by the owner.
5. **wasi-libc's source was read** from a clone of github.com/WebAssembly/wasi-libc at 2e6fb9d, the commit wasi-sdk 34's `VERSION` names, under `context/prior_art/wasi-libc` (Apache-2.0 with LLVM exception, Apache-2.0, MIT; recorded in `context/prior_art/README.md`).

### Patches

None. `recipes/patches/perl-5.42.3/` does not exist. EXECUTED (`ls`).
