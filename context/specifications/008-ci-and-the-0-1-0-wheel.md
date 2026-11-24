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

(Written when built.)
