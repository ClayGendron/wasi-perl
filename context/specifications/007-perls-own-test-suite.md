# 007 — Perl's own `t/` suite under the runtime

- **Status:** pre-agreed by the owner 2026-11-24 (the roadmap); written by the orchestrator.
- **Owner:** Clay Gendron
- **Kind:** the judge (`tests/perl_t/`), a CI job.
- **Depends on:** 002 (modules), 003 (refusals), 004 (the runner).
- **Research it stands on:** Perl's `t/TEST` and `t/harness` (READ): each `.t` file is a separate `perl` process printing TAP; `t/test.pl`'s `runperl` and `fresh_perl` start a child perl through `` `…` `` or `system`, which the sandbox refuses (003). 617 `.t` files under `t/` in 5.42.3 (EXECUTED, `find t -name '*.t' | wc -l`).

## The problem

The owner's bar is that Perl's own suite passes, apart from a listed set of exclusions with a reason for each. Perl's harness cannot run inside the guest, because it starts a process per file. So the host runs each file as its own guest.

## Decisions

1. **The host is the harness.** `tests/perl_t/run_suite.py` copies the pristine `t/` and `lib/` of the pinned tarball (from `build/target/`, unpatched files only) into a fresh temporary folder, grants it writable at `/perl`, and runs each `t/**/*.t` with `run(["-I../lib", "-I.", file], cwd="/perl/t", deadline_s=…, fuel=None)`, one guest per file, several at once on the host's cores. Tests write their scratch files beside themselves, which is why the copy is writable and fresh per run.
2. **A file passes** when its guest exits 0 and its TAP has a plan, no `not ok` (a `# TODO` is not a failure), and as many results as the plan says, or a `1..0 # skip` with a reason. The harness parses TAP itself; no CPAN module.
3. **Exclusions are a file, each with a reason.** `tests/perl_t/exclusions.toml` maps a test file to one of a few reasons, each written once at the top of the file:
   - `subprocess`: the file starts a child perl or a command (`runperl`, `fresh_perl`, `system`, pipes), which the sandbox refuses by design (003);
   - `signals`: it needs a signal or `alarm`;
   - `refused`: it calls another operation the sandbox refuses that starts no process (`pipe`, `waitpid`) (added after review);
   - `threads`: it needs threads, which the build leaves out;
   - `porting`: it checks Perl's source tree or git (`t/porting/`), not the interpreter;
   - `dynamic-loading`: it needs `DynaLoader` to load a shared object;
   - `platform`: a WASI difference named in the entry's own `note` (for example, no users or groups, no `chmod` bits);
   - `not-this-platform`: a test for another operating system, which t/TEST runs only there (`t/win32/`) (added after review);
   - `bug`: a defect of this build, with the entry's `note` saying what fails. A `bug` entry is a gap, not a design choice, and the landing notes count them.
   A file not in the list must pass. A listed file that passes is reported, so a stale exclusion is seen and removed. Partial exclusions are not used: a file is in or out.
   **Partial runs count through a guard** (added after review). Each entry records `ok_at_least`, the number of `ok` results the file reaches when it is classified; a run in which an excluded file reaches fewer has regressed and fails like a failure. A `subprocess`, `signals` or `refused` file must also show "is not available in the wasi-perl sandbox" in its output; a file that catches the refusal and never prints it names, in `shows`, a pattern its output does show, with a note saying where it is refused. So the part of an excluded file that runs before its first refusal is guarded, though it is not a pass.
