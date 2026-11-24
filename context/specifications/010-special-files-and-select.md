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

## Acceptance

1. The checks pass, EXECUTED, output in the landing notes; 001–009's checks still pass.
2. A fresh agent's review lands.

## Landing notes

(Written when built.)
