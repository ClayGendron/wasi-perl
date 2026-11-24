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
   - `threads`: it needs threads, which the build leaves out;
   - `porting`: it checks Perl's source tree or git (`t/porting/`), not the interpreter;
   - `dynamic-loading`: it needs `DynaLoader` to load a shared object;
   - `platform`: a WASI difference named in the entry's own `note` (for example, no users or groups, no `chmod` bits);
   - `bug`: a defect of this build, with the entry's `note` saying what fails. A `bug` entry is a gap, not a design choice, and the landing notes count them.
   A file not in the list must pass. A listed file that passes is reported, so a stale exclusion is seen and removed. Partial exclusions are not used: a file is in or out.
4. **The scoreboard.** The run writes `build/perl_t/results.json` and prints one line: files run, passed, excluded by reason, failed. `tests/perl_t/test_suite.py` (marked `slow`) fails if any non-excluded file fails or any excluded file passes.
5. **Where it runs.** Locally on demand, and in CI on Linux x86-64 on every pull request into `dev` that changes the build or the runner (008).

## Acceptance

1. A full run, EXECUTED, with its scoreboard line in the landing notes: zero failures outside the exclusions.
2. Every exclusion has a reason; `bug` entries are listed in the landing notes.
3. A fresh agent's review lands.

## Landing notes

Built 2026-11-24 on `spec/007-perl-t`, against the `perl.wasm` of `spec/001-build` at `bf33687` (SHA-256 `c544325d3989c8456b19d42248e2630c6d6531f03c2eb9c555e6ca69be9d6116`, copied to `build/snap/` so a rebuild there cannot change a run) and the runner of `spec/004-runner` at `c07360c`. This is the first full run, before 002 (the core XS modules) and 003 (the refusals) land, so most `bug` entries are "needs <Module> (spec 002)" and are expected to turn into passes or other reasons when those land.

### The scoreboard

EXECUTED, about 27 s wall on 4 cores (`build/perl_t/results.json` records it):

```
perl t/: 617 run, 300 passed, 317 excluded (subprocess 92, signals 5, porting 17, platform 12, bug 191), 0 failed, 0 stale exclusions
```

Acceptance 1 (zero failures outside the exclusions) holds for this binary. The owner's bar is not met yet: 191 `bug` entries are gaps.

### Commands

```
export WASI_PERL_ARTIFACTS=$PWD/build/snap/artifacts   # perl.wasm, lib/perl5, dev/null
export WASI_PERL_TARGET=$PWD/build/snap/target         # the target tree: lib/ (built) and config.sh
uv run --with-editable ../004-runner python tests/perl_t/run_suite.py --jobs 4 --list-failures
uv run --with pytest --with-editable ../004-runner pytest -q tests/perl_t           # 28 passed (slow test included)
uv run --with pytest pytest -q -m "not slow" tests/perl_t                         # TAP parser, exclusion list, scoreboard
```

A failing file's whole stdout and stderr are in `build/perl_t/logs/<file>.stdout|.stderr`. `--only 'op/*'` writes `results-only.json` and keeps the full run's file.

### How the harness follows t/TEST (READ: `t/TEST` `_scan_test`, `TestInit.pm`)

- Decision 1's argv is followed as t/TEST gives it: no switch for `base/`, `comp/` and `run/` (those files set `@INC` themselves), `-I.. -MTestInit` elsewhere (TestInit sets `@INC = ('../lib')` and `PERL_CORE`), and a `-t`/`-T` from the `#!` line. With TestInit, `@INC` is only `../lib`, so the tests see the built `lib/` of the target tree, never the artifact library: exactly what `make test` sees.
- The copy is the whole pristine tree (tests read `../MANIFEST`, `../pod`, `../cpan`, ...), with the target tree's `lib/` files that the tarball lacks (Config.pm, the extensions' Perl halves, `lib/unicore` tables) and its `config.sh` laid over it. Files the tarball has are kept as shipped.
- One copy per worker, not one shared copy: wasi-libc's emulated `getpid` gives every guest the same `$$`, so `test.pl`'s `tempfile()` names (built from `$$`) would collide between concurrent guests in one folder.
- The env is t/TEST's additions (`PERL_DESTRUCT_LEVEL=2`, `PERL_DL_NONLAZY=1`) plus `HOME`, `TMPDIR=/tmp` (a second writable grant per worker), `PATH` and `LC_ALL=C`. Limits: 300 s per file, 2 GiB of memory, 8 MiB per output stream, no fuel.
- All 617 `t/**/*.t` files run, `japh/`, `benchmark/`, `bigmem/` and `win32/` included, although t/TEST runs those only when asked or on Windows.
- An uncaught `die` exits with the errno value, as natively, so WASI's numbers show up: 44 (ENOENT), 52 (ENOSYS), 58 (ENOTSUP).

### Counts by reason (EXECUTED, the run above)

| Reason | Files |
|---|---|
| subprocess | 92 |
| signals | 5 |
| threads | 0 (no file failed for threads: they skip themselves) |
| porting | 17 (19 of `t/porting/` pass) |
| dynamic-loading | 0 (`re` shows "dynamic loading not available", but the fix is to link it statically, so it is `bug`, needs re) |
| platform | 12 |
| bug | 191 |

How the classification was made (INFERRED from each file's output, a scratch script not kept): a missing module on the fatal path gives `bug` "needs X (spec 002)"; a file whose every failing test's `# Failed test N at LINE` falls within 15 lines of `runperl`/`fresh_perl`/`run_perl`/`system`/backticks/a pipe open gives `subprocess`; `alarm` or `kill` gives `signals`; the rest (64 files) were read by hand. Today a refused child returns `undef` with `$?` of -1 (001's stubs), so these are `not ok`, not deaths; with 003 they will die with "not available in the wasi-perl sandbox", which makes the next classification surer.

