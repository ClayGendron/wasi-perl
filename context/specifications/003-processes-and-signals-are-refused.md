# 003 — Processes and signals are refused, clearly

- **Status:** pre-agreed by the owner 2026-11-24 (the roadmap); written by the orchestrator.
- **Owner:** Clay Gendron
- **Kind:** the build recipe (a new `recipes/sandbox-refusals.c`, linker flags in `recipes/wasi-cc` or `recipes/config.sh`), checks in `tests/build/`, `docs/`.
- **Depends on:** 001 (its stubs: the process calls Perl names even without `fork`).
- **Research it stands on:** Perl's sources (READ): with `HAS_FORK` undefined, `pp_fork` dies "Unsupported function fork" (`pp_sys.c`); `pp_system` calls the platform's `do_aspawn`/`do_spawn`; `exec` reaches `Perl_do_exec3` (`doio.c`); backticks and pipe opens reach `Perl_my_popen`/`Perl_my_popen_list` (`util.c`); a `%SIG` store reaches `Perl_magic_setsig` (`mg.c`). The zeroperl study (§2, §5): its stubs answered these by failing quietly or with a wrong signature, which trapped.

## The problem

WASI preview1 has no processes and no signals. An agent's script that calls `system` or opens a pipe must learn at once, in words it can act on, that the sandbox cannot do it. Today the outcomes are a mix: Perl's own "Unsupported function fork", a `-1` from `system` with an `ENOSYS` `$!`, or, at worst, a trap that kills the guest with no Perl-level message.

## Decisions

