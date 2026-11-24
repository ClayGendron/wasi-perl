# Review: 007 — Perl's own `t/` suite under the runtime

Reviewer: an agent (Claude), a fresh sub-agent of https://claude.ai/code/session_01ThzDEUwftdmy8zWBJK1gWa; not the author

- **Branch:** `spec/007-perl-t` at `69d790a`, against `origin/dev` at `eee84ac`.
- **Read:** AGENTS.md, `context/constitution.md`, the specification with its landing notes, `tests/perl_t/run_suite.py`, `exclusions.toml`, `test_suite.py`, `git diff origin/dev...HEAD` and each commit on the branch.
- **Build:** the author's `build/artifacts` and `build/target`, copied (not rebuilt). `perl.wasm` SHA-256 `23dd4c68…a895f`, the same as the landing notes. The pristine tree was unpacked fresh by `run_suite.py` from the pinned tarball after its hash check.

## What was run (EXECUTED)

| Command | Result |
|---|---|
| `uv run python tests/perl_t/run_suite.py --jobs 3 --quiet --list-failures` | `617 run, 433 passed, 184 excluded (subprocess 141, signals 11, porting 6, platform 14, bug 12), 0 failed, 0 stale exclusions`, 86 s |
| the same again, with no timing history, so files ran in a different order and slot | same scoreboard. Of 617 files, only `benchmark/gh7094-…` changed its `not ok` list. That file is excluded and has a cause (H1). |
| `uv run pytest -q -m "not slow" tests/perl_t`; `ruff check`; `ruff format --check` | 28 passed; clean; clean |
| every one of the 184 excluded files under native perl 5.42.3 (`build/native/perl`, in a copy of its tree, with `t/perl` linked as `make test` does it, using the harness's own argv) | the native results were compared line by line with the guest's results up to the guest's last result |
| `stderr` of all 141 `subprocess` logs, read by hand, with the source line each one dies at | see the audit |
| one more run with a copy of `perl.wasm` placed at `t/perl` in each slot (a scratch copy of the harness; the repository was not changed) | same scoreboard. `op/pack.t` went from 7 to 4177 results before dying at a `runperl` backtick. |
| guest probes: `times` around a 0.95 s loop; `eval { kill 0 }`, `pipe`, `waitpid(0,1)` | `times` user delta 9,501,317 s; `CLOCKS_PER_SEC` 1e9, `_SC_CLK_TCK` 100. `kill`, `pipe` and `waitpid` each die "… is not available in the wasi-perl sandbox". |

## The exclusion audit

I checked all 184 entries by comparing them with native perl, and read about 35 of them line by line: every reason, all 6 porting, all 14 platform, all 12 bug, and every subprocess entry whose death was not a plain test.pl:881 refusal.

**Subprocess (141).** In 138 files, the results the guest prints before it stops match native perl. The only differences are descriptions that vary from run to run (hash order, random primes, temp names, fd numbers) and natively-skipped locale tests. In those files, every `not ok` before the death is a `# TODO`, or sits inside a `qx`/`runperl` (comp/colon.t 8 and 19, op/recurse.t 27). Each death is at a refusal, or at `which_perl` where the file makes warnings fatal and the next statement is `fresh_perl`. Or it is a child run whose message is not seen: op/die_exit.t, where STDERR goes to a file; re/substT.t and re/subst_wamp.t, which `do` re/subst.t and then exit 0; and op/override.t. No interpreter bug hides in front of a first runperl in these files. Three subprocess entries are not right:

- **op/waitpid.t** starts no child. It calls `waitpid(0, WNOHANG)` only to show that the call does not loop forever. It dies because `waitpid` is refused. Mislabelled.
- **io/pvbm.t** tests 4 and 5 call `pipe()`, which starts no command. WASI preview1 has no pipe. This is a platform difference, not a child process. Mislabelled.
- **op/taint.t**: the reason holds (backticks at lines 174, 185 and 1210). The note leaves out the larger cause. The file's `tainted()` is `not eval { …, kill 0; 1 }`, and `kill` is refused, so every value looks tainted. About 140 `isnt_tainted` checks fail for that reason, not for backticks. The note is wrong, but the reason is defensible.

**Signals (11).** All true. The four mro/recursion_* files fail all 8 tests because `alarm` and `$SIG{ALRM}` are refused inside the `eval` that guards each test. op/local.t dies at line 543 of 866 when it assigns `$SIG{TERM}`.

**Porting (6).** All true. perlfunc.t and podcheck.t plan `1..0 # Not all files are available during cross-compilation`. t/TEST would call that "skipped". This harness counts it as a failure, which is stricter and so acceptable.

**Platform (14).** Eight entries are true WASI or 32-bit differences: chmod (op/filetest.t, op/stat.t), the time-zone database, descriptor reuse, `bind`, and the 32-bit regex size limit. The other six do not fit:

- **benchmark/gh7094-…** hides a real defect. The note says the timing ratios "vary run to run". The cause is `times`, which reports CPU seconds about 10^7 too large: wasi-libc's `clock_t` counts nanoseconds (`CLOCKS_PER_SEC` = 1e9), and Perl divides by `HZ` = 100. Benchmark.pm measures with `times`, so it reports "too few iterations" and its ratios are noise. Every agent who uses `times` or Benchmark gets nonsense. This entry is a `bug`. Mislabelled.
- **op/pack.t** is filed as "no perl executable file". With a file at `t/perl` it runs 4177 of 14724 tests, all ok, and then dies at a `runperl` backtick. Its real reason is `subprocess`. The "no ./perl" condition comes from the harness, not from WASI. Mislabelled.
- **win32/\* (6)** are Windows-only files that t/TEST never runs on Unix. They fail natively on Linux too. "platform" is defined as "a WASI difference", which these are not. The reason does not fit, but nothing is hidden.

**Bug (12).** All true, and all listed in the landing notes.

**Count: 4 mislabelled**: benchmark/gh7094-speed-up-keys-on-empty-hash.t (a `bug` filed as `platform`), op/pack.t (`subprocess` filed as `platform`), op/waitpid.t and io/pvbm.t (no child process). On top of those, 6 win32 entries sit under a reason whose definition does not fit them, and 1 note (op/taint.t) is wrong.

**Coverage, for the record (EXECUTED, the native run against the guest run).** Natively, the 184 excluded files report about 55,500 results. The guest reaches about 14,500 of them before each file stops. Most of the rest (about 22,000) are in subprocess files: core files such as uni/upper.t (6338 natively, 1 in the guest), op/lc.t, re/pat*.t, op/sprintf2.t and op/cmpchain.t, which stop at their first `runperl`. For comparison, the 433 passing files report 692,918 results.

## The harness

- **TAP.** The parser handles all of the following correctly, and I checked each one EXECUTED: a plan first or last; numbers left out; a duplicate number or a missing one (reported as out of sequence); `not ok … # TODO` (not a failure); `not ok … # skip` (a failure, which is strict); `Bail out!`; two plans; skip_all with and without a reason; skip_all followed by results. A plan of N with M < N results is "ran M of N" and a failure, whatever the exit code; re/substT.t is a real case of a file that exits 0 like that. A trap after the plan fails as `trapped`, a non-zero exit fails, the deadline fails as `trapped: deadline`, and output past the cap fails as `output over …`. None of these paths can produce a pass. Lines the parser does not recognise are ignored, so `Not ok 1` passes silently, where t/TEST fails a core file on unexpected output (L3).
- **The tree.** It is the pristine tarball tree, laid over only with files the tarball lacks, then made writable. There is one copy per worker, under `build/perl_t/work/`, removed at the end of the run. The guest sees only that copy (at `/perl`), a per-worker `/tmp`, and the runner's read-only library and `/dev`. Its environment is fixed, so nothing from the host's environment reaches it. Two weaknesses are L1 and L2.
- **Determinism.** As shown above, a second run in a different order gave identical results for every file except one, the benchmark. Slots are reused from file to file within a run, so one file's leftovers can reach a later file in the same slot. No effect was seen.
- **Spec numbers in code.** The `.py` files have none. `exclusions.toml` names "spec 009" in eight notes (L4).

## Findings

| # | Rank | Where | Finding | Suggested fix |
|---|---|---|---|---|
| H1 | High | `tests/perl_t/exclusions.toml:190` | benchmark/gh7094 is excluded as `platform`, and its note says the timing ratios merely vary. The real cause is a defect of this build: `times` returns CPU seconds about 10^7 too large (`CLOCKS_PER_SEC` 1e9 against `HZ` 100), which also breaks Benchmark.pm. A bug filed as a platform difference is left out of the landing notes' bug list, so Acceptance 2 does not hold. | Re-file it as `bug` with the cause in its note, in its own commit. Add it to the landing notes' bug list (13 bugs). A one-liner test of `times` belongs with the builders. |
| M1 | Medium | `tests/perl_t/run_suite.py:399-418` | The harness checks only that an excluded file still fails, not that it fails for its stated reason or as far into the file as before. op/sort.t, for example, could start crashing at test 10 instead of dying at its runperl at test 165, and the scoreboard would not change. Excluded files report about 14,500 results that nothing guards. | Record each excluded file's expected failure: a stderr pattern (for subprocess and signals, "not available in the wasi-perl sandbox"; for a bug, its message) and the number of results it reached. Report a mismatch the same way a stale exclusion is reported. |
| M2 | Medium | `tests/perl_t/exclusions.toml:42,123,194` | Three more entries are mislabelled. op/waitpid.t starts no child (it is refused at `waitpid`). io/pvbm.t calls `pipe()`, which starts no command. op/pack.t is really `subprocess`: given a `t/perl` file, it runs 4177 tests, all ok, then dies at `runperl`. | Re-file them, with notes saying what was seen: op/waitpid.t and io/pvbm.t as `platform` (waitpid and pipe are refused, and WASI has no pipe), op/pack.t as `subprocess`. |
| M3 | Medium | `tests/perl_t/run_suite.py:256-279`; spec Decision 3 | The guest's `$^X` is `perl` and `t/` has no `perl` file, so `which_perl` fails. Files that read `$^X`, such as op/pack.t, stop early, and nine files die at a fatal warning rather than at a clean refusal. Separately, because a refusal dies, about 22,000 native results in subprocess files never run. | Place a non-executable stand-in at `t/perl` in each slot. That is EXECUTED harmless: the scoreboard is unchanged and op/pack.t then runs 4177 tests. Ask the owner whether a later specification should let subprocess files count the tests before their first refusal. |
| L1 | Low | `tests/perl_t/run_suite.py:223-224` | An already-unpacked `build/perl-5.42.3/` is reused with no check, so an edited file there would reach the tests as if it were "pristine". | Write a stamp holding the tarball hash when unpacking, and unpack again when the stamp is missing or different. |
| L2 | Low | `tests/perl_t/run_suite.py:215` | `find_tarball` assumes that the main checkout sits at `../../wasi-perl`. | Use `git rev-parse --git-common-dir`, or require `WASI_PERL_TARBALL`. |
| L3 | Low | `tests/perl_t/run_suite.py:119,126-154` | The parser ignores lines it does not recognise, so `Not ok 1` passes. `Bail out!` is matched case-sensitively, while t/TEST matches it case-insensitively. | Match `Bail out!` case-insensitively. Outside `lib/`, `ext/`, `dist/` and `cpan/`, treat an unrecognised line as t/TEST does, as a failure. |
| L4 | Low | `tests/perl_t/exclusions.toml:208-217` | Eight notes name "spec 009", and AGENTS.md says a file under `tests/` names no specification number. | Say "no dup for `>&` handles in this build" instead. |
| L5 | Low | `tests/perl_t/exclusions.toml:115` | op/taint.t's note leaves out that `kill` is refused, and that refusal is what makes about 140 `isnt_tainted` checks fail. | Name both causes in the note. |
| L6 | Low | `tests/perl_t/exclusions.toml:15,198-203` | The six win32/ entries are filed under `platform`, which is defined as "a WASI difference". t/TEST does not run win32/ on Unix at all. | Either leave win32/ out of the run, as t/TEST does, or widen the reason's definition to cover files for other operating systems. |
| L7 | Low | commit `4886b90` | Nine new exclusions arrived inside a commit titled "Reclassify", and the message does not name the files. AGENTS.md asks for each addition, with its reason, in its own commit. | In future, add new exclusions in a commit of their own that names the files. Here, list the nine in the landing notes. |
| L8 | Low | `tests/perl_t/run_suite.py:134` | `directive[4:]` turns `Skipped: x` into `ped: x`. This is cosmetic; the verdict is not affected. | Strip with `re.sub(r"(?i)^skip\w*[:\s]*", "", directive)`. |
| L9 | Low | `tests/perl_t/test_suite.py:187-189` | The slow test skips, and does not fail, when there is no build. CI calls `run_suite.py` directly, so this is acceptable. | Optional: fail instead of skipping when `CI` is set. |

## Verdict

Does not land. The harness is sound: its parser, its limits, its isolation and its determinism hold, and the scoreboard reproduces exactly. But one exclusion hides an interpreter defect (H1, `times` off by 10^7) under `platform` with a wrong note, so the landing notes' bug list is incomplete and Acceptance 2 does not hold. Re-file H1 and the three entries in M2 in one commit, with notes that say what was seen. Update the landing notes' counts. Then it lands. M1 and M3 can be follow-ups the owner agrees to.

## Round 2

- **Branch:** `spec/007-perl-t` at `2dfe2a2`. This round covers the fix commit `6e79be0`, which changes the harness, the build and the spec, and the exclusions commit `2dfe2a2`, which changes the exclusions alone.
- **Build:** the author's `build/artifacts` and `build/target`, copied. Their worktree is clean at `2dfe2a2`, and the copied files were built after its last recipe edit. `perl.wasm` SHA-256 `36094df4…7cef1a`. The pristine tree was unpacked fresh, with a hash check.

### What was run (EXECUTED)

| Command | Result |
|---|---|
| `uv run python tests/perl_t/run_suite.py --jobs 3 --quiet --list-failures` | `616 run, 433 passed, 183 excluded (subprocess 140, signals 11, refused 2, porting 6, platform 6, not-this-platform 6, bug 12), 0 failed, 0 stale exclusions, 0 regressed (18424 guarded oks)`, 83 s. This matches the landing notes. |
| guest: `times` around a 2e7-iteration loop; `POSIX::times` | wall 0.968 s, `times` user 0.970 s (it was 9,501,317 s in round 1). `POSIX::times` is in ticks too. |
| `uv run pytest -q tests/build -k times`; `pytest -m "not slow" tests/perl_t`; `ruff check`; `ruff format --check` | 1 passed; 36 passed and 1 skipped; clean; clean |
| the guards, in a scratch copy of the harness and exclusion list, with `--only` on four files. The copy was removed afterwards and the edited tree was unpacked again. | All four went red and the run exited 1. (a) op/sort.t's `ok_at_least` raised from 164 to 165: "REGRESSED op/sort.t: 164 ok, fewer than the 165 recorded". (b) re/substT.t's `shows` changed so it no longer matches: "no what its entry shows". (c) `die "boom"` put at the top of op/waitpid.t, a `refused` file with `ok_at_least = 0`: "no a refusal in its output". (d) op/each.t made to die just before its first `fresh_perl`, so it keeps its ok count but loses the refusal: "no a refusal in its output". |
| t/TEST in the pinned tarball, lines 501 and 600 | `benchmark/` runs only with `--benchmark` or `PERL_BENCHMARK`, as the harness now does |

### The findings, one by one

| # | Status | Seen |
|---|---|---|
| H1 | Fixed | `times` is wrapped at link time (`recipes/config.sh` ldflags, `recipes/wasi-stubs.c` `__wrap_times`) and converted to `_SC_CLK_TCK` ticks. A ratio check against `Time::HiRes::clock` covers it. benchmark/ now follows t/TEST: it runs only with `PERL_BENCHMARK`. The project counts operations, not time, so this is not hiding a failure, and with `times` fixed the file has no known defect behind it. |
| M1 | Fixed | `ok_at_least` on every entry, and a refusal pattern (or `shows`) on every subprocess, signals and refused entry. Both go red, as shown above. 18,424 oks are guarded. |
| M2 | Fixed | op/pack.t is `subprocess` (ok_at_least 4177); op/waitpid.t and io/pvbm.t are `refused`. |
| M3 | Fixed, partly answered | There is an empty `t/perl` stand-in. Partial counting is left to the owner, which is acceptable. |
| L1 | Partly fixed | A stamp now ties the unpacked tree to the pinned tarball hash. Edits inside the unpacked tree still go unnoticed: the guard test above ran my edited op/waitpid.t from `build/perl-5.42.3/` without complaint. This is residual and Low. |
| L2–L9 | Fixed | The tarball is found through the git common dir and `WASI_PERL_DOWNLOAD_CACHE`. Strict TAP outside lib/; `Bail out!` matched with any case; no spec numbers left in `exclusions.toml`; op/taint.t's note names `kill`; `not-this-platform`; the nine reclassify-commit additions named in the landing notes; skip reasons stripped properly; the slow test fails without a build under `CI`. |

### New, all Low

| # | Rank | Where | Finding | Suggested fix |
|---|---|---|---|---|
| R2-1 | Low | `tests/perl_t/exclusions.toml:193-228` | Seventeen porting, platform, not-this-platform and bug entries have `ok_at_least = 0` and no failure signature. Such a file could start trapping, or fail for a new reason, and still count as excluded. For example, op/hash-clear-placeholders.t is meant to fail at a dup; nothing would notice if it began to trap instead. | Allow `shows` on any reason, and give these entries the message their note quotes. |
| R2-2 | Low | `tests/perl_t/run_suite.py:286-288` | The stamp checks the tarball, not the tree it unpacked (L1, residual). | Store a hash of the file listing and sizes beside the stamp, or always unpack into a fresh directory. |
| R2-3 | Low | `tests/perl_t/run_suite.py` (the `regressed` message in `scoreboard`) | The message reads "no a refusal" and "no what its entry shows". | "no refusal in its output" / "its output lacks the entry's `shows` pattern". |

### Verdict, round 2

Lands. The scoreboard reproduces, `times` is fixed at its root and checked, and the four mislabelled entries are re-filed. The new guards catch a file that falls short of its ok count, and one that stops showing its refusal. Each finding from round 1 is fixed or answered in writing. What remains is Low.
