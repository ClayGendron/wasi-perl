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
   - `test` (matrix: ubuntu-24.04, ubuntu-24.04-arm, macos-14, windows-2025): installs the wheel from the `build` job into a fresh venv with `uv pip install`, with no checkout of `src/` on the path, and runs the runner's tests (004), the escape tests (005), the module and refusal checks through `run()` (002, 003), and the one-liners compared with `oracle.json` (006). The same bytes give the same answers everywhere, or the job is red.
   - `perl-t` (ubuntu-24.04, x86-64): Perl's own suite (007).
   - Actions are pinned by commit SHA.
3. **`release.yml`, on a `v*` tag and `workflow_dispatch`:** the same build and test matrix, then a `github-release` job that attaches the wheel, the sdist, `perl.wasm`, `MANIFEST` and `SHA256SUMS`, and a `publish` job in the `pypi` environment with `id-token: write` that uploads with trusted publishing (`pypa/gh-action-pypi-publish`, pinned). It needs the owner to register the publisher on PyPI (owner `ClayGendron`, repository `wasi-perl`, workflow `release.yml`, environment `pypi`) and to make the `pypi` environment; neither is done by an agent. Nothing is published until the owner tags.
4. **Version 0.1.0** in `src/wasi_perl/__init__.py`, and `CHANGELOG.md` with a `0.1.0` section listing what the release carries, the excluded modules and the sandbox's refusals.

## Acceptance