6. **Small bugs the suite finds are fixed here, with a check** (added after review). A defect of the build that the suite exposes and that a few lines fix is fixed in this specification, at its root, with a check under `tests/build/` seen failing first; only a larger one is filed as `bug` for its own specification. The first: `times` was 10^7 too large (below).
7. **The harness follows t/TEST's conventions** (added after review): t/TEST's switches per directory; strict TAP outside `lib/` (a line that is not TAP or a `#` comment is unexpected output); an empty stand-in `t/perl`, as `make test` has the built perl there, so `which_perl` finds it and a child perl is refused at its start rather than missing; and `t/benchmark/`, which asserts wall-clock speed ratios, runs only when `PERL_BENCHMARK` is set, as in t/TEST, since this project's checks count operations, not time.
4. **The scoreboard.** The run writes `build/perl_t/results.json` and prints one line: files run, passed, excluded by reason, failed. `tests/perl_t/test_suite.py` (marked `slow`) fails if any non-excluded file fails or any excluded file passes.
5. **Where it runs.** Locally on demand, and in CI on Linux x86-64 on every pull request into `dev` that changes the build or the runner (008).

## Acceptance

1. A full run, EXECUTED, with its scoreboard line in the landing notes: zero failures outside the exclusions.
2. Every exclusion has a reason; `bug` entries are listed in the landing notes.
3. A fresh agent's review lands.

## Landing notes

Built 2026-11-24 on `spec/007-perl-t`, merged with dev at `7afccfb` (001 to 006, 002's XS modules, 003's refusals and its `%SIG` amendment), built in this worktree by `WASI_PERL_DOWNLOAD_CACHE=/home/user/wasi-perl/downloads WASI_PERL_JOBS=3 scripts/build.sh` and run by dev's own runner. The review (`context/reviews/007-perls-own-test-suite-pr-2026-11-24.md`) is answered here.

### The scoreboard

EXECUTED, about 95 s on 4 cores, and the same again inside `uv run pytest tests/build tests/perl_t` (348 passed):

```
perl t/: 616 run, 433 passed, 183 excluded (subprocess 140, signals 11, refused 2, porting 6, platform 6, not-this-platform 6, bug 12), 0 failed, 0 stale exclusions, 0 regressed (18424 guarded oks)
```

616 rather than 617 because `t/benchmark/` runs only with `PERL_BENCHMARK` (decision 7). Its one file, `gh7094-speed-up-keys-on-empty-hash.t`, passed in one run with `times` fixed and failed in the next (EXECUTED: global vs lexical `keys` speeds 1.45e7/s and 1.89e7/s, a ratio of 0.30 against its bound of 0.25, with four guests sharing the cores). That is timing noise, not a defect, so it is neither excluded nor run by default.

History (EXECUTED): against 001's minimum build, `617 run, 300 passed, 317 excluded …, bug 191`; against dev at `eee84ac`, `617 run, 433 passed, 184 excluded …, bug 12`, after 142 stale exclusions left. The nine files added in the reclassification commit (`4886b90`, L7) were comp/colon.t, comp/hints.t, io/closepid.t, lib/overload_fallback.t, op/dbm.t, op/die.t, op/waitpid.t, porting/globvar.t and run/locale.t: each passed while a refused child returned undef, and dies at its first refusal since 003.

### The `times` fix (H1)

`pp_tms` divides `times()`'s `clock_t` values by `PL_clocktick`, which `perl.c` takes from `sysconf(_SC_CLK_TCK)` (READ, `perl.c` 422-426, `pp_sys.c` 4963). wasi-libc's emulated `times()` counts nanoseconds (`CLOCKS_PER_SEC` 1e9), while its `sysconf(_SC_CLK_TCK)` answers 100, so `times` was 10^7 too large and Benchmark.pm's numbers were noise. The fix is at the root: the link wraps `times` (`-Wl,--wrap=times` in `recipes/config.sh`'s `ldflags`) and `recipes/wasi-stubs.c` converts every field and the returned elapsed time to the ticks `sysconf` names, so every caller, `POSIX::times` included, agrees. The check, `tests/build/test_perl_wasm.py::test_times_agrees_with_cpu_clock`, compares `times`' CPU seconds with `Time::HiRes::clock`'s over a loop of 0.3 CPU seconds, as a ratio within 0.8 to 1.25. It failed before the fix with `3000024.25 / 0.300004` and passes after it (EXECUTED).