1. **One message form.** Every refusal dies, through `Perl_croak`, with `"<what> is not available in the wasi-perl sandbox"`, to which Perl adds ` at FILE line N.` as for any `die`. So it can be caught by `eval`, it sets `$@`, and an uncaught one exits 255 with the message on stderr. `<what>` names the Perl-level operation: `fork`, `exec`, `system`, `backticks` (for `` `…` ``, `qx//` and `readpipe`), `pipe open` (for `open` with `|`, `-|`, `|-` or a list form), `pipe`, `wait`, `waitpid`, `kill`, `alarm`, `dump` (for `CORE::dump`, added by the review round: it raised SIGABRT at itself through the `kill` stub and ended silently with 0), and `signal handler for SIGNAME` for a store into `%SIG`.
2. **No patch: the refusals are linked in over Perl's own functions.** `recipes/sandbox-refusals.c` defines `__wrap_` versions of the internal functions named under "Research", and the link passes `-Wl,--wrap=<symbol>` for each, so every call Perl makes to them from another object lands on ours. The `pp_` functions of operations that have no internal function to wrap (`pp_fork`, `pp_wait`, `pp_waitpid`, `pp_kill`, `pp_alarm`, `pp_pipe_op`) are wrapped the same way: `PL_ppaddr` names them from another object. Each wrapper has the exact prototype of Perl's function. If a symbol cannot be wrapped because its only reference is in its own object, that one operation is refused by a patch under `recipes/patches/perl-5.42.3/` instead, recorded as AGENTS.md requires.
3. **`%SIG` keeps what is not a signal.** `$SIG{__WARN__}` and `$SIG{__DIE__}` are hooks, not signals: they work exactly as natively. Reading `%SIG` works. A store of any value (`'IGNORE'` and `'DEFAULT'` included) for a real signal name dies with decision 1's message. `local $SIG{ALRM}` dies at the store. A `delete $SIG{INT}` is not a store and works, as natively: it puts back the default, which installs nothing (review round, L1). This is stricter than a no-op, on purpose: a script that relies on a handler must not run on believing it has one.
4. **What still works.** `$$` (wasi-libc's emulated `getpid`), `getppid`, `times`, `sleep` and `select(undef,undef,undef,$t)` (WASI's clock), `exit`, `END` blocks, `POSIX::_exit`, and `die`/`warn` hooks.
5. **Checks** (`tests/build/test_refusals.py`), each run through the wasmtime CLI and, once the runner lands, through `run()`: each operation in decision 1, uncaught (exit 255, the message on stderr, no trap) and inside `eval` (`$@` matches the message, the program goes on); `$SIG{__WARN__}` and `$SIG{__DIE__}` hooks; `open(my $fh, "-|", "ls")` and `open(F, "ls |")`; `system("true") == 0 or die` gives the refusal, not "died"; the module still imports only `wasi_snapshot_preview1`. Each check is seen failing before the wrappers are linked.
6. **Documented.** `docs/sandbox.md` lists every refused operation and what still works.
7. **Exit codes from `$!` match native.** *(Added by the orchestrator while building, 2026-11-24.)* Perl's `die` takes its exit status from `errno` (`Perl_my_failure_exit`, `perl.c`). WASI numbers `errno` differently from Linux: `ENOENT` is 44, not 2; `ENOTTY` is 59, not 25. So `open(...) or die "...: $!"` would end with 44 in the guest and 2 natively, and agents check exit codes. `Perl_my_failure_exit` is wrapped: it computes the status as Perl does on every platform but VMS (`errno`, else `$? >> 8`, else 255), with `errno` first turned into its Linux (glibc) number through a table, and ends through `Perl_my_exit`. Linux is the reference because the oracle is native perl on Linux. Only the exit status changes: `errno` is not touched, so `$!` as a number stays WASI's and `$!` as text is unchanged. WASI's `ENOTCAPABLE` has no Linux number and maps to `EACCES` (13). A number the table does not know passes unchanged.
8. **`dup2` is a move.** *(Added by the orchestrator while building, from the `t/` run, 2026-11-24.)* WASI preview1 cannot copy a descriptor, so wasi-libc's `dup2` fails with `ENOTSUP`, and with it every `open STDOUT, '>', 'out.txt'`. Perl reopens a standard handle by opening the file at a new descriptor, `dup2`-ing it onto 0, 1 or 2 and closing the new one (`S_openn_cleanup`, `doio.c`), with no descriptor opened between the two calls (READ). The same `dup2(fd, savefd)` (`doio.c:1070`) is also reached by the fdopen forms (`>&=`, `<&=`, `doio.c:826`, `:1103-1117`) with a descriptor another handle still uses (corrected by the review round, H1: the first build moved it away and lost that handle's output). The remaining `dup2` callers are the child side of a fork (`util.c` `my_popen`, `doio.c` exec), which the refusals stop first. So option (a) is taken, without a patch, and only for a descriptor with one owner: `dup2` is wrapped (`--wrap=dup2`, since wasi-libc's `dup.o` also defines `dup`, which Perl needs, a plain definition would collide) and becomes `__wasilibc_fd_renumber(old, new)` when PerlIO counts exactly one handle on `old` (`PL_perlio_fd_refcnt[old] == 1`; the build has no threads, so no lock). Any other `dup2` fails with `ENOTSUP`, as wasi-libc's does, so every `>&=` open fails with "Not supported" and both handles keep working. After a move the old number is closed. `close` is wrapped with it: the one `close` of the number just moved succeeds, as natively, and leaves `errno` as it was (otherwise its `EBADF` would become the exit status of a later `die`, by decision 7). Any other use of the moved number fails with `EBADF`, WASI's own answer. `dup` itself stays WASI's: `open(STDERR, '>&', \*STDOUT)` fails with `$!` "Not supported". It is an `open` failure, not a refusal, because the script that checks `open`'s result gets an honest `$!`, and one that does not would get the same from any failed `open`.
9. **The exit status is the low byte.** *(From the 004 review, 2026-11-24.)* The `__wasi_proc_exit` wrapper (001 decision 8) masks the status with `& 0xff` before keeping it and before passing it on. `exit 256` ends with 0, `exit -1` with 255 and `exit 300` with 44, as natively; the `wasi_perl_exit_status` export answers 0 to 255, and -1 only when the program never exited.

## Acceptance

1. Every check of decision 5 passes, EXECUTED, output in the landing notes.
2. No patch, or each patch recorded with its reason.
3. A fresh agent's review lands.

## Landing notes

Built 2026-11-24 on branch `spec/003-refusals` (from dev at c62aa07), in this container (Linux x86-64, 4 cores shared, make capped at 3 jobs with `OMP_NUM_THREADS=3`, which `nproc` honours).

### Commands and the artifact

    rm -rf build && OMP_NUM_THREADS=3 WASI_PERL_DOWNLOAD_CACHE=/home/user/wasi-perl/downloads scripts/build.sh
    # 00_download: ok / 10_native: ok / 20_target: ok; real 2m13.6s                    EXECUTED
    sha256sum build/artifacts/perl.wasm; stat -c %s build/artifacts/perl.wasm
    # 1699abe2027ae01d91a1ff611cac33c1fccb8207dc75cdd62a2b8c187ff06690, 4026155 bytes   EXECUTED

    uv run --no-project --with pytest --with wasmtime==51.0.0 pytest -v tests/build    EXECUTED
    150 passed in 2.41s

After the review round (one fresh `scripts/build.sh` of the target, same command):

    169 passed in 4.93s                                                               EXECUTED

The module's hash changes with every build, even from the same inputs: Perl stamps `__DATE__`/`__TIME__` ("Compiled at") into it (001's note; the review measured 635 differing bytes, all that string and the offsets after it). A later specification (CI, release) pins it.

