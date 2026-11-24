# Review: 016, the guest's reads and writes (PR #24, 2026-11-24)

Reviewer: an agent (Claude Opus 5.5, `claude-opus-5-5`), a fresh sub-agent of https://claude.ai/code/session_01ThzDEUwftdmy8zWBJK1gWa; not the author of any file in this change.

- **Scope:** `git diff origin/dev...HEAD` on `spec/016-the-guests-reads-and-writes` at `98cf9fe`: 31 files, +1383 −14. I read the specification with its landing notes, `AGENTS.md`, the constitution, the 013 review, the memo and its scripts, `recipes/wasi-memchr.c`, the `recipes/wasi-fd-table.c` diff with every function it touches (`new_file`, `start`, `release`, `look`, `path_open`, `path_rename`, `fd_renumber`, `dup_from`, `dup_onto`, `fd_fdstat_get`, `open_random`), `recipes/wasi-cc`, `recipes/config.sh`, `scripts/20_target.sh`, both test files, `docs/sandbox.md`, the README and `CHANGELOG.md`.
- **Build:** I did not rebuild. `build/artifacts/perl.wasm` and `src/wasi_perl/artifacts/perl.wasm` are both SHA-256 `06ea464b…a53d`, the hash the landing notes give. dev's module is in `build/before/artifacts/` (`1a566f80…b343`) (EXECUTED: `sha256sum`).
- **Probes:** in `/tmp/claude-0/-home-user-wasi-perl/ecbe2564-8e44-50cd-9bc9-1c1b283f37e5/scratchpad/probes-016/`. No probe was timed, so none held the machine lock.

## Verdict

Lands. The three decisions are built as the specification says, and the checks prove them. They fail on dev's module and pass on this one. CI is green on all four systems and on Perl's whole `t/` suite.
- `memchr` is correct for every start, length and target, for `c` above 255, for `n == 0`, and for a range that ends at the last byte of linear memory. Its loads cannot pass the end of memory.
- The `isatty` flag follows the open file through every table operation. It is never set on a standard descriptor, a terminal, the random device or a FIFO.
- The 64 KiB buffer gives the same bytes as native perl on `-i`, `-0777`, `-00`, `-lane` and record reads over files larger than it.

The two Medium findings are not defects in the change. M1 is a user-visible effect of decision 1 that nothing names. Fix it in this branch with a few lines of docs. M2 is a gap in CI that this change leans on more than any before it. It touches `.github/`, outside this specification's kind, so answering it in writing with a follow-up recorded in the roadmap is enough.

## Findings, ranked

### High

None.

### Medium

