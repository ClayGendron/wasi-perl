# Review: 002, the core XS modules and the library (branch `spec/002-xs-modules`)

Reviewer: an agent (Claude), a fresh sub-agent of https://claude.ai/code/session_01ThzDEUwftdmy8zWBJK1gWa; not the author

- **Reviewed:** `git diff origin/dev...c1e7745` (dev at c62aa07), with the specification and its landing notes, AGENTS.md and the constitution.
- **Date:** 2026-11-24.

## What was run

    WASI_PERL_DOWNLOAD_CACHE=/home/user/wasi-perl/downloads WASI_PERL_JOBS=2 scripts/build.sh      EXECUTED
    # 00_download ok / 10_native ok / 20_target ok / 30_library ok (1461 files in MANIFEST); real 3m54s
    sha256sum build/artifacts/perl.wasm build/artifacts/MANIFEST; stat -c %s build/artifacts/perl.wasm   EXECUTED
    # perl.wasm 886f0cbe…1def, 9102529 bytes (the author's is 5f7020f7…, same size: __DATE__, as 001 found)
    # MANIFEST 0ff3cc07…8bb, byte-identical to the author's built in another checkout
    uv run --no-project --with pytest --with wasmtime==51.0.0 pytest tests/build -q                 EXECUTED
    # 147 passed in 9.48s
    uvx ruff check tests/build; uvx ruff format --check tests/build                                 EXECUTED
    # clean
    python -I -c 'from write_sums import sums; ...'  # origin/spec/004-runner:scripts/write_sums.py  EXECUTED
    # sums(build/artifacts) == SHA256SUMS: True; MANIFEST's 1461 lines are a subset of SHA256SUMS's 1463

Probes below ran the author's `perl.wasm` under the pinned wasmtime 51 command line with the library at `/usr/local/lib/perl5` and `dev/` at `/dev`, beside the native perl 5.42.3 of the same tree. EXECUTED unless labelled otherwise.

## What holds

