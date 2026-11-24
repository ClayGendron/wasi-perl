# Review: 019, the library index (PR #29)

Reviewer: a fresh agent (Claude Opus 5.5, `claude-opus-5-5`) that did not write the change. 2026-11-24.

- **Reviewed:** round 1, `git diff origin/dev...HEAD` on `spec/019-the-library-index` at head `48ef643` (`48ef6434720cf0678b584948d72388306344f1b5`), base `8897c22`. Round 2, the fixes `48ef643..f47b716` (`d4db0ab`, `0a08cf5`, and `f47b716`, a merge of dev with spec 020); its section is at the end and holds the current verdict. Read against `AGENTS.md`, `context/constitution.md`, the specification with its landing notes, the memo `context/research/2026-11-24-the-library-index-measured.md` and the PR body.
- **Probes:** in `/tmp/claude-0/-home-user-wasi-perl/ecbe2564-8e44-50cd-9bc9-1c1b283f37e5/scratchpad/r019/`. The reverted-code run used a scratch worktree, `/home/user/wasi-perl-wt/review-019-scratch`, since removed. No code in the worktree was changed.
- **"Before"** is dev's set at `8897c22`, the one the memo measured: `scratchpad/base-artifacts/`, `perl.wasm` SHA-256 `5124d6b4…`, with a `MANIFEST` byte for byte the branch's (EXECUTED: `sha256sum`, `cmp`). **"After"** is the worktree's `build/artifacts/`, `perl.wasm` `55cdde9c…`.

Labels: EXECUTED means I ran it and saw the result. READ means I read it in a file. INFERRED means my reasoning.

## Verdict, round 1

**Does not land.** The index is well built. On Linux, for every path a module search makes, it answers exactly as the host did, and CI is green on every leg. But the rule it is built on is "the index answers only where the host's answer is already known". Two kinds of path break that rule:

- **H1.** On a host whose file names ignore case (macOS and Windows, two of the four release platforms), a wrong-case library name was found and now is not. The specification did not decide this, the owner has not agreed it, and no check runs it on such a host.
- **M1.** On Linux, two errnos change: `ENAMETOOLONG` and `EILSEQ` become `ENOENT`. The CHANGELOG says every error is unchanged.

Fix H1 and M1 in one round. The Low findings can be fixed or answered in writing.

## Findings

### High

**H1. On a case-insensitive host, the index answers "not found" for a name the host would find.**

