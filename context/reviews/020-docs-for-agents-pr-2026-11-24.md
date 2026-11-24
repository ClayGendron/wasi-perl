# Review: 020, docs for agents: the fast way to write common programs (PR #28)

- **Reviewer:** a fresh agent (Claude Opus 5.5) that did not write the change. 2026-11-24.
- **Reviewed:** round 1, `git diff origin/dev...HEAD` on `spec/020-docs-for-agents` at head `88a73ec` (`88a73ec2b3…`), base `origin/dev` at `8897c22`. Round 2, the fixes `88a73ec..08572de`; its section is at the end and holds the current verdict. Read against `AGENTS.md`, `context/constitution.md`, the specification `context/specifications/020-docs-for-agents.md`, the memo `context/research/2026-11-24-fast-perl-measured.md` with its `measure.py` and `results/`, and the PR body.
- **Scratch:** a detached worktree `/home/user/wasi-perl-wt/review-020-scratch` at the head, with spec 019's branch (`48ef643`) merged in and 019's built `build/artifacts` linked. Every break below was made there and reverted; the worktree was removed after. No file of the branch was changed apart from this review.

Labels: EXECUTED means I ran it and saw the result. READ means I read it in a file. INFERRED means my reasoning.

## Verdict, round 1

**Does not land.** The work is sound and nearly all of it holds: every number on the page is a real fuel count, the checks are two-sided and fail when the fast form regresses, the module group's refusals work end to end, and CI is green on all seven checks. One Medium finding blocks: the check that ties the page's figures to the fuel checks matches substrings. So the page can quote a number no check runs (M1). Fix M1. Fix or answer the Low findings in writing.

## What I ran

1. **READ.** `AGENTS.md`, the constitution, spec 020, the full diff, the memo, `measure.py`, `results/dev.md`, `results/f-base.md`, the PR body (`pull_request_read get`), and the 018 review as a model.
2. **EXECUTED.** `uv run pytest -m "not slow"` on the head: 526 passed, 2 skipped. `ruff check src tests`: clean. `ruff format --check src tests`: 33 files already formatted.
3. **EXECUTED.** `flock machine.lock uv run pytest tests/oneliners -ra` against the native oracle in `build/native-install` and `build/native-cpan`: 503 passed (`build/logs/review-020-oneliners.log`).
4. **EXECUTED.** `measure.py` twice under the lock. Both runs are byte-identical to each other and to `results/dev.md`, apart from a uv warning line (`build/logs/review-020-measure-{1,2}.md`).
5. **EXECUTED.** The same `measure.py` on 020 merged with spec 019, against 019's `perl.wasm`. Every ratio moved by less than 0.5% (table under "Spec 019" below). `test_fast_perl.py` there: 12 passed.
6. **EXECUTED.** `test_fast_perl.py -k "whole or keyed or posix"` against study F's pre-016 set (`perf-base/build/artifacts`): the two `-0777` checks fail ("5.97×; the page says 9×", "5.64×; the page says 7.5×"); the sort and POSIX checks pass.
7. **EXECUTED.** Two breaks of the ratio checks in scratch:
   - `fast-json-maybexs` changed to `-MJSON::PP`: fails, "1.00×; the page says 29×" (lower bound).
   - The sort figure in `RATIOS` changed from 5 to 4: fails, "5.51×; the page says 4×" (upper bound).
8. **EXECUTED.** The JSON::MaybeXS and CSV pairs under three hash seeds (0; 12345 and 987654321 with `PERL_PERTURB_KEYS=2`): 26.195×, 26.163×, 26.163×; 13.169×, 13.162×, 13.162×. The spread is 0.12% or less.
9. **EXECUTED.** The refusals reverted in scratch (`check_module_versions` never raises; `load_recorded` skips the `cpan_versions` test): `test_oracle_logic.py` gives 5 failed, 33 passed. The failures are the three spec-named checks (the stale one in all three of its parameters).
10. **EXECUTED.** The real flow with a broken `build/native-cpan` in scratch:
    - `Text/CSV.pm` deleted: `record_oracle.py` exits 2, "refused: … Text::CSV missing (pinned 2.06); run scripts/10_native.sh". `test_oneliners.py -k module_case`: 10 errors, 0 skipped, each "Failed: the oracle's … does not hold the pinned CPAN modules".
    - `Algorithm::Diff`'s `$VERSION` set to 1.1903: the same, "Algorithm::Diff 1.1903 (pinned 1.201)".
