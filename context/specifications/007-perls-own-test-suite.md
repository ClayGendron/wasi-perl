# 007 — Perl's own `t/` suite under the runtime

- **Status:** pre-agreed by the owner 2026-11-24 (the roadmap); written by the orchestrator.
- **Owner:** Clay Gendron
- **Kind:** the judge (`tests/perl_t/`), a CI job.
- **Depends on:** 002 (modules), 003 (refusals), 004 (the runner).
- **Research it stands on:** Perl's `t/TEST` and `t/harness` (READ): each `.t` file is a separate `perl` process printing TAP; `t/test.pl`'s `runperl` and `fresh_perl` start a child perl through `` `…` `` or `system`, which the sandbox refuses (003). 617 `.t` files under `t/` in 5.42.3 (EXECUTED, `find t -name '*.t' | wc -l`).

## The problem

The owner's bar is that Perl's own suite passes, apart from a listed set of exclusions with a reason for each. Perl's harness cannot run inside the guest, because it starts a process per file. So the host runs each file as its own guest.

## Decisions

1. **The host is the harness.** `tests/perl_t/run_suite.py` copies the pristine `t/` and `lib/` of the pinned tarball (from `build/target/`, unpatched files only) into a fresh temporary folder, grants it writable at `/perl`, and runs each `t/**/*.t` with `run(["-I../lib", "-I.", file], cwd="/perl/t", deadline_s=…, fuel=None)`, one guest per file, several at once on the host's cores. Tests write their scratch files beside themselves, which is why the copy is writable and fresh per run.
2. **A file passes** when its guest exits 0 and its TAP has a plan, no `not ok` (a `# TODO` is not a failure), and as many results as the plan says, or a `1..0 # skip` with a reason. The harness parses TAP itself; no CPAN module.
3. **Exclusions are a file, each with a reason.** `tests/perl_t/exclusions.toml` maps a test file to one of a few reasons, each written once at the top of the file:
   - `subprocess`: the file starts a child perl or a command (`runperl`, `fresh_perl`, `system`, pipes), which the sandbox refuses by design (003);
   - `signals`: it needs a signal or `alarm`;
   - `threads`: it needs threads, which the build leaves out;
   - `porting`: it checks Perl's source tree or git (`t/porting/`), not the interpreter;
   - `dynamic-loading`: it needs `DynaLoader` to load a shared object;
   - `platform`: a WASI difference named in the entry's own `note` (for example, no users or groups, no `chmod` bits);
   - `bug`: a defect of this build, with the entry's `note` saying what fails. A `bug` entry is a gap, not a design choice, and the landing notes count them.
   A file not in the list must pass. A listed file that passes is reported, so a stale exclusion is seen and removed. Partial exclusions are not used: a file is in or out.
4. **The scoreboard.** The run writes `build/perl_t/results.json` and prints one line: files run, passed, excluded by reason, failed. `tests/perl_t/test_suite.py` (marked `slow`) fails if any non-excluded file fails or any excluded file passes.
5. **Where it runs.** Locally on demand, and in CI on Linux x86-64 on every pull request into `dev` that changes the build or the runner (008).

## Acceptance

1. A full run, EXECUTED, with its scoreboard line in the landing notes: zero failures outside the exclusions.
2. Every exclusion has a reason; `bug` entries are listed in the landing notes.
3. A fresh agent's review lands.

## Landing notes

(Written when built.)
