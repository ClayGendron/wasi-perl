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

Built 2026-11-24 on `spec/007-perl-t`. The current run is against this worktree's own build of dev at `eee84ac` (001 to 006, with 002's XS modules and 003's refusals), built by `WASI_PERL_DOWNLOAD_CACHE=/home/user/wasi-perl/downloads WASI_PERL_JOBS=3 scripts/build.sh`: `perl.wasm` SHA-256 `23dd4c6853b0ee08be2f27489e91b893f0580deb5b0e0ff704c09942c52a895f`, run by dev's own runner (`src/wasi_perl`).

### The scoreboard

EXECUTED, 95 s wall on 4 cores:

```
perl t/: 617 run, 433 passed, 184 excluded (subprocess 141, signals 11, porting 6, platform 14, bug 12), 0 failed, 0 stale exclusions
```

Acceptance 1 (zero failures outside the exclusions) holds. 12 `bug` entries remain as gaps.

History (EXECUTED): the first run, against 001's minimum build before 002 and 003, was `617 run, 300 passed, 317 excluded (subprocess 92, signals 5, porting 17, platform 12, bug 191), 0 failed`, with 188 of the 191 bugs a missing 002 module. On dev, 142 of those exclusions passed and were removed in their own commit; nine files that passed before now die at their first refused child (003) and were added as subprocess or signals.

### Commands

```
scripts/build.sh                                               # build/artifacts and build/target/lib
uv run python tests/perl_t/run_suite.py --quiet --list-failures   # what CI runs; exit 1 on a failure or a stale exclusion
uv run pytest -q tests/perl_t                                  # the slow suite test and the fast ones
uv run pytest -q -m "not slow" tests/perl_t                    # TAP parser, exclusion list, scoreboard
```

Defaults match CI's perl-t job (008's ci.yml, READ): the tarball in `downloads/` (verified against `downloads/SHA256SUMS`, then unpacked once at `build/perl-5.42.3/`), the artifact set at `build/artifacts/`, and the target library at `build/target/lib/`. `WASI_PERL_ARTIFACTS`, `WASI_PERL_TARGET` and `WASI_PERL_TARBALL` override them. A failing file's whole stdout and stderr are in `build/perl_t/logs/`, which CI uploads with `results.json`. `--only 'op/*'` writes `results-only.json`.

### How the harness follows t/TEST (READ: `t/TEST` `_scan_test`, `TestInit.pm`)

- Decision 1's argv is followed as t/TEST gives it: no switch for `base/`, `comp/` and `run/` (those files set `@INC` themselves), `-I.. -MTestInit` elsewhere (TestInit sets `@INC = ('../lib')` and `PERL_CORE`), and a `-t`/`-T` from the `#!` line. With TestInit, `@INC` is only `../lib`, so the tests see the built `lib/` of the target tree, never the artifact library: exactly what `make test` sees.
- The copy is the whole pristine tree (tests read `../MANIFEST`, `../pod`, `../cpan`, ...), with the target tree's `lib/` files that the tarball lacks (Config.pm, the extensions' Perl halves, `lib/unicore` tables) and its `config.sh` laid over it. Files the tarball has are kept as shipped.
- One copy per worker, not one shared copy: wasi-libc's emulated `getpid` gives every guest the same `$$`, so `test.pl`'s `tempfile()` names (built from `$$`) would collide between concurrent guests in one folder.
- The env is t/TEST's additions (`PERL_DESTRUCT_LEVEL=2`, `PERL_DL_NONLAZY=1`) plus `HOME`, `TMPDIR=/tmp` (a second writable grant per worker), `PATH` and `LC_ALL=C`. Limits: 300 s per file, 2 GiB of memory, 8 MiB per output stream, no fuel.
- All 617 `t/**/*.t` files run, `japh/`, `benchmark/`, `bigmem/` and `win32/` included, although t/TEST runs those only when asked or on Windows.
- An uncaught `die` exits with the errno value, as natively (Linux numbers since 003).
- The guest's `$^X` is `perl`, so `test.pl`'s `which_perl` looks for `./perl`, which the guest does not have; a file that makes warnings fatal then dies there, before its child would be refused.

### Counts by reason (EXECUTED, the run above)

| Reason | Files | How it was decided |
|---|---|---|
| subprocess | 141 | the file dies with "`<what>` is not available in the wasi-perl sandbox" from runperl/fresh_perl in `test.pl` or its own `system`, backticks or pipe open (129); finds no perl to run (9, op/override.t included); or is refused where the message is not seen (comp/colon.t's `qx`, io/pvbm.t's `pipe`, op/die_exit.t's `system` with STDERR in a file) |
| signals | 11 | dies with "signal handler for SIG… is not available" or "kill is not available" |
| threads | 0 | the thread tests skip themselves |
| porting | 6 | `t/porting/` files that refuse a command or plan `1..0` without the word skip; 30 of `t/porting/` pass |
| dynamic-loading | 0 | every extension is static |
| platform | 14 | below |
| bug | 12 | below |

The classification is INFERRED from each file's output by a scratch script plus a hand reading of the rest; the refusal messages make it direct.

### The `bug` entries, grouped by cause (for the builders)

- **No dup for `>&` handles (spec 009), 8 files:** op/write.t (stops at line 315, before the 636-test format suite), op/fh.t, io/dup.t, io/perlio.t, op/hash-clear-placeholders.t and op/hash-rt85026.t (Devel::Peek's capture of STDERR), op/numify_chkflags.t, test_pl/can_isa_ok.t.
- **Four-argument `select` with a non-empty bit vector traps "memory out of bounds", 1 file:** op/tie_fetch_count.t line 274. EXECUTED: `perl -e '$v="\xff"; select $v,undef,undef,0'` traps, `"\x01"` does not. INFERRED cause: wasi-libc's `fd_set` is a count and an array of descriptors, not the bit mask Perl's `pp_sselect` writes into. A trap is the worst outcome; it belongs to 001/003.
- **XS::APItest is not built, 2 files:** bigmem/stack.t, bigmem/stack_over.t.
- **Unexplained, 1 file:** lib/deprecate.t passes all 10 tests, then exits 2: its END block's `File::Path` cannot load Carp after the test has changed directory away from its relative `@INC`.

### The `platform` entries

chmod unimplemented: op/filetest.t, op/stat.t (also a hard link's ctime). No time-zone database: op/time.t. Descriptors are not lowest-free: op/require_37033.t. No sockets: op/coreamp.t (`&CORE::bind`). 32-bit sizes: bigmem/regexp.t. No perl executable file: op/pack.t. A wall-clock benchmark: benchmark/gh7094-speed-up-keys-on-empty-hash.t. Windows only: win32/crypt.t, fs.t, runenv.t, seekdir.t, stat.t, symlink.t.
