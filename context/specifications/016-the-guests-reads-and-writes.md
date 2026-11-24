# 016 — The guest's reads and writes

- **Status:** agreed by the owner 2026-11-24 ("Agree all four (Recommended)"). Written by the orchestrator; amended the same day for the review `../reviews/perf-research-and-specs-013-016-pr-2026-11-24.md` (L3, L4, L5).
- **Owner:** Clay Gendron
- **Kind:** the build recipe (`recipes/config.sh`, a new `recipes/wasi-memchr.c`, `recipes/wasi-fd-table.c`, `recipes/wasi-cc`, `scripts/20_target.sh`), checks in `tests/build/`.
- **Depends on:** 009, 010, 013.
- **Research it stands on:** `../research/2026-11-24-perf-interp.md` (study C) §3–§5, with its recipe diffs and `proto/memchr-test.c`. Also `../research/2026-11-24-perf-vfs-io.md` (study E) §4.3, and `../research/2026-11-24-perf-codegen.md` (study B) §4.

## Intent

Every WASI call that reaches the host's files costs 15–60 µs under wasmtime-py, against under 1 µs for one the guest answers itself (study C §3.5). Under vfs, a provider answers each one. Perl reads and writes 8 KiB at a time and asks whether every file it opens is a terminal. Its line search, `memchr`, checks 4 bytes a step.

After this specification, on study C's workloads (EXECUTED in study C, `results/time3-summary.md`):
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
2. **`memchr`.** The test program moves from study C's `proto/` to `tests/build/memchr-test.c`. The build compiles it against the recipe's `memchr` into `build/tests/memchr-test.wasm`, and a check in `tests/build/` runs it under wasmtime-py. It compares with a byte loop at every start offset from 0 to 31, every length from 0 to 79 and every target position, with a match planted just before and just after the range, and `c` above 255. All 108,800 cases pass under wasmtime.
3. **Fuel.** `perl -ne '$n++ if /ERROR \d+/; END {print $n}'` over the baseline's 10 MB log uses at least 8% less fuel than the 0.1.0 module. This is a count, so it is stable.
4. **`isatty`.**
   - Opening 200 granted files makes at least 200 fewer `fd_fdstat_get` calls than today: one fewer per file opened.
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

## Landing notes

Built 2026-11-24 by the implementer (Claude Opus 5.5) on `spec/016-the-guests-reads-and-writes`, from dev at `236dddc`, Linux x86-64, CPython 3.13, wasmtime-py 51.0.0, as root. **Not yet reviewed.** Every claim below was run (EXECUTED) unless it says otherwise.

### What was built

- **Decision 1.** `recipes/config.sh`'s `ccflags` begins with `-DPERLIOBUF_DEFAULT_BUFSIZ=65536`, with a comment saying why.
  - Two effects the decision did not weigh, both found by the review (M1, L1) and now in `docs/sandbox.md`, "Differences from native that remain": a call stopped by its deadline, fuel or a trap loses up to 64 KiB of buffered stdout, not 8 KiB (`$| = 1` flushes every print); and a buffered read reads ahead 64 KiB, so `sysseek(F, 0, 1)` after one `<F>` answers 65536 where native answers 8192. And each handle in use holds 64 KiB, so 250 handles under a 16 MiB `memory_bytes` now run out (L2), which the README's limits section says.
- **Decision 2.** `recipes/wasi-memchr.c`, written for this project from study C's prototype: aligned 16-byte loads, `i8x16.eq`, `i8x16.bitmask`, the bytes outside the range masked off. `scripts/20_target.sh` compiles it alone with `-msimd128` into `build/stubs/wasi-memchr.o`, which `libs` and `perllibs` name after `wasi-stubs.o`, so it is linked ahead of wasi-libc. `20_target.sh` also links `tests/build/memchr-test.c` with the same object into `build/tests/memchr-test.wasm`, and the test source is one of the step's stamp inputs.
- **Decision 3.** `recipes/wasi-fd-table.c`: each open file has a `regular` flag, set from the look before `path_open` and cleared for every other new entry. `__wrap_isatty` answers 0 with errno ENOTTY when the descriptor's open file has the flag and still has a host descriptor; everything else calls wasi-libc's `isatty`. `recipes/wasi-cc` adds `--wrap=isatty`. dup, dup2, `fd_renumber` and a reopen after a rename keep the open file, so they keep the flag.
- No patch to Perl. No change under `src/`.

### Choices the specification left to the implementer

