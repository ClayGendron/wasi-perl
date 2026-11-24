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

(Written when built.)
