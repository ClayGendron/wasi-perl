# Review: 008 — CI and the 0.1.0 wheel

Reviewer: an agent (Claude), a fresh sub-agent of https://claude.ai/code/session_01ThzDEUwftdmy8zWBJK1gWa; not the author

- **Branch:** `spec/008-ci` at `1d89d0a` (the orchestrator merged `origin/dev` at `b1ebb2b`, with 007, into `84311df` while this review ran), against `origin/dev`. The workflows, `scripts/assemble_artifacts.py` and `pyproject.toml` are the same at both heads. The merge added only 007's files.
- **Read:** AGENTS.md, `context/constitution.md`, the specification with its landing notes, `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `scripts/assemble_artifacts.py`, the `pyproject.toml` and `.gitattributes` changes, `tests/oneliners/{record_oracle,oneliner_oracle,test_oneliners}.py`, and `tests/perl_t/run_suite.py`'s command line. The guide was wasi-python's `.github/workflows/release.yml`: its `verify`, `attest`, `publish` and `pypi` jobs and its action pins.
- **Build:** reused `/home/user/wasi-perl-wt/008-ci/build/` (artifacts, target, native-install), written 05:37 UTC. It was not rebuilt. 007's change to `recipes/config.sh` came in after it, so these bytes are not the bytes of `1d89d0a`. The checks below are about the packaging, and that does not change them.

## What was run (EXECUTED unless marked)

| Command | Result |
|---|---|
| `uv run --no-project python -I scripts/assemble_artifacts.py --source …/008-ci/build/artifacts --target …/008-ci/build/target --downloads /home/user/wasi-perl/downloads` | `assembled 1455 files, 32191853 bytes` |
| `uv build --out-dir <scratch>/dist` | "Building wheel from source distribution…". `wasi_perl-0.1.0-py3-none-any.whl` 9874505 bytes, SHA-256 `7778c44a…fa01`. `wasi_perl-0.1.0.tar.gz` 8947633 bytes. |
| `unzip -Z1` of the wheel, compared with `find` over `src/wasi_perl/artifacts` | 1474 entries. Under `wasi_perl/artifacts/`: the same 1458 files as on disk (`diff` empty), i.e. the 1455 files in `SHA256SUMS` plus `SHA256SUMS`, `Artistic` and `Copying`. Outside it: the 11 modules of `wasi_perl/` and the dist-info (`licenses/LICENSE` among them). No `__pycache__`, no test, no extra file. |
| the sdist unpacked alone, then `uv build --wheel` inside it | a wheel with all 1458 artifact files, `perl.wasm`, `SHA256SUMS` and `Artistic` among them |
| `uv venv v1 && uv pip install --python v1/bin/python <wheel>`, then `v1/bin/wasi-perl run -- -e 'print 1'` from `/` | `{"exit_code": 0, "fuel_used": null, "stderr": "", "stdout": "1", "trapped": null, "truncated": []}`, exit 0. `wasi_perl.__version__` is `0.1.0`, imported from `site-packages`. |
| the same with the venv's own pip (`uv venv --seed v2; v2/bin/python -m pip install <wheel>`) | the same line, exit 0 |
| a scratch pytest project: a module-level `pytest.skip(allow_module_level=True)` in `tests/escapes/test_escapes.py`, run with `--junitxml` | `<testcase classname="" name="tests.escapes.test_escapes">` with `<skipped message="collection skipped">`. The guard's filter (`classname.startswith("tests.escapes")`) does not see it (M1). |
| `grep` of every `uses:` in both workflows | each of the 7 actions is pinned by a 40-hex commit with its tag in a comment. Each is the same commit wasi-python's `release.yml` pins. |
| `grep` of the diff outside `context/` for specification or review numbers | none. `ci.yml:295` says "its own specification", which names no number. |
| `grep secrets\.\|pull_request_target\|github.token` in both workflows | no `secrets.`. No `pull_request_target`. `github.token` only in `attest`'s verify step and `github-release`'s create step. |

**GitHub runs (READ, through the GitHub MCP tools; nothing was re-run).**

- **45327670541** (head `178b8c5`, the last completed run before the dev merges): lint, build and perl-t green. perl-t was the notice-only pass, since there was no harness then. ubuntu-24.04, ubuntu-24.04-arm and macos-15 were green, each `293 passed, 327 skipped, 1 xfailed`, and the guard printed `222 guest checks, 0 skipped`. **windows-2025 failed** with `17 failed, 270 passed, 332 skipped, 2 xfailed`. Every failure was the in-place rename ("failed to rename work file … Permission denied"), which 010, since merged, owns. The guard allowed exactly the two "POSIX links" skips and the one "POSIX FIFOs" skip.
- The macos-15 leg is arm64: setup-uv fetched `uv-aarch64-apple-darwin`, and git and Python came from `/opt/homebrew`.
- The downloads were checked against their digests (`digest-mismatch: error`, "SHA256 digest of downloaded artifact is …"). The legs checked out `refs/remotes/pull/8/merge` with `persist-credentials` false.
- **45334725145** (`ddf3d70`) was cancelled. **45334966934** (`84311df`) was superseded. **45335166298** (`1d89d0a`): **every job green**.
  - ubuntu-24.04, ubuntu-24.04-arm and macos-15: `295 passed, 344 skipped`. windows-2025: `287 passed, 350 skipped, 2 xfailed`, the two `hosts = win32` known differences. Each leg installed `wasi-perl==0.1.0` from the wheelhouse, and the guard printed `222 guest checks, 0 skipped` on each.
  - perl-t ran the real suite: `perl t/: 616 run, 439 passed, 177 excluded (…), 0 failed, 0 stale exclusions, 0 regressed`.
  - build: `tests/build` gave `394 passed`, then `assembled 1455 files, 32192047 bytes`, and the wheel and sdist were built.

## Checks the owner asked for

- **Actions are pinned by SHA.** Yes, all of them (table above).
- **Least privilege.** `ci.yml` has `contents: read` at the top. `release.yml` has `{}` at the top, then per job: `ci` gets `contents: read`. `attest` gets `contents: read`, `id-token: write` and `attestations: write`. `github-release` gets `contents: write`. `publish` gets `contents: read` and `id-token: write`. So `id-token` is in two jobs, not only in publish. `attest` needs it to get a Sigstore certificate. That job runs only on a pushed tag, and it is the guide's exact shape. I accept it and note it as L1.
- **`pull_request` cannot reach secrets or publish.** `ci.yml` uses no secret and has no write scope. `release.yml` has no `pull_request` trigger. Its `attest`, `github-release` and `publish` jobs run only when `github.event_name == 'push' && github.ref_type == 'tag'`, so a dispatch only runs `ci`. Caches: a pull request's cache is scoped to its own ref, and every cached download is checked against `downloads/SHA256SUMS` before use.
- **Publish uploads only this run's wheel and sdist.** `download-artifact` with no `run-id` reads this run's `dist` artifact. That is the artifact the four test legs installed, and its digest is checked. `release.yml:155-163` refuses a `dist/` that is not exactly `wasi_perl-$version-py3-none-any.whl` and `wasi_perl-$version.tar.gz`. `attest` has already refused a tag that does not name the wheel's version.
- **The legs test the installed wheel.** `src/` is deleted. `WASI_PERL_ARTIFACTS` names `site-packages/wasi_perl/artifacts`. The smoke step asserts `site-packages` is in `wasi_perl.__file__`. `python -m pytest` puts the repo root on `sys.path`, but no `wasi_perl` package is left there. The checks that need wasi-sdk skip on the legs, as the spec says. They are not guest checks.
- **The skip guard.** It holds for the per-test skips it sees. The allowance for missing host features is win32-only and by exact message. It can be passed by a collection-level skip, and it has no floor (M1).
- **The oracle.** `build` records it with `build/native-install/bin/perl`. `find_oracle` refuses any perl whose `$^V` is not `v5.42.3`. `load_recorded` fails, and does not skip, on a different `format`, a different `perl` or a different `corpus_sha256`, or a missing case. On the legs no native perl is found (no `build/`, no `WASI_PERL_ORACLE`), so the recorded file is the only source.
- **Windows quoting.** Every `run` is Git bash. Paths go through `"$RUNNER_TEMP/…"` and `"$WHEEL_PY"`. The heredocs are quoted (`<<'PY'`). The `$(...)` that returns the artifacts path worked on Windows: 270 guest tests passed against it. `.gitattributes` (`tests/** -text`) and `core.autocrlf false` keep the corpus byte-exact.
- **Caches keyed by hash.** `downloads-${{ hashFiles('downloads/SHA256SUMS') }}`, saved only on a miss.
- **Wheel contents, sdist, pip install, version.** All as in the table: every artifact and both of Perl's licence texts, nothing extra, the sdist builds a full wheel, and version 0.1.0.
- **Spec numbers in code comments.** None.

## Findings

| Rank | Where | Finding | Suggested fix |
|---|---|---|---|
| **High** | `release.yml:97-122`; spec decision 4 (`008-ci-and-the-0-1-0-wheel.md:23`) | **There is no `CHANGELOG.md` in the repository.** Decision 4 asks for one with a `0.1.0` section: what the release carries, the excluded modules and the sandbox's refusals. AGENTS.md step 4 asks every PR for lines under "Unreleased". `github-release` reads the notes from that file. So a `v0.1.0` tag would run `attest`, whose signature is permanent in the public transparency log, and then fail at "Write the release notes" (`awk` cannot open the file). It would never reach PyPI. The landing notes leave this "to the integration", but it is this specification's decision. | Add `CHANGELOG.md` with an `## Unreleased` heading and a `## 0.1.0` section that lists what decision 4 names. Also move the notes check (the `awk` and the non-empty `grep`) into `attest` before `actions/attest`, so a tag with no notes signs nothing. |
| **Medium** | landing notes `:69` | **Acceptance 1 is met but not recorded.** Run 45335166298 on `1d89d0a` is green on all four `test` legs and on `perl-t`. It is the first green run, after 010's Windows `-pi` fix and 007's harness. The landing notes still say "No run id exists yet", and acceptance 1 asks for the run id there. | Put run 45335166298 and the leg and suite summaries above in the landing notes. If the H1 fix adds a commit, record that commit's green run instead. |
| **Medium** | `ci.yml:261-280` | **The skip guard can be passed without a skip it sees.** (a) A module-level skip, or any collection-level skip, of `tests/escapes/test_escapes.py` or `tests/oneliners/test_oneliners.py` shows in JUnit with `classname=""` and the module path as `name`. The filter never counts it (EXECUTED, table above). (b) The guard only needs `ran > 0`. A deselection, or a corpus that shrinks, lowers "222 guest checks" without any failure. | Also count a testcase whose `classname` is empty and whose `name` starts with `tests.escapes` or `tests.oneliners` and has a `<skipped>` child. Then require `ran` to be at least the escape tests plus the corpus size: read `len(cases)` from `tests/oneliners/cases.toml` with `tomllib`, or commit a floor beside the guard. |
| **Medium** | `ci.yml:295-305` | **perl-t still passes with a notice when the harness is missing.** 007 is on `dev`, so the "harness not here" branch now only serves to hide a renamed or deleted `run_suite.py`. A release would then publish without goal 3 checked. | Remove the presence check. Let the job run `run_suite.py` without a condition, so a missing harness is a red job. |
| Low | `release.yml:41-48` | `id-token: write` is in `attest` as well as in `publish`. It is needed for Sigstore, and it matches the guide, but it is not "id-token only in publish". | Keep it. Say in the job comment that the token is for the Sigstore certificate and is never sent to PyPI. Or move the signing into `publish`, at the cost of the guide's ordering. |
| Low | `release.yml:134-145` | Nothing in the repository gates publishing beyond the tag. The guide also needs `vars.PYPI_PUBLISH == 'true'`. If the `pypi` environment does not exist yet, GitHub creates it on first use with no protection rules. | Owner: create `pypi` with a deployment rule for `v*` tags and a required reviewer before the first tag. Or add the guide's repository-variable switch to `publish`'s `if`. |
| Low | `ci.yml:218` | `uv venv "$venv"` takes whatever Python the runner has: 3.15.2 on macos-15. The floor that `requires-python` declares (3.11) is never tested. | `uv venv --python 3.11 "$venv"` on the legs, or a second Python in the matrix. |
| Low | `pyproject.toml:7` | The wheel ships Perl's `Artistic` and `Copying`, but its metadata says only `License-Expression: Apache-2.0` and `License-File: LICENSE`. | `license = "Apache-2.0 AND (Artistic-1.0-Perl OR GPL-1.0-or-later)"` and `license-files = ["LICENSE", "src/wasi_perl/artifacts/Artistic", "src/wasi_perl/artifacts/Copying"]`, or say in the README why the expression covers only the project's own code. |
| Low | spec `:41` | Decision 3 names `pypa/gh-action-pypi-publish`. The workflow uses `uv publish --trusted-publishing always`. The notes ask the owner to confirm, and nobody has. The owner's own words to this reviewer, "PyPI publishing uses `release.yml`, environment `pypi`, trusted publishing", are met either way. | Amend decision 3 to name `uv publish` once the owner agrees, so the specification and the file agree. |
| Low | spec `:69`, `:79`, `:101` | The landing notes are stale. They still say "Untested until it runs on GitHub … No run id exists yet" and "`tests/perl_t/` (007) is not on `dev`", and they leave the CHANGELOG to "the integration". | Rewrite those paragraphs after the green run. |
| Low | `pyproject.toml:48` | The sdist carries `context/` (the specifications, the reviews and `prior_art/README.md`). It does no harm, but it is not part of the package. | Optionally add `"context"` to the sdist `exclude`. |

## Verdict

Does not land. The workflows are sound on every security point the owner listed: pins, permissions, no secrets on pull requests, a publish limited to this run's tested wheel and sdist, and trusted publishing from `release.yml` in `pypi`. The wheel is right: every artifact and licence file, nothing extra, version 0.1.0, it builds from the sdist, and it installs and runs `wasi-perl run -- -e 'print 1'` from a fresh venv. Run 45335166298 is green on all four systems and on Perl's own suite. One thing stops it: there is no `CHANGELOG.md` (decision 4), so a `v0.1.0` tag would sign the assets and then fail before the GitHub release and PyPI. Add it, record the green run id in the landing notes, and preferably fix the two skip-guard and perl-t findings (M1, M2) in the same round. Then it lands.

## Round 2

- **Branch:** `spec/008-ci` at `03daa76`, read as `git diff bf10e7f..03daa76`. That range holds:
  - `16e05b3`: a merge of `dev`, bringing in 011.
  - `2b6445b`: the orchestrator's `CHANGELOG.md` and README.
  - `03daa76`: the author's answer to this review.
- **Build:** the same reused build tree as round 1, `/home/user/wasi-perl-wt/008-ci/build/`, written 05:49. It assembles to `32193546` bytes, the same size CI's build job printed for `03daa76`.

### What was run (EXECUTED unless marked)

| Command | Result |
|---|---|
| `assemble_artifacts.py` (as in round 1), then `uv build` | the wheel has 1476 entries: round 1's 1474, plus `dist-info/licenses/src/wasi_perl/artifacts/{Artistic,Copying}`. METADATA reads `License-Expression: Apache-2.0 AND (Artistic-1.0-Perl OR GPL-1.0-or-later)`, with three `License-File` lines. |
| `uv venv --python 3.11`, `uv pip install <wheel>`, then `wasi-perl run -- -e 'print 1'` from `/` | Python 3.11.18. `{"exit_code": 0, … "stdout": "1", …}`, exit 0 |
| `uv build` and `uv sync` with no assembled set | both succeed. hatchling drops the missing licence files without a word. Such a wheel has no `perl.wasm` either, and the build job's new METADATA check refuses one built that way. |
| `tar -tzf` of the sdist | `context/` is gone, except `context/prior_art/README.md`, one file (L-a) |
| run **45337272812** (READ, `03daa76`, `pull_request`): every job | **all green.** lint `101 passed, 345 skipped, 242 deselected`. build `tests/build` `406 passed`. perl-t `616 run, 439 passed, 177 excluded (…), 0 failed, 0 stale exclusions, 0 regressed (19052 guarded oks)`. ubuntu-24.04, ubuntu-24.04-arm and macos-15 `304 passed, 344 skipped`. windows-2025 `296 passed, 350 skipped, 2 xfailed`. Every leg ran CPython 3.11 (3.11.14 on Linux, 3.11.9 on macOS and Windows), and every guard printed `231 guest checks (at least 225: 209 cases, 16 escape tests), 0 skipped`. |

### The answers

- **H1, no CHANGELOG.** Answered. `CHANGELOG.md` has an `## Unreleased` and a `## 0.1.0 — 2026-11-24` section. The notes check now runs in `attest` before `actions/attest`. A tag with no section fails with "nothing is signed or published". The notes go to `github-release` as a `release-notes` artifact, so the job that holds `contents: write` no longer checks anything out. The awk pattern matches `## 0.1.0 — 2026-11-24`: the version is followed by a space.
- **Acceptance 1.** Met. Run 45335166298 is recorded in the landing notes. Run 45337272812, on the head itself, is green too and is recorded here.
- **M1, skip guard.** Answered:
  - A testcase with an empty `classname` now counts as its module. In round 1, a skipped module showed as `name="tests.escapes.test_escapes"` with an empty `classname`. That name now matches.
  - An `<error>` counts as a check that did not run.
  - The floor is the number of corpus cases in `cases.toml` plus the number of top-level `test_` functions in `test_escapes.py`, so 209 + 16. A missing file fails the guard.
  - What remains: the floor is one sum over both suites, and it counts one run per parametrized function. A parametrized escape test that drops some of its cases still passes. That is acceptable for a guard against skips.
- **M2, perl-t.** Answered. The presence check is gone, so a missing harness now fails the job.
- **L1, id-token in attest.** Answered with a comment.
- **L3, the Python floor.** Answered: `uv venv --python 3.11`.
- **L4, licence metadata.** Answered, and the build job now checks it.
- **L6, stale landing notes.** Answered: the notes are rewritten.
- **L7, `context/` in the sdist.** Answered, apart from L-a.
- **L2, publishing gate, and L5, `uv publish`.** Left to the owner and listed under "For the owner". That is right: both are the owner's settings or agreement.

### The CHANGELOG against the merged specifications and the code

Checked, and they hold:

| CHANGELOG claim | Checked against |
|---|---|
| 616 files, 439 pass, 0 fail, 177 excluded; 143 subprocess, 11 signals, 3 bug; 19,052 guarded results | 007's landing notes (`:49`, `:68`), `exclusions.toml` (177 entries) and run 45337272812 |
| 209 one-liners | `cases.toml`: 209 `[[case]]` |
| the module list, and the left-out list (threads, IPC::SysV, Sys::Syslog, DB_File and its kin, PerlIO::mmap) | `docs/modules.md:7` and `:15-20` |
| the refusals: fork, exec, system, backticks, pipe open, pipe, wait, waitpid, kill, alarm, dump | `recipes/sandbox-refusals.c` |
| defaults of 60 s, 1 GiB and 16 MiB | `runner.py:57`: `Defaults(memory_bytes=1 << 30, deadline_s=60.0, output_bytes=16 << 20)` |
| the cache: folder 0700, files 0600 | `_cache.py:8`, `:145`, `:186` and `:269` |
| the `wasi_perl_exit_status` export and the 126-and-above refusal | `runner.py:32-33` |
| wasi-sdk 34 and wasmtime 51 | `downloads/SHA256SUMS` and `wasmtime>=51,<52` |
| "no patch to Perl" | there is no `recipes/patches/` |
| in-place editing on Windows, and 10 ms clock slices | 010 and 011, and the green Windows leg |

### Round 2 findings

| Rank | Where | Finding | Suggested fix |
|---|---|---|---|
| Low | `CHANGELOG.md:31` | "each with its reason (143 start a child process, 11 need signals, 3 are known bugs)" names 157 of the 177. It leaves out refused 2, porting 6, platform 6 and not-this-platform 6. It reads like a full breakdown. | List all seven classes, or write "among them". |
| Low | `CHANGELOG.md:32` | "byte for byte … on … Windows x86-64" leaves out the two cases that are allowed to differ on win32 alone: `files-tests` and `files-errors`, the 2 xfailed on the Windows leg. | Add "except two directory cases on Windows, listed in `known_differences.toml`". |
| Low (L-a) | `pyproject.toml:50` | The sdist still carries `context/prior_art/README.md` despite `exclude = [..., "context"]`. | Add `"context/**"`, or leave it: it is one README. |
| Owner | L2, L5 | Protect the `pypi` environment to `v*` tags and confirm `uv publish`. Register the publisher before the first tag. | As listed in the specification's "For the owner". |

### Verdict

Lands. Both workflows now meet every point the owner set. The release refuses to sign without its notes. The skip guard sees module-level skips and errors, and it has a floor. perl-t is required. The wheel is version 0.1.0 and carries the set with Perl's licence declared, and it installs and runs on Python 3.11. Run 45337272812 on `03daa76` is green on all four systems and on Perl's own suite. The Lows are wording, and L2 and L5 are the owner's.