### The other findings

- **M1:** the `ok_at_least` and refusal guard (decision 3). 18424 `ok` results of excluded files are now guarded; 9 entries use `shows` (comp/colon.t, io/crlf_through.t, io/pvbm.t, op/die_exit.t, op/override.t, op/recurse.t, re/substT.t, re/subst_wamp.t, run/cloexec.t), each EXECUTED-checked where the refusal is hidden (`eval { pipe … }`, `eval q{qx:…:}` and `eval { waitpid(0,1) }` each give the sandbox message in `$@`).
- **M2:** op/pack.t is `subprocess` (with `t/perl` it runs 4177 tests, all ok, then a runperl is refused); op/waitpid.t and io/pvbm.t are `refused`.
- **M3:** the stand-in `t/perl`. The scoreboard did not change; op/pack.t now reaches 4177.
- **L1:** the unpacked tree carries a stamp of the tarball's hash and is unpacked again when it is missing or different. **L2:** the tarball is found through `WASI_PERL_TARBALL`, `downloads/`, `WASI_PERL_DOWNLOAD_CACHE`, then `git rev-parse --git-common-dir`. **L3:** `Bail out!` is case-insensitive; strict TAP outside `lib/` (decision 7); no file changed outcome. **L4:** no specification numbers in `exclusions.toml`. **L5:** op/taint.t's note names `kill`. **L6:** `not-this-platform`. **L7:** above. **L8:** a skip reason is stripped of `skip`, `Skipped:` and kin. **L9:** the slow test fails, not skips, without a build when `CI` is set.

### Commands

```
scripts/build.sh                                               # build/artifacts and build/target/lib
uv run python tests/perl_t/run_suite.py --quiet --list-failures   # what CI runs; exit 1 on a failure, a stale or a regressed exclusion
uv run pytest -q tests/perl_t                                  # the slow suite test and the fast ones
uv run pytest -q tests/build -k times                          # the times check
```

Defaults match CI's perl-t job (008's ci.yml, READ): the tarball in `downloads/`, the set at `build/artifacts/`, the target library at `build/target/lib/`; `WASI_PERL_ARTIFACTS`, `WASI_PERL_TARGET` and `WASI_PERL_TARBALL` override them. Failing files' whole output is in `build/perl_t/logs/`.

### Counts by reason (EXECUTED, the run above)

| Reason | Files |
|---|---|
| subprocess | 140 |
| signals | 11 |
| refused | 2 (op/waitpid.t, io/pvbm.t) |
| threads | 0 |
| porting | 6 (30 of 36 `t/porting/` pass) |
| dynamic-loading | 0 |
| platform | 6: chmod (op/filetest.t, op/stat.t), the time-zone database (op/time.t), descriptor reuse (op/require_37033.t), no sockets (op/coreamp.t), 32-bit regex sizes (bigmem/regexp.t) |
| not-this-platform | 6 (`win32/`) |
| bug | 12 |

### The `bug` entries, grouped by cause

- **No dup for `>&` handles, 8 files:** op/write.t (stops at line 315, before 636 format tests), op/fh.t, io/dup.t, io/perlio.t, op/hash-clear-placeholders.t, op/hash-rt85026.t, op/numify_chkflags.t, test_pl/can_isa_ok.t.
- **Four-argument `select` with a non-empty bit vector traps "memory out of bounds", 1 file:** op/tie_fetch_count.t line 274 (EXECUTED: `perl -e '$v="\xff"; select $v,undef,undef,0'` traps). INFERRED cause: wasi-libc's `fd_set` is a count and an array, not the bit mask `pp_sselect` writes. Larger than a few lines, so it is left for its own fix.
- **XS::APItest is not built, 2 files:** bigmem/stack.t, bigmem/stack_over.t.
- **Unexplained, 1 file:** lib/deprecate.t passes its 10 tests, then its END block's File::Path cannot load Carp after the test leaves its relative `@INC`, and it exits 2.
