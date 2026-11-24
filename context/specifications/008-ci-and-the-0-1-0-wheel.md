# 008 — CI and the 0.1.0 wheel

- **Status:** pre-agreed by the owner 2026-11-24 (the roadmap); written by the orchestrator.
- **Owner:** Clay Gendron
- **Kind:** `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `scripts/assemble_artifacts.py`, `CHANGELOG.md`, the version.
- **Depends on:** 002, 004, 005, 006 (and 007 for its CI job).
- **Research it stands on:** wasi-python's `.github/workflows/release.yml` (READ): trusted publishing from `release.yml` in the `pypi` environment, actions pinned by commit, a verify job that installs the wheel on each system. The owner, 2026-11-24: one `py3-none-any` wheel; the release workflow is `release.yml`, environment `pypi`.

## The problem

The owner's bar: version 0.1.0, installable with pip, and the built wheel works on Linux x86-64 and arm64, macOS arm64 and Windows x86-64. That has to be shown by running it on those systems, not inferred from the wasm being portable.

## Decisions

1. **One wheel.** `perl.wasm`, the library folder, `dev/null`, `MANIFEST`, `SHA256SUMS` and the licence notices (Perl's `Artistic` and `Copying`) are copied by `scripts/assemble_artifacts.py` into `src/wasi_perl/artifacts/`, and `uv build` makes `wasi_perl-0.1.0-py3-none-any.whl` and the sdist. The wheel's size is recorded.
2. **`ci.yml`, on every pull request into `dev` and every push to `dev`:**
   - `lint`: `ruff check`, `ruff format --check`, the fast unit tests.
   - `build` (ubuntu-24.04, x86-64): `scripts/build.sh` from the pins (downloads cached by their hash), the wheel, and the oracle's answers: native perl 5.42.3 runs the one-liner corpus (006) once and writes `oracle.json`. Uploads the wheel and `oracle.json`.
   - `test` (matrix: ubuntu-24.04, ubuntu-24.04-arm, macos-15, windows-2025): installs the wheel from the `build` job into a fresh venv with `uv pip install`, with no checkout of `src/` on the path, and runs the runner's tests (004), the escape tests (005), the module and refusal checks through `run()` (002, 003), and the one-liners compared with `oracle.json` (006). The same bytes give the same answers everywhere, or the job is red.
   - `perl-t` (ubuntu-24.04, x86-64): Perl's own suite (007).
   - Actions are pinned by commit SHA.
3. **`release.yml`, on a `v*` tag and `workflow_dispatch`:** the same build and test matrix, then a `github-release` job that attaches the wheel, the sdist, `perl.wasm`, `MANIFEST` and `SHA256SUMS`, and a `publish` job in the `pypi` environment with `id-token: write` that uploads with trusted publishing (`pypa/gh-action-pypi-publish`, pinned). It needs the owner to register the publisher on PyPI (owner `ClayGendron`, repository `wasi-perl`, workflow `release.yml`, environment `pypi`) and to make the `pypi` environment; neither is done by an agent. Nothing is published until the owner tags.
4. **Version 0.1.0** in `src/wasi_perl/__init__.py`, and `CHANGELOG.md` with a `0.1.0` section listing what the release carries, the excluded modules and the sandbox's refusals.

## Acceptance

1. A green `ci.yml` run on the pull request, its run id in the landing notes, with all four `test` legs green.
2. `release.yml` is valid (the dispatch is the owner's) and names the `pypi` environment.
3. A fresh agent's review lands.

## Landing notes

### What is built

- `scripts/assemble_artifacts.py` (stdlib only). It refuses `build/artifacts/` unless it passes the runner's own rules: `SHA256SUMS` lists every file of the set (`perl.wasm`, `MANIFEST`, every file under `lib/` and `dev/`), each matches, none is unlisted, none is a link, and `dev/` is exactly one empty `null`. It also refuses any two paths that differ only by case (after NFC and case folding, folders included) and any name Windows cannot hold: `CON`, `PRN`, `AUX`, `NUL`, `COM1`…`LPT9` with any extension, a trailing dot or space, `<>:"|?*`, `\` or a control character. It copies the set and Perl's `Artistic` and `Copying` (from `build/target/`, else from the pinned tarball after its hash is checked) into a staging folder, verifies the copy, and renames it to `src/wasi_perl/artifacts/`. `tests/build/test_portable_names.py` checks the name rules and the built set.
- `.github/workflows/ci.yml`, on pull requests into `dev`, pushes to `dev`, dispatch and `workflow_call`:
  - `lint`: ruff and the fast tests.
  - `build`: `scripts/build.sh`, with the downloads cached by the hash of `downloads/SHA256SUMS`; then `tests/build`, which needs the build tree; then the assembly and `uv build`. The wheel is checked for the set, both licence texts and their `License-File` lines. Native perl's answers are recorded in `oracle.json`, and the artifacts are uploaded.
  - `test`, on ubuntu-24.04, ubuntu-24.04-arm, macos-15 and windows-2025: the wheel is installed by its plain name into a fresh Python 3.11 venv (the `requires-python` floor), with the checkout's `src/` deleted. `tests/runner`, `tests/escapes` and `tests/oneliners` then run against the wheel's own set, with the recorded oracle. The skip guard fails the leg if any guest check did not run (a whole-module skip counts), or if fewer ran than one per one-liner case plus one per escape test function. The one allowance is, on Windows alone, a skip whose reason is exactly "POSIX links" or "POSIX FIFOs".
  - `perl-t`: Perl's own suite, `tests/perl_t/run_suite.py`, unconditionally.
  - Every action is pinned by commit SHA, every `run` is bash, `permissions: contents: read`.