- **Evidence.**
  - `index_answer` (`recipes/wasi-fd-table.c:287-332`) compares with `strcmp`, so `5.42/Strict.pm` misses and is answered `ENOENT` with no host call (READ).
  - The host is no longer asked. With `count_calls.sh` on Linux, `-e "/usr/local/lib/perl5/5.42/Strict.pm"` makes 1 failed host stat on dev's set and 0 host calls on the branch. `eval { require Strict }` makes 8 failed host stats on dev and 0 on the branch (EXECUTED).
  - macOS's default APFS volume and NTFS ignore case, so there the host finds `strict.pm` for `Strict.pm`. So `use Strict` loaded `strict.pm` there before, and now dies "Can't locate Strict.pm". INFERRED: I could not mount a case-insensitive file system here (`mount -o loop` was refused), and the implementer did not run one either. The landing notes say so.
  - The same holds for any other name a Windows host resolves to a library file the index spells differently. Candidates are a trailing `.` or blank, an 8.3 short name such as `STRICT~1.PM`, and a `\` the host might read as a separator. INFERRED: not run, and I did not check what cap-std does with each.
  - The specification's Intent says "`@INC`, `.pmc` handling, every errno and every other behaviour stay as they are". Decision 3 says "the index answers only where the host's answer is already known". Here the host's answer differs by platform, so it is not known. `docs/modules.md` now documents the new behaviour. But documenting it is not the owner's agreement, and AGENTS.md says "nothing is built that no specification describes". CI's macOS and Windows legs pass only because no test uses a wrong-case name (READ: the PR body; EXECUTED: `get_check_runs`, all seven green).
- **Fix.** Ask the owner with AskUserQuestion. Either option needs a check that runs on CI's macOS and Windows wheel legs, so the claim is EXECUTED there.
  - **Recommended: (b) agree the behaviour.** A wrong-case or host-mangled library name is "not found" on every host, as on Linux, the oracle. Amend Decision 3 to say so. Add a check under `tests/runner/` or `tests/escapes/`, which those legs run: `-e "$L/5.42/Strict.pm"` is false with `$!{ENOENT}`, and `require Strict` dies "Can't locate Strict.pm". The result is the same bytes on every platform. It matches the oracle, and it also keeps Windows device names such as `5.42/CON` away from the host.
  - **(a) keep the specification's literal rule.** `lib_index.sh` also writes the names lowercased and sorted. On a miss, the table walks the lowercased path through that array, and it answers itself only when that walk misses too. Any component the Windows host may rewrite (a trailing `.` or blank, a `~`, a `\`, a `:`) goes to the host. This keeps the host's answer, at the cost of a second search on every miss.

### Medium

**M1. On Linux, two errnos change from the host's to `ENOENT`.**

- **Evidence** (EXECUTED: `run_matrix.py` with `m2.pl`, through the runner with the library read-only, on dev's set and on the branch; output in `b2.txt` and `a2.txt`).

  | path under `/usr/local/lib/perl5` | dev (the host) | branch (the index) |
  |---|---|---|
  | `5.42/` + 256 × `a` (`-e` and `open "<"`) | 37 `ENAMETOOLONG` | 44 `ENOENT` |
  | 300 × `a` + `/x` | 37 `ENAMETOOLONG` | 44 `ENOENT` |
  | `5.42/\xe9.pm`, one byte that is not UTF-8 | 25 `EILSEQ` | 44 `ENOENT` |
  | `5.42/\xff/x` | 25 `EILSEQ` | 44 `ENOENT` |
  | `5.42/strict.pm/\xe9` | 25 `EILSEQ` | 54 `ENOTDIR` |
  | `5.42/\xc3\xa9.pm`, valid UTF-8 | 44 | 44 |
  | 255 × `a`, the limit | 44 | 44 |

  - The host refuses a component longer than 255 bytes, and wasmtime refuses a path that is not UTF-8, before either looks the name up. `index_answer` only bounds the whole path, at 1,023 bytes, and treats any byte as a name byte (READ).
  - For the 256-byte case the change moves away from native Linux perl, which answers `ENAMETOOLONG`. The CHANGELOG line says "every error a program sees are unchanged". The specification says "every errno … stay as they are".
  - The first, larger matrix (`matrix.pl`: 45 paths × 14 operations, including `.`, `..`, `//`, a trailing `/`, the grant itself, `chdir` into the library and relative paths, and `O_DIRECTORY`, `O_NONBLOCK`, `O_EXCL`, `O_TRUNC`, `O_NOFOLLOW`, `O_RDWR` and `O_CREAT|O_EXCL`) differs between dev and the branch in only one row, the `\xe9` one. Everything else answers byte for byte as before (EXECUTED: `diff before.txt after.txt`).
- **Fix.** In `index_answer`, ask the host for any path with a byte of 0x80 or above, and for any component longer than 255 bytes. Every library name is ASCII (EXECUTED: `grep -P '[^\x00-\x7f]' MANIFEST` finds none), so this costs nothing in practice. Add the 256-byte row and one non-UTF-8 row to check 3's table in `tests/build/test_library_lookups.py`, both as "from the host". Reword the CHANGELOG line, and fix its grammar ("every error … is unchanged").

### Low

**L1. `lib_index.sh` lets a name with a newline through, and then writes a wrong index.**
- **Evidence.** The script says it refuses a control character, but it checks `find … | grep`, which reads line by line. A library holding `5.42/x⏎b.pm` gave exit 0 and an index of `5.42`, `5.42/A.pm`, `5.42/x`, `b.pm`: two names that do not exist, and the real name missing (EXECUTED: `scratchpad/r019/nl/`). The C stays valid, since each piece passes the quote and backslash test, so nothing is injected. In the real build, `30_library.sh`'s check has the same blind spot, and the build would fail later in `listed`'s `sha256sum`, with an unclear message (INFERRED).
- **Fix.** Refuse with `find . -mindepth 1 -name "$(printf '*\n*')"`, or with a `find -print0` test, in `lib_index.sh`, and the same in `30_library.sh`. Add the case to `test_lib_index_refuses_an_empty_folder_and_a_link`.

**L2. `40_link.sh` reports "up to date" with no `perl.wasm` in the set.**
- **Evidence.** Its up-to-date test is the stamp plus `$ART/SHA256SUMS` (`scripts/40_link.sh:26`). Delete only `build/artifacts/perl.wasm` and every step says "up to date", leaving a set with no `perl.wasm`. On dev, `20_target.sh`'s test looked for `$ART/perl.wasm`, so that deletion rebuilt it (READ).
- **Fix.** Also require `[ -f "$ART/perl.wasm" ]` in `40_link.sh`'s up-to-date test.