11. **EXECUTED.** CI's path in scratch, with `build/native-install` removed so only a recording serves:
    - A fresh recording (format 2, the five versions): 226 guest cases passed.
    - The same recording with `format` set to 1: 226 errors, each "format 1, expected 2".
    - The same with `Text::CSV` recorded as 2.04: 226 errors, each "recorded with the CPAN modules {…'Text::CSV': '2.04'…}, expected {…}".
12. **EXECUTED.** The page's Python example, run as written with a real folder: `stdout=b'./src/a.c\n'`. `wasi-perl run --grant /work=… --cwd /work` gives the same; without `--cwd` it exits 0 with empty stdout.
13. **EXECUTED.** Native checks of the page's prose: `parse_line(",", 0, …)` turns `"said ""hi"""` into `said hi` and loses the row whose field holds a newline; Text::CSV gives `said "hi"` and `two\nlines`.
14. **EXECUTED.** `pull_request_read get_check_runs` on `88a73ec`: all 7 checks "success". `get_job_logs` of the build job: "10_native: ok", then "recorded 226 cases … (corpus 1f3eb7e64ba0)". `get_job_logs` of the Windows leg: "282 guest checks (at least 267: 226 cases, 19 escape tests, 3 page checks, 19 kept-scan checks), 0 skipped".
15. **EXECUTED.** A search of the diff's added lines under `src/`, `tests/`, `scripts/`, `recipes/` and `.github/` for a spec or review number: none.

## The two deviations

**1. The `-0777` ratios (9× and 7.5×, not 4.8–5.6×). Supported.**
- The page's figures are today's measurements, rounded: 9.07× and 7.55× (EXECUTED, step 4).
- The old figures are reproduced on study F's own base set: 4.84× in F's form and 5.64× (READ: `results/f-base.md`). The checks fail on that set (EXECUTED, step 6). So the move is real, and the checks would catch its loss.
- Quoting the old range would have failed the spec's own Decision 4, the 20% check. A spec cannot be built to quote a number its check refuses. The owner agreed the items and the margin; the figure is a measurement. The dated line under Decision 1 records it.
- One caveat, as Low L3: the memo says "The cause is specification 016" as a fact. The base set is older than 013, 016 and 018, which all change `perl.wasm`. Only 016 changes reads, so 016 is the likely cause, but that is INFERRED by elimination.

**2. The CSV figure (12×). The figure is right to add, but should be 13×.**
- Decision 1 asks for "the fact and the number" for each item, and Decision 4 makes every ratio on the page a check. A number with a check meets both.
- But 12× is not this page's measurement. The page's pair measures 13.18× (EXECUTED). 12.05× is spec 018's ratio for a different program (READ: `CHANGELOG.md`, 18,215 M / 1,511 M). See L1.

## Findings

### Medium

**M1. The page's figures are matched as substrings, so the page can quote a number no check runs.**

