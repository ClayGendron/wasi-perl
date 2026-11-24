# 003 — Processes and signals are refused, clearly

- **Status:** pre-agreed by the owner 2026-11-24 (the roadmap); written by the orchestrator.
- **Owner:** Clay Gendron
- **Kind:** the build recipe (a new `recipes/sandbox-refusals.c`, linker flags in `recipes/wasi-cc` or `recipes/config.sh`), checks in `tests/build/`, `docs/`.
- **Depends on:** 001 (its stubs: the process calls Perl names even without `fork`).
- **Research it stands on:** Perl's sources (READ): with `HAS_FORK` undefined, `pp_fork` dies "Unsupported function fork" (`pp_sys.c`); `pp_system` calls the platform's `do_aspawn`/`do_spawn`; `exec` reaches `Perl_do_exec3` (`doio.c`); backticks and pipe opens reach `Perl_my_popen`/`Perl_my_popen_list` (`util.c`); a `%SIG` store reaches `Perl_magic_setsig` (`mg.c`). The zeroperl study (§2, §5): its stubs answered these by failing quietly or with a wrong signature, which trapped.

## The problem

WASI preview1 has no processes and no signals. An agent's script that calls `system` or opens a pipe must learn at once, in words it can act on, that the sandbox cannot do it. Today the outcomes are a mix: Perl's own "Unsupported function fork", a `-1` from `system` with an `ENOSYS` `$!`, or, at worst, a trap that kills the guest with no Perl-level message.

## Decisions

1. **One message form.** Every refusal dies, through `Perl_croak`, with `"<what> is not available in the wasi-perl sandbox"`, to which Perl adds ` at FILE line N.` as for any `die`. So it can be caught by `eval`, it sets `$@`, and an uncaught one exits 255 with the message on stderr. `<what>` names the Perl-level operation: `fork`, `exec`, `system`, `backticks` (for `` `…` ``, `qx//` and `readpipe`), `pipe open` (for `open` with `|`, `-|`, `|-` or a list form), `pipe`, `wait`, `waitpid`, `kill`, `alarm`, and `signal handler for SIGNAME` for a store into `%SIG`.
2. **No patch: the refusals are linked in over Perl's own functions.** `recipes/sandbox-refusals.c` defines `__wrap_` versions of the internal functions named under "Research", and the link passes `-Wl,--wrap=<symbol>` for each, so every call Perl makes to them from another object lands on ours. The `pp_` functions of operations that have no internal function to wrap (`pp_fork`, `pp_wait`, `pp_waitpid`, `pp_kill`, `pp_alarm`, `pp_pipe_op`) are wrapped the same way: `PL_ppaddr` names them from another object. Each wrapper has the exact prototype of Perl's function. If a symbol cannot be wrapped because its only reference is in its own object, that one operation is refused by a patch under `recipes/patches/perl-5.42.3/` instead, recorded as AGENTS.md requires.
3. **`%SIG` keeps what is not a signal.** `$SIG{__WARN__}` and `$SIG{__DIE__}` are hooks, not signals: they work exactly as natively. Reading `%SIG` works. A store of any value (`'IGNORE'` and `'DEFAULT'` included) for a real signal name dies with decision 1's message. `local $SIG{ALRM}` dies at the store. This is stricter than a no-op, on purpose: a script that relies on a handler must not run on believing it has one.
4. **What still works.** `$$` (wasi-libc's emulated `getpid`), `getppid`, `times`, `sleep` and `select(undef,undef,undef,$t)` (WASI's clock), `exit`, `END` blocks, `POSIX::_exit`, and `die`/`warn` hooks.
5. **Checks** (`tests/build/test_refusals.py`), each run through the wasmtime CLI and, once the runner lands, through `run()`: each operation in decision 1, uncaught (exit 255, the message on stderr, no trap) and inside `eval` (`$@` matches the message, the program goes on); `$SIG{__WARN__}` and `$SIG{__DIE__}` hooks; `open(my $fh, "-|", "ls")` and `open(F, "ls |")`; `system("true") == 0 or die` gives the refusal, not "died"; the module still imports only `wasi_snapshot_preview1`. Each check is seen failing before the wrappers are linked.
6. **Documented.** `docs/sandbox.md` lists every refused operation and what still works.

## Acceptance

1. Every check of decision 5 passes, EXECUTED, output in the landing notes.
2. No patch, or each patch recorded with its reason.
3. A fresh agent's review lands.

## Landing notes

(Written when built.)
