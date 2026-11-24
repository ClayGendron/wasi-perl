# Review: 018, JSON, CSV and diff for agents (PR #26)

- **Reviewer:** a fresh agent (Claude Opus 5.5) that did not write the change. 2026-11-24.
- **Reviewed:** round 1, `git diff origin/dev...HEAD` on `spec/018-json-csv-and-diff` at head `1725686` (`17256860d6…`). Round 2, the fixes `1725686..bd43962`; its section is at the end and holds the current verdict. Read against `AGENTS.md`, `context/constitution.md`, the specification with its landing notes, study F (`context/research/2026-11-24-perf-libs.md`) and the memo `context/research/2026-11-24-json-csv-and-diff-measured.md`.
- **Probes:** in `/tmp/claude-0/-home-user-wasi-perl/ecbe2564-8e44-50cd-9bc9-1c1b283f37e5/scratchpad/probes-018/`. No code in the worktree was changed.

Labels: EXECUTED means I ran it and saw the result. READ means I read it in a file. INFERRED means my reasoning.

## Verdict, round 1

**Does not land.** The work is careful and almost all of it holds. But the pull request's CI run is red. The build fails at `20_target.sh`'s `make -j4 perl utils/Makefile`. The cause is a race this branch adds (H1): Text-CSV_XS's `Makefile.PL` needs the Unicode name tables, and Perl's Makefile does not order the static extensions after them. Fix H1 and see CI green. Fix M1 and M2 in the same round. The Low findings can be fixed or answered in writing.

## Findings

### High

**H1. CI is red: a build race on `lib/unicore/Name.pm`.**

- **Evidence.**
  - The PR's only CI run, 45459918586 on head `17256860`, ended "failure" (EXECUTED: `gh api repos/ClayGendron/wasi-perl/actions/runs?head_sha=…`). In the build job, `00_download` and `10_native` passed. Then 41 s into `20_target`: `FAILED: make -j4 perl utils/Makefile (see …/build/logs/20_target.log)`. The tests never ran. Dev at `f44e0fd` is green (EXECUTED: same API).
  - I could not read the uploaded `build-logs` artifact. Its blob host is refused by this session's proxy. So the cause on CI is INFERRED. The race itself was EXECUTED:
  - `cpan/Text-CSV_XS/Makefile.PL` line 7 is `use charnames ":full"`, and line 88 uses `\N{LATIN CAPITAL LETTER O WITH STROKE}` (READ). `charnames.pm` line 5 is `use unicore::Name`. That file is written by `mktables`, the `uni.data` target.
  - In `build/target/Makefile`, `$(static_ext)` depends on `$(MINIPERL_EXE) lib/buildcustomize.pl preplibrary makeppport $(DYNALOADER) FORCE` (line 587), not on `uni.data` (line 407) (READ). Under `make -j` nothing orders the two.
  - In a scratch clone of the branch, built clean, I moved `lib/unicore/Name.pm` and `Name.pl` aside. Then I ran `./miniperl -Ilib make_ext.pl lib/auto/Text/CSV_XS/CSV_XS.a …`. It failed: `Can't locate unicore/Name.pm in @INC … Compilation failed in require at Makefile.PL line 7` (EXECUTED; `race.out`).
  - Locally the race is won narrowly. In both the worktree's log and my clean build's log, `make_ext.pl lib/auto/Text/CSV_XS/CSV_XS.a` starts at line 6377. `mktables` writes `Updating 'mktables.lst'`, its last step, at line 6436 and 6442 (EXECUTED: `grep -n` of both `20_target.log`). A slower or differently scheduled machine loses, as CI did.
  - Three local builds of this head passed: the implementer's, mine (2 m 7 s) and one in `018-ci-repro`. That is consistent with a race, not evidence against one.
- **Fix.** In `scripts/20_target.sh`, build the Unicode tables before the step that builds the static extensions. For example, `run "$LOG" make -j"$JOBS" uni.data` just before `make -j"$JOBS" perl utils/Makefile`, with a comment in words: one CPAN `Makefile.PL` loads `charnames`, and Perl's Makefile does not make its extensions wait for `mktables`. The script already has a fix of the same kind for `generate_uudmap`. Then push and see CI green, including the step "No check of the CPAN modules was skipped" on a real run. A green re-run without the fix proves nothing, because the race can be won by chance.

### Medium

**M1. A `lib/` that is a symbolic link passes `30_library.sh` and ships host files.**