- **How the checks count calls.** Check 1 asks for "a counting probe linker". wasmtime-py's WASI functions cannot be called from a Python host function: a wrapper that counts and passes the call on fails with "missing required memory export" (EXECUTED: a scratch probe on dev's module). So the checks count from the wasmtime 51 command line's trace, `WASMTIME_LOG=wasmtime_wasi=trace`, which is the same wasmtime-wasi; study C counted the same way. The traced runs get a throwaway `/dev` and a folder of their own and no library, since the command line grants every folder writable; none of their programs loads a module.
- **What "1 MiB" is.** Exactly 1,048,576 bytes: 16,384 lines of 64 bytes, every fourth holding an `x`.
- **Check 3's "0.1.0 module".** No 0.1.0 wheel is published (EXECUTED: PyPI answers "Not Found" for wasi-perl). The `perf-base` worktree's `perl.wasm` is built from the same recipes and scripts as 0.1.0 (EXECUTED: `git diff aac5d4d a5fd83a -- recipes scripts` is empty). The check holds its count, `FUEL_0_1_0 = 643_780_719`, measured by the check's own `_fuel`, and the memo gives the command. The check is not marked slow: it takes about two seconds.
- **Check 4's "fewer than today".** The check cannot run today's module, so it asserts two things that are each false today: no `fd_fdstat_get` is made on any descriptor `path_open` gave (today 201), and 200 files make at most 199 more `fd_fdstat_get` than one file (today 398).
- **More checks than the four,** each a part of decision 3: `-t` and `POSIX::isatty` on a file opened for reading, for append, on a copy of it and on `STDIN` reopened onto it all answer as native perl does, "notty" with ENOTTY; a file created by its open, which the look could not see, still asks the host; and `-t` on each of `STDIN`, `STDOUT` and `STDERR` makes one more host call. And for decision 2: `memchr` in `perl.wasm` and in `memchr-test.wasm` holds `i8x16` instructions, and no other function of `perl.wasm` holds a SIMD instruction.
- **`memchr` without SIMD.** Study C's prototype had a 64-bit word loop for a build without `-msimd128`. Decision 2 has no such build, so the file stops with `#error` instead.
- **The SIMD note** is in `docs/sandbox.md` ("What the host must run") and in the README's first lines, where the host's needs are stated.

### The checks

`tests/build/test_reads_and_writes.py`, 15 checks. On dev's module (the "before" build, `build/artifacts` before the change) 10 failed and 5 passed (EXECUTED: `uv run pytest tests/build/test_reads_and_writes.py`):

