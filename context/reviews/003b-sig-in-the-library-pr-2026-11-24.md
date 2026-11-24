# Review: 003b — `%SIG` stores in the runtime's own library

- **Reviewer:** an agent (Claude), a fresh sub-agent of https://claude.ai/code/session_01ThzDEUwftdmy8zWBJK1gWa; not the author
- **Branch:** `spec/003b-sig-in-the-library` at bc4f7fa (one commit on dev eee84ac), against `origin/dev`
- **Date:** 2026-11-24

## What was checked, and how

- Read: `git diff origin/dev...HEAD`. That is the amended decision 3 with its landing notes, `docs/sandbox.md`, `recipes/sandbox-refusals.c` (`path_is_under`, `in_runtime_library`, the `magic_setsig` wrapper), and the changes to `test_modules.py` and `test_refusals.py`. I also read the library files that store handlers (`Math/Complex.pm`, `CPAN.pm`, `sigtrap.pm`, `IPC/Cmd.pm`, `HTTP/Tiny.pm`, `Net/Cmd.pm`) and the runner's grant checks (`src/wasi_perl/_grants.py:82-88`). READ.
- I built from a clean tree in a fresh worktree: `WASI_PERL_DOWNLOAD_CACHE=/home/user/wasi-perl/downloads WASI_PERL_JOBS=2 scripts/build.sh`. All four steps were ok, with 1453 library files. Then `uv run --no-project --with pytest --with wasmtime==51.0.0 pytest tests/build -q` gave **296 passed**. EXECUTED.
- I tried to get user-controlled code judged as library code, with each case run through the same wasmtime-py `Guest` as the checks, beside the oracle. The library was granted read-only at `/usr/local/lib/perl5` and a scratch folder writable at `/work`. EXECUTED; the results are in the table below.
- The test-file changes that are not new checks only reflow lines (one assertion per line, no condition removed). The `Math/Complex` entry leaves `FAILS_IN_THE_SANDBOX` because that module now loads, and the CPAN entry moves to CPAN's next real blocker (`dup`). Nothing is weakened. No comment in `recipes/` or `tests/` names a specification number. READ.

### Attempts

| Attempt | Result |
|---|---|
| `-e` code with `#line 1 "/usr/local/lib/perl5/5.42/x.pm"`, then `$SIG{INT} = sub {}` | **accepted** silently |
| `eval "#line 1 \"…/5.42/strict.pm\"\n\$SIG{INT} = sub {}"`, and `# line 7 "…/wasm32-wasi/POSIX.pm"` (with a space) | **accepted** |
| `do "./e.pl"`, where the user wrote `e.pl` with a `#line` naming the library | **accepted** |
| An `@INC` hook returning source that starts with a `#line` naming the library | **accepted**. The same hook without `#line` is refused, at `/loader/0x…/Foo.pm` |
| `use sigtrap qw(handler my_handler INT TERM)` and `use sigtrap qw(die normal-signals)` | **accepted**. `$SIG{INT}` reads `main::my_handler` |
| `do "/usr/local/lib/perl5/5.42/../../../../../work/e.pl"` | fails to open (EPERM): wasi-libc does not climb out of the grant, and the `..` rule would refuse it anyway |
| `do "/usr/local/lib/perl5/5.42//strict.pm"` (empty step) | loads; it is a real library file |
| A `%INC` name spoofed to a library path, then `require` of a user `Evil.pm` | refused, at `Evil.pm line 1`: the file name comes from the path require opened, not from `%INC` |
| `CORE::GLOBAL::require` wrapping `CORE::require`, then `use Math::Complex` | loads, as natively |
| `local *CORE::GLOBAL::die` and similar | no effect on the check |
| A runner grant at `/usr/local/lib/perl5/5.42/x`, at `/usr/local/lib/perl5` or at `/usr/local/lib` | refused by `wasi_perl._grants.check` (ValueError) |
| Symlinks | not a path: `CopFILE` is the name Perl was given, and that name lies in the grant it was opened through. The library grant is read-only, so no link can be planted inside it |
| `use Math::Complex; $SIG{FPE} = …`, and `use Math::Trig; …; $SIG{ALRM} = 1` | refused at `-e line 1`, as they should be |

Two related paths fail honestly and need nothing here. `POSIX::sigaction` returns undef with `$!` "Function not implemented". `Time::HiRes::alarm` dies "unimplemented in this platform". EXECUTED.

## Findings