- **Evidence.** I made a tarball `Evil-1.0/` whose `lib` entry is a symbolic link to an outside folder holding `Leak.pm`. I pinned it, listed it in `recipes/cpan-pure`, and ran the real `30_library.sh` in a scratch checkout with a stand-in `installperl`. It printed `30_library: ok`. `lib/perl5/5.42/Leak.pm` was in the library with the host file's bytes, and `MANIFEST` listed it (EXECUTED; `lib30/`). A relative link with `../` does the same, because GNU tar 1.35 extracts both kinds (EXECUTED: `libsym.tar.gz` and `libsym-rel.tar.gz` in `unpack-work/`).
  - The cause: `[ -d "$dist/lib" ]` and `cd "$dist/lib"` follow the link. `find . ! -type f ! -type d` then checks the link's target, not the tarball's tree. The landing notes say "a `lib/` holding anything but files and folders is refused"; the folder itself is not checked.
  - The real defence is the pin. A hostile pinned tarball could also run code as root through `Makefile.PL` in `10_native.sh`. So this guard adds little today. But the guard exists to treat downloads as untrusted, and it can be walked around.
- **Fix.** Make `unpack_pinned` refuse any tarball with an entry that is neither a file nor a folder: after `tar`, `find "$_work/top" ! -type f ! -type d` must be empty. None of the five has a link of any kind (EXECUTED: `tar -tvzf` of each: only `-` and `d` entries). That also stops relying on GNU tar's own refusals. As well, or instead, refuse `[ -L "$dist/lib" ]` in `30_library.sh`. Add a check for it.

**M2. Three of the refusals the landing notes list have no check.**

- **Evidence** (READ: `tests/build/test_cpan_unpack.py`):
  - **A top entry that is a symbolic link.** `unpack_pinned` refuses it through `[ -L "$_work/top/$_top" ]`. The parametrized shapes are "two folders", "a folder and a hidden file" and "one file". If the `-L` test were deleted, `[ ! -d … ]` follows a link to a folder, and no check would fail.
  - **`30_library.sh`'s "would replace a file already in the library".** It works: a pinned tarball with `lib/strict.pm` stopped the step with exit 1, the library's `strict.pm` unchanged, and no `MANIFEST`, `SHA256SUMS` or stamp written (EXECUTED; `lib30/`). The refusal runs inside a `while` loop at the end of a pipeline, which is a subshell, so it relies on `set -e` seeing the pipeline fail. That is easy to break with no check watching.
  - **`30_library.sh`'s "neither a file nor a folder".** No check.