`ruff check` and `ruff format --check` on `tests/build` are clean. EXECUTED.

The 150 are 001's 36 (two of them tightened, below), plus `test_exit_status_is_the_low_byte` (4) and `test_no_wasm_opt` (1) in `test_perl_wasm.py`, plus 109 in `test_refusals.py`: `test_refused_uncaught` and `test_refused_in_eval` (35 cases each: every operation of decision 1 in several forms, among them `open(my $fh, "-|", "ls")`, `open(F, "ls |")`, list and implicit-fork pipe opens, `$SIG{INT} = 'IGNORE'`, `'DEFAULT'`, `undef`, `local $SIG{ALRM}` and `local $SIG{ALRM} = sub {…}`), `test_system_true_or_die`, `test_refusal_message_is_dollar_at`, `test_refusal_line_numbers`, `test_hooks_match_native` (11, against the oracle), `test_hooks_survive_a_refusal`, `test_local_sig_restores_quietly`, `test_still_works` (6: `$$`, `getppid`, `sleep 0`, `times`, `select` with a timeout, `END`; each also run on the oracle), `test_exit_and_end`, `test_still_imports_only_wasi`, the decision 7 checks (`test_die_status_from_errno_matches_native` ×3, `test_use_missing_module_status_matches_native`, `test_missing_script_status_matches_native`, `test_errno_number_stays_wasi`, `test_die_without_errno_is_255`, `test_die_status_from_child_status`) and the decision 8 checks (`test_reopen_standard_handle_matches_native` ×6, comparing stdout, stderr, status and the files written with the oracle run in a scratch folder; `test_dup_of_a_handle_fails_clearly`). All EXECUTED.

An uncaught refusal is checked by `assert_exit_refused` (`tests/build/conftest.py`): wasmtime's error is only the exit-status refusal ("exit with invalid exit status outside of [0..126)"), the innermost frame is `__wasi_proc_exit`, and the export holds 255. So no trap happened on the way. `test_die_uncaught` and `test_exit_status_export` use it too.

### Seen failing first (EXECUTED)

- `test_refusals.py` as first written, on the module built from 001's tree with no wrapper: 80 failed, 20 passed. The 20 were the hooks, `$$`, `sleep`, `times`, `END`, the import check, and the three checks of behaviour that was already native (`$!` number, die with no errno, die from `$?`). Every refusal check failed: `fork` said "Unsupported function fork", `system` returned -1, pipe opens failed quietly, `%SIG` stores succeeded. `use Nonexistent` and `open … or die "$!"` ended with 44 and 54 where the oracle ends with 2 and 20. `getppid` died "Unsupported function getppid".
- With the refusals linked and before the `dup2` wrapper: the 6 reopen checks failed ("Not supported"); 140 passed. With `dup2` and before the `close` wrapper: `open STDERR, ">", "f"; die "d\n"` ended with 9 (the `EBADF` of the close of the moved number) where the oracle ends with 25 (`ENOTTY`, left by an `isatty` check).
- Before the `& 0xff` mask: the 4 low-byte checks failed (the export answered 256, -1, 300, 511).
- Two of my own checks were wrong and fixed, not weakened: `$SIG{"SIGHUP"}` is not a signal name for Perl (it warns "No such signal: SIGSIGHUP"; the case now uses `"HUP"`), and `print select(undef,undef,undef,0.01)` prints the list `(0, 0)` natively (now `scalar(...)`).

### The wrapped symbols

`recipes/wasi-cc` passes `-Wl,--wrap=<symbol>` for each at every link, and links `build/stubs/libwasi-perl-refusals.a` once it exists. An archive member is pulled in only by a link that calls one of the symbols, so other links are unchanged. A perl link without it fails on the missing `__wrap_` symbol, never quietly. `scripts/20_target.sh` compiles `recipes/sandbox-refusals.c` right after `Configure -S`, with the exact command `cflags` gives Perl's own objects, plus `-Werror -Wno-c++-keyword` (Perl's `proto.h` trips that one warning). Each wrapper is declared `__typeof__(<Perl function>)`, so a prototype that drifts from Perl's fails to compile.

Each symbol below is defined in one object and referenced from another (`llvm-nm -A -u build/target/*.o`, EXECUTED), so `--wrap` catches the calls that matter, and every `__wrap_` symbol is in the final module (`llvm-nm build/target/perl`, EXECUTED). wasm-ld redirects calls inside the defining object too: `magic_clearsig` reaches `__wrap_Perl_magic_setsig` from within `mg.o` (EXECUTED by the review, L1). Every wrapper is written for that: each passes on to `__real_` whatever it does not refuse.

