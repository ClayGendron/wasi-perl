# 010 — Special files are refused at open, and `select` reads Perl's bit vectors

- **Status:** written by the orchestrator 2026-11-24, under the owner's pre-agreement of the work toward 0.1.0; it closes two gaps the escape tests (005) and the descriptor table (009) found.
- **Owner:** Clay Gendron
- **Kind:** the build recipe (`recipes/wasi-fd-table.c`, perhaps a `--wrap` in `recipes/wasi-cc`), checks in `tests/build/` and `tests/escapes/`.
- **Depends on:** 009.
- **Research it stands on:**
  - Specification 005's landing notes (EXECUTED): a FIFO inside a grant makes wasmtime's host `open` wait for the other end, past the deadline, because the deadline stops wasm code only. It is a strict xfail today.
  - wasi-python's answer (READ, `src/wasi_python/_fs_paths.py`, `_look`): before forwarding an open to wasmtime, it asks wasmtime for the file's type with `path_filestat_get` on the same descriptor, lookup flags and path, and refuses anything that is not a folder, a regular file or a link the open will not follow, with `ENXIO`.
  - Specification 009's landing notes (EXECUTED): `op/sselect.t` tests 19–20 fail. Perl's four-argument `select` passes its bit-vector strings as the `fd_set` buffers (`pp_sys.c`, `pp_sselect`), but wasi-libc's `fd_set` is a count and a list of descriptors, not a bitmask.

## The problem

Two places where the guest's C library and the host disagree about what a call means. An open of a FIFO can hold the host past every limit. And `select(my $r = $bits, …)` reads garbage.

## Decisions

1. **The guest refuses special files before the host opens them.** Specification 009's `__wasi_path_open` wrapper first calls the real `__wasi_path_filestat_get` with the same descriptor, lookup flags and path. If the name exists and is not a directory, a regular file or (when the open does not follow links) a symbolic link, the open answers `ENXIO` and the host never opens it. A name that does not exist is forwarded unchanged, so `O_CREAT` works. The check runs inside the guest, which is enough here: the only code in the guest is Perl's, and a Perl program has no way to call a WASI import directly (no FFI, no dynamic loading). The residual race, a host process swapping the name between the look and the open, is the one wasi-python names too, and is documented.
2. **The FIFO escape check becomes a pass.** The strict xfail in `tests/escapes/` is turned into a check: a read open and a write open of a FIFO in a grant each fail with "No such device or address" well within a fuel limit, and the call returns. So does a guest-made link to the FIFO.
3. **Four-argument `select` reads Perl's bit vectors.** The guest translates between Perl's bitmask layout and wasi-libc's `fd_set` at the one place Perl calls `select` with a raw vector buffer. That is a wrap of `select` if no other object in the link uses `FD_SET` on a real `fd_set`; otherwise a wrap of Perl's own call site. The builder decides by reading the link with `llvm-nm`, and records it. Readable regular files and pipes answer as natively; the timeout form `select(undef, undef, undef, $t)` is unchanged.
4. **Checks** compare with native where native is defined: `select` over STDIN/a file with a bit vector built by `vec`, the returned vectors, and `IO::Select->can_read(0)` on a file. `op/sselect.t` is rerun.