- **Fix.** Add a parametrized case for a top-level link. Add two cases that run `30_library.sh` in a scratch checkout, as `_checkout_with_a_tampered` already does, with a stand-in `miniperl` that lays out a tiny privlib. Use one tarball that clashes with a library file and one with a link in `lib/`. Each must fail with its one line and write no `MANIFEST`. Write them first and see them fail where they should (M1's case fails today).

### Low

**L1. Raising `max_depth` traps the encoder too, and the docs speak only of the decoder.**

- **Evidence.**
  - Decoding with the default of 512 is safe in every context I tried. All of these ended as natively, with no trap: 512 nested objects (`{"a":…}`); 512 levels with `relaxed` and `allow_barekey`; 512 levels decoded from inside 20,000 levels of Perl recursion; from inside a `sort` block; and with a `filter_json_object` callback at every level. A Perl structure 100,000 deep refused by `encode_json` and `incr_parse` of `"[" x 100000` both died with "exceeds maximum nesting level" (EXECUTED: `scripts/hostile.py`, `hostile.out`).
  - With `max_depth(1e6)`, encoding a 3,000-deep structure traps in the guest ("stack overflow") and succeeds natively. The guest encodes 1,500 levels and traps at 2,000 (EXECUTED: `scripts/limits.py`, `limits.out`). The memo found the decoder's limit at 1,719 levels. So the default has a margin of about 3× on both sides.
  - A trap ends only the guest. In the same process, the next `wasi_perl.run` calls ran and returned normally (EXECUTED: `limits.py`). The memo's §4 labels that INFERRED; it can be EXECUTED.
- **Judgement on the stack.** Keep it at 8 MiB. The stack is reserved in linear memory for every call, so a larger one costs every one-liner, and native perl has the same cliff, further out (a segmentation fault at 100,000). A trap is a clean, contained end. The docs line is the right fix, and it is firm enough for the decoder.
- **Fix.** In `docs/modules.md`, say the same of `encode` ("Raised, its decoder and encoder run out of the guest's stack at about 1,500 to 1,700 levels"). In the memo's §4, record the encoder's limit and make the host claim EXECUTED.

**L2. The memo says fuel is "the same on any machine", but it moves between runs.**

- **Evidence.** I re-ran three workloads under the lock with `corpus.py`. `json_decode+cpanel` took 369,407,168 instructions against the memo's 369,988,764 (−0.16%); `csv_xs+csv_xs` 1,509,870,493 against 1,510,976,638 (−0.07%); `load_none` 1,614,991 against 1,613,692 (+0.08%). The answers were the same. Wall time was 41.5 ms and 129.5 ms against 44.9 and 132.5 (EXECUTED: `fuel-rerun.json`, `wall-rerun.json`). Perl now seeds its hash order from the host's randomness (CHANGELOG, Security), so its instruction count varies a little per run (INFERRED).
- **Fix.** In the memo's terms, say that fuel varies by about 0.2% between runs, from the random hash seed. Then "the same instructions before and after, to within 0.5%" reads as what it is: within noise. No conclusion changes.

**L3. The landing notes are out of date about CI.** They say "Not seen on CI: no pull request was opened." The pull request is open and its CI is red (H1). **Fix:** update the line after H1, with the run's number and result.

**L4. One stamp in `10_native.sh`: keep it, and say what it costs.** A new pin in either list rebuilds native perl, about 100 s on CI's runner (EXECUTED: CI log, `10_native: ok` 98 s after `00_download`). Through `.native.stamp`, it also rebuilds `perl.wasm`. But `20_target.sh`'s stamp already hashes every file under `recipes/`, `cpan-pure` included, so a second stamp would save only the native rebuild (READ). The cost is paid only when a pin moves. **Fix:** none needed. One sentence in the landing notes would make the trade-off plain: the native rebuild is the only cost of one stamp.

**L5. The module names live in several places: keep them, guard both directions.** A name is in `recipes/cpan-xs` (as the folder), in `recipes/config.sh` three times, and in `tests/build/test_modules.py`, `test_cpan_modules.py` and `test_cpan_unpack.py`. `20_target.sh` refuses a `cpan-xs` entry missing from any of the three `config.sh` lists. The reverse case, a name left in `config.sh` after its `cpan-xs` line is taken out, fails later in `make_ext.pl` with a less clear message (INFERRED). Generating the lists would mean editing the hand-written `config.sh` at build time, which Decision 1 chose not to do. **Fix:** optional. Add the reverse check in `20_target.sh`: a name in `static_ext` that is in neither the Perl tarball's folders nor `cpan-xs` fails with one line.

**L6. Small things.**

- After a `tar` failure, `unpack_pinned` leaves `<dest>.unpack` behind, because `run` exits before the `rm -rf`. The next call removes it first, so this is untidy only. The comment says the folder "is removed after" (EXECUTED: `unpack-work/out/*.unpack` after the failed probes).
- `10_native.sh` unsets `PERL_MM_OPT`, `PERL_MB_OPT` and `PERL_LOCAL_LIB_ROOT`, but not `PERL5OPT`, which also reaches each `Makefile.PL` from the caller's shell. Add `-u PERL5OPT`.
- `tests/build/test_cpan_modules.py` names "study F" in two comments and its docstring. `AGENTS.md` asks code to name no specification or review number. A study label is close to that; say "the 1.4 MB file of 20,000 rows" instead.

## What holds

- **The pins.** Each of the five tarballs' SHA-256 in `downloads/SHA256SUMS` equals the `sha256` in its author folder's PAUSE `CHECKSUMS` (each PGP-signed). Each URL is `https://www.cpan.org/authors/id/<cpan_path>/<tarball>`, with the `cpan_path` that `CHECKSUMS` gives. The local tarballs hash to their pins. Fetched afresh from those URLs, all five verify (EXECUTED: `checksums/`, `fetched/`). They equal study F's table (READ, `perf-libs.md:182-200`).
- **`unpack_pinned`.** EXECUTED on crafted, pinned tarballs (`scripts/probe.sh`, `unpack-work/`):
  - A `..` member: GNU tar refuses it ("Member name contains '..'") and the step fails.
  - An absolute member: tar strips the `/`, so it becomes a second top entry. Refused: "does not unpack to one top folder".
  - A link inside the top folder, absolute or relative, with a member written through it: tar refuses ("Cannot open: Not a directory").
  - A top folder that is a link, with a member through it: refused the same way.
  - Hard links to a file outside, absolute or with `..`: refused.
  - Two top folders: refused.
  - Nothing outside the scratch tree was written in any case.
  - A tarball swapped between the first check and the copy: I stood in a `cp` that copies other bytes. The second check refused it, and the work folder was removed (EXECUTED: `swapbin/`).
  - The one gap is a link that is left in place and followed later (M1).
- **Where downloaded code runs.** It runs only as build tools, never under the system's interpreter:
  - `10_native.sh` runs each distribution's `Makefile.PL`, `make` and `make install` with the built native perl, in `build/native-cpan-src/<dist>`. Every generated Makefile's `PERL` and `FULLPERL` is `build/native-install/bin/perl` (READ).
  - `20_target.sh` runs the two XS `Makefile.PL`s under the host `miniperl` through `make_ext.pl`, in `build/target/cpan/` (READ: `PERL = "../../miniperl"`).
  - `30_library.sh` runs no code of the distributions; it copies files.
  - The `Makefile.PL`s do little: Cpanel compiles a probe for `xlocale.h` with `$Config{cc}`, and Text-CSV_XS reads `~/.perlcriticrc` only for an author target (READ).
  - The checks run the modules under native perl, as the oracle already runs Perl's own tarball. Nothing runs `/usr/bin/perl` (READ: `grep`).
- **The guest's imports.** There are 33, all `wasi_snapshot_preview1`, in the same order as dev's `perl.wasm` (`d70858dc…`). I checked with the tests' parser and again with wasmtime-py's `Module.imports` (EXECUTED).
- **Hostile input.** The check's own cases end as natively (the check passes). I ran 38 more. 35 ended byte for byte as natively, two differ only in a printed address, and one, an encode with `max_depth` raised, trapped (L1) (EXECUTED: `hostile.out`). They include the cases under L1, and these:
  - JSON: overlong UTF-8, 5-byte UTF-8 and surrogates encoded as UTF-8; an escaped bad pair; a 100,000-digit number; `allow_bignum` on a 5,000-digit integer and a 3,000-digit float; `1e400`; infinity and NaN with and without `stringify_infnan`; a 64 MB string and 1 M escaped characters; `max_size`; `allow_tags` with `THAW`, `FREEZE` and an unknown package; `allow_blessed` with `convert_blessed`; `allow_unknown` and a code ref; `allow_singlequote`, `allow_dupkeys` and `dupkeys_as_arrayref`; and incremental parsing of garbage.
  - CSV: invalid UTF-8 with `decode_utf8`; a NUL with `binary => 0`; loose quotes and escapes; a two-character separator; 200,000 quoted multi-line rows; a 32 MB field; `csv()`; a callback that dies; and `strict`.
  - Memory: past the memory limit, the guest ends with Perl's own "Out of memory" and exit 12, not a trap (EXECUTED: `limits.out`).
- **The library copy.**
  - The five `.pm` files of the three pure distributions are in `lib/perl5/5.42`, byte for byte as their tarballs ship them, mode `0444` (EXECUTED: `cmp`, `ls -l`).
  - `MANIFEST` (1,462 lines) and `SHA256SUMS` (1,464) list them with the four `.pm` files of the XS two (EXECUTED: `grep`).
  - The runner's `_check_tree` walks `lib/` and `dev/`, refuses any file not listed, any link and any file off its hash (READ: `src/wasi_perl/_artifacts.py:234-270`). So the new files are covered like every other file.
  - The overwrite refusal stops the step (M2 has the run).
  - Each module's `COPYRIGHT` or `LICENSE` POD ships inside it, so the licence notices travel with the code (EXECUTED: `grep -c '^=head1'`).
- **The changed module checks are not a weakening.** `STATIC_EXT` gains exactly the two names that `$Config{static_ext}` now holds, and the exact-equality asserts are unchanged. `test_every_listed_extension_loads_as_natively` sets `PERL5LIB` only for `Cpanel/JSON/XS` and `Text/CSV_XS`, and still demands `ok` from native perl. Without `build/native-cpan`, that check fails rather than skipping. `test_the_oracle_perl_alone_has_none_of_them` guards the oracle's isolation (READ).
- **The new checks would catch a regression:**
  - a module missing from `perl.wasm` or from the library, or the wrong backend picked: the per-module checks and `test_the_selectors_pick_the_linked_parsers`;
  - any change against native output: the hostile-input check;
  - a new import: `test_the_import_list_is_unchanged`;
  - a step that no longer checks its pins: the tamper checks, which also see the build tree unchanged;
  - a fuel ratio that fell 3× from today's 30×: the fuel check.
  - The gaps are in M2.
- **The CI gate works.** I ran the step's Python on my JUnit file of `tests/build`. It reported "23 checks of the CPAN modules (at least 15), 0 skipped" with exit 0. Then I marked one case skipped in a copy, and it named the case and exited 1 (EXECUTED: `scripts/gate.py`, `junit-skip.xml`). With no JUnit file, it exits 0, which is right: the earlier step has already failed the job, as it did on CI. It has not yet run on CI against real results (H1).
- **The memo's numbers.** `table.py results` reprints the memo's table exactly. Every ratio in §1 follows from the raw `fuel-*.json` and `wall-*.json`, for example 11,156,440,042 / 369,988,764 = 30.2. `sets.txt` gives both hashes and sizes, and `edge-guest.txt` equals `edge-native.txt` once ` trapped None` is removed (EXECUTED). The re-run is in L2.
- **No upstream patch.** Neither Perl nor any distribution is changed. `recipes/patches/` is untouched, and `src/` is untouched (READ: the diff).

## The tests run

All at head `1725686`, with `WASI_PERL_ARTIFACTS=build/artifacts` and `WASI_PERL_ORACLE=build/native-install/bin/perl`, under `flock /home/user/wasi-perl-wt/machine.lock`:

| command | result |
|---|---|
| `uv run pytest tests/build -p no:cacheprovider -ra --junitxml=…` | 461 passed (EXECUTED) |
| `uv run pytest -m "not slow"` | 496 passed, 2 skipped ("root can write every folder"; "macOS, run in CI"), 245 deselected (EXECUTED) |
| `uv run pytest tests/escapes` | 25 passed (EXECUTED) |
| `uv run ruff check`; `uv run ruff format --check` | all checks passed; 40 files already formatted (EXECUTED) |
| `scripts/build.sh` in a clean clone of the branch | all four steps ok, 2 m 6.6 s; the library's `SHA256SUMS` lines equal the worktree's. `perl.wasm` differs only by its "Compiled at" time, as every build's does (EXECUTED) |
| CI run 45459918586 | failure at "Build from the pins" (H1) (EXECUTED: `gh api`) |

Not run by me: Perl's own `t/` suite and `tests/oneliners` in full. The landing notes report them green.

## Round 2

Reviewed `git diff 1725686 bd43962`: eight commits that answer round 1, at head `bd439622ea88…`. I checked each fix against the diff, not against the builder's report. Where it was cheap, I took the fix out in a scratch clone and watched its check fail. The scratch clone is `probes-018/clean/`, a clone of the branch at `bd43962`; every build there held the machine lock. Its tree was clean again after each mutation (EXECUTED: `git status`).

### Verdict, round 2

**Lands.** Every High and Medium finding is fixed, and each fix has a check that fails without it. CI is green on the head, and the gate of the CPAN checks ran there against real results. The Low findings are fixed, except L5, which is answered in writing with a sound reason. No new finding blocks landing.

### Each finding

- **H1, fixed.**
  - `scripts/20_target.sh` now runs `make -j"$JOBS" uni.data` on its own, before `make -j"$JOBS" perl utils/Makefile`. Its comment gives the reason in words (READ).
  - In a fresh build of `bd43962` in the scratch clone (2 m 33 s), mktables writes its last line, `Updating 'mktables.lst'`, at log line 974. Cpanel's `make_ext.pl` starts at 3416 and Text-CSV_XS's at 6630. Before the fix, the order was the other way round: 6442 against 3216 and 6377 (EXECUTED: `grep -n` of both logs).
  - The new check, `test_the_cpan_extensions_are_configured_after_the_unicode_tables`:
    - at `bd43962`, run against the old log from my round-1 build of `1725686`: it failed with `('Cpanel/JSON/XS', 3215, 6441)` (EXECUTED);
    - after the rebuild with the fix: it passes (EXECUTED).
  - So it catches the race even when the build happens to win it. It reads the log of the last real build; if that log is missing, it skips, and CI's gate turns any skip into a failure.
  - CI run 45467350965 on `bd43962` is green in all seven jobs: build, lint, Perl's own suite, and the wheel on four platforms (EXECUTED: `gh api …/runs/45467350965/jobs`). The build job's log shows all four steps ok, `471 passed`, and the gate's line `33 checks of the CPAN modules (at least 20), 0 skipped` (EXECUTED: `gh api …/jobs/138705695102/logs`). This is the first time the gate has run on CI against real results.
  - The new step "The end of the failing step's log" runs only `if: failure()` and only prints a log (READ).
- **M1, fixed.**
  - After `tar`, `unpack_pinned` refuses any entry that is neither a file nor a folder, with one line: "… holds an entry that is neither a file nor a folder" (READ: `scripts/lib.sh`).
  - I deleted those four lines in the scratch clone. Six checks then failed: the four shapes of `test_a_tarball_holding_a_link_is_refused` (the top folder a link out, `lib` a link out, a file a link out, a file a link within) and both link cases of `test_the_library_step_refuses_a_distribution` (EXECUTED).
  - My round-1 probe, a `lib` that links to a host folder, is one of those cases. It now stops `30_library.sh` with no `MANIFEST` written and no `Leak.pm` in the library (READ: the check's asserts; EXECUTED: it passes).
- **M2, fixed.**
  - `_library_checkout` runs the real `30_library.sh` in a scratch checkout, with a stand-in `miniperl` that lays out a three-file privlib (READ). The checks see:
    - a distribution laid in at mode 0444 and listed in `MANIFEST`;
    - a file that would replace `strict.pm` refused, with `strict.pm` unchanged;
    - each refusal leaving no `MANIFEST`, no `SHA256SUMS` and no stamp.
  - I deleted the line `would replace a file already in the library` from `30_library.sh` in the scratch clone. `test_the_library_step_refuses_a_distribution[a file the library has]` then failed (EXECUTED).
  - A top-level link is now covered too. Its old guard, `[ -L … ]`, is still there behind the new one.
- **L1, fixed.**
  - `deep.py` now also bisects the encoder. Its result: 1,923 levels encode, and 1,924 trap. After each trap, the next call answers. Native perl encodes 100,000 levels (READ: `results/deep.txt`). My round-1 bracket was 1,500 to 2,000, and 1,923 falls inside it.
  - `docs/modules.md` and the memo's §4 now say so, and keep the 8 MiB stack, for the reason round 1 gave.
- **L2, fixed.** The memo says fuel varies by about 0.2% between runs. It gives my re-run as the evidence and labels the cause, the random hash seed, INFERRED.
- **L3, fixed.** The landing notes record CI's failed run, the cause, the fix, and the clean-clone results.
- **L4, answered.** The landing notes say that rebuilding native perl is the only cost of one stamp.
- **L5, answered in writing.** The reverse check would need `make_ext.pl`'s own rules for turning a name into a folder. I accept that; a stale name still fails the build, with `make_ext.pl`'s message.
- **L6, fixed.**
  - A `tar` failure now removes `<dest>.unpack`; `test_a_tarball_tar_cannot_read_leaves_nothing_behind` sees the destination's parent empty.
  - `10_native.sh` unsets `PERL5OPT`.
  - The checks no longer name study F (READ: the diff).

### Notes, not findings

- **The log check depends on the last build.** `test_the_cpan_extensions_are_configured_after_the_unicode_tables` reads `build/logs/20_target.log`. In a tree whose last build came before this fix, such as this worktree's `build/` until it is rebuilt, the check fails. That is the right answer for that log. Rebuild before running `tests/build` here.
- **The memo's terms still open with "the same on any machine".** The new sentence after it qualifies this. The wording could be tightened later; nothing depends on it.

### The tests run in round 2

| command | result |
|---|---|
| `flock …machine.lock scripts/build.sh` in the scratch clone at `bd43962` | 00–30 ok, 1,462 files, real 2 m 33 s (EXECUTED) |
| `uv run pytest tests/build` there, with its own build | 471 passed (EXECUTED) |
| the CI gate's Python on that run's JUnit file | "33 checks of the CPAN modules (at least 20), 0 skipped", exit 0 (EXECUTED) |
| `uv run pytest tests/escapes` there | 25 passed (EXECUTED) |
| `uv run pytest -m "not slow"` in the worktree at `bd43962`, on that build | 496 passed, 2 skipped (EXECUTED) |
| `uv run ruff check`; `uv run ruff format --check` | all checks passed; 40 files already formatted (EXECUTED) |
| `tests/build/test_cpan_unpack.py` with one fix taken out (old log; link refusal; overwrite refusal) | 1, 6 and 1 failed, as above (EXECUTED) |
| CI run 45467350965 on `bd43962` | success, all seven jobs (EXECUTED: `gh api`) |

Not run by me in round 2: Perl's own `t/` suite and `tests/oneliners`. CI's "Perl's own suite" job is green on this head, and the landing notes report both green.