- **Evidence.** `test_the_page_quotes_each_checked_figure` asserts `f"{figure}×" in text`. "9×" is found inside "29×", and "5×" inside "7.5×". The ratio checks compare against `RATIOS`, not against the page. In scratch I changed the count sentence to "runs 12× fewer" and the sort sentence to "runs 50× fewer". `test_fast_perl.py -m "not slow"` gave 4 passed (EXECUTED). The slow checks still check 9 and 5, so they pass too. The page would then claim 12× and 50×, and no check would see it.
- This is the one link between the page's text and Decision 4 ("Each instruction ratio the page claims is a check"), so it must be exact.
- **Fix.** Tie each figure to its section and to the whole page. For example:
  - Give each `RATIOS` entry its section heading. Require the figure, matched as `(?<![\d.])9×`, inside that `## ` section.
  - Require the set of all `\d+(?:\.\d+)?×` on the page to equal the set of `RATIOS` figures, so a number added to the page without a check fails.
  - See the new check fail on the two edits above before it passes.

### Low

**L1. The CSV figure is 12×; the page's own pair measures 13.18×.**
- **Evidence.** `results/dev.md`: 15,313,473,477 / 1,161,441,020 = 13.18×. With 019, 13.17× (EXECUTED). The check's upper bound is 14.4×, 9% above. The -0777 figures were centred on the measurement; this one was not, and the memo does not say where 12 comes from.
- **Fix.** Quote 13× on the page, in `RATIOS` and in `CHANGELOG.md`. Or keep 12× and say in the memo that it is 018's figure for another program, and why the page uses it.
- JSON::MaybeXS needs no change. Its 26.3× is 9% under the page's 29×, inside the margin both ways (bounds 23.2× and 34.8×). The 29× is the figure the spec fixes. Cpanel::JSON::XS measures 28.67×.

**L2. Two small wordings say more, or less, than the fact.**
- `docs/fast-perl.md`: "costs 16 M instructions on every call". This can be read as every call of a POSIX function. The check measures one run. Say "on every run".
- `README.md`: "says which forms of common programs cost several times fewer instructions: …, and a `cwd` for `File::Find`". The `cwd` is about a right answer, not about instructions. Say, for example, "… `use POSIX ()`; and that `File::Find` needs a `cwd`."

**L3. The memo states the cause of the `-0777` move as a fact.**
- **Evidence.** §2: "The cause is specification 016". The base set `32dfb73` predates 013, 016 and 018. All three change `perl.wasm` (READ: `git diff --stat 32dfb73 origin/dev -- recipes scripts/20_target.sh` touches `config.sh`, `wasi-fd-table.c`, `wasi-memchr.c`, `sandbox-refusals.c`, `cpan-xs`, `wasi-cc`). Only 016 changes reads.
- **Fix.** Label the sentence INFERRED, with that reason, or measure the pair on 016's merge `d8fd00c` and its first parent. The same applies to "after spec 016" in the spec's dated line.

**L4. The note on spec 017 lives only in the PR body.**
- **Evidence.** The PR body says that once 017 lands, the File::Find section needs "a grant or a mount", and its "without a `cwd`" sentence holds only when nothing is mounted at `/`. Spec 017 allows a mount at `/` and a `cwd` in a mount (READ: 017 §6–7). No file in the repo records this. `test_guest_file_find_needs_a_working_folder` uses grants only, so it will not fail when 017 lands. The page stays true after 017, but it becomes incomplete.
- `AGENTS.md`: "All project knowledge lives in this repo."
- **Fix.** Add one line to the memo's §5 Limits, or to 017's landing notes if it lands second: whichever of 017 and 020 lands second updates the File::Find section of `docs/fast-perl.md` for mounts.

**L5. `test_native_is_deterministic` passes when native perl is broken. Out of scope; record it.**
- **Evidence.** The gap is older than this PR (spec 006). Two runs that fail the same way are equal. `find_oracle` checks only `print $^V`, which needs no library (READ).
- This PR narrows the gap. The module group's version probe runs `require` with the core library. In CI the recorder runs that probe for every recording. A native perl that cannot find its library therefore stops the build job (INFERRED from the code; step 10 shows the probe refusing).
- **Fix.** Not in this PR. Add a roadmap follow-up: `find_oracle` should also run a program that loads core modules, such as `-Mstrict -MPOSIX -e 1`, and refuse a perl where it fails.

