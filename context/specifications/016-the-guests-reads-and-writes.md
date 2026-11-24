# 016 — The guest's reads and writes

- **Status:** a draft by the orchestrator, 2026-11-24, for the owner to agree. The owner chose specifications 013 to 016 to come first (2026-11-24).
- **Owner:** Clay Gendron
- **Kind:** the build recipe (`recipes/config.sh`, a new `recipes/wasi-memchr.c`, `recipes/wasi-fd-table.c`, `recipes/wasi-cc`, `scripts/20_target.sh`), checks in `tests/build/`.
- **Depends on:** 009, 010, 013.
- **Research it stands on:** `../research/2026-11-24-perf-interp.md` (study C) §3–§5, with its recipe diffs and `proto/memchr-test.c`. Also `../research/2026-11-24-perf-vfs-io.md` (study E) §4.3, and `../research/2026-11-24-perf-codegen.md` (study B) §4.

## Intent

Every WASI call that reaches the host's files costs 15–60 µs under wasmtime-py, against under 1 µs for one the guest answers itself (study C §3.5). Under vfs, a provider answers each one. Perl reads and writes 8 KiB at a time and asks whether every file it opens is a terminal. Its line search, `memchr`, checks 4 bytes a step.

After this specification, on study C's workloads:
- `grep_log` drops from 111 to about 66 ms;
- `-pi` from 203 to about 112 ms;
- 200 small files from 67 to about 50 ms.

The fuel (guest instructions) drops by 10–20% on line work. Perl's behaviour does not change.

## Decisions

1. **A 64 KiB PerlIO buffer.**
   - `ccflags` gains `-DPERLIOBUF_DEFAULT_BUFSIZ=65536`. Only `perlio.c` reads that macro (READ: `perlio.h` line 152).
   - Reads and writes on files larger than the buffer fall by 8×.
   - It is 64 KiB, not 256 KiB: 256 KiB made the many-small-files workload slower in both of study C's runs (EXECUTED, C §4.1).
   - Memory is one buffer per handle in use, against the runner's 1 GiB cap.
2. **Our own `memchr`, comparing 16 bytes a step with WebAssembly SIMD128.**
   - It lives in `recipes/wasi-memchr.c`, written for this project, and is linked before wasi-libc so the libc member is never pulled in.
   - It uses aligned 16-byte loads and masks off the bytes outside the range. An aligned load that holds a byte of the range cannot pass the end of linear memory, whose size is a multiple of 64 KiB.
   - The module then needs SIMD128. That narrows no host this project supports: every host that runs exception handling, which perl.wasm already needs, also runs SIMD128. wasmtime enables it by default (INFERRED from the README's host list; study B §4.2).
   - Only this file is compiled with `-msimd128`. Study B found no gain from building everything with it.
3. **The descriptor table answers `isatty` for a regular file it has looked at.**
   - The table already looks at each name before opening it, to refuse special files (spec 010). If that look saw a regular file, `isatty` on the descriptor answers "not a terminal" with no host call. The link gains `--wrap=isatty`.
   - Everything else, and any descriptor the table did not look at (stdin, stdout and stderr included), asks the host as today.
   - A name swapped between the look and the open is the race the look already accepts. The worst outcome is a terminal buffered like a file, and a grant cannot reach a terminal.
4. **Not in this specification:**
   - wasm-opt (synthesis spec 9);
   - the leaner `@INC` (synthesis spec 7);
   - `NO_TAINT_SUPPORT`, which the owner declined on 2026-11-24 to keep `-T`.

## Checks (written first, seen failing; they count operations)

1. **Reads and writes.** A counting probe linker records the guest's WASI calls. `perl -ne 'print if /x/'` over a 1 MiB granted file makes at most 17 `fd_read` calls, against 129 today. `-pi` over it makes at most 17 `fd_write` calls.
2. **`memchr`.** A build check compiles `memchr-test.c` against the recipe's `memchr`. It compares with a byte loop at every start offset from 0 to 31, every length from 0 to 79 and every target position, with a match planted just before and just after the range, and `c` above 255. All 108,800 cases pass under wasmtime.
3. **Fuel.** `perl -ne '$n++ if /ERROR \d+/; END {print $n}'` over the baseline's 10 MB log uses at least 8% less fuel than the 0.1.0 module. This is a count, so it is stable.
4. **`isatty`.**
   - Opening 200 granted files makes 200 fewer `fd_fdstat_get` calls than today.
   - `-t $fh` is false on a regular file.
   - `-t STDIN` still asks the host.
   - A FIFO in a grant is still refused with `ENXIO`.

## Acceptance

1. The checks pass (EXECUTED).
2. The one-liner oracle passes on every platform in CI.
3. The *whole* of Perl's `t/` suite passes with no new exclusion. Study C ran only a 92-file slice.
4. The escape tests pass.
5. A research memo records the wall times before and after on the baseline's workloads, with commands.
6. `CHANGELOG.md` under "Unreleased", and `docs/` note that the module needs SIMD128.
7. A fresh agent's review lands.