1. A green `ci.yml` run on the pull request, its run id in the landing notes, with all four `test` legs green.
2. `release.yml` is valid (the dispatch is the owner's) and names the `pypi` environment.
3. A fresh agent's review lands.

## Landing notes

### Draft, 2026-11-24 (first written before 001, 004 and 006 landed on `dev`; reconciled after)

**What is written.**

- `scripts/assemble_artifacts.py` (stdlib only). It verifies `build/artifacts/` by the runner's rules before copying anything: `SHA256SUMS` lists every file of the set (`perl.wasm`, `MANIFEST`, every file under `lib/` and `dev/`), each matches, none is unlisted, none is a link, `dev/` is exactly one empty `null`. It copies the set and Perl's `Artistic` and `Copying` (from `build/target/`, else from the pinned tarball after its hash is checked against `downloads/SHA256SUMS`) into a staging folder, verifies the copy again, then renames it to `src/wasi_perl/artifacts/`. A refusal is one line on stderr and exit 1.
- `.github/workflows/ci.yml`: `lint`, `build` (with `tests/build`), `test` (four systems), `perl-t`, on pull requests into `dev`, pushes to `dev`, dispatch, and `workflow_call`. Every action is pinned by commit SHA, every `run` is `bash`, `permissions: contents: read`, one concurrency group per pull request or ref (only pull requests cancel).
- `.github/workflows/release.yml`: on a `v*` tag and dispatch. It calls `ci.yml`, then, on a pushed tag only: `attest` (the tag must match the wheel's version and the five assets must be exactly the wheel, the sdist, `perl.wasm`, `MANIFEST`, `SHA256SUMS`; `actions/attest` signs them, and `gh attestation verify` checks each against `release.yml` at the tag), `github-release` (`contents: write`; notes are the version's section of `CHANGELOG.md`), and `publish` (environment `pypi`, `id-token: write`, the wheel and the sdist only).

**One deviation from decision 3, for the owner to confirm.** `publish` uploads with `uv publish --no-config --trusted-publishing always`, as wasi-python's `release.yml` does (READ), not with `pypa/gh-action-pypi-publish`. It is the same trusted-publishing exchange and needs the same publisher registration; it saves a pinned Docker action. Swapping back is one step.

**Checked here.** EXECUTED in the 008 worktree, on `dev` at `adff3ac` merged in:

- actionlint 1.7.12 (`actionlint_1.7.12_linux_amd64.tar.gz`, SHA-256 `8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8`, matching the release's checksums file) with shellcheck 0.10.0 (`shellcheck-py==0.10.0.1`): exit 0, no findings, on both workflows.
- `WASI_PERL_DOWNLOAD_CACHE=/home/user/wasi-perl/downloads scripts/build.sh` with `nproc` answering 2: `00_download: ok`, `10_native: ok`, `20_target: ok`, 174 s.
- The `build` job's own `run:` blocks, read out of `ci.yml` and run with `bash -eo pipefail` by a scratch driver: "Seal the set" (no `SHA256SUMS` from the build: 1623 `MANIFEST` lines, 1625 `SHA256SUMS` lines); `uv run pytest tests/build` (36 passed); `assemble_artifacts.py` (`assembled 1625 files, 41377989 bytes`); `uv build` and the wheel check (`wasi_perl-0.1.0-py3-none-any.whl` 12278431 bytes, SHA-256 `c4b5f40e…853d`; sdist 11042886 bytes); `record_oracle.py` (`recorded 209 cases … (corpus 4fbf7aa13b27)`); the two tars (set 42.8 MB, target `lib/` 53.3 MB).
- The `test` job's `run:` blocks the same way, in a copy of the checkout with `src/` removed and the wheel and `oracle.json` where the downloads put them: the venv install, the import and `hello` check (`Completed(exit_code=0, stdout=b'hello\n', …)`), then `tests/runner tests/escapes tests/oneliners`: **35 failed, 255 passed, 327 skipped, 3 xfailed**. All 35 are `test_guest_matches_native` cases for modules the build on `dev` does not have yet (`Can't locate File/Glob.pm`, and List::Util, POSIX, Digest, Encode and kin): 002's work. The same command from the source tree with the native oracle fails the identical 35 (`diff` of the two `FAILED` lists is empty), so recorded mode and native mode agree. The skip guard then reads `222 guest checks, 0 skipped`.
- The first try showed that `--import-mode=importlib` cannot import `tests/oneliners`' helper module, so the legs use pytest's default mode; deleting `src/` is what keeps the checkout's package off the path.
- It also showed that `uv build` builds the wheel from the sdist, and the sdist left out the git-ignored set, so the wheel had no `perl.wasm`. `pyproject.toml` now names `src/wasi_perl/artifacts` under the sdist target too (one line, 004's file); the wheel built from the sdist then carries the set.
- `lint`'s commands: ruff clean; the fast tests fail 2 here (`tests/runner/test_artifacts.py::test_dev_null_must_be_empty`, `tests/runner/test_limits.py::test_without_the_export_a_refused_status_is_wasmtimes_words`: `from conftest import …` finds `tests/oneliners/conftest.py` once both folders are collected). They run only where wasi-sdk is present, so on a bare runner they skip; the clash is on `dev` and is 004's and 006's to fix.

**Untested until it runs on GitHub.** The downloads cache, the uploads and downloads between jobs, the arm64 Linux, macOS and Windows legs (paths and `uv venv` there), `perl-t`, the reusable-workflow call, attestation, the release and the upload. No run id exists yet; acceptance 1 is open.

**Interfaces, reconciled with `dev` at `adff3ac` (001, 004, 005, 006 merged).**

1. `scripts/build.sh` runs `00_download.sh`, `10_native.sh` and `20_target.sh`; `WASI_PERL_DOWNLOAD_CACHE` is honoured by `00_download.sh` (001, as merged). Logs are under `build/logs/`.
2. The set's seal is the runner's: `SHA256SUMS` lists every file of the set but itself (`perl.wasm`, `MANIFEST`, every file under `lib/` and `dev/`), in `scripts/write_sums.py`'s format, and the runner refuses an unlisted file under `lib/` or `dev/` (004's `_artifacts.py`). On `dev` today the build writes neither `MANIFEST` nor `SHA256SUMS`; 002's `scripts/30_library.sh` (in review) writes both, `MANIFEST` as `<sha256>  <path>` for `lib/` and `dev/`. So `build` has a "Seal the set" step: when the build wrote no `SHA256SUMS`, it writes `MANIFEST` in 002's format (if missing) and runs `write_sums.py`; once 002 lands, the step does nothing. `assemble_artifacts.py` applies the runner's rules (every file listed and matching, nothing unlisted, no link, `dev/` exactly one empty `null`) and does not parse `MANIFEST`, whose content 002 and 004 describe differently.
3. `Artistic` and `Copying` sit at the top of `wasi_perl/artifacts/`, outside `SHA256SUMS`; the runner checks only `perl.wasm`, `MANIFEST`, `lib/` and `dev/`, so they do not disturb it.
4. `tests/build` needs the build tree (wasmtime-py over `build/artifacts`, the wasmtime command line under `build/tools/`, native perl), so it runs in `build` only, on x86-64 Linux, never on the installed-wheel legs.
5. The `test` legs run `tests/runner`, `tests/escapes` and `tests/oneliners` with `WASI_PERL_ARTIFACTS` naming the installed wheel's own `wasi_perl/artifacts/` (the shared `perl` fixture finds a set only through that variable or `build/artifacts`, and there is no `build/` there), and with `WASI_PERL_ORACLE_JSON` naming the recorded oracle. A step after the tests reads pytest's JUnit file and fails the leg if any escape test or `test_guest_matches_native` case was skipped (an expected failure from `known_differences.toml` is allowed). The runner's stand-in tests need wasi-sdk and skip on the legs; they run in `lint` only where wasi-sdk is present (it is not on a bare runner either).
6. `uv.lock` is committed and its `dev` group is pytest and ruff (004). The legs install the wheel by its plain name, then `uv export --only-dev --no-emit-project --no-hashes` into the same venv.
7. `tests/perl_t/` (007) is not on `dev`. `perl-t` checks for `tests/perl_t/run_suite.py` and, when it is absent, posts a notice and passes with every later step skipped. When present it runs `uv run python tests/perl_t/run_suite.py --quiet --list-failures` with `WASI_PERL_ARTIFACTS=build/artifacts`, the tarball verified by `00_download.sh`, and the build job's `build/artifacts/` and `build/target/lib/` (the harness reads generated files such as `Config.pm` from there) unpacked from the `artifact-set` upload. It runs on every pull request, not only those that change the build or the runner (007 decision 5).

**The oracle contract, as 006 built it.** `tests/oneliners/record_oracle.py --perl <native perl> --out <path>` runs every case of the corpus once with that perl and writes `oracle.json`. The build job calls it with `build/native-install/bin/perl`. On the test legs no native perl is the oracle (the runners' system perls never are): `WASI_PERL_ORACLE_JSON=<path>` names the file, and the harness compares each case with it instead. The file is UTF-8 JSON:

```json
{
  "format": 1,
  "perl": "5.42.3",
  "corpus_sha256": "<hex: the harness's own hash of the corpus it recorded>",
  "cases": {
    "<stable case id>": {
      "exit_code": 0,
      "stdout": "<base64>",
      "stderr": "<base64>",
      "files": {"<path relative to the case's work folder>": "<base64 of its bytes after the run>"}
    }
  }
}
```

`perl` is native perl's `$^V` without the `v`. Bytes are base64 so no byte is lost. As 006 built it, each stored result is the harness's normalised one (work-folder paths replaced), the same normalisation the guest's result gets, and a folder is its path with a trailing `/` and an empty value. A case missing from the file, a `format` other than 1, or a `corpus_sha256` that differs from the checkout's corpus fails the test; it does not skip. When both `WASI_PERL_ORACLE` (a native perl) and `WASI_PERL_ORACLE_JSON` are set, the native perl wins.

**Left to the integration.** Version 0.1.0 in `src/wasi_perl/__init__.py` (004's file; its draft already says `0.1.0`), the `CHANGELOG.md` 0.1.0 section that `github-release` reads, the wheel's size (recorded by `build` in its step summary), and a `.gitattributes` if any fixture must stay byte-exact for clones outside CI (the `test` legs turn `core.autocrlf` off before checkout).