| Symbol | Defined in | Referenced from | Becomes |
|---|---|---|---|
| `Perl_pp_fork` | `pp_sys.o` | `globals.o` (`PL_ppaddr`) | `fork` refusal |
| `Perl_pp_wait` | `pp_sys.o` | `globals.o` | `wait` refusal |
| `Perl_pp_waitpid` | `pp_sys.o` | `globals.o` | `waitpid` refusal |
| `Perl_pp_alarm` | `pp_sys.o` | `globals.o` | `alarm` refusal |
| `Perl_pp_pipe_op` | `pp_sys.o` | `globals.o` | `pipe` refusal |
| `Perl_pp_getppid` | `pp_sys.o` | `globals.o` | pushes 0 |
| `Perl_do_exec3` | `doio.o` | `pp_sys.o` | `exec` refusal (string form) |
| `Perl_do_aexec5` | `doio.o` | `pp_sys.o`, `mathoms.o` | `exec` refusal (list and block forms) |
| `Perl_my_popen` | `util.o` | `doio.o`, `pp_sys.o` | `backticks` when `PL_op` is `OP_BACKTICK`, else `pipe open` |
| `Perl_my_popen_list` | `util.o` | `doio.o` | `pipe open` (list form) |
| `Perl_apply` | `doio.o` | `pp_sys.o` | `kill` refusal when the type is `OP_KILL`; otherwise Perl's own |
| `Perl_magic_setsig` | `mg.o` | `globals.o` (`PL_magic_vtables`); `mg.o` (`magic_clearsig`) | `signal handler for SIGNAME` for a store for a real signal; Perl's own for `__WARN__`/`__DIE__`, non-signals, a delete (no value) and the scope-end restore (`PL_localizing == 2`) |
| `Perl_my_unexec` | `perl.o` | `pp_ctl.o` (`pp_goto`, for `CORE::dump`) | `dump` refusal |
| `Perl_my_failure_exit` | `perl.o` | `pp_ctl.o` (`die_unwind`) | decision 7 |
| `dup2` | wasi-libc `dup.o` | `doio.o` | decision 8: a move when one handle holds the descriptor, else `ENOTSUP` |
| `close` | wasi-libc | `doio.o`, `perlio.o`, `util.o` | decision 8 |
| `__wasi_proc_exit` | wasi-libc | wasi-libc `_Exit` | 001's wrapper, now masked (decision 9) |

`do_aspawn` and `do_spawn`, which `pp_system` calls when there is no `fork` (`pp_sys.c`, READ), are this project's own symbols, not Perl's. They moved from `recipes/wasi-stubs.c` to `recipes/sandbox-refusals.c` and now raise the `system` refusal; nothing is wrapped for `system`. `kill` has no `pp_kill` to wrap: `opcode.h` defines `Perl_pp_kill` as `Perl_pp_chown`, shared with `chmod`, `chown`, `unlink` and `utime` (READ). So `Perl_apply` is wrapped instead and looks at the op type.

A refusal clears `errno` before `Perl_croak`, so an uncaught one ends with 255 by Perl's own rule; `$!` is 0 after it.

### Patches

None. `recipes/patches/perl-5.42.3/` does not exist. EXECUTED (`ls`).

### Deviations, and what was added while building