### The `bug` entries, grouped by cause (for the 001/002/003 builders)

Needs a core XS module (spec 002), by module (EXECUTED: each file's fatal "Can't locate X.pm in @INC" or "Can't load module X, dynamic loading not available"):

| Module | Files | Examples |
|---|---|---|
| mro | 65 | 65 of the 73 `mro/*.t` (of the other 8, 4 pass, 2 are signals, 2 subprocess) |
| re | 41 | op/closure.t, op/ref.t, op/sub.t, re/reg_mesg.t, and re/regexp*.t (6: `_charnames.pm` uses re) |
| POSIX | 18 | op/groups.t, op/quotemeta.t, re/charset.t, re/fold_grind_*.t, re/uniprops01-10.t, run/switches.t, uni/overload.t (`t/loc_tools.pl` dies "Invalid locale category name 'CTYPE'" without POSIX's `LC_` constants) |
| File::Glob | 9 | io/open.t, io/perlio.t, op/glob.t, op/lvref.t |
| attributes | 7 | op/attrs.t, op/attrhand.t, op/attrproto.t, op/state.t |
| Fcntl | 6 | io/eintr.t, io/scalar.t, op/goto_xs.t |
| Cwd, File::Spec, File::Spec::Functions (PathTools) | 11 | comp/require.t, lib/croak.t, io/argv.t, op/stat.t, op/chdir.t |
| B | 5 | op/coresubs.t, op/svflags.t, perf/opcount.t |
| IO (IO::File, IO::Handle, IO::Select) | 6 | io/iofile.t, io/crlf.t, op/filehandle.t, op/readline_nb.t |
| Hash::Util | 4 | op/each.t, op/hash.t |
| Devel::Peek | 3 | op/hash-rt85026.t |
| Scalar::Util, List::Util | 5 | op/smartmatch.t, op/aassign.t, op/lc.t |
| Encode (with PerlIO::encoding) | 3 | uni/gv.t, uni/method.t, io/layers.t |
| XS::APItest | 2 | bigmem/stack.t, bigmem/stack_over.t |
| Time::HiRes, Data::Dumper, Filter::Util::Call | 3 | io/eintr_print.t, op/signame_canonical.t, op/incfilter.t |

Not a module (spec 001's build):

- **Reopening STDOUT or STDERR onto a file fails "Not supported"** (io/dup.t line 22, op/die_exit.t line 61; also seen in run/cloexec.t). `config.sh` says `d_dup2='define'`, but the call fails: the build's `dup2` refuses. WASI's `fd_renumber` could serve `dup2(new, 1)`. Agents' scripts do `open STDOUT, '>', $file`, so this one matters beyond the suite (INFERRED).
- **op/universal.t dies with no message** after test 112 (near line 212, inside a `$SIG{__WARN__}` that dies). Unexplained.

Also noted inside entries of other reasons, to recheck when 002 lands: op/eval.t test 130 needs re; op/gv.t tests 268-269 need IO.

### The `platform` entries

`>&STDOUT` duplication (no `dup` in WASI preview1): op/fh.t, op/write.t (the whole format suite stops at line 315). `chmod` unimplemented: op/filetest.t. No time-zone database: op/time.t test 7. Descriptors are not lowest-free: op/require_37033.t. 32-bit sizes: bigmem/regexp.t. No perl executable file for `$^X`: op/pack.t. A wall-clock benchmark: benchmark/gh7094-speed-up-keys-on-empty-hash.t. Windows-only: win32/crypt.t, fs.t, runenv.t, seekdir.t.

op/write.t (636 tests) and op/pack.t (14724 tests) are large files lost to one line each, so they are worth a second look once 002 and 003 land.