## What holds

- **The page.** Every example is a case of `cases.toml` word for word, the Python one included (READ and EXECUTED: `test_every_example_on_the_page_is_a_case`). Every case passes against native perl (EXECUTED, step 3). The File::Find text is honest. It says a `cwd`-less `find` over `.` exits 0 and finds nothing, and the check covers all three outcomes. I saw the same through `run()` and the CLI (EXECUTED, step 12). The ParseWords and Text::CSV prose is true (EXECUTED, step 13).
- **The fuel checks.**
  - They are two-sided at 20% (`claim * 0.8 <= ratio <= claim * 1.2`). Both bounds fail when broken (EXECUTED, step 7).
  - They count `fuel_used` with `deadline_s=None`. No check reads a clock.
  - They are deterministic. `BASE_ENV` fixes `PERL_HASH_SEED=0`, so two runs are byte-identical (EXECUTED, step 4). Other seeds move a ratio by 0.12% or less (EXECUTED, step 8). The smallest headroom is 8.9%: the keyed sort at 5.51× against a bound of 6.0×.
  - A real regression fails them (EXECUTED, step 6).
- **Spec 019.** All seven figures stay inside their bounds on 019's build (EXECUTED, step 5):

  | pair | dev | with 019 | bounds |
  |---|---:|---:|---|
  | count | 9.07× | 9.07× | 7.2–10.8 |
  | substitute | 7.55× | 7.55× | 6.0–9.0 |
  | keyed sort | 5.51× | 5.51× | 4.0–6.0 |
  | JSON::MaybeXS | 26.30× | 26.19× | 23.2–34.8 |
  | Cpanel::JSON::XS | 28.67× | 28.61× | 23.2–34.8 |
  | CSV | 13.18× | 13.17× | 9.6–14.4 |
  | POSIX import | 16.3 M | 16.4 M | 12.8–19.2 M |

- **The module group** meets Decision 3:
  - `PERL5LIB` is set only by `group_env` for `group = "cpan"`. Native runs build their environment from scratch, so no other case sees it (READ; `test_only_the_module_group_gets_the_cpan_library`).
  - A missing module or a wrong `$VERSION` refuses the oracle and fails the group as errors, not skips (EXECUTED, step 10).
  - A recording with other versions, without versions, or in format 1 fails as stale, with a message that names the cause (EXECUTED, step 11).
  - The three spec-named checks fail without the refusals (EXECUTED, step 9).
- **CI.** The build job records with `build/native-cpan`, which `scripts/build.sh` makes through `10_native.sh` (READ, and EXECUTED in the job log). Every wheel leg runs the module cases and the page's guest checks against that recording, with no `-m` filter. The skip gate counts their names (`test_guest_*` in both files) and fails on any skip or error. Windows reports 0 skipped (EXECUTED, step 14).
- **No test was weakened.** `cases.toml` only gains 17 cases. No golden output, known difference or exclusion changed. The old determinism and guest checks are split into a plain and a module half that together cover every case. The host-path leak check gains `build/native-cpan`. `test_oracle_logic.py` only adds checks; its `_recording` helper gains the versions a valid recording now needs (READ).
- **Code comments** name no spec or review number (EXECUTED, step 15).

## Round 2

- **Reviewed:** `git diff 88a73ec 08572de`, one commit, "Review of 020, round 1: figures checked by section, CSV at 13x". It touches 9 files: `test_fast_perl.py`, the page, the README, `CHANGELOG.md`, the memo with its `measure.py` and `results/dev.md`, the spec's dated line and `context/roadmap.md`. No case, oracle code or CI file changed (READ: `git diff --stat`).
- **Scratch:** a fresh detached worktree `/home/user/wasi-perl-wt/review-020-scratch` at `08572de`, removed after.

### What I ran