| check | before | after |
|---|---|---|
| a line read over 1 MiB: at most 17 `fd_read` | failed, 129 | passed, 17 |
| `-pi` over 1 MiB: at most 17 `fd_write` (and `fd_read`) | failed, 128 | passed, 16 (17) |
| `memchr-test.wasm`: 108,800 cases | failed, not built | passed, "ok 108800" |
| `memchr` holds `i8x16`, in `perl.wasm` and in the test | failed, both | passed |
| only `memchr` uses SIMD | failed | passed |
| fuel of `grep_log` at most 92% of 0.1.0's | failed, 100.0% | passed, 89.2% |
| 200 files: no `fd_fdstat_get` on an opened file, at most 199 more than one file | failed, 201 and 398 | passed, 0 and 199 |
| a regular file is not asked whether it is a terminal | failed, 5 | passed, 0 |
| a created file still asks the host twice | failed, 3 (with `/dev/null`'s) | passed, 2 |
| `-t` on a regular file, as native | passed | passed |
| `-t STDIN`, `STDOUT`, `STDERR` ask the host | passed (3) | passed (3) |
| a FIFO in a grant: ENXIO, and no `path_open` of it | passed | passed |

The five that passed before are controls: they hold a behaviour the change must keep. Perl opens `/dev/null` at every start, a regular file in the `/dev` grant, so every program saves one `fd_fdstat_get`; the counts above include it.

### Results

```
$ uv run pytest tests/build tests/escapes
463 passed in 57.42s
$ uv run pytest -m "not slow"
472 passed, 3 skipped, 245 deselected
$ uv run ruff check src tests && uv run ruff format --check src tests
All checks passed!
31 files already formatted
$ uv run --no-project python -I scripts/assemble_artifacts.py
assembled 1455 files, 32195712 bytes
$ WASI_PERL_ORACLE=$PWD/build/native-install/bin/perl WASI_PERL_ARTIFACTS=$PWD/src/wasi_perl/artifacts \
    uv run pytest tests/runner tests/escapes tests/oneliners -ra
678 passed, 2 skipped               # the oneliners alone: 448 passed
$ flock /home/user/wasi-perl-wt/machine.lock uv run python tests/perl_t/run_suite.py --quiet --list-failures --jobs 4
perl t/: 616 run, 439 passed, 177 excluded (subprocess 143, signals 11, refused 2, porting 6,
platform 6, not-this-platform 6, bug 3), 0 failed, 0 stale exclusions, 0 regressed (19052 guarded oks)
```

- The skips: `test_arguments.py:208` (root can write every folder), `test_fixed_cost.py:392` (macOS, run in CI), and in the first run `tests/perl_t/test_suite.py:155` (the pristine tree was not yet unpacked).
- The whole `t/` suite has the same score as 013's, with `tests/perl_t/exclusions.toml` untouched.
- The one-liner oracle here is native perl, `build/native-install/bin/perl`, not the recorded JSON CI uses. Acceptance 2 asks for every platform in CI; that is for the pull request's run (not run here).
- `perl.wasm` is SHA-256 `06ea464ba43c62fc2a3967326c9cea247c6f95bfe1e73c1bf21d64dc1c21a53d`, the same in `build/artifacts` and in the package copy.
- wasmtime 51 refuses the module with SIMD turned off ("failed to compile: wasm[0]::function[3518]::memchr"), and runs dev's.

### Measured

`../research/2026-11-24-the-guests-reads-and-writes-measured.md`, against dev's module, three interleaved rounds under the lock: `grep_log` 113.6 → 61.3 ms, `-pi` 202.3 → 122.4 ms, 200 small files 57.7 → 45.4 ms, `count_lines` over the 10 MB log 72.8 → 32.2 ms. Fuel against 0.1.0: −10.8% `grep_log`, −13.0% `-pi`, −19.9% `count_lines`, −16.0% `many_files`, −2.5% `sum_csv`, nothing elsewhere. `-pi` lands 10 ms above study C's prediction of 112 ms; the other two at or below theirs.

### Doubts

- **The runner relies on wasmtime's default for SIMD.** `src/wasi_perl/_engines.py` states four wasmtime defaults the speed depends on, but not `wasm_simd`, which the module now needs to load at all. wasmtime-py 51 enables it (EXECUTED: every runner check passes). Stating it there belongs to `src/`, which specification 015 is changing now, so it is left for a later change (INFERRED: a one-line `cfg.wasm_simd = True`).
- **The checks need the wasmtime command line** for their counts. It is in `build/tools` on Linux x86-64 only, so on another host those checks skip with the name of the script that fetches it, as the other checks of `tests/build` that use it do.

### Revised against the review, round 1

The review (`../reviews/016-the-guests-reads-and-writes-pr-2026-11-24.md`, "Lands": M1, M2, L1–L6) is answered in one commit.

- **M1, up to 64 KiB of stdout lost at a stop: documented.** `docs/sandbox.md` ("Differences from native that remain") says a stopped call keeps only what was flushed, that up to 64 KiB can be lost against native's 8 KiB, and that `$| = 1` flushes every print. The README's limits section points there. Decision 1's notes above say so. The reviewer's numbers (READ, the review): a fuel stop after 60,000 bytes kept 57,344 bytes on dev's module and keeps 0 on this one.
- **L1, 64 KiB read-ahead: documented** beside M1.
- **L2, memory per handle: documented** in one README line: each open handle holds 64 KiB once read or written; 250 take about 16 MiB.
- **L3, `memchr` at the end of linear memory: checked.** `tests/build/memchr-test.c` now grows memory by one page and searches ranges of 0 to 80 bytes that end at its last byte, with the target at every position, `c` above 255, a range that stops just before the target and a hit just before the range; the last byte found from 1 to 4,096 bytes away; and two empty ranges at and past the end of memory, which must load nothing. That is 13,979 more cases. The rebuilt test prints `ok 108800` and `end of memory ok 13979` (EXECUTED: `wasmtime run build/tests/memchr-test.wasm`, and the check). It could not be seen failing first: the code was already right, as the reviewer's own 238,756-case probe found.
- **L4, count checks with upper bounds only: lower bounds added.** `16 <= fd_read <= 17` over the 1 MiB file, `16 <= fd_write <= 17` and `16 <= fd_read <= 17` for `-pi`, at least 200 `path_open` in the 200-file run and one in the one-file run, and at least 2 in the regular-file run. Seen working on an empty trace (EXECUTED: with the trace's regular expression patched to match nothing, the four checks that hold these bounds failed: "4 failed"; the same patch on the previous version of the file gave "4 passed").
- **M2, a skip in `tests/build` passes CI, and L5, `_engines.py` does not state `wasm_simd`: recorded** as open items in `../roadmap.md`, under the speed section. Both touch files outside this specification's kind (`.github/`, `tests/build/conftest.py`, `src/`).
- **L6, `-t` on the random device sets ENOTTY where Linux sets EINVAL:** not this change's (the review: dev's module answers the same). Not fixed here.

The rebuild for `memchr-test.c` rebuilt `perl.wasm`: SHA-256 `c8d7340333a8b077c1659b7626abf70885d703a61b81c11ff725550b68165e41`. It differs from `06ea464b…a53d` only in its "Compiled at" time, two bytes (EXECUTED: `cmp -l` gives 2 lines; `strings` shows `14:21:35` and `14:53:35`).