1. **The checks run under wasmtime-py 51, not the wasmtime CLI**, as in 001 (its CLI cannot grant read-only). Through `run()` once 004 lands: not yet done.
2. **`getppid` was wrapped** (decision 4 says it works; with `d_getppid` undef it died "Unsupported function getppid", EXECUTED). It returns 0: the parent is the host, outside the sandbox, as for a Linux process whose parent is in another PID namespace. Wrapping the pp function keeps `config.sh` untouched.
3. **`scripts/20_target.sh` changed** by one compile step and one `llvm-ar` after `Configure -S`. The refusals need Perl's configured headers, so they cannot be compiled with the stubs before the tree exists. The change is in a different hunk from 002's `make -j` edit.
4. **A store of `undef` for a signal is refused too** (decision 3 says "any value"). The restore at the end of a `local` scope is let through, or every refused `local $SIG{X}` would die again while unwinding.
5. **`POSIX::_exit` (decision 4) is not checked:** POSIX is not built until 002.
6. **`glob` is not a refusal.** Without File::Glob (002) it fails at compile time with "Can't locate File/Glob.pm" (EXECUTED); it never reaches `my_popen`.
7. **The 001 review findings fixed here:** M1, `wasi-cc` uses only the pinned SDK under `build/tools` and does not read an ambient `WASI_SDK` (`WASI_SDK=/bogus recipes/wasi-cc --version` runs the pinned clang 23.1.0, EXECUTED); M2, `wasi-cc` links with `--no-wasm-opt` (a fake `wasm-opt` placed beside clang ran on a plain clang link and did not run through `wasi-cc`, EXECUTED) and `test_no_wasm_opt` checks the module names no binaryen; M4, the CLI hello check grants only a throwaway `dev/` and no artifact folder; L10, `assert_exit_refused`; L11, 001's decision 3 text no longer lists `dup` as a stub, and `>&` duplication is a documented difference in `docs/sandbox.md`.
8. **The errno table** was generated from the names in wasi-libc's `__errno_values.h` and glibc's `<errno.h>` (`gcc -E -dM`), matched by name (EXECUTED), and is written out in `sandbox-refusals.c` as designated initializers keyed by WASI's macros, so each row reads `[ENOENT] = 2`. That a plain `dup2` definition would collide with wasi-libc's `dup.o` is INFERRED from `llvm-nm` (that one object defines `dup`, `dup2` and `dup3`, EXECUTED), not linked to see it fail.

### The review round (`context/reviews/003-processes-and-signals-are-refused-pr-2026-11-24.md`)

Fixed in one commit, each new check seen failing on the module before the fix (11 failed, 158 passed, EXECUTED), then passing (169, EXECUTED):

- **H1:** `__wrap_dup2` moves only a descriptor with PerlIO count 1, else `ENOTSUP`. `test_fdopen_of_a_shared_descriptor_fails_cleanly` (4: `open STDERR, ">&=STDOUT"`, `open(STDERR, ">&=", \*STDOUT)`, `open STDOUT, ">&=", $h` and `">&=" . fileno($h)`) checks that each fails with "Not supported" and that both handles keep writing where they did (`$h`'s file holds its line, stdout its own; native succeeds at each). Decision 8 and `docs/sandbox.md` are corrected.
- **M1:** `Perl_my_unexec` is wrapped; `CORE::dump` dies with the `dump` refusal, uncaught and in `eval` (2 cases each in `REFUSED`).
- **L1:** a delete is let through (decision 3 says so now); three `delete` forms are in `HOOKS`, against the oracle. The landing-note sentence on in-object calls is corrected above.
- **L2:** answered in the docs: a number put into `$!` is read as WASI's.
- **L3:** `@SIG{…} = …`, `%SIG = (…)`, a store inside `local %SIG` and a `CORE::GLOBAL::readpipe` override are in `REFUSED`; the deletes in `HOOKS`; the `&=` forms in the H1 check.
- **L4:** `wasi-cc` reads `WASI_SDK_NAME` from `scripts/lib.sh` and stops if it is missing, so the pin is written once.
- **L5:** one line in the docs' differences.
- **L6:** noted with the hash above.

### For the next specifications

- **002:** File::Glob makes `glob` work; POSIX brings `POSIX::_exit`, `POSIX::dup2` (a move here, decision 8) and `POSIX::sigaction`, which reaches `sigaction` directly and is not refused by these wrappers (INFERRED; neither read nor run). Refuse it in 002 or here in a follow-up.
- **009 (descriptor aliases):** `dup` and `dup2` belong to 009, which replaces decision 8 with a real copy. What 009 inherits: `__wrap_dup2` (in `recipes/sandbox-refusals.c`) returns `newfd` for `dup2(fd, fd)` when `fcntl(fd, F_GETFD)` succeeds; otherwise, if `PL_perlio_fd_refcnt[old] != 1`, it fails with `ENOTSUP`; else it calls `__wasilibc_fd_renumber(old, new)`, which closes `old`, and remembers `old` in one slot, `moved_fd`. `__wrap_close` calls the real `close`. If the fd is `moved_fd`, it clears the slot, and when the real close failed with `EBADF` it puts back the `errno` from before the call and returns 0. `dup` is untouched (wasi-libc's, `ENOTSUP`). Both wraps are in `wasi-cc`'s list, and the checks are `test_reopen_standard_handle_matches_native` (6) and `test_dup_of_a_handle_fails_clearly`. The latter expects the failure and is to be inverted by 009.
- **004:** the runner reads `wasi_perl_exit_status`, now always 0 to 255 or -1.
- **005/006:** exit statuses from `$!` now match the oracle; `$!` as a number does not.
