# 018 — JSON, CSV and diff for agents

- **Status:** agreed by the owner 2026-11-24, through AskUserQuestion. Asked to agree "The five pinned distributions, and the docs page", the owner answered "Agree both (Recommended)". That agrees this text's new choices: the five distributions and their versions, leaving out YAML::XS and JSON.pm, and installing the five into a native folder for check 1. (The docs page is spec 020, built separately.) Written by the orchestrator, 2026-11-24, for synthesis item 6. The owner agreed to build the ten items that day: "You can do all 10 actually, but do not change the API around perl to be different than wasi python, unless wasi python could adopt the same changes." Amended the same day for the review `../reviews/specs-017-022-pr-2026-11-24.md` (M6, M7).
- **Owner:** Clay Gendron
- **Kind:** the build recipe (new `recipes/cpan-xs` and `recipes/cpan-pure` lists, `recipes/config.sh`), `scripts/00_download.sh`, `scripts/10_native.sh`, `scripts/20_target.sh`, `scripts/30_library.sh`, `downloads/SHA256SUMS`, checks in `tests/build/`, `docs/modules.md`.
- **Depends on:** 002; it builds after 016, whose recipe changes it meets.
- **Research it stands on:** `../research/2026-11-24-perf-libs.md` (study F), its §4, §8 and §9, and the two prototype diffs in its evidence folder.

## Intent

Agents over vfs read and write JSON and CSV more than anything but plain text. Today:
- JSON::PP decodes 1.4 MB in 1,350 ms, against 61 ms for wasi-python's `json`;
- the core has no fast CSV parser;
- the core has no diff module.

After this specification, `use Cpanel::JSON::XS` decodes the same file in about 46 ms, 29× faster. `use Text::CSV` parses through Text::CSV_XS, 9× faster than `Text::ParseWords`. `use Algorithm::Diff` gives a real diff. All of it comes from pinned CPAN releases, built statically with no change to their source.

wasi-python's API is untouched. This is the guest's library only.

## Decisions

1. **Two XS distributions, linked statically:**
   - Cpanel-JSON-XS 4.83;
   - Text-CSV_XS 1.65.

   Both are under Perl's own terms, and both built for wasm32-wasip1 unchanged on the first try (F §4). Together they add about 124 KB to `perl.wasm`, and no import (F §4, its costs). They are listed in `recipes/cpan-xs`, one line per distribution: the tarball's name and the folder under `cpan/` it unpacks as. `config.sh` appends their module names to `static_ext`, `extensions` and `known_extensions`, so Perl's own `make_ext.pl` builds them as spec 002 builds the core's.
2. **Three pure-Perl distributions, in the library:**
   - JSON-MaybeXS 1.004008, which picks Cpanel::JSON::XS by itself;
   - Text-CSV 2.06, which uses Text::CSV_XS when it is linked;
   - Algorithm-Diff 1.201.

   They are listed in `recipes/cpan-pure`. `30_library.sh` copies each distribution's `lib/` tree into `lib/perl5/5.42`, refusing to overwrite a file Perl installed, and `MANIFEST` covers them like any other library file.