- Every module of decision 1 loads and does its real thing as natively (the checks, and my own probes of POSIX `strftime`, `floor`/`ceil`/`fmod`/`round`/`trunc`, `strtol`/`strtod`, `setlocale(LC_ALL, "C")` = `C`, `localeconv`, `mktime`, `:sys_wait_h`, `:errno_h` with `Errno` agreeing, `$!{ENOENT}`; Storable's depth limits refuse cleanly at 5000 levels).
- The refused POSIX calls refuse: `sigaction` (and `%SIG` is left untouched), `sigprocmask`, `sigpending`, `sigsuspend`, `pause`, `mkfifo` give undef with "Function not implemented"; `ttyname` undef with "Not a tty"; `dup`, `dup2`, `pipe` "Not supported"; `setsid`, `nice`, `tcgetpgrp`, `Termios` die "not implemented on this architecture".
- The stand-in prototypes match POSIX exactly, and `wasi-posix-stubs.c` is compiled with `-Wall -Wextra -Werror` against its own header, so a drift is a build error. No implicit declaration anywhere in the 20_target log; the only `-Wint-conversion` lines are Time::HiRes `const-c.inc:215` and `:316`.
- The library holds no `pod/`, `CORE/`, `*.a`, `*.bs`, `.packlist`, `extralibs.ld`, man page or `auto/`; no file names the checkout; `%Config` gives `extensions` = `static_ext` + `nonxs_ext`, an empty `dynamic_ext`, a wasm32 `cppsymbols`.
- `MANIFEST`/`SHA256SUMS` are what 004's `write_sums.py` writes and what 004's `_check_tree` reads (`<hex>  <path>`, component-sorted, `dev/null` listed).
- Each exclusion's reason is true except Devel::PPPort's (M3). Thread::Queue/Semaphore are absent and `use Thread` fails as natively.
- Nothing is copied from zeroperl: its `stubs/stubs.c` fakes success (`kill` returns 0, `setuid` 0) and uses `-Wno-int-conversion` with `-Wno-implicit-function-declaration` globally; this branch refuses with `ENOSYS` and keeps implicit declarations errors.
- No comment under `recipes/`, `scripts/` or `tests/` names a specification or review number.

## Findings

| # | Rank | Where | Finding | Suggested fix |
|---|---|---|---|---|
| H1 | High | `recipes/config.sh:29-30`, `:88`; `tests/build/test_modules.py:407-414` | `-Wno-error=int-conversion` masks a real bug. wasi-libc's `clockid_t` is a pointer to a `struct __clockid`, and Time::HiRes's typemap (`clockid_t T_IV`) casts any Perl integer to it. `Time::HiRes::clock_gettime(-1)`, `clock_getres(-1)` and `clock_nanosleep(-1, 1000)` each end the run with `wasm trap: out of bounds memory access` (native: -1 with "Invalid argument"). `clock_gettime(1)`, Linux's `CLOCK_MONOTONIC` written as a number, silently returns the wall clock (1795492287.6; native 4296.05, monotonic), and 99 succeeds where native gives EINVAL. `CLOCK_MONOTONIC`/`CLOCK_REALTIME` print as data addresses (13610796, 13610800) that move with the link, against native 1 and 0. A trap is the crash the constitution forbids. The named calls themselves work: `clock_gettime(CLOCK_MONOTONIC)` increases, `clock_getres` gives 1e-09 for both clocks, `clock_nanosleep` and `clock` work. | Give Time::HiRes integer clock ids with Linux's values: a recipe header (through a `recipes/hints/` file for Time-HiRes, as decision 1 allows, or `wasi-perl-libc.h`) that redefines `CLOCK_REALTIME` 0 and `CLOCK_MONOTONIC` 1 and routes `clock_gettime`/`clock_getres`/`clock_nanosleep` to stubs that map 0 and 1 to wasi-libc's clocks and fail any other id with `EINVAL`. Then drop `-Wno-error=int-conversion`. Add checks against native: `clock_gettime(CLOCK_MONOTONIC)` increasing, `clock_getres`, the constants' values, and `clock_gettime(-1)` / `(99)` giving -1 with EINVAL. |
| M1 | Medium | `recipes/wasi-posix-stubs.c:5`, `:19-25`, `:29-35`; `recipes/include/wasi-perl-posix.h:18`; `docs/modules.md:27` | Signal sets do not "behave as on any system". wasi-libc's `sigset_t` is `unsigned char` (`__typedef_sigset_t.h`), so `bad_signal` refuses every signal above 8. `POSIX::SigSet->new(2, 15)` dies "failed to add signal 15" (native: a set); `SigSet->new($n)` fails for every n from 9 to 20; `sigfillset` quietly skips 9 to 64; `ismember(15)` returns -1, which Perl reads as true. The docs say the sets work. | Either say the truth (docs and both comments: "signals 1 to 8 only; larger numbers fail with EINVAL", and make `sigfillset` not claim a full set), or make the sets wide enough, for example a recipe-defined 64-bit set type used by the stubs and POSIX through a `#define sigset_t` ahead of `<signal.h>`, checked for ABI clashes. Add a check of `SigSet->new(SIGTERM)` and `fillset`/`ismember` against whichever behaviour is chosen. |
| M2 | Medium | `recipes/config.sh:88` | The flag is in `ccflags`, so it applies to the core and all 43 extensions, not to the one file that needs it. A future pointer/integer mix-up anywhere else (the kind that traps on wasm32) would build with a warning among 331 others. | Remove it with H1's fix. Until then, make `scripts/20_target.sh` fail when the log has a `-Wint-conversion` warning outside `dist/Time-HiRes/const-c.inc`. |
| M3 | Medium | `docs/modules.md:21`; `recipes/config.sh:20`; spec landing notes line 61 | Devel::PPPort's reason is false. The native `make install` does install it: `build/native-install/lib/5.42.3/x86_64-linux/Devel/PPPort.pm`, version 3.73, and `perl -MDevel::PPPort` loads natively. The spec's evidence (no `.so` under `auto/`) shows only that nothing XS is installed. XS::APItest and XS::Typemap are indeed not installed. | Give the true reason (it writes `ppport.h` for XS authors, and the sandbox has no C compiler), or ship its pure-Perl `PPPort.pm` in `nonxs_ext`. Add Devel::PPPort to the left-out check whichever way. |
| L1 | Low | `recipes/include/sys/wait.h:21` | `WIFSIGNALED` differs from Linux when the low byte is 0xff: for 0xff and 0xffff the guest gives 1, native 0 (glibc treats low 7 bits 0x7f as stopped, not signalled, whatever bit 7 is). | `#define WIFSIGNALED(s) (((s) & 0x7f) != 0 && ((s) & 0x7f) != 0x7f)`, and add 0xff to a check against native. |
| L2 | Low | `tests/build/test_modules.py:229-244` | The left-out check covers 7 of the 14 `LEFT_OUT` entries; GDBM_File, NDBM_File, ODBM_File, Thread::Semaphore, Devel::PPPort, XS::APItest and XS::Typemap are never shown not to load. | Parametrize over `LEFT_OUT` (with `/` turned to `::`). |
| L3 | Low | `scripts/30_library.sh:54-61` | Only a backslash is refused, but `xargs` also splits on blanks and reads quotes, so a library file with a space or an apostrophe would be hashed as two names or stop the build with an unclear error. No such file exists today. | Refuse any name with whitespace or quotes as well, or feed `xargs -d '\n'`. |
| L4 | Low | `scripts/30_library.sh:45` | `$ROOT` goes into `sed` as a regular expression; a checkout path containing `|` or `&` breaks the substitution. The `grep -F` guard on line 47 catches the leftover path, so the build stops; it does not ship one. | Escape the path before `sed`, or do the replacement with the native perl's `\Q…\E`. |
| L5 | Low | library: `Encode/encode.h`, the six `Encode/*.e2x`, `ExtUtils/xsubpp`, `ExtUtils/typemap` | Build-only files still ship. Harmless, but decision 2's rule is "what a sandboxed run never reads". | Remove them, or say in the landing notes why they stay (ExtUtils::MakeMaker is in the library). |
| L6 | Low | spec decision 2 (line 18); `origin/spec/004-runner:src/wasi_perl/_artifacts.py` (`Artifacts` docstring); `origin/spec/008-ci` spec 008 acceptance 2 | Three texts describe the set three ways. Decision 2 and 008 say `SHA256SUMS` covers only `perl.wasm` and `MANIFEST`. 004's docstring says `MANIFEST` is "every pinned source and recipe hash the build used". The landing notes record the first as a deviation, but the decision text is unchanged. | Amend decision 2 to say what is built, and note it for 004 and 008 so their texts follow. |
| L7 | Low | repository root | AGENTS.md step 4 asks for `CHANGELOG.md` lines under "Unreleased"; there is no `CHANGELOG.md` on dev or on this branch. | Start it with this pull request's lines. |
| L8 | Low | `docs/modules.md:25-35` | Differences an agent can meet are not listed. Errno numbers are WASI's (`ENOENT` 44, `EACCES` 2; `Errno` and `POSIX` agree), so `$! == 2` means something else. `POSIX::tzname()` gives `("", "")` against native `("GMT", "GMT")`. ILP32 shows: `LONG_MAX` 2147483647, `strtoul("4294967296")` saturates at 4294967295. | Add one line each. |
| L9 | Low | for 003 | `POSIX::raise(15)` returns 0 and the program goes on (wasi-libc's emulated `raise`); `POSIX::abort()` ends in a wasm trap. Neither is this branch's code, but both belong on 003's list. | Hand to 003. |

## Verdict

Does not land: H1 is a reachable trap behind the very flag the review was asked to check, and M1 and M3 are claims in `docs/` that the binary contradicts. The rest of the branch is sound and well evidenced. With H1 fixed and checked, and M1 to M3 fixed or answered in writing, it lands.