**M1. A call stopped by its deadline, its fuel or a trap now loses up to 64 KiB of stdout, not 8 KiB, and nothing says so.** `recipes/config.sh` (`ccflags`), with decision 1.
- PerlIO flushes stdout when its buffer fills. The guest's stdout is a host file, not a terminal, so it is fully buffered. A program that is stopped never flushes what is left in the buffer.
- EXECUTED (`wasi_perl.run`, dev's module against this one):
  - `print "x" x 60000; 1 while 1` with `fuel=50_000_000`: dev keeps 57,344 bytes of stdout, this module keeps 0.
  - 20,000 `print "line $_\n"` (208,894 bytes), then a loop, with `deadline_s=1.5`: dev keeps 204,800 bytes, this module 196,608.
- The specification says "Perl's behaviour does not change". Decision 1 weighs only memory. A stopped one-liner is exactly when an agent reads partial output: a `grep` over a large log that hits the deadline now returns up to 64 KiB fewer matches. Native perl killed by a signal also loses its buffer, but that buffer is 8 KiB.
- **Fix:** say it in `docs/sandbox.md`, or in the README's limits paragraph, and in decision 1's landing notes. For example: "A call stopped by its deadline or fuel keeps the output the program had flushed. PerlIO flushes stdout every 64 KiB, so up to 64 KiB written just before the stop is lost; `$| = 1` flushes every print." No code change is needed.

**M2. In CI, a skipped check in `tests/build` passes the job, and the checks for acceptance 1 and 4 skip whenever the wasmtime command line is not at one hard-coded path.** `tests/build/_build_helpers.py:17`, `tests/build/conftest.py:50-52`, `.github/workflows/ci.yml:87-88`.
- `WASMTIME_CLI` is `build/tools/wasmtime-v51.0.0-x86_64-linux/wasmtime`. The `wasmtime_cli` fixture skips when that file is missing. CI runs `pytest tests/build -ra` with no step that fails on a skip. The test job has such a step ("No guest check was skipped"), but it covers only the escape and one-liner checks.
- Today the checks run in CI. The build job unpacks the command line through `scripts/00_download.sh`, and the job log of `98cf9fe` shows `438 passed`, the same as `pytest tests/build --collect-only` collects here (EXECUTED: `gh api …/actions/jobs/138523891680/logs`; READ: `00_download.sh:36-43`). So nothing is hidden now.
- The risk is a later change. A wasmtime bump in `downloads/SHA256SUMS` that leaves `_build_helpers.py` alone would skip 9 of this file's 15 checks, every call-count check among them (READ: the checks that take the `wasmtime_cli` fixture), plus the command-line checks of four other files, and CI would stay green. The pattern is older than this change. This change is the first to rest a specification's acceptance checks on it.
- Other skips have the same shape in CI, all absent today: `llvm-objdump`, native perl and wasmtime-py.
- **Fix:** one of these two.
  - Preferred: a step after "The checks on the built perl.wasm" that reads a JUnit file, as the test job does, and fails on any skip in `tests/build`. The skip of `test_a_fifo_in_a_grant_is_still_refused` for "POSIX FIFOs" cannot happen on Linux.
  - Or: make `_need` in `tests/build/conftest.py` fail instead of skip when `CI` is set.
  - Either way it is a change to CI and test infrastructure. Record it as a follow-up in `context/roadmap.md`, or answer it here.

### Low

**L1. `sysseek` and `sysread` after a buffered read differ from native.** Decision 1.
- EXECUTED (`open F, "<", big; <F>; print sysseek(F,0,1), " ", tell(F)` over a 1 MiB file): native perl 5.42 and dev's module print `8192 64`. This module prints `65536 64`.
- A program that mixes `<F>` with `sysread` or `sysseek` on one handle sees the larger read-ahead. This is rare in one-liners and follows from decision 1 itself.
- **Fix:** one line in decision 1's landing notes, and in `docs/sandbox.md` beside M1, that the read-ahead is 64 KiB, against native's 8 KiB.

**L2. Many open handles need more memory.** Decision 1 ("one buffer per handle in use, against the runner's 1 GiB cap").
- EXECUTED: 250 handles, each with one line read. With the default limit and with `memory_bytes=32 << 20`, both modules print `250`. With `memory_bytes=16 << 20`, dev's module prints `250`, and this module dies with "Out of memory in perl:util:safesysmalloc".
- The table allows 256 numbers (`FD_LIMIT`), so the buffers take at most 16 MiB. That is harmless under 1 GiB, or the README's 128 MiB example, but a caller that sets a small `memory_bytes` can now fail where it did not before.
- **Fix:** add "each open handle holds 64 KiB once it is read or written" to the README's limits paragraph.

**L3. The `memchr` check does not test the one property decision 2 rests on: a range that ends at the end of linear memory.** `tests/build/memchr-test.c`.
- The test searches a 256-byte static buffer in the middle of memory. A wrong rounding of the last block would not be caught there, only by a trap in use.
- My probe (`memchr-probe.c`, linked with `build/stubs/wasi-memchr.o`, run under the wasmtime 51 command line) covered that case and more: 238,756 cases, 0 different from a byte loop (EXECUTED). It tested:
  - every range of 0 to 100 bytes that ends at `__builtin_wasm_memory_size(0) * 65536`, with the target at each position, just outside the range, and `c` as `'x'`, `'x'+256` and `'x'-512`;
  - the last byte of memory found from 4,095 bytes away;
  - `c` from −1000 to 1000, `INT32_MIN` and `INT32_MAX`;
  - ranges starting at address 0;
  - `n == 0` with a pointer past the end of memory, which returns `NULL` without a load;
  - `n` of `SIZE_MAX`, `SIZE_MAX - 5` and `SIZE_MAX - 16`, with a match present;
  - 200,000 random ranges up to 64 KiB.
- **Fix:** add the end-of-memory ranges and the `n == 0` case past the end to `memchr-test.c`. Grow memory by a page so the top is unused, then search ranges that end at its last byte.

**L4. The call-count checks have upper bounds only.** `tests/build/test_reads_and_writes.py`.
- If the trace regular expression ever stopped matching, for example after a wasmtime log-format change, every count would be 0. Five assertions would then pass without proving anything: `fd_read <= 17`, `fd_write <= 17`, `on_opened(...) == 0` twice, and `per_199 <= 199`.
- Checks with exact counts in the same file would still fail (`== 2`, `+ 1`, the FIFO's `path_open`), so the suite would notice. The checks one by one would not.
- EXECUTED: the parse works today. Over the 1 MiB file, `-ne` gives `fd_read` 17 and `fd_write` 4. `-pi` gives `fd_read` 17 and `fd_write` 16.
- **Fix:** add lower bounds, for example `16 <= fd_read <= 17`, and `t.count("path_open") >= 200` in the 200-file check.

**L5. `_engines.py` does not state `wasm_simd`. I agree with the author's choice to defer it, but the doubt should be tracked.** `src/wasi_perl/_engines.py:180-192`.
- The four defaults stated there are performance defaults. If one changed, every call would get slower without anyone seeing. SIMD is different. Without it the module does not compile (EXECUTED by the author, READ: "failed to compile: wasm[0]::function[3518]::memchr"), so every runner check would fail at once. The failure is loud, so the deferral costs nothing today.
- `wasm_exceptions` is stated because the module needs it to load. `wasm_simd` is now in the same class, so `cfg.wasm_simd = True` belongs next to it (INFERRED).
- The doubt lives only in the landing notes.
- **Fix:** name it in specification 015's notes or in the roadmap, so the change that next touches `_engines.py` makes it.

**L6. Not this change's: `-t` on the virtual `/dev/urandom` sets `$!` to ENOTTY, and native Linux gives EINVAL.**
- EXECUTED (`tty.pl`): native perl prints `urandom: notty 22` and `random: notty 22`. Both this module and dev's print `notty ENOTTY`. So `-t` is false, as natively, and this change does not alter the device's answer. The random device keeps `regular` at 0, and `isatty` goes to wasi-libc and the table's own `fd_fdstat_get`.
- **Fix (a follow-up to 013, optional):** `__wrap_isatty` could answer for `VIRTUAL_RANDOM` with EINVAL itself, which would also spare its one `fd_fdstat_get`.

## What holds (checked and found sound)

1. **`recipes/wasi-memchr.c` is correct.**
   - **No load leaves memory.** The first load is the aligned block that holds `s`. A later block is loaded only when `left > 16` held for the one before it, so the range reaches into the new block. Every block loaded is aligned to 16 and holds a byte of the range. Linear memory is a whole number of 64 KiB pages, so the block lies inside memory. WebAssembly has no unmapped pages below the end, so nothing below it faults (READ, with the probe in L3).
   - **The answer is right.** The first block masks off the `skip` bytes before `s`. `i < left` rejects a hit at or past the range's end in every block. `n == 0` returns before any load. `(int8_t)(unsigned char)c` takes `c` modulo 256, as C requires. `left` is cut short only when `n > SIZE_MAX - 16`. Memory ends before that point, and the scan stops at the first match.
   - **Only this `memchr` is linked.** wasm-ld's `--trace-symbol=memchr` on a link with the object shows the definition from `wasi-memchr.o` and no extraction of `libc.a(memchr.c.obj)`. That member defines only `memchr`. The other libc members (`strnlen`, `memmem`, `strstr`, `fgets`, `getdelim` and others) reference `memchr` itself, not an alias. `wmemchr` is a separate function. `perl.wasm` has one function named `memchr` (EXECUTED: `llvm-nm -A libc.a`, `llvm-objdump -d perl.wasm`).
   - `test_only_memchr_uses_simd` would catch SIMD spreading to other code, and `test_the_memchr_linked_compares_sixteen_bytes_a_step` would catch libc's `memchr` coming back.
2. **The `isatty` flag is right through every table operation.**
   - `regular` lives on the open file, not the number. `new_file` clears it, and it is the only place a free entry is reused. `path_open` alone sets it, from the same `path_filestat_get` the look already makes. The rename's reopen keeps the flag, and it keeps the descriptor only if the reopened file is a regular file. So `dup`, `dup2`, `dup3`, `F_DUPFD`, `fd_renumber`, close and reuse are all right by construction (READ).
   - EXECUTED, `tty.pl` against native perl: a file, an `F_DUPFD` copy, a `dup`, the copy after the original was closed, a created file on a reused number, a folder, `/dev/null`, `STDOUT` reopened onto a file, `STDIN` reopened onto a file, `POSIX::dup2` onto 0, `POSIX::isatty(99)` (EBADF), and a handle held across a rename over its name. Every answer and errno equals native's, apart from L6. The output is byte-identical to dev's module.
   - **Terminals.** `start` gives 0, 1 and 2 fresh entries, so `regular` is 0 on them. EXECUTED under a pseudo-terminal (`script -qec`, wasmtime 51 command line): `STDIN` and `STDOUT` are `tty`, a saved copy is `tty`, `STDIN` on a file is `notty`, and `STDIN` and `STDOUT` restored from their copies are `tty` again. The look refuses character devices, so no grant can reach a terminal. A name swapped between the look and the open is the race the specification accepts.
   - libc's own stdio calls `__isatty`, which the wrap does not catch. It still asks the host, which is harmless (READ: `llvm-nm`).
   - The FIFO is still refused with ENXIO before any `path_open` (the check, run).
3. **The 64 KiB buffer gives native's bytes.** Twelve cases over files larger than the buffer, byte-identical to native perl 5.42 in stdout, stderr, exit code and every file afterwards (EXECUTED: `runcases.py`). The files were a 40,000-line file mixing CRLF, blank lines and no final newline; a 270 KB line and lines of 65,535 and 65,536 bytes; a file whose 64 KiB boundary falls exactly at a newline. The cases were `-ne`, `-0777 -ne`, `-00 -ne`, `-lane`, `-pi`, `-pi.bak`, `-0777 -pi`, `$/ = \70000`, `read` of 4,097 bytes with `seek` and `eof`, and `+<` mixing a read with a write. The one-liner corpus also passes against native perl (653 passed with the runner tests).
4. **Counting from the command line's trace is fair.** The guest decides which WASI calls it makes. The host only answers them, and the command line and wasmtime-py 51 share wasmtime-wasi 51's preview1 code. Regular-file reads come back full in both, and the grants' rights do not change a read's size. The checks skip only where the command line, `llvm-objdump`, native perl or wasmtime-py is absent, and CI has all of them today (M2).
5. **The checks fail on regression.** EXECUTED: the 15 checks with `WASI_PERL_ARTIFACTS=build/before/artifacts` (dev's module): 8 failed and 7 passed. The 7 that passed are the 5 controls the landing notes name, plus the two that run `build/tests/memchr-test.wasm`. That binary is this branch's in either case, which matches the landing notes' "10 failed" on a tree built before the change.
6. **The fuel constant reproduces.** `uv run python context/research/2026-11-24-the-guests-reads-and-writes-measured/check_fuel.py /home/user/wasi-perl-wt/perf-base/build/artifacts/perl.wasm build/artifacts/perl.wasm` gives `643780719` twice for `perf-base` (`38c80f9c…ab30`), with this branch's library and with `perf-base`'s own. It gives `574055110` twice for this branch: 89.2% of the constant, against the bound of 592,278,261 (EXECUTED). `perf-base` is at `a5fd83a`, and the landing notes' empty `git diff aac5d4d a5fd83a -- recipes scripts` ties it to 0.1.0's recipes.
7. **The SIMD requirement is documented.** The README's first lines name exception handling and SIMD128. `docs/sandbox.md` has a new "What the host must run" section, and `CHANGELOG.md` says it too. "Nothing else in the module uses SIMD" is enforced by a check.
8. **The rules of `AGENTS.md`.** No patch to Perl. The adaptation is recipe files only, and `memchr-test.c` is a stamp input of `20_target.sh`. No file under `recipes/`, `scripts/` or `tests/` names a specification or review. Every commit carries the model trailer. `tests/perl_t/exclusions.toml` is untouched, and no existing check was edited. The memo labels every number and gives its commands.

## Tests run

All in the worktree, with `WASI_PERL_ARTIFACTS=$PWD/build/artifacts` and `WASI_PERL_ORACLE=/home/user/wasi-perl-wt/perf-base/build/native-install/bin/perl` unless the row says otherwise.

| command | result |
|---|---|
| `uv run pytest -m "not slow" -q -ra` | 473 passed, 2 skipped (`test_arguments.py:208` root, `test_fixed_cost.py:392` macOS), 245 deselected |
| `uv run pytest tests/build tests/escapes -q -ra` | 463 passed |
| `uv run ruff check src tests`; `uv run ruff format --check src tests` | All checks passed; 31 files already formatted |
| `uv run pytest tests/oneliners tests/runner -q -ra` (native oracle) | 653 passed, 2 skipped (the same two) |
| `WASI_PERL_ARTIFACTS=build/before/artifacts uv run pytest tests/build/test_reads_and_writes.py` | 8 failed, 7 passed (What holds 5) |
| `check_fuel.py` on `perf-base` and this branch | 643,780,719 ×2; 574,055,110 ×2 |
| probes `memchr-probe.c`, `tty.pl`, `runcases.py`, the pty run and the stop and memory runs | as reported above |
| CI on `98cf9fe` (READ: `gh api …/commits/98cf9fe…/check-runs`) | lint, build, the four wheel tests and Perl's own suite: all success |

I did not run Perl's `t/` suite here. CI's "Perl's own suite" job passed on this head, and the landing notes record a local run with 0 failed and no new exclusion.

## After the review (the orchestrator)

The implementer's commit `644f564` makes the fixes the verdict above recommended:
- **M1 and L1.** A new first bullet under "Differences from native that remain" in `docs/sandbox.md` says how much output a stopped call can lose and how read-ahead differs. The README's limits section points to it.
- **L2.** A README line gives the memory each handle holds.
- **L3.** `memchr-test.c` gains 13,979 cases of ranges that end at the end of linear memory. They pass. They could not be seen failing, because the code was already right.
- **L4.** The count checks gain lower bounds. Patched to parse nothing from the trace, the checks then fail, where the previous version passed.
- **M2 and L5** are recorded as open items in `context/roadmap.md`.
- **L6** predates this change.

Runs after the fixes, under the lock:
- `tests/build` and `tests/escapes`: 463 passed;
- `-m "not slow"`: 473 passed, 2 skipped;
- ruff: clean.

The rebuilt `perl.wasm` differs from the reviewed one only in the two bytes of its "Compiled at" time (`cmp -l`). No recipe changed. The verdict stands: **Lands.**