- `.github/workflows/release.yml`, on a `v*` tag and dispatch, calls `ci.yml`. Then, on a pushed tag only:
  - `attest` checks that the tag names the wheel's version and that `CHANGELOG.md` has that version's section, before it signs anything. It then signs the five assets with `actions/attest` and verifies each with `gh attestation verify` against `release.yml` at the tag.
  - `github-release` creates the release with those notes.
  - `publish`, in environment `pypi`, uploads the wheel and the sdist and nothing else, by trusted publishing.
- `pyproject.toml` (004's file, three lines): the sdist carries the assembled set, because `uv build` builds the wheel from the sdist. The licence is `Apache-2.0 AND (Artistic-1.0-Perl OR GPL-1.0-or-later)`, with Perl's two texts as licence files. `context/` stays out of the sdist.
- `.gitattributes`: `tests/** -text`, so the corpus and fixtures check out byte-exact on Windows.

### Acceptance 1: the green runs

- **45335166298** (head `1d89d0a`, READ through the GitHub tools): every job green.
  - ubuntu-24.04, ubuntu-24.04-arm and macos-15: `295 passed, 344 skipped`.
  - windows-2025: `287 passed, 350 skipped, 2 xfailed`. The two xfails are the `hosts = ["win32"]` known differences.
  - The guard printed `222 guest checks, 0 skipped` on every leg.
  - perl-t: `perl t/: 616 run, 439 passed, 177 excluded (…), 0 failed, 0 stale exclusions, 0 regressed`.
  - build: `tests/build` 394 passed.
- The run on this round's head is recorded below once it finishes.

### What the runs found, and what was done

- **macos-14:** GitHub no longer serves it ("The configuration 'macos-14-arm64-us-default' is not supported"), so the leg is `macos-15`, also arm64.
- **Windows, pod/ and Pod/:** a case-blind file system merged the two. 002 removed `pod/`; the assembly and `tests/build` now refuse any such pair.
- **Windows test bugs, fixed:** fixtures written as text came out as CRLF, so they are written as bytes now. A host path pasted into Perl source read its backslashes as escapes; it now goes in single-quoted. `nul.dat` is a device name on Windows, so that case uses `records.dat`. A guest-made link, and a name holding a backslash, are refused outright by a Windows host, so those checks assert the refusal.
- **Windows host differences, accepted by name:** `files-tests` (NTFS reports a directory's size as 0) and `files-errors` (EINVAL where Linux says EISDIR). Both use a `hosts` key in `known_differences.toml`; on every other host the case must match.
- **The in-place rename on Windows** was a product bug; 010 fixed it.

### Locally, this round (EXECUTED)

- actionlint 1.7.12 with shellcheck 0.10.0: no findings.
- `scripts/build.sh`: up to date.
- The build job's steps, run from `ci.yml` by a scratch driver:
  - `tests/build`: 406 passed.
  - `assembled 1455 files, 32193546 bytes`.
  - Wheel 9884251 bytes, SHA-256 `4d4d430a…f091`.
  - 209 oracle cases recorded.
- The test leg's steps, in a copy without `src/`, on Python 3.11.18: `304 passed, 344 skipped`. The guard printed `231 guest checks (at least 225: 209 cases, 16 escape tests), 0 skipped`.
- The same guard fails on a JUnit file whose escape module was skipped whole: `did not run: tests.escapes.test_escapes::(the module): collection skipped`.

### The review's findings (`context/reviews/008-ci-and-the-0-1-0-wheel-pr-2026-11-24.md`)

- **H1:** `CHANGELOG.md` is in. Its section `## 0.1.0 — 2026-11-24` matches the notes' `awk`, which accepts `## 0.1.0` or `## [0.1.0]` followed by a space and anything. The check runs in `attest` before signing.
- **M1:** the green runs are recorded above.
- **M2:** the guard counts module-level skips and has a floor. EXECUTED as above.
- **M3:** perl-t has no "harness absent" path; a missing `run_suite.py` is a red job.
- **L1, `id-token` in `attest`:** kept. It is for the Sigstore certificate that signs the attestations, and the job talks to no package index. The job's comment says so.
- **L2, nothing gates publishing beyond the tag:** for the owner. Create the `pypi` environment with a deployment rule for `v*` tags and a required reviewer before the first tag. A repository-variable switch, as wasi-python has, would also work, but it is the owner's to choose.
- **L3, the Python floor:** the legs use Python 3.11.
- **L4, the licence metadata:** fixed as above.
- **L5, `uv publish` against decision 3's `pypa/gh-action-pypi-publish`:** for the owner to confirm. Both are the same trusted-publishing exchange with the same registration, and wasi-python uses `uv publish`.
- **L6, stale notes:** rewritten.
- **L7, `context/` in the sdist:** excluded.

### Interfaces it relies on

- `scripts/build.sh`, with `WASI_PERL_DOWNLOAD_CACHE` (001).
- `scripts/30_library.sh`'s `MANIFEST` and `SHA256SUMS` (002).
- `WASI_PERL_ARTIFACTS` and the runner's set checks (004).
- `tests/oneliners/record_oracle.py` and `WASI_PERL_ORACLE_JSON` (006).
- `tests/perl_t/run_suite.py --quiet --list-failures` (007).

### The oracle contract (as 006 built it)

`record_oracle.py --perl <native perl> --out <path>` writes UTF-8 JSON: `{"format": 1, "perl": "5.42.3", "corpus_sha256": …, "cases": {id: {"exit_code", "stdout", "stderr", "files"}}}`, with bytes as base64 and each result normalised as the guest's is. A missing case, another format, another perl or another corpus hash fails the run; it does not skip. A native perl, when one is found, wins over the file.

### For the owner

- Register the PyPI publisher: owner `ClayGendron`, repository `wasi-perl`, workflow `release.yml`, environment `pypi`.
- Create and protect the `pypi` environment (L2).
- Confirm `uv publish` (L5).
- Push the `v0.1.0` tag.
