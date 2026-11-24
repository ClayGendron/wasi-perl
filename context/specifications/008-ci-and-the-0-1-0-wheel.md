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

### Draft, 2026-11-24 (written before 001/002, 004 and 006 landed on `dev`)

**What is written.**

- `scripts/assemble_artifacts.py` (stdlib only). It verifies `build/artifacts/` before copying anything: every line of `SHA256SUMS` (which must hash `perl.wasm` and `MANIFEST`), every line of `MANIFEST`, no file under `lib/` or `dev/` missing from `MANIFEST`, no symbolic link, `dev/null` empty. It copies the set and Perl's `Artistic` and `Copying` (from `build/target/`, else from the pinned tarball after its hash is checked against `downloads/SHA256SUMS`) into a staging folder, verifies the copy again, then renames it to `src/wasi_perl/artifacts/`. A refusal is one line on stderr and exit 1.
- `.github/workflows/ci.yml`: `lint`, `build`, `test` (four systems), `perl-t`, on pull requests into `dev`, pushes to `dev`, dispatch, and `workflow_call`. Every action is pinned by commit SHA, every `run` is `bash`, `permissions: contents: read`, one concurrency group per pull request or ref (only pull requests cancel).
- `.github/workflows/release.yml`: on a `v*` tag and dispatch. It calls `ci.yml`, then, on a pushed tag only: `attest` (the tag must match the wheel's version and the five assets must be exactly the wheel, the sdist, `perl.wasm`, `MANIFEST`, `SHA256SUMS`; `actions/attest` signs them, and `gh attestation verify` checks each against `release.yml` at the tag), `github-release` (`contents: write`; notes are the version's section of `CHANGELOG.md`), and `publish` (environment `pypi`, `id-token: write`, the wheel and the sdist only).

**One deviation from decision 3, for the owner to confirm.** `publish` uploads with `uv publish --no-config --trusted-publishing always`, as wasi-python's `release.yml` does (READ), not with `pypa/gh-action-pypi-publish`. It is the same trusted-publishing exchange and needs the same publisher registration; it saves a pinned Docker action. Swapping back is one step.

**Checked here.** EXECUTED in the 008 worktree:

- `build/tools/actionlint/actionlint -shellcheck=<shellcheck 0.10.0 from shellcheck-py==0.10.0.1>` → exit 0, no findings, on both workflows. actionlint 1.7.12, `actionlint_1.7.12_linux_amd64.tar.gz` SHA-256 `8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8`, matching the release's own checksums file.
- `uv run --no-project --with pyyaml python -c 'yaml.safe_load(...)'` loads both files.
- `ruff check` and `ruff format --check` (line length 100, rules E,F,I,UP,B,TID252, as 004's `pyproject.toml` sets them) on `scripts/assemble_artifacts.py` → clean.
- `assemble_artifacts.py` on a hand-made set in the scratchpad: assembles; refuses a changed library file, an unlisted library file, a changed `perl.wasm`, a missing tarball; reads the licences out of a pinned `.tar.xz` when `build/target/` is absent.
- With 004's in-progress `pyproject.toml` (hatchling, `artifacts = ["src/wasi_perl/artifacts"]`) and that hand-made set: `uv build` gives `wasi_perl-0.1.0-py3-none-any.whl` and `wasi_perl-0.1.0.tar.gz`, both holding all eight artifact files including the empty `dev/null`; the wheel-content check in `build` passes. The `test` leg's install commands (`uv venv`, `uv pip install <wheel>`, `uv export --only-dev --no-emit-project --no-hashes`, `uv pip install -r`) run, and with `src/` moved away `wasi_perl` imports from the venv's `site-packages`.

**Untested until it runs on GitHub.** Everything that needs a real build or a runner: `scripts/build.sh` on ubuntu-24.04, the downloads cache, the oracle recording, the four `test` legs (arm64 Linux, macOS, Windows paths and `uv venv` there), `perl-t`, the reusable-workflow call, attestation, the release and the upload. No run id exists yet; acceptance 1 is open.

**Interfaces this draft codes against.** Each is someone else's; the orchestrator reconciles them.

1. `scripts/build.sh` builds everything into `build/artifacts/` and is executable; `scripts/00_download.sh` alone verifies (and unpacks tools); `WASI_PERL_DOWNLOAD_CACHE` names a folder of pinned files (001, matching its draft). Build logs land in `build/logs/`.
2. `build/artifacts/MANIFEST` is `<sha256>  <path>` lines with paths relative to `build/artifacts/` (so `lib/perl5/5.42/strict.pm`, `dev/null`), covering every file under `lib/` and `dev/`; it may also list `perl.wasm`. `SHA256SUMS` is the same format over `perl.wasm` and `MANIFEST` (002).
3. `build/target/` holds the unpacked tarball, so `build/target/Artistic` and `Copying` exist after a build; native perl is `build/native-install/bin/perl` (001).
4. The package finds its artifacts at `src/wasi_perl/artifacts/` (installed: `wasi_perl/artifacts/`), including the extra files `Artistic` and `Copying` at its top, which its own checks must tolerate. `wasi_perl.run(["-e", 'print "hello\n"'])` with no other argument grants the library and `/dev` and returns an object with `exit_code` and `stdout` (bytes) (004).
5. `uv.lock` is committed and the `dev` group holds pytest (004, as drafted). The wheel is built by plain `uv build` (its wheel is built from the sdist, so the sdist must carry the artifacts, which 004's `artifacts =` setting does).
6. Test folders run on the four systems: `tests/runner` (004), `tests/escapes` (005), `tests/build` (001 to 003: each check runs through `run()` or skips when there is no `build/`), `tests/oneliners` (006). A missing folder makes the leg red, by design. No conftest may need `src/` on the path; tests run with `--import-mode=importlib` from the checkout with `src/` deleted.
7. `tests/perl_t/run_suite.py` (007) runs with `uv run`, reads the pristine `t/` and `lib/` from `build/target/` (the `perl-t` job unpacks the verified tarball there, not the build job's tree), uses the package's assembled artifacts, and writes `build/perl_t/results.json`. It runs on every pull request, not only those that change the build or the runner (007 decision 5); a path filter can come later.

**The oracle contract, for 006.** `tests/oneliners/record_oracle.py --perl <native perl> --out <path>` runs every case of the corpus once with that perl and writes `oracle.json`. The build job calls it with `build/native-install/bin/perl`. On the test legs no native perl is the oracle (the runners' system perls never are): `WASI_PERL_ORACLE_JSON=<path>` names the file, and the harness compares each case with it instead. The file is UTF-8 JSON:

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

`perl` is native perl's `$^V` without the `v`. Bytes are base64 so no byte is lost; any normalisation (for example of the program name in a message) is the harness's, applied to both sides at compare time, never stored. A case missing from the file, a `format` other than 1, or a `corpus_sha256` that differs from the checkout's corpus fails the test; it does not skip. When both `WASI_PERL_ORACLE` (a native perl) and `WASI_PERL_ORACLE_JSON` are set, the native perl wins.

**Left to the integration.** Version 0.1.0 in `src/wasi_perl/__init__.py` (004's file; its draft already says `0.1.0`), the `CHANGELOG.md` 0.1.0 section that `github-release` reads, the wheel's size (recorded by `build` in its step summary), and a `.gitattributes` if any fixture must stay byte-exact for clones outside CI (the `test` legs turn `core.autocrlf` off before checkout).