**L3. A writable library: files made at run time are half-visible, and the look skips its device guard for them.**
- **Evidence.** Under the wasmtime command line, with a writable copy of the library, a program created `5.42/New.pm`. Then `-e` said "No such file or directory", `require New` failed, and a `mkdir`'d folder was not seen by `-d`, yet `readdir` listed `New.pm` (EXECUTED). On dev's set, all of these see it. Also, `look()` asks the index before its stat (`wasi-fd-table.c:1013`). So for a write-shaped open of a name the index lacks, the guard that refuses a FIFO or a device is skipped (READ). The runner never mounts the library writable (`runner.py:340`; `_grants.py`'s `RESERVED`, READ), and constitution 4.5 says the runtime's files are never writable. So only an embedder outside the runner meets this.
- **Fix.** In `docs/modules.md` and the table's comment, say the library must be mounted read-only. Optionally, drop the index from `look()`. For reads, `path_open` has already asked the index, so the index in `look()` only saves a host stat on a write-shaped open, which the read-only library refuses anyway. That would be a one-line amendment to Decision 3.

**L4. A test comment names the specification.**
- `tests/build/test_library_lookups.py:110`: "Decision 3's table: …" (READ). AGENTS.md says a file under `tests/` gives the reason in words. **Fix:** "Each row: a program's call under the library, the errno it gives, and whether the call reaches the host."

## What holds

- **The rule on Linux, for what modules do.** The index answers only `path_filestat_get`, the look's stat, and a `path_open` that asks for none of `CREAT`/`TRUNC`/`EXCL`, write rights, or `APPEND`/sync flags (READ: `asks_only_to_read`, `index_answer`). `.`, `..`, `//`, a trailing `/`, the grant itself, relative paths after `chdir`, and every `sysopen` flag set I tried answer as on dev (EXECUTED: the matrix). The four write-shaped rows keep `EPERM`.
- **The descriptor check.** `libdir` is a real descriptor that never changes. `close` on a grant is a no-op, and `fd_renumber` and `dup2` refuse to move or replace one (`wasi-fd-table.c:1090-1150`, READ). So no other folder can ever take the library's number. A descriptor for a library subfolder is never judged.
- **The binary search.** The prefix test is right: in byte order, every name starting with `p` sorts after `p` and before any other name greater than `p` (READ, INFERRED). `strcmp` and `LC_ALL=C sort` agree on byte order.
- **Security.** The index only ever returns an error or passes the call on. It never answers success, so it cannot make anything visible (READ). No name can break the C: quotes, backslashes, blanks and control characters are refused, except for L1's newline, which splits a name but injects nothing. Trigraphs are off under clang's default `gnu17` (INFERRED).
- **The marker and `SHA256SUMS`.** The shipped `perl.wasm` holds the marker once, with the hash of the built index's 1,728 names. `sha256sum -c SHA256SUMS` passes for all 1,464 files (EXECUTED).
- **The relink.** `40_link.log` holds exactly one `wasi-cc` command, `-o perl`, with `build/index/lib-index.o` before the stubs and no `-c`. The only other lines are `make_ext.pl`'s up-to-date checks. `mktables` says "not bothering to rebuild" (EXECUTED: `grep`). The relink has no `-j` and runs one make, so it adds no race.
- **The stamps** (READ). `.link.stamp` covers `40_link.sh`, `lib_index.sh`, `lib.sh` and `.library.stamp`. `.library.stamp` covers `.target.stamp`, which covers all of `recipes/`, the new header and the empty index among them. A rerun of `20_target.sh` removes `build/artifacts/`, which forces 30 and 40 to run again. The implementer's touch runs (`build/logs/touch-*.out`, `up-to-date.out`) agree.
- **The tests fail without the fix.** With dev's `recipes/wasi-fd-table.c` in a scratch worktree, three of the C probe's checks fail: no-host `ENOENT`, no-host `ENOTDIR`, and a create with no look stat (EXECUTED). With `WASI_PERL_ARTIFACTS` set to dev's set, 8 checks fail: checks 1 and 2, the four "from the index" rows, check 6 and the shipped marker. The rest pass, as pins should (EXECUTED).
- **No test was weakened.** The only change to an existing test is `test_cpan_unpack.py`'s stand-in. It moves from `artifacts/perl.wasm` to `target/perl`, because that is now `30_library.sh`'s precondition. Its assertions are unchanged (READ). `tests/perl_t/exclusions.toml` is untouched.
- **CI.** Run 45477511487 is green on all seven jobs (EXECUTED: `get_check_runs`). In the build job, `40_link: ok`, 509 passed, and the gate printed "71 checks of the CPAN modules and the library index (at least 40), 0 skipped" (EXECUTED: the job's log). So the C probe, which needs wasi-sdk, ran in CI and cannot skip there without failing the job.
- **The memo's counts.** I reran `count_calls.sh` on both sets. dev: `-MPOSIX -e 1` gives 22 ok and 73 failed stats, the five modules 36 and 118. Branch: 22 and 0, 36 and 0. Opens: 11 and 18 on both (EXECUTED). They match the memo.
- **The API.** Only the `Artifacts` docstring changes. No signature moves away from wasi-python's (READ).

## The tests run

| command | result |
|---|---|
| `uv run pytest tests/build/test_library_lookups.py tests/build/test_lib_index.py tests/build/test_cpan_unpack.py tests/escapes -p no:cacheprovider` | 86 passed (EXECUTED) |
| the same two new files with `WASI_PERL_ARTIFACTS=<dev's set>` | 8 failed, 30 passed, as above (EXECUTED) |
| `test_lib_index.py` with dev's `wasi-fd-table.c` (scratch worktree) | the three probe checks fail (EXECUTED; a fourth failure was my scratch's path, not the code) |
| `uv run ruff check src tests`; `uv run ruff format --check src tests` | clean (EXECUTED) |
| `count_calls.sh`, both sets, `-MPOSIX -e 1` and the five modules | as the memo (EXECUTED) |
| `run_matrix.py` with `matrix.pl` and `m2.pl`, both sets | M1's rows differ; nothing else (EXECUTED) |
| the wasmtime command line, writable library copy, both sets | L3 (EXECUTED) |
| `lib_index.sh` on a name with a newline | L1 (EXECUTED) |
| full build, Perl's `t/` suite, the one-liner oracle | not rerun. CI's build and "Perl's own suite" jobs are green on the head (EXECUTED: check runs) |

## Round 2

- **Reviewed:** `git diff 48ef643 f47b716` on `spec/019-the-library-index`, head `f47b716` (`f47b7167678e5f32a330ff0a095823fcbe1ff340`). I left out the dev merge's own content (spec 020). The merge changes no file under `recipes/`, `scripts/` or `src/`, only `.github/workflows/ci.yml`, whose 020 lines sit beside this branch's gate (EXECUTED: `git diff 0a08cf5 f47b716 --stat -- recipes scripts src .github`).
- **The owner's decision on H1:** keep the host's behaviour. The index may answer only where every host would agree. Decision 3 carries a dated amendment that records it (READ: the specification, lines 97–101).
- **The build** in the worktree is the head's. `flock …machine.lock scripts/build.sh` gave every step "up to date", and the stamps cover all of `recipes/` and the scripts (EXECUTED). Its `perl.wasm` is `d48222e0…`.

### Verdict, round 2

**Lands.** Every finding of round 1 is fixed. Each fix that changes the guest has a check that fails without it, and I saw it fail.

- I fuzzed the new rule with 36,982 paths, each as a stat and as a read. They mix case at every depth, use `.`, `..`, trailing `/`, `.` and blank, `~`, `:`, device names and non-UTF-8 bytes. The index answered 47,822 lookups itself. Every one of those answers equals both the Linux host's answer and a case-insensitive host's answer.
- Two new Low findings remain, N1 and N2. Neither is a case where the index hides a file some host would find. They may be fixed in a follow-up or answered in writing.
- On CI, the ARM leg's abort at interpreter exit is weighed below as unrelated to this change. **Merge only if the CI run of this commit is green.** If the ARM leg aborts the same way again, two in a row is no longer chance: do not merge, and look into it on its own first.

### Each finding

| finding | fix | evidence |
|---|---|---|
| H1, case | `lib_index.sh` sorts by ASCII-lowercased bytes, keeps each name's case, and refuses two names that differ only in case. `index_answer` searches with a folding compare and narrows the range per folder. ENOENT is answered only when no case of the name is there. ENOTDIR is answered only below a file reached by its exact names. Anything else goes to the host. | READ: `recipes/wasi-fd-table.c` `fold_cmp`, `index_find`, `index_answer`; the fuzz below. With `fold` made the identity, 7 of the C probe's host-path checks fail (EXECUTED) |
| H1, Windows | A component ending in `.` or a blank, or holding `~`, `\` or `:`, goes to the host. So does one whose stem is a device name in any case. | With `is_device` made to return 0, 5 checks fail: `CON`, `con.pm`, `LPT1.x`, `Nul.txt/x`, `conout$` (EXECUTED) |
| H1, on real hosts | `tests/runner/test_library_case.py` compares the guest with a plain host open of the same name. CI's macOS and Windows legs ran its 3 checks with 0 skipped. `test_a_host_that_finds_a_name_in_another_case_is_heard` simulates a case-insensitive host on Linux. | EXECUTED: the macOS and Windows job logs, "285 guest checks (… 3 library-case checks), 0 skipped". INFERRED: the macOS runner's volume ignores case, as APFS does by default, so there the checks take the "found" branch |
| M1 | A component over 255 bytes, or any byte of 0x80 or above, goes to the host. | Rows in check 3 give `ENAMETOOLONG` and `EILSEQ`. With both deferrals removed, 4 probe checks fail (EXECUTED) |
| L1 | Both scripts refuse a name with a newline with `find -name`. | Checks in `test_lib_index_refuses` and `test_cpan_unpack.py` (READ; they pass, EXECUTED) |
| L2 | `40_link.sh` is up to date only while `perl.wasm` exists. | `test_the_link_step_is_not_up_to_date_without_perl_wasm` (READ; passes, EXECUTED) |
| L3 | The index is out of `look()`. `docs/modules.md` and the `Artifacts` docstring say the library is mounted read-only. | Failed stats are still 118 → 0 and 73 → 0; found stats are 36 and 22, opens 18 and 11 (EXECUTED: `count_calls.sh` on the head's set). The create probe is back to dev's "1 stats, 1 opens" (READ) |
| L4 | The comment is reworded. | READ |

### Trying to break the new rule

- **The method.** In the scratchpad (`r019/fz/`), I linked a probe like the C probe, but reading many paths from stdin, with the head's table (renamed as the check renames it). I built it twice: with an index of a test library and with the empty index. The library holds `5.42/{strict.pm, strict.pmc, File/Find.pm, File/Spec.pm, File/Spec/Unix.pm, Text/Abbrev.pm, A-B/c.pm, a.b/x.pm, wasm32-wasi/POSIX.pm, wasm32-wasi/auto/POSIX/x.ix, Z.pm, _u.pm}`. `_u.pm` sorts before `A-B` once folded, which tests the folded order.
- **The paths.** `fuzz2.py` built 36,982 paths of one to four components from 70 words. Among them: `File`/`FILE`/`file`, `Strict.PM`, `STRICT.pm`, `FIND.PM`, `WASM32-WASI/AUTO`, `.`, `..`, `File.`, `strict.pm ` (trailing blank), `STRIC~1.PM`, `a:b`, `CON`, `nul.pm`, `%e9`, and a trailing `/` on one path in ten. It also built every folder prefix × word × word, so folder-level case variants and deep mixed cases are covered.
- **The result** (EXECUTED). For each path the index answered itself, I compared its answer with two others: the Linux host's answer, from the empty-index probe; and the answer of a model host that ignores ASCII case. The index answered 47,822 lookups (47,038 ENOENT, 784 ENOTDIR), with **0 mismatches**. It judged no path holding `~`, `:`, `\`, a non-ASCII byte, a device stem, or a component ending in `.` or a blank. Where the index passed a lookup to the host, the host's answer was the same as through the empty index.
- **Hand-picked cases** (EXECUTED). These go to the host: `5.42/Strict.pm/x`, `5.42/FILE/Find.pm/x`, `5.42/File/FIND.PM/x`, `5.42/File./Find.pm`, `5.42/file/spec/UNIX.PM`. These are answered by the index, correctly on every host: `5.42/FILE/nope` (ENOENT); `5.42/strict.pm/X` and `5.42/File/Spec/Unix.pm/x` (ENOTDIR, below an exact-case file).
- **The search** (READ, INFERRED). In folded byte order, the names under a folder are contiguous. `index_find` with tail `/`, and then with `'0'` (the byte after `/`), bounds exactly that block. `fold_cmp` orders a shorter name first. The ENOTDIR test, `memcmp` of the whole prefix, needs every component to match its exact case. I found no flaw.

### New findings, round 2

**N1 (Low). Windows' other invalid names are still answered ENOENT by the index.**
- **Evidence.** These are answered ENOENT with no host call: `5.42/a*b`, `a?b`, `a<b`, `a|b`, `a"b`, a name holding byte 0x01, and the device stems with trailing blanks `CON .pm` and `nul .pm` (EXECUTED: the multi-path probe).
  - No file can have such a name on Windows, so no host finds a library file this way: nothing is hidden.
  - But a Windows host may answer another errno, for example from `ERROR_INVALID_NAME`. Older Windows also takes `CON .pm` for the console (INFERRED, not run).
  - That is the same kind of errno-only difference as M1, on one host only.
- **Fix.** Add `* ? < > | "` and bytes below 0x20 to `index_plain`'s refusals, and strip trailing blanks from the stem in `is_device`. Add one check row for each. No Perl module name holds any of them, so the counts do not change.

**N2 (Low). The memo's "after" fuel is of the round-1 build, not the head's.**
- **Evidence.** The memo measured `ade0a15c…` (its §2). The head's set, `d48222e0…`, adds the device rule, and its fuel is a little higher. Five modules: 123,227,113, which is +1.25% against dev (memo: 123,193,275). `-MPOSIX -e 1`: 72,010,878, +1.33% (memo: 71,991,025). This is EXECUTED: `measure.fuel` under the machine lock.
- **Fix.** Add the head's hash and these two numbers to the memo, or rerun its fuel column.

### The ARM leg's abort on `f47b716`

- **What happened.** The run is 45486240838, job 138774884353. In the smoke step, `run()` returned `Completed(exit_code=0, stdout=b'hello\n', …)`. Then two `tokio-rt-worker` threads panicked with "panic in a function that cannot unwind", and the process aborted with exit 134 before any test ran (EXECUTED: the job log). The other six jobs passed. In the build job, 559 passed and the gate printed "121 checks … 0 skipped". The Windows and macOS legs ran 285 guest checks with 0 skipped (EXECUTED: the job logs).
- **Why I judge it unrelated** (READ, INFERRED).
  - The panic is in the host's wasmtime-wasi threads, at interpreter exit, after the call had returned.
  - This branch changes nothing in `src/` but the `Artifacts` docstring (EXECUTED: `git diff 8897c22 f47b716 --stat -- src/`). `_engines.py`'s exit path, `_release_at_exit`, which frees modules and engines, is dev's.
  - The guest side cannot reach those threads except through WASI calls. A hello program makes no lookup under the library (`-e 1`: 0 calls, EXECUTED in round 1 and again on this head). Its start-up calls (`fd_prestat_get` and `fd_prestat_dir_name` per grant) did not change after `d4db0ab`, whose ARM leg passed. After `d4db0ab`, the code changed only in `index_answer` and `is_device`, which a hello program never reaches.
  - The likeliest cause is a teardown race between Python's exit and wasmtime-wasi's tokio runtime, which this change neither adds nor touches.
- **How I weigh it.** It is not a finding against this pull request, and on its own it does not block landing. It does need the one rerun this commit triggers. Green there: merge. The same abort again: do not merge, and record it as its own issue against the runner's exit path.

### The tests run in round 2

| command | result |
|---|---|
| `flock …machine.lock scripts/build.sh` | every step "up to date" (EXECUTED) |
| `uv run pytest tests/build tests/runner/test_library_case.py tests/escapes -p no:cacheprovider -ra` | 587 passed (EXECUTED) |
| scratch worktree, `test_lib_index.py`'s probe checks, with `fold` made the identity / `is_device` returning 0 / both M1 deferrals removed | 7 / 5 / 4 failed, as above; restored, 26 passed (EXECUTED); scratch removed |
| `fuzz.py`, `fuzz2.py` over the multi-path probe | 0 mismatches in 47,822 answers (EXECUTED) |
| `count_calls.sh build/artifacts`, `-MPOSIX -e 1` and the five modules | 22 ok / 0 failed / 11 opens; 36 / 0 / 18 (EXECUTED) |
| `measure.fuel` on the head's set, under the lock | 123,227,113 and 72,010,878 (EXECUTED) |
| CI run 45486240838 | 6 of 7 green; the ARM leg aborted at exit, as above (EXECUTED: check runs and job logs) |
