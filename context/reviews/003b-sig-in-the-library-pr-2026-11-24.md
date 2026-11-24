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

## Round 2

The answer to round 1 is fix commit 7ddfa59. A store is now accepted only when the innermost eval frame that is not an eval block is a `require`, the file it opened is a library file, and the statement's `CopFILE` is that file. The opened path is recorded by wrapping `PerlIO_openn` (under `OP_REQUIRE`) and `Perl_lex_start` (which ties the path to the frame about to be pushed), and a save-stack destructor drops the record when the frame ends. READ (`recipes/sandbox-refusals.c:159-263`).

I built from a clean tree in a fresh worktree: all four steps ok. `pytest tests/build -q` gave **309 passed**. EXECUTED.

### Round 1's bypasses, again (EXECUTED)

| Attempt | Result |
|---|---|
| `#line` naming the library in `-e`, in a string `eval`, in a `do FILE`, and in source from an `@INC` hook | all refused |
| `use sigtrap qw(handler my_handler INT)` | refused, at `sigtrap.pm line 28` (M1 fixed) |
| A store right after `require Math::Complex` | refused |
| `use Math::Trig; use Math::Complex` | loads and computes as natively |

### Attacks on the new mechanism (EXECUTED)

| Attempt | Result |
|---|---|
| Nested require: a user `Scalar/Util.pm` placed first in `@INC`, so Math::Complex's own `use Scalar::Util` loads it, and the user file stores | refused at `/work/u/Scalar/Util.pm line 1`. The innermost frame is the user file's require, which has no record |
| The same user file with `#line 1 ".../Math/Complex.pm"` | refused. Its frame is still the user file's |
| Library code that string-evals | refused by construction: the innermost non-block eval is then the string eval, not a require. READ (`:247-256`) |
| `do "/usr/local/lib/perl5/5.42/Math/Complex.pm"` | Math::Complex's own `local $SIG{FPE}` is refused (`OP_DOFILE` is not a require), so a `do` of the library fails where native succeeds. This is by design and harmless; L4 |
| An `@INC` hook returning a filehandle that the hook opened on a library file | no record is made: the hook's `open` runs under `OP_OPEN`. A later store is refused |
| A stale pending handle: `require "Math"` opens the library *directory*, then a later `@INC` hook returns a user filehandle under `#line ".../5.42/Math"` | refused |
| `local @INC = ("/usr/local/lib/perl5/5.42", "/work")` with a user `E.pm` | refused |
| An `@INC` hook asked for `Scalar/Util.pm` while Math::Complex loads, storing from the hook's own `-e` code | refused |
| **The same hook, with `#line 1 "/usr/local/lib/perl5/5.42/Math/Complex.pm"` above its store** | **accepted**. The hook runs while Math::Complex's require frame is innermost, since the frame of the require that called the hook is not pushed yet, and `#line` makes `CopFILE` equal the recorded path. The program ends with `$SIG{INT}` holding its own code ref and no message: L3 |

### Findings, round 2

| # | Rank | Where | Finding | Suggested fix |
|---|---|---|---|---|
| M1 | — | | **Fixed.** sigtrap's stores, at import or later, are refused. | |
| M2 | — | | **Fixed, apart from L3.** `#line`, a string `eval`, a `do`, `@INC` hooks and `%INC` on their own no longer pass. | |
| L1 | — | | **Fixed.** Only stores made while the file loads are accepted. | |
| L2 | — | | **Fixed.** `test_program_stores_cannot_pass_as_the_library` covers 11 cases. | |
| L3 | Low | `recipes/sandbox-refusals.c:258`; `docs/sandbox.md:38` | **Program code can still pass as the loading file.** It needs code that runs *inside* a library file's require (an `@INC` hook called for that file's own `use`) and that carries a `#line` naming exactly that file. The store passes because the check trusts `CopFILE`, which `#line` sets. It is deliberate and contrived: no honest code does it, and the handler never runs. But the docs' "code that names a library file in `#line` … is refused" is not quite true. | Judge the code, not its name. Record the require's eval CV (`PL_compcv` right after `lex_start`) with the frame. Accept only if walking `CvOUTSIDE` from `find_runcv(NULL)` reaches that CV. A library file's `BEGIN` blocks and subs have it as an outer scope. A hook, a `__WARN__` handler or any other program sub compiled elsewhere never does, whatever its `#line`. Add the hook case to the checks. Otherwise, qualify the docs sentence. |
| L4 | Low | `docs/sandbox.md:38` | `do` of a library file that stores at load (for example Math::Complex) now fails where native succeeds. | One line in the docs: only `require` and `use` load such a module. |

## Verdict, round 2

Lands: no High or Medium finding remains. M1, M2, L1 and L2 are fixed and checked on a fresh build (309 passed). Every round-1 bypass and every new attack aimed at the frame records is refused, except one contrived `@INC`-hook-plus-`#line` combination (L3). It can only store a handler that never runs. Fix it with the `CvOUTSIDE` check or qualify the docs.