3. **Every tarball is pinned and checked.**
   - Each tarball has a line in `downloads/SHA256SUMS`, in its three-field format (`<sha256>  <name>  <url>`), and `00_download.sh` fetches and verifies it as every other input. The five lines:

     | tarball | SHA-256 | URL |
     |---|---|---|
     | `Cpanel-JSON-XS-4.83.tar.gz` | `daabf6ad0376ee4613e671c521f5d05650c42205e403cac077e79d8594da3ade` | `https://www.cpan.org/authors/id/R/RU/RURBAN/Cpanel-JSON-XS-4.83.tar.gz` |
     | `Text-CSV_XS-1.65.tgz` | `4f0c16457c87def295a994e9c4ba397a8baafad96cefaf2d61483125f90ff974` | `https://www.cpan.org/authors/id/H/HM/HMBRAND/Text-CSV_XS-1.65.tgz` |
     | `JSON-MaybeXS-1.004008.tar.gz` | `cd3937afa78831f80a2ad5abab6c51b9e82fca4c31e5856ea208d598db5dc867` | `https://www.cpan.org/authors/id/E/ET/ETHER/JSON-MaybeXS-1.004008.tar.gz` |
     | `Text-CSV-2.06.tar.gz` | `dfcaec925a788b0ba41e51bc6d16e21b0e98b4c7af9b79395090add75f5e506f` | `https://www.cpan.org/authors/id/I/IS/ISHIGAKI/Text-CSV-2.06.tar.gz` |
     | `Algorithm-Diff-1.201.tar.gz` | `0022da5982645d9ef0207f3eb9ef63e70e9713ed2340ed7b3850779b0d842a7d` | `https://www.cpan.org/authors/id/R/RJ/RJBS/Algorithm-Diff-1.201.tar.gz` |

   - **The evidence.** The hashes are F §4's table (`perf-libs.md:180-200`, READ). Each also matches the `sha256` its author folder's PAUSE `CHECKSUMS` file gives. The author folder is the one whose `CHECKSUMS` lists the tarball (EXECUTED: `sha256sum` of study F's tarballs under `/home/user/wasi-perl-downloads/cpan/`, against `CHECKSUMS.R_RU_RURBAN`, `.H_HM_HMBRAND`, `.E_ET_ETHER`, `.I_IS_ISHIGAKI` and `.R_RJ_RJBS`, 2026-11-24; all five match). The URL is the author folder's path on `www.cpan.org`, where F fetched them (F §4, :180).
   - The unpack step refuses a tarball that is unpinned, does not match its pin, unpacks to more than one top folder, or would replace a folder already in the tree.
   - The stamp of `20_target.sh` and of `30_library.sh` includes each listed tarball's SHA-256, not only the list.
4. **Not in this specification, with the reason for each:**
   - **YAML::XS:** the largest addition (+158 KB), and a C parser of a complex format; vfs's catalog asks for JSON and CSV far more. A later specification if the owner wants YAML.
   - **Sort::Key:** the docs of spec 020 get most of its gain.
   - **JSON::XS:** needs Canary::Stability at configure time, and is no faster than its fork Cpanel.
   - **JSON.pm:** it keeps JSON::PP unless `PERL_JSON_BACKEND` names Cpanel. Shipping it would make the slow path the default for `use JSON`, so it is left out, and the docs say to use JSON::MaybeXS.
   - **List::MoreUtils::XS and Unicode::UTF8:** their configure steps fail under miniperl (F §4).
5. **The POD stays in the shipped `.pm` files.** The library is as `installperl` and the distributions lay it out; stripping is a separate choice for all files at once.
6. **One documented difference from JSON::PP:** Cpanel::JSON::XS encodes a whole-number float as `5.0` (F §1). `docs/modules.md` says so.

## Checks (written first, seen failing)

1. **Each module loads and does one real thing in the guest, equal to native perl 5.42.3 with the same versions installed:**
   - a Cpanel decode and encode round trip;
   - a JSON::MaybeXS call that reports `Cpanel::JSON::XS` as its backend;
   - Text::CSV parsing quoted fields with embedded newlines, reporting `Text::CSV_XS` as its backend;
   - an `Algorithm::Diff` hunk list.

   **How native perl gets the same versions.** `10_native.sh` builds the five from the same pinned tarballs, after native perl: `perl Makefile.PL INSTALL_BASE=build/native-cpan`, then `make` and `make install`, with the native compiler. They go into a folder of their own, so the oracle's native perl (spec 006) sees no new module. The check runs native perl with `PERL5LIB=build/native-cpan/lib/perl5`. The stamp of `10_native.sh` includes the five SHA-256s.
2. **Hostile input ends as natively** (F's `edge.pl`): JSON nested 100,000 deep, truncated, with a NUL or trailing garbage; a 5,000-digit number; invalid UTF-8; CSV with broken quotes, a NUL field or a 1 MB field. Each is a catchable Perl error or a result, never a trap.
3. **The import list of `perl.wasm` is unchanged:** only `wasi_snapshot_preview1`, with the same names.
4. **Fuel** for the 1.4 MB decode through Cpanel is under 1/10 of JSON::PP's. This is a count.
5. **A tampered tarball fails the build** at the unpack step: a one-byte change against its pin.

## Acceptance

1. The checks pass (EXECUTED).
2. The one-liner oracle, the escape tests and the whole of Perl's `t/` suite pass, with no new exclusion.
3. `docs/modules.md` gains a "From CPAN" section with each distribution's version, licence and purpose.
4. `CHANGELOG.md` under "Unreleased".
5. A fresh agent's review lands.

## Landing notes

Built 2026-11-24 by the implementer (Claude Opus 5.5) on `spec/018-json-csv-and-diff`, from dev at `f44e0fd`, Linux x86-64, 4 vCPUs, CPython 3.13, wasmtime-py 51.0.0, as root. **Not yet reviewed.** Every claim below was run (EXECUTED) unless it says otherwise. Every build and wall-clock run held `flock /home/user/wasi-perl-wt/machine.lock`.

### What was built

- **Decision 1.** `recipes/cpan-xs` lists `Cpanel-JSON-XS-4.83.tar.gz Cpanel-JSON-XS` and `Text-CSV_XS-1.65.tgz Text-CSV_XS`. `recipes/config.sh` appends `Cpanel/JSON/XS Text/CSV_XS` to `static_ext`, `extensions` and `known_extensions`. `scripts/20_target.sh` unpacks each into `cpan/<folder>` after the patches and before `Configure -S`, and make_ext.pl builds both unchanged. `perl.wasm` is 9,387,903 bytes (`064dfdbb9dd18ca82e5458ced2e059062d70aa21668870a6fda54435b96c25a6`), 126,212 more than dev's (`stat -c %s`, `sha256sum`).
- **Decision 2.** `recipes/cpan-pure` lists the three tarballs. `scripts/30_library.sh` unpacks each under `build/cpan-pure/` and copies its `lib/` tree into `lib/perl5/5.42`. `MANIFEST` has 1,462 files, nine more than dev's: the five `.pm` files of the three, and the four of the two XS modules.
- **Decision 3.** The five lines are in `downloads/SHA256SUMS` as the table gives them. `scripts/00_download.sh` fetched all five from those URLs on www.cpan.org and verified them (`build/logs/00_download.log`). The unpack is `unpack_pinned` in `scripts/lib.sh`, used by all three steps. The stamps of `10_native.sh`, `20_target.sh` and `30_library.sh` hold each listed tarball's name and pin.
- **Check 1's native install.** `scripts/10_native.sh`, after native perl, builds the five into `build/native-cpan` with `Makefile.PL INSTALL_BASE=…`, `make` and `make install`.
- **Decision 6.** `docs/modules.md` has a "From CPAN" section: each distribution's version, licence (READ: each `META.json` says `perl_5`, and Algorithm::Diff's POD says "the same terms as Perl"), and purpose; the `5.0` difference; and to use JSON::MaybeXS, since JSON.pm is not shipped.
- No patch to Perl or to any distribution. No change under `src/`. No `scripts/40_link.sh`, which is 019's. Spec 020's docs page is not built.

### Choices the specification left to the implementer

- **`recipes/cpan-pure` has one field a line,** the tarball. The folder under `build/cpan-pure/` is the tarball's name without `.tar.gz` or `.tgz` (`dist_of`). Nothing else needs a name for it. INFERRED.
- **`recipes/config.sh` is edited by hand,** as Decision 1 says and as study F's prototype did. So the module names live in two files. `20_target.sh` fails at once, with one line, when a `cpan-xs` entry's module is missing from any of the three lists (EXECUTED once, by hand, in a scratch copy with `Text/CSV_XS` taken out of `known_extensions`: "FAILED: recipes/config.sh's known_extensions does not name Text/CSV_XS, which recipes/cpan-xs lists", exit 1). No check in `tests/` does this. INFERRED that the build's own refusal is enough.
- **Every listed tarball is checked against its pin before the step's stamp is read**, in all three steps, as `perl-5.42.3.tar.xz` already was. So a tampered tarball fails even a step that is up to date, and no step removes anything before the check. That is how check 5 is met "at the unpack step" without a build: the check runs each step in a scratch copy of the checkout, whose build tools are empty stand-ins, and sees it stop on the tarball with nothing in its build tree changed.
- **What `unpack_pinned` refuses,** each with one line on stderr: a name pinned on no line or on two (both "is not pinned"); a missing tarball; a destination that exists, before anything is made; a tarball off its pin, checked once on the original and again on the copy that is unpacked, so the bytes checked are the bytes unpacked; and anything but one top entry that is a real folder (two folders, a folder and a hidden file, one loose file, a symbolic link). tar runs with `--no-same-owner --no-same-permissions`, into `<dest>.unpack`, which is removed after.
- **One stamp for native perl and the five.** The specification asks that `10_native.sh`'s stamp include the five pins; I kept the one stamp the step had. So changing a pin or a list rebuilds native perl, and, through `.native.stamp`, `perl.wasm` too. The native rebuild is the only cost of one stamp: `20_target.sh`'s stamp already hashes every file under `recipes/`, so a second stamp would save nothing else (the review, L4). A rebuild of both took about 2 minutes here (`build/logs/build-after.out`: real 2m2.6s). INFERRED: simpler than two stamps, at a cost paid only when a pin moves.
- **The native build's environment.** `PERL_MM_OPT`, `PERL_MB_OPT` and `PERL_LOCAL_LIB_ROOT` are unset, so the caller's shell cannot move the install; `PERL5LIB` is `build/native-cpan/lib/perl5`, so each later distribution sees the earlier ones; `PERL_MM_USE_DEFAULT=1` and stdin from `/dev/null`, so no question waits. The XS two are built first.
- **The library copy.** The pure trees are copied before the step's pruning, so the same rules cover them (none of the five has a `.pod` file: READ, `tar -tzf`). Each file is made mode 0444, as installperl installs a module. A `lib/` holding anything but files and folders is refused. A file that would replace any file already in the library, Perl's or an earlier distribution's, is refused.
- **CI runs the checks.** `.github/workflows/ci.yml`'s build job has the build tree, `build/native-cpan` and the tarballs, and runs `tests/build`. A new step, "No check of the CPAN modules was skipped", reads that run's JUnit file and fails when any case of `test_cpan_modules.py` or `test_cpan_unpack.py` skipped, or when fewer cases ran than the two files have test functions. Tried locally on a full run (23 cases, 0 skipped: exit 0) and on a run with no `perl.wasm` (8 skipped: exit 1). Not seen on CI: no pull request was opened.
- **The existing module checks.** `tests/build/test_modules.py`'s `STATIC_EXT` gains the two names, since `$Config{static_ext}` now has them. `test_every_listed_extension_loads_as_natively` runs native perl with `PERL5LIB=build/native-cpan/lib/perl5` for those two only, since the oracle's own perl has neither; every other extension is checked as before. These are changes of the expected list, not of what is checked; the review should see them.
- **Check 2 has more cases than `edge.pl`:** a decode at 513 levels; the same bad UTF-8 through a UTF-8 decoder, which refuses it; a huge exponent; empty input; a lone surrogate; a CSV line of 100,000 fields; and every CSV case through Text::CSV as well as Text::CSV_XS.
- **Check 4's file** is study F's `t.json`, 1,355,560 bytes, made by the check as `corpus.py` makes it.
- **Check 3** passes on dev's module and on this one by design: its list is the one `test_randomness.py` already holds. It was not seen failing; nothing here could make it fail but a new import.

### A finding

With `max_depth` raised above its default of 512, Cpanel::JSON::XS's decoder recurses past the guest's stack at 1,720 levels and the call ends as trapped, "stack overflow". Native perl decodes 10,000 and segfaults at 100,000 (`context/research/2026-11-24-json-csv-and-diff-measured/deep.py`, `results/deep.txt`). The default refuses deep input with a Perl error, which check 2 covers. `docs/modules.md` says to keep the default. No decision is unsound for it: the trap ends the guest, not the host (INFERRED).

### The checks

`tests/build/test_cpan_modules.py`, 10 cases, and `tests/build/test_cpan_unpack.py`, 13 cases.

| check | before the code | after |
|---|---|---|
| 1: each module does its work as natively (4 cases) | skipped at first (no `build/native-cpan`); with it built, failed on dev's set: "Can't locate" | passed |
| 1: the selectors pick the linked parsers | failed | passed |
| 1: native has the pinned versions | skipped (no `build/native-cpan`) | passed |
| 1: the oracle's perl alone has none of the five | passed (a guard) | passed |
| 2: hostile input ends as natively | skipped, then failed on dev's set | passed, no trap |
| 3: the import list is unchanged | passed (by design) | passed: 33 functions, all `wasi_snapshot_preview1` |
| 4: Cpanel under a tenth of JSON::PP's fuel | failed | passed: 369,988,764 against 11,156,440,042, 30.2× |
| 5: `unpack_pinned` (8 cases: unpacks; one byte off; unpinned; pinned twice; three shapes not one folder; a folder that exists) | failed: no such function | passed |
| 5: every listed tarball pinned once | failed: no lists | passed |
| 5: a tampered tarball fails `20_target.sh` (2 cases) and `30_library.sh` | failed: each step went on, and stopped later on its stand-in tools | passed |
| 5: a tampered tarball fails `10_native.sh` | not run before the code: the old step would have started building native perl in the scratch copy. INFERRED to fail: it checked no CPAN tarball | passed |

"Failed on dev's set" is `WASI_PERL_ARTIFACTS=build/before/artifacts uv run pytest tests/build/test_cpan_modules.py tests/build/test_modules.py`: 9 failed, the seven above and the two changed checks of `test_modules.py`. Two of my programs had faults of their own on the first build (a wide character printed with no layer; a version asked of a module not loaded); they were fixed in their own commit.

### The acceptance runs

| command | result |
|---|---|
| `flock …machine.lock scripts/build.sh` | 00–30 ok; 1,462 files; a second run: every step "up to date" |
| `uv run pytest tests/build -ra` | 461 passed, 0 skipped |
| `WASI_PERL_ORACLE=build/native-install/bin/perl uv run pytest tests/runner tests/escapes tests/oneliners -ra` | 701 passed, 2 skipped ("root can write every folder"; "macOS, run in CI"), as on dev |
| `uv run python tests/perl_t/run_suite.py --quiet --list-failures`, under the lock | 616 run, 439 passed, 177 excluded, 0 failed, 0 stale exclusions, 0 regressed (19,052 guarded oks); `tests/perl_t/exclusions.toml` unchanged |
| `uv run pytest -m "not slow"` | 496 passed, 2 skipped, 245 deselected |
| `uv run ruff check`; `uv run ruff format --check` | all checks passed; 40 files already formatted |

The measurements are in `context/research/2026-11-24-json-csv-and-diff-measured.md`: the 1.4 MB decode takes 370 M instructions and 45 ms instead of JSON::PP's 11,156 M and 1,250 ms; 50,000 quoted CSV rows take 1,511 M and 133 ms through Text::CSV_XS instead of 18,215 M and 1,342 ms through Text::ParseWords.

### Round 1 of the review

The review is `../reviews/018-json-csv-and-diff-pr-2026-11-24.md`. Its findings were fixed in a clean clone of the branch, `/home/user/wasi-perl-wt/018-ci-repro`, built from nothing under the lock each time.

- **H1, CI's failed build (run 45459918586).** The cause is a race this branch added. Text-CSV_XS's `Makefile.PL` does `use charnames ":full"` and uses `\N{…}`, so compiling it needs `lib/unicore/Name.pm`, which `mktables` (the `uni.data` target) writes. Perl's Makefile orders only its own Unicode::Normalize after `uni.data`; `$(static_ext)` does not wait for it. Under `make -j4` the two raced.
  - Seen, not guessed: with `20_target.sh`'s `make perl utils/Makefile` swapped for `make -j4 lib/auto/Text/CSV_XS/CSV_XS.a` in the clean clone, the step failed with "Can't locate unicore/Name.pm in @INC … Compilation failed in require at Makefile.PL line 7", exit 1 (EXECUTED). Every local build until then won the race: in the worktree's log, Text-CSV_XS's `make_ext.pl` starts at line 6377 and `mktables` finishes ("Updating 'mktables.lst'") at 6436.
  - The check, written first: `test_the_cpan_extensions_are_configured_after_the_unicode_tables` reads `build/logs/20_target.log` and fails unless every `cpan-xs` extension's `make_ext.pl` line comes after mktables' last line. It failed on the worktree's log, from a build that had passed (EXECUTED: "Cpanel/JSON/XS, 3201, 6435"). So it catches the race whether or not a build loses it.
  - The fix: `20_target.sh` runs `make -j"$JOBS" uni.data` alone before `make -j"$JOBS" perl utils/Makefile`. A later make finds the tables up to date and does not rewrite them (EXECUTED: one "Updating 'mktables.lst'" in the log). The check passes on the clean builds since.
  - CI now prints the last 60 lines of the newest build log in the job's own output when the build fails, in a step "The end of the failing step's log". The scripts keep their one-line failures.
- **M1.** `unpack_pinned` now refuses a tarball holding any entry that is neither a file nor a folder: "… holds an entry that is neither a file nor a folder". The checks, written first and seen failing: a link as the top folder, as `lib`, as a file pointing out and as one pointing in; and `30_library.sh` run on a tarball whose `lib` links to a host folder, which wrote the host's file into the library and `MANIFEST` before the fix (EXECUTED: exit 0) and now stops with no `MANIFEST`. None of the five tarballs has a link (READ by the review: `tar -tvzf`).
- **M2.** `30_library.sh` now has checks in a scratch checkout with a stand-in `miniperl` whose `installperl` lays out a three-file privlib: one that lays a distribution in (mode 0444, listed in `MANIFEST`), one that would replace `strict.pm`, and two holding links. Each refusal writes no `MANIFEST`, `SHA256SUMS` or stamp. With the overwrite refusal removed from the script, its check fails (EXECUTED, as a mutation, then restored). The top-folder link refusal is now caught by M1's rule first, so its case moved to M1's check. `30_library.sh`'s own "neither a file nor a folder" test is kept, though `unpack_pinned` now refuses such a tree before it.
- **L1.** The encoder traps too: with `max_depth` raised, the guest encodes 1,923 levels and traps at 1,924; the decoder decodes 1,719. After either trap, the next call in the same process answers as usual (EXECUTED: `deep.py`, `results/deep.txt`). `docs/modules.md` and the memo's §4 say so. The stack stays at 8 MiB.
- **L2.** The memo says fuel varies by about 0.2% between runs, from the hash seed Perl draws from the host (INFERRED), with the review's re-run as the evidence.
- **L5.** The names stay where they are. The reverse check (a `config.sh` name with no `cpan-xs` line) is not added: telling a core extension's name from a CPAN one needs make_ext.pl's own name-to-folder rules, which is not cheap to do right in `sh`. A stale name fails later in `make_ext.pl`, with its own message.
- **L6.** A tar failure now removes `<dest>.unpack` (a check sees nothing left); `10_native.sh` unsets `PERL5OPT` too; the checks name no study.
- **`perl.wasm` changes every build.** Two builds of one tree differ only in Perl's "Compiled at" time and what follows from it (EXECUTED: `strings`; the review saw the same). So the hash is per build: the clean build after this round is `9064379f4f3b8b15d020144fed92e1df755f01341cbc42f35e7df6060c963dba`, 9,387,903 bytes, the same size as before.

| command, in the clean clone, under the lock | result |
|---|---|
| `rm -rf build; scripts/build.sh` with `WASI_PERL_JOBS=4` | 00–30 ok, 1,462 files, real 2m35s |
| `uv run pytest tests/build` | 471 passed |
| `uv run python tests/perl_t/run_suite.py --quiet --list-failures` | 616 run, 439 passed, 177 excluded, 0 failed, 0 stale, 0 regressed (19,052 guarded oks) |
| `WASI_PERL_ORACLE=build/native-install/bin/perl uv run pytest tests/runner tests/escapes tests/oneliners` | 701 passed, 2 skipped (as on dev) |
| `uv run pytest -m "not slow"` | 496 passed, 2 skipped |
| `uv run ruff check`; `uv run ruff format --check` | all checks passed; 40 files already formatted |