1. **EXECUTED.** In scratch, the round-1 mutations of the page. Each run is `pytest tests/oneliners/test_fast_perl.py -m "not slow"`:
   - unchanged: 5 passed;
   - the count sentence at "12×": 2 failed, `test_the_page_quotes_each_checked_figure` and `test_every_ratio_on_the_page_is_a_checked_one`;
   - the sort sentence at "50×": the same 2 failed;
   - both edits together: the same 2 failed;
   - the POSIX "16 M" changed to "61 M": `test_the_page_quotes_each_checked_figure` failed.
2. **EXECUTED.** In scratch, one further mutation: the two figures of the `-0777` section swapped ("a count … 7.5× fewer …, and a substitution 9× fewer"). 5 passed. See N1.
3. **EXECUTED.** On the worktree at `08572de`:
   - `uv run pytest -m "not slow"`: 527 passed, 2 skipped;
   - `ruff check src tests`: clean, and `ruff format --check src tests`: 33 files already formatted;
   - `flock machine.lock uv run pytest tests/oneliners`: 504 passed, the 6 ratio checks among them with CSV now at 13.
4. **EXECUTED.** `measure.py` under the lock. Its output equals the new `results/dev.md` byte for byte (`build/logs/review-020-measure-r2.md`). The fuel counts are the same as in round 1. Only the CSV "page" column moved, from 12× to 13×.
5. **EXECUTED.** CI on `08572de`: run 45482037914, whose `head_sha` is `08572de8e4…`. All 7 checks are "success".
6. **EXECUTED.** The added lines under `tests/` name no spec or review number.

### The round-1 findings

- **M1, fixed.**
  - Each `RATIOS` entry now names its section. `quotes()` requires the figure with no digit or dot before it, inside that one `##` section. The POSIX "587 names" and "16 M instructions" are held to "Imports", and "Can't cd to /" to the File::Find section.
  - `test_every_ratio_on_the_page_is_a_checked_one` requires the set of every `N×` on the page to equal the set of checked figures.
  - Both of my round-1 edits now fail both checks, alone and together (EXECUTED, step 1).
- **L1, fixed.** CSV is 13× on the page, in `RATIOS` and in `CHANGELOG.md`. The check is centred on the measured 13.18×: its bounds are 10.4× and 15.6×, 21% below the measurement and 18% above it. The memo says 12× was spec 018's figure for another program (READ).
- **L2, fixed.** The page says "16 M instructions on every run". The README lists four faster forms, then says in its own sentence that `File::Find` needs a `cwd` to give the right answer. The CHANGELOG line says the same (READ).
- **L3, fixed.** The memo's §2 labels 016 as the cause INFERRED. It names 013 and 018 as the other changes since the base set, and says no build between the two was measured. The spec's dated line says "the likeliest cause, INFERRED" (READ).
- **L4, fixed.** The memo's §5 records the 017 note: whichever of 017 and 020 lands second updates the File::Find section. It says the check uses grants only and will not fail by itself (READ).
- **L5, recorded.** `context/roadmap.md` gains "Open items from the review of 020", with the determinism gap and two ways to close it (READ).

### New finding

**N1 (Low, no fix needed to land). Two figures in one section can be swapped unseen.**
- **Evidence.** The section check asks only that each figure appear somewhere in its section, and the `-0777` section holds two. With the count and substitution figures swapped, every check passes (EXECUTED, step 2). The page would then say 7.5× for a count that measures 9.07×, which is outside 20%.
- This edit is narrow, and it is the only section with two figures. The fix I asked for in round 1 had the same gap. It does not block.
- **Fix, if wanted later.** Tie a figure to the sentence that names its form. For example, give each `RATIOS` entry a word that must come before the figure in the same sentence: "count" and "substitution".

## Verdict, round 2

**Lands.** Every round-1 finding is fixed or recorded as asked, and I saw each fix work. The M1 mutations now fail. The fuel counts are unchanged and byte-identical, and CI is green on `08572de`. N1 is a narrow leftover of the same check. It can be fixed later and does not block.