| # | Rank | Where | Finding | Suggested fix |
|---|---|---|---|---|
| M1 | Medium | `recipes/sandbox-refusals.c:163-169`; `docs/sandbox.md:38` | **`use sigtrap` installs the program's own handlers silently.** `sigtrap.pm:28` does `$SIG{$_} = $handler` with the handler the program named. The statement is in the library, so `use sigtrap qw(handler my_handler INT TERM)` and `use sigtrap qw(die normal-signals)` are accepted. The script then reads its handler back from `%SIG` and believes it will run (EXECUTED). This is ordinary, honest code, the exact case decision 3 exists to stop. The rule judges where the statement *is*, but sigtrap stores on the caller's behalf. Other library helpers that store a handler the caller passes (`IPC::Cmd`) are reached only after a refused `fork` or `alarm`, so sigtrap is the one live case found. | Refuse stores made by `sigtrap.pm`: exclude it in `in_runtime_library`, with its reason. Better, accept a library store only while a `require` of a library file is on the context stack (see M2). sigtrap's `import` runs after its `require` has returned, so it would be refused. Math::Complex's store is in a `BEGIN` block (`Math/Complex.pm:20-63`; line 2052 is POD), and CPAN's (`CPAN.pm:17`) also runs inside their own loads, so both would still be accepted. Add `use sigtrap qw(handler h INT)` to `REFUSED`. |
| M2 | Medium | `recipes/sandbox-refusals.c:165` (`CopFILE(PL_curcop)`); spec decision 3 (amended, `003-…md:17`); `docs/sandbox.md:38` | **`#line` makes any code library code.** `CopFILE` follows `#line`, so a `#line N "/usr/local/lib/perl5/5.42/…"` directive is enough. It works in `-e`, in a string `eval`, in a `do FILE` and in `@INC`-hook source, and every store after it is accepted (EXECUTED, table above). This is not a sandbox escape: the handler never runs, and honest code never names the runtime's library in `#line` (code generators name their own templates). It does make two sentences untrue: decision 3's "A user module in any granted folder is user code and is refused", and the docs' "What counts is where the statement is". It also lets a script defeat the "fail clearly" rule on purpose. | A sound check judges the compiling unit, not the statement's reported name. (1) Wrap `PerlIO_openn` (perlio.o, called from pp_ctl.o). When `PL_op` is `OP_REQUIRE` and the path is under privlib/archlib by `path_is_under`, record that path with the `cxstack_ix` of the eval frame about to be pushed. (2) In the `magic_setsig` wrapper, find the innermost `CXt_EVAL` frame and accept only if it is a require (`CxOLD_OP_TYPE(cx) == OP_REQUIRE`) whose recorded path is a library file. A `#line` cannot forge that frame, and a string eval, a `do FILE` or an `@INC` hook never produces it. `%INC` must not be the source: code compiled from a hook can rewrite its own `%INC` entry from a `BEGIN` block. If this is judged too much for the gain, keep `CopFILE` but say plainly in decision 3 and the docs that the judgement trusts the reported file name, which `#line` sets. Either way, add the four `#line` cases to the checks, as refused or as documented. |
| L1 | Low | `recipes/sandbox-refusals.c:163-169` | Beyond what the commit message says (stores while loading), the exemption covers every library statement at any time. Examples are `HTTP::Tiny`'s `local $SIG{PIPE} = 'IGNORE'` at request time and `Net::Cmd`'s. That is harmless today, because they need sockets first. But it is wider than the measured need. | The require-frame rule of M2 narrows it to load time. Otherwise, say in decision 3 that run-time stores by library code are accepted too. |
| L2 | Low | `tests/build/test_refusals.py:374-418` | The negative checks cover a granted folder, a lookalike folder, `site_perl` and two climbs, but not `#line`, string `eval`, `do FILE`, an `@INC` hook or `sigtrap`. | Add them with M1 and M2. |

## Verdict

Lands, once M1 and M2 are fixed or answered in writing. There is no High finding. The change does what it was made for: Math::Complex and Math::Trig load and match native. The path test is careful: a lookalike folder, `site_perl`, empty, `.` and `..` steps and a spoofed `%INC` are all refused, and the runner keeps every grant out of the library's path. Neither weakness lets a guest do more than store a handler that never runs. But `use sigtrap` (M1) quietly breaks the "fail clearly" rule for honest code, and `#line` (M2) makes the amended decision's own claim untrue.