5. **A rename over a file the program holds open succeeds on every host.** *(Added by the orchestrator while building, from the Windows leg of 008's CI, 2026-11-24.)* Every `perl -pi` edit fails on Windows with exit 13, "Cannot complete in-place edit of …: failed to rename work file … Permission denied". Perl's `argvout_final` (`doio.c`) renames the work file over the original while ARGV still holds the original open. wasmtime's host rename on Windows refuses to replace a file the process holds open, and reports `ERROR_ACCESS_DENIED` and `ERROR_SHARING_VIOLATION` as `EACCES` (wasmtime 51, `crates/wasi/src/filesystem.rs`, `from_raw_os_error`, READ). Linux and macOS allow it. The fix is the guest's, in the descriptor table, with no patch to Perl. `path_open` records, for each real descriptor it opens, the open flags, the rights and the descriptor flags. When the real `path_rename` fails with `EACCES` or `EPERM`, and the program holds open descriptors on the destination (the same `dev` and `ino` by `fd_filestat_get` and `path_filestat_get`), the guest takes these steps. It remembers each one's offset and closes its real descriptor. It retries the rename once. Then it opens the destination again for each, with its recorded arguments less `O_CREAT`, `O_EXCL` and `O_TRUNC`, seeks to the saved offset clamped to the new size, and puts the new real descriptor under the same virtual numbers. If the retry fails, it puts back what it can and returns the original error. Where the first rename succeeds, as on Linux and macOS, nothing runs. The one documented difference: after such a rename, a read through the old handle reads the new file, which for `-i` is already at its end. **Checks:** a test build of perl on Linux, relinked from the same objects with the table compiled with `-DWASI_PERL_TEST_HELD_RENAME`, under which the guest's host rename refuses a held destination as Windows does. That flag is set only by the test's own compile, so the shipped module has no seam a guest can reach. The checks are `-pi`, `-pi.bak`, `-lani`, `-0777 -pi` and multi-file edits compared with native, and a held file reopened under its number. The real proof is the Windows leg of 008's CI.

## Acceptance

1. The checks pass, EXECUTED, output in the landing notes; 001–009's checks still pass.
2. A fresh agent's review lands.

## Landing notes

Built 2026-11-24 on branch `spec/010-special-files-and-select`, from 009's head plus the spec commit, then merged with origin/spec/009-fd-table at f5199bd (009's review round and dev's 003 amendment, 7afccfb). Nothing was rebased.

### Commands and the artifact

    WASI_PERL_DOWNLOAD_CACHE=/home/user/wasi-perl/downloads WASI_PERL_JOBS=3 scripts/build.sh
    # 20_target: ok / 30_library: ok (1453 files)                                              EXECUTED
    sha256sum build/artifacts/perl.wasm; stat -c %s build/artifacts/perl.wasm
    # 7d8c48a71a499da90665467382eec87987abe1160472c7ada7f721a5a9387491, 9258122 bytes            EXECUTED

    uv run --no-project --with pytest --with wasmtime==51.0.0 pytest tests/build -q             EXECUTED
    377 passed in 14.26s
    WASI_PERL_ARTIFACTS=$PWD/build/artifacts WASI_PERL_ORACLE=$PWD/build/native-install/bin/perl \
        uv run pytest -q tests/escapes tests/runner tests/oneliners                              EXECUTED
    2 failed, 617 passed, 1 skipped

The two failures are the `from conftest import` collisions in `tests/runner` that are already on dev and that 009's notes record. `tests/escapes` alone: 13 passed in 3.99 s, the FIFO check included. EXECUTED.

### Decision 1: special files refused at open

`__wrap___wasi_path_open` calls `look()` after translating the directory and before the host open. `look()` asks the real `__wasi_path_filestat_get` with the open's directory, lookup flags and path. It answers ENXIO for anything but a directory, a regular file or a symbolic link, and ENOTDIR when the open has `O_DIRECTORY`. A failed look (the name does not exist, or the host will not describe it) is passed to the open unchanged, so `O_CREAT` works and every other error stays the host's. `O_CREAT|O_EXCL` skips the look, because the host refuses an existing name before it opens anything. This is the same rule as wasi-python's `_look` and `_refusal` (READ), less the memory juggling: the guest calls `path_filestat_get` with its own buffer. The race with a host process that swaps the name between the look and the open remains, and is documented in the code.

- `tests/build/test_special_files.py`, 10 checks, each guest in a child under the wasmtime CLI with a 20 s fail-safe:
  - The FIFO opens: `<`, `>`, `>>`, `+<`, `<:raw`, absolute, and `/g/./pipe`. Each gives "No such device or address".
  - A guest-made `symlink` to the FIFO is refused too, and `readlink` still reads it.
  - `open … or die "$!\n"` ends with status 6 (Linux ENXIO, through 003's errno table).
  - `-e`, `opendir` of the FIFO (ENOTDIR, as natively), `readdir`, create, append, read and `unlink` beside a FIFO all match native. All EXECUTED.
- **Two things found while writing them** (EXECUTED). `-p` is false for a FIFO in the guest: WASI has no file type for one, so `stat` cannot say so. The check uses `-e` instead, and `docs/sandbox.md` says so. Also, `sysopen($f, $name, 0)` gives EINVAL in the guest, because WASI's `O_RDONLY` is not 0. That one is not this specification's, and is recorded here only.
- `tests/escapes/test_escapes.py::test_a_fifo_in_a_grant_is_refused` was 005's strict xfail. It is now a check through `run()` with a 2,000,000,000 fuel cap and a 5 s deadline. A regular-file control opens every way. Then the FIFO's read, write and append opens and a link to it are each refused with ENXIO, `fuel_used` is reported (so the call ended inside the fuel limit, not at the deadline), and the call returns. EXECUTED.

### Decision 3: select over Perl's bit vectors

`llvm-nm -A -u` over every object of the link (`build/target/*.o` and every extension's objects) finds one reference to `select`, in `pp_sys.o` (`pp_sselect`), and no reference to `pselect` (EXECUTED). Nothing else in the link calls `select` with a real `fd_set`, so `select` itself is wrapped (`--wrap=select` in `recipes/wasi-cc`). `pp_sselect` passes `nfds = maxlen * 8` and buffers padded past `nfds / 8` bytes (READ, `pp_sys.c`).

`__wrap_select` reads the bits below `min(nfds, 1024)` into three static wasi-libc `fd_set`s, calls wasi-libc's `select`, and on success writes back exactly those bits. It leaves bits at or past 1,024 as they were, which is what Linux does past its descriptor table (native `vec($r, 2000, 1)` comes back set, EXECUTED). On an error the vectors are left as they were, as POSIX says.

Two host behaviours had to be answered in `poll_oneoff` for `select` to match native, and they serve `IO::Poll` too (READ, wasmtime 51 `crates/wasi/src/p1.rs`, `poll_oneoff`):

- **A regular file is ready only once a background read is done.** wasmtime answers a file subscription through `read_via_stream`, so a zero timeout can return first (`_poll(0, …)` on a file gave 0, EXECUTED). The guest answers a regular file's subscription itself, as always ready, which is what POSIX says.
- **A read subscription on STDOUT, or a write subscription on STDIN, fails the whole call with EBADF.** wasmtime reads only from Stdin and files, and writes only to Stdout, Stderr and files. The guest drops a subscription whose descriptor lacks `FD_READ` (or `FD_WRITE`), so that entry is never ready, as natively.

With either kind answered locally, a caller's clock is replaced by a zero timeout, so the call does not wait. That is the same rule as 009's closed-number events.

- `tests/build/test_select.py`, 13 checks against native:
  - Read select on STDIN and on a file; write select on a file; one descriptor in all three sets (`6`, `110` per handle).
  - Unset bits stay clear; a UTF-8-flagged mask; a closed number (-1, EBADF, vector kept); a bit past the table (0, bit kept).
  - Empty and undef masks; the timeout-only form; `IO::Select` `can_read`, `can_write` and `select`; a read select on a copy of STDOUT (0, while the write select is 1); the default output handle after a select.
  - All EXECUTED.
- **op/sselect.t passes** under 007's harness, copied unchanged from 007's worktree and run as in 009's notes. It failed tests 19 and 20 with 009, and tests 17 and 18 before the rights rule above. EXECUTED.

### Decision 5: a rename over a held file

- **Recorded at open.** `__wrap___wasi_path_open` stores each file's `oflags`, `fdflags` and both rights. The host's own descriptors (stdio, grants) are not reopenable.
- **The retry.** `__wrap___wasi_path_rename` passes the rename to the host. On EACCES or EPERM it runs `find_held()`. If the destination is a regular file and the program holds open files whose `(dev, ino)` equal it (wasmtime gives `dev: 1` and `ino` from one metadata hash in both `fd_filestat_get` and `path_filestat_get`, READ), the guest:
  - records each one's offset with `fd_tell` and closes its real descriptor (marked `UINT32_MAX` meanwhile);
  - retries the rename once;
  - reopens each held file at the rename's destination with its recorded open arguments, less `CREAT`, `EXCL` and `TRUNC`;
  - seeks to the saved offset, clamped to the new size.

  The virtual numbers are untouched. If the retry fails, the reopen finds the old file at the destination, and the first error is returned. If a reopen fails, that file reads EBADF from then on.
- **The test build.** With `-DWASI_PERL_TEST_HELD_RENAME`, the host rename is `host_rename()`, which refuses a held destination with EACCES, as Windows does. Only `tests/build/test_rename_over_open.py` compiles that flag. Its session fixture compiles a test copy of the table into its temporary folder and relinks perl from `build/target`'s objects with that copy first: perl's link variables come from the Makefile through `make -f Makefile -f -`, and wasi-cc's own archive then supplies nothing. The shipped module is never built with the flag. So no seam exists that a guest could reach. EXECUTED (the link takes about a second).
- **`tests/build/test_rename_over_open.py`**, 8 checks:
  - `-pi`, two files, `-pi.bak`, `-lani`, `-0777 -pi` to a shorter file, and `-ni` with `eof` across two files. Stdout, stderr, status and files match native.
  - A held file renamed over: it keeps its number and, with `sysread`, reads `w file` (the new file from offset 2) where native reads `d file`. The shipped module reads `d file`, as natively, which shows the retry runs only where the host refuses.
  - A held destination in the read-only library grant: the retry fails too, `$!` is "Permission denied", and the handle reads on (`package| strict`).

  All EXECUTED.
- **Seen failing (EXECUTED):** with the retry disabled in the test copy (`n = 0`), 7 of 8 fail. `-pi` ends with exit 13 and "Cannot complete in-place edit", as on the Windows CI leg. The eighth, the retry-also-fails case, passes either way, as it should.
- **Not proved here:** that wasmtime on Windows reports the refusal as EACCES and that the reopen succeeds there. The mapping is READ from wasmtime 51's `from_raw_os_error` (`ERROR_ACCESS_DENIED` and `ERROR_SHARING_VIOLATION` give `Access`, which is EACCES). The proof is the Windows leg of 008's CI.

### Seen failing first (EXECUTED)

On 009's module, before any of this: 18 of the 22 select and special-file checks failed. Each FIFO check hit its 20 s fail-safe. The 4 that passed were the select checks that need no descriptor mask: past the table, empty masks, timeout only, and the default handle. The escape check timed out at its fail-safe.

### Found while building

- **op/tie_fetch_count.t** trapped "memory out of bounds" with no table (the no-table baseline), with 009 and with 009's merged review round. With dev's 003 amendment it runs to its end and fails only tests 170 and 171 (`chown 0,0,$tied_glob`, FETCH count 0). Neither result is from the table: it trapped before there was one. EXECUTED.

### Patches

None. EXECUTED (`ls recipes/patches` finds nothing).

### The review round (2026-11-24)

Findings of `context/reviews/010-special-files-and-select-pr-2026-11-24.md`, fixed in one commit.

- **H1, `utime` on a FIFO held the host.** Fixed. `__wasi_path_filestat_set_times` is wrapped. When the call follows links, the guest first looks with `path_filestat_get` and the same flags. A folder or a regular file goes on unchanged. A special file that is the name itself (looked at again with flags 0) goes on without following. wasmtime 51 then sets the times with `utimensat(parent, name, AT_SYMLINK_NOFOLLOW)` through the parent folder and opens nothing (`primitives/unix/mod.rs`, `set_times_nofollow`). That sets the same times, as natively. A link to a special file answers ENXIO. With following, wasmtime opens the file, for writing and then for reading, to call `futimens` (`set_times`, same file). That open was the hold. READ. Checks, all EXECUTED:
  - The escape check's program now also runs `utime(undef, undef, "pipe")`, `utime(1, 1, "pipe")` and `utime` through a guest-made link: `utime now`, `utime set`, then `utime link: No such device or address`, inside the fuel limit. Its regular-file control sets all three.
  - `test_utime_on_a_fifo_is_native` checks the times read back by `stat` (`1000 2000`) and a regular file beside the FIFO, against native.
  - `test_utime_through_a_link_to_a_fifo_is_refused` checks ENXIO through a link, while a link to a regular file still sets its target's times.
- **The audit of every host call that takes a path** (wasmtime-wasi 51.0.0's own `filesystem/primitives`, READ, from static.crates.io; Linux and macOS):
  - `path_filestat_get` is `stat`. On Linux that is `open_beneath(O_PATH)`; an `O_PATH` open never opens a FIFO for I/O. Otherwise it is `stat_unchecked`, an `fstatat` on the last component. `metadata_hash_at` is the same `stat`.
  - `path_readlink`, `path_link`, `path_rename`, `path_unlink_file`, `path_remove_directory`, `path_create_directory` and `path_symlink` each `open_parent` and then call the `*at` syscall on the last component (`readlinkat`, `linkat`, `renameat`, `unlinkat`, `mkdirat`, `symlinkat`). None opens it.
  - Every component in the middle of a path is opened with `dir_options()`, which is `O_DIRECTORY` (plus `O_PATH` on Linux). The kernel refuses a FIFO there with ENOTDIR before opening anything.
  - `path_filestat_set_times` without following is the `utimensat` above. With following it is now looked at first.
  - `path_open` is looked at first (decision 1).
  - The `fd_*` calls act on descriptors that are already open. `fd_readdir` lists entries and stats them. It opens none.

  On Windows, `stat` and `metadata_hash_at` open a metadata handle (`open_metadata_handle`). Windows has no FIFO in a folder; its device names are the security review's L6, for 008's Windows leg.
- **M1, an event past the caller's array.** Fixed. The host now writes into a buffer of the wrapper's own, one event per subscription it is sent. At most as many events as the caller's array has left are copied after the local ones, and the added zero clock's event is dropped. `tests/build/test_poll_buffer.py` compiles the table from the recipe with only its call to the host renamed to a fake host. The fake answers every subscription it is sent, and a canary follows the caller's two events. With the 010 head's table the canary breaks ("canary broken at 0"); with this one it is whole and the events are `10 11`. EXECUTED. `test_select.py` adds a no-timeout select of a file and STDOUT against native.
- **L1.** Fixed. The error set is never passed to wasi-libc, which answers ENOSYS when it has nothing else to watch. On success its bits below the table's size come back clear, as natively (`error-set-only` in `test_select.py`, EXECUTED).
- **L2.** Fixed. The reopen after a held rename is looked at first, as every open is. What it opened must be a regular file, or it is closed again and the number answers EBADF. The comment names the race with another host process.
- **L3.** Fixed. Before anything is closed, each held file's current descriptor flags are read with `fd_fdstat_get`, and its rights are narrowed to the current ones. The reopen uses those.
- **L4.** Fixed. Before any held descriptor is closed, the destination is opened once with those arguments and closed again. If that probe fails for any file, the rename's first error is returned and nothing is touched.
- **L5.** Fixed. A file whose reopen failed is marked `GONE`. Its `close` succeeds without asking the host. `poll_oneoff` answers it locally with EBADF, for that entry alone. Every other call through it answers EBADF.
- **L6.** The module is not byte-reproducible: `perl -V` embeds `__DATE__` and `__TIME__`, as 001 found. So a hash from one build is not another build's. The hash in "Commands and the artifact" is of the build made at the head named there, 6f355aa.

