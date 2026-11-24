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

(Written when built.)
