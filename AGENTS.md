# Project guidance for agents

## What this is

wasi-perl is Perl 5.42 compiled to `wasm32-wasip1` as one static binary, with the core XS modules linked in, and the small `wasi_perl` wheel that carries it with the pure-Perl core library. It runs agent-generated Perl, above all `perl -ne`, `-pi -e`, `-lane` and `-0777` one-liners over granted files, inside a sandbox the host fully controls. The product is the artifact set (`perl.wasm`, the library folder, `MANIFEST`, `SHA256SUMS`) and the wheel. Read `context/constitution.md` first: it is the mission, the goal and the hard constraints, and it governs this file. Then `context/roadmap.md` and the specification you are on.

The sister project, wasi-python (github.com/ClayGendron/wasi-python), is the guide for everything that is not Perl-specific: the runner's API, the command line, the module cache and its checks, the safety defaults and this way of working. Where this file is silent, do what wasi-python does.

## Rules that bind all work

- **Specification first.** Nothing is built that no specification under `context/specifications/` describes. A specification decides every choice it needs and says why; it leaves no open question. The owner agrees a specification before it is built.
- **Upstream patches are few, recorded and temporary.** An adaptation is a recipe file under `recipes/` first (the hand-written `config.sh`, a compiler wrapper, a stand-in header, a stub C file). An unavoidable source change is a patch file under `recipes/patches/perl-<version>/`, reviewed, named in `CHANGELOG.md`, and applied by the build after the tarball's hash is checked. Nobody edits a tree under `build/` by hand.
- **Every input is pinned by URL and SHA-256** in `downloads/SHA256SUMS` and verified before use (`scripts/00_download.sh`). Downloads are untrusted: they unpack under `downloads/` and `build/`, never into the system, and run only as build tools or under wasmtime.
- **One target: `wasm32-wasip1`.** No Emscripten, no JavaScript, no Asyncify. `die` and `eval` unwind through setjmp/longjmp lowered to WebAssembly exception handling, so the host must support exceptions, as wasi-python's does. What cannot be done inside WASI is left out and documented.
- **The guest reaches the host only through WASI preview1.** No custom import. No host file is visible and no network is reachable unless the embedder grants it.
- **Refusals are clear, not crashes.** `fork`, `exec`, `system`, backticks, pipe opens and `%SIG` handlers fail with an error that says "not available in the wasi-perl sandbox"; they never trap.
- **uv only.** `uv run …`, `uv sync`; never a bare `python` or `pip`. `ruff check`, `ruff format --check` and `pytest` stay green on `src/` and `tests/`.
- **Prior art is studied, never copied.** `context/prior_art/` holds shallow clones, git-ignored and read-only. Read, grep, cite, describe in your own words; every line of code here is ours. Check the licence right after cloning and record it in `context/prior_art/README.md`. zeroperl (MIT) is studied there; its lessons are in `context/research/`. wasi-python is the sister project, by the same owner and under the same licence: its code may be adapted here, trimmed to what this project needs, and each adapted file says so in its module docstring.
- **Tests count operations, never wall-clock.** Exit codes, bytes and equality with native perl 5.42. Speed numbers belong in a research memo.
- **Artifacts never live in git.** `*.wasm`, `*.cwasm`, `*.zip`, tarballs and `build/` are ignored; hashes and sizes are recorded in `MANIFEST` or a memo.
- **Claims carry their evidence.** A number in a document has the command that produced it beside it, labelled EXECUTED, READ or INFERRED. A result that was not run is not reported as run.
- **Code speaks for itself.** A file under `src/`, `tests/`, `scripts/` or `recipes/` names no specification or review number in its comments; it gives the reason in words. A `context/` document may cite code.
- **All project knowledge lives in this repo**, under `context/` (the agents' memory) and `docs/` (what we tell users). Never save a memory outside the repository.
- **Anything waiting on the owner is asked with the AskUserQuestion tool**, with concrete options and the recommended one first.

## Working autonomously

Two long-lived branches. `dev` is the integration branch; it changes only by the merge of a pull request. `main` holds released states; only the owner moves it.

1. **Orient from the tests, not from memory.** Start with `uv run pytest -m "not slow"` and read the failures. Then read the specification you are on.
2. **Write the check before the code.** A new behaviour gets a test first, seen failing. A new failure mode, once seen, becomes a test.
3. **Branch from `dev`.** `spec/NNN-<slug>`, one to three specifications per branch, in a worktree under `../wasi-perl-wt/<name>` if several agents build at once. The orchestrator makes and clears worktrees.
4. **Open a pull request into `dev`** from the template in `.github/PULL_REQUEST_TEMPLATE.md`, with `CHANGELOG.md` lines under "Unreleased".
5. **One review by someone who did not write the change.** A fresh agent reads the specification, the diff and the test run, and writes `context/reviews/NNN-<slug>-pr-YYYY-MM-DD.md`: a `Reviewer:` line, ranked findings, and a verdict that begins "Lands" or "Does not land". Findings are fixed in one commit per round or answered in writing. The review file is the branch's last commit.
6. **Merge on green with "Lands".** The orchestrator merges into `dev`; then clears the worktree and the local branch.

These hold at every step.

- **Never weaken a test to get a pass.** Do not edit a check, a golden output, or the exclusion list of Perl's own suite (`tests/perl_t/exclusions.toml`) to turn a run green. An exclusion is added with its reason in its own commit, and the review sees it.
- **Two strikes, then write it down.** After two failed attempts on one hypothesis, stop, record where you stood and why in the specification or a memo, and move to work that does not depend on it.
- **A commit's trailer names the model that wrote it.**
- **Commit only what you mean.** Read `git diff --cached --stat` before each commit.
- **Keep output short.** Build logs go to files under `build/logs/`; a failure is one line with its reason.

## Explaining things

Short sentences. One idea each. Define a term of art the first time it appears. In the README and `docs/`, say the fact and stop: what the thing is and what it does, then a link.
