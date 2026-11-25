# Roadmap to 0.1.0

The owner's plan. Each row is one specification; rows on the same line of the "after" column can be built in parallel. The clock: started 2026-11-24 02:47 UTC, eight hours.

| Spec | What it delivers | After |
|---|---|---|
| 001 | Pinned toolchain (wasi-sdk, Perl 5.42 tarball, wasmtime CLI); native perl 5.42 and host miniperl; hand-written `config.sh`; a `perl.wasm` that prints hello under wasmtime with exceptions on | — |
| 002 | The core XS modules linked statically; the pure-Perl library laid out as one read-only folder; `die`/`eval` through exception handling proved by tests | 001 |
| 003 | Processes and signals refused clearly (`fork`, `exec`, `system`, backticks, pipe opens, `%SIG`) | 001 |
| 004 | The Python package: `run(argv, grants=[Grant(host, guest)])` → `Completed`, the limits and their defaults, the module cache with its safety checks, `wasi-perl run -- …` printing JSON | 001 (uses any `perl.wasm`) |
| 005 | Sandbox escape tests ported from wasi-python: reads outside a grant, `../` climbs, links out, writes to a read-only grant | 004 |
| 006 | The one-liner oracle: agent-style `-ne`, `-pi -e`, `-lane`, `-0777` cases compared with native perl 5.42 | 002, 004 |
| 007 | Perl's own `t/` suite under the runtime, with `tests/perl_t/exclusions.toml` giving each exclusion's reason | 002, 003 |
| 009 | Descriptors the guest can duplicate: `dup`, `dup2`, `F_DUPFD` through a guest-side descriptor table, so `>&STDOUT` and Test::More work | 002, 003 |
| 008 | CI and the 0.1.0 wheel: build the artifacts on Linux, build one `py3-none-any` wheel, install and test it on Linux x86-64 and arm64, macOS arm64 and Windows x86-64 | 004, 005, 006 |

The wheel is one `py3-none-any` wheel: `perl.wasm` and the library are the same bytes everywhere (the owner, 2026-11-24). The process in `AGENTS.md` is the lighter one (the owner, 2026-11-24).

PyPI publishing (spec 008) is by trusted publishing from `.github/workflows/release.yml`, job environment `pypi`, as wasi-python does; the owner registers that publisher on PyPI (asked 2026-11-24).

# After 0.1.0: speed under vfs

The research phase of 2026-11-24 (`research/2026-11-24-perf-synthesis.md`) proposes ten specifications. The owner's answers on 2026-11-24:

- **Order: "1–4 first."** These are 013–016 below, agreed by the owner the same day ("Agree all four (Recommended)"). Then: "You can do all 10 actually, but do not change the API around perl to be different than wasi python, unless wasi python could adopt the same changes. We are going to eventually unify the shared components." That agrees to building the items that became 017–022. Their texts were written on branch `batch/specs-017-022` the same day, and each is proposed, not yet agreed by the owner: each Status line names the choices the owner is still to agree.
- **The preload snapshot: "Install-time images only."** It may be built only as an image the embedder makes itself, so that its fixed hash seed is never published. It is specified after the leaner `@INC`.
- **wasmtime-py's 56 µs file call: "No, clone the library and determine the root cause first. Another option is we could build our own rust crate that compiles wasmtime."** The root cause is found (`research/2026-11-24-wasmtime-py-file-call-cost.md`): wasmtime's C API never sets `allow_blocking_current_thread`, so every file call is handed to another thread. A 19-line C API patch brings the calls to the CLI's speed. The comparison with Monty (`research/2026-11-24-wasi-perl-against-monty.md`) makes it the largest single lever measured there: 100 small files 36–39 → 2.3–3.0 ms. That makes wasi-perl's host reads 2–10× faster than Monty's pool with a mount, which pays a pipe round trip per OS call. Against the `monty` CLI, which serves its mount in-process, they take 0.52–1.12× its time. **Upstream: "Record this, I will do the issue filing later."** (owner, 2026-11-24). The issue and PR texts are drafted in `upstream/wasmtime-allow-blocking-current-thread.md`, for the owner to file. wasi-perl takes the flag in a specification of its own once a wasmtime-py release carries it.
- **`NO_TAINT_SUPPORT`: "Keep -T."** It is not built.

| Spec | What it delivers | After |
|---|---|---|
| 013 | Seeds from the host's randomness: a guest-side `/dev/urandom` and `/dev/random` from WASI `random_get`, so the hash seed and `rand` are no longer guessable from the clock | 009, 010 |
| 014 | The runner's fixed cost: version once, `SHA256SUMS` per stamp, rule inputs only when needed, one `InstancePre`, the `.cwasm` mapped, the version from `dist-info`, and four engine pins | 004, 012 |
| 015 | The runner's checks kept across calls: runtime-file identities per folder stamp (its tree record in the module cache was dropped by the owner) | 014 |
| 016 | The guest's reads and writes: a 64 KiB PerlIO buffer, a SIMD `memchr`, and `isatty` answered for files the table looked at | 009, 010, 013 |

Then, under "You can do all 10":

| Spec | What it delivers | After |
|---|---|---|
| 017 | Mounts: files served from the embedder's code, with wasi-python 0.1.6's API and protocol unchanged | 004, 005, 009, 010, 011, 012; before or after 014 (017's Decision 14 says what each order means) |
| 018 | JSON, CSV and diff: Cpanel::JSON::XS and Text::CSV_XS linked from pinned CPAN tarballs; JSON::MaybeXS, Text::CSV and Algorithm::Diff in the library | 002, 016 |
| 019 | The library index: lookups of missing library files answered in the guest; and the build's order after the library, with a new `scripts/40_link.sh` that links last and writes `SHA256SUMS` last | 013, 016, 018 |
| 020 | Docs for agents: the fast way to write common programs, each example a check | 018 |
| 021 | `wasm-opt -O3` after the last link, in 019's `40_link.sh`; over `perl-reactor.wasm` too once 022 is built | 016, 019; before or after 022 |
| 022 | A preload image the embedder makes at install time, with wasi-python's snapshot API | 013, 014, 017, 019; before or after 021 |

Open items from the review of 016 (`reviews/016-the-guests-reads-and-writes-pr-2026-11-24.md`, M2 and L5), for a later change:

- **A skip in `tests/build` should fail CI.** The checks on the built `perl.wasm` skip when a tool is missing: the wasmtime command line at its one pinned path, `llvm-objdump`, native perl or wasmtime-py. CI's build job passes on skips, so a wasmtime bump that leaves `tests/build/_build_helpers.py` behind would skip 016's call-count checks unseen. Either a step after the build job's checks reads a JUnit file and fails on any skip, as the wheel-test job does for the guest checks, or `_need` in `tests/build/conftest.py` fails instead of skipping when `CI` is set.
- **`src/wasi_perl/_engines.py` should state `wasm_simd = True`** beside `wasm_exceptions`. Since 016 the module needs SIMD128 to load at all; wasmtime enables it by default today, and the engine states only the performance defaults.

Open items from specification 015 and its review (`reviews/015-the-runners-checks-kept-across-calls-pr-2026-11-24.md`, H1), for a later change:

- **014's checks skip in CI.** Most of `tests/runner/test_fixed_cost.py` runs the stand-in guest, which needs wasi-sdk, and no CI job that runs `tests/runner` has it. So those checks are skipped there, unseen. 015 moved its own checks to the set `$WASI_PERL_ARTIFACTS` names, as the wheel jobs set it, and added them to the wheel job's gate on skips; 014's can follow the same way.
- **A shared-mapping write within one process.** The per-stamp `digest()` of `perl.wasm` and `MANIFEST` (0.1.0) and the per-stamp parse of `SHA256SUMS` (014) are used again while the file's stamp is unchanged. A write through a shared mapping can change a file without moving its stamp (`research/2026-11-24-the-runners-fixed-cost-measured.md` §8.4), so a process's later calls would not see it. It is the owner's to decide.

Open items from the review of 020 (`reviews/020-docs-for-agents-pr-2026-11-24.md`, L5), for a later change:

- **`test_native_is_deterministic` passes when native perl is broken.** Two runs that fail the same way are equal, and `find_oracle` checks only `print $^V`, which needs no library. A native perl that cannot find its own library therefore passes every determinism check. The check should also require exit 0 and the expected output, or `find_oracle` should run a program that loads core modules, such as `-Mstrict -MPOSIX -e 1`, and refuse a perl where that fails.

Open items from the review of 021 (`reviews/021-wasm-opt-after-the-link-pr-2026-11-24.md`, L3), for the owner's later choice about DWARF:

- **`--strip-dwarf -O3 -g`.** It keeps a name section of 245,961 bytes, so a trap's backtrace names its functions, at the same fuel as the shipped `-O3` (`hello`, `grep_log`, `eval_die`, `sort`: +0.00% each), and its module is 8,360,960 bytes, 314 KB smaller than today's, since the 534,499 bytes of `.debug_*` sections go (EXECUTED in the review). `-O3 -g` with the DWARF kept loses inlining instead (+0.55% to +1.07% fuel). Spec 021's Decision 5 keeps the DWARF and gives no `-g`; whether to strip the DWARF, and then whether to keep the names, is the owner's.

Open items from the review of 022 (`reviews/022-a-preload-image-made-at-install-time-pr-2026-11-25.md`, H1 and L2), for a later change:

- **Re-read the locale, `PERL_UNICODE` and `PERLIO` set-up in the reactor's `run`, so those calls keep the image.** Today a call whose `env` names one of them, `LC_ALL=C` among them, runs `perl.wasm` (spec 022, amendment A15). Redoing `Perl_init_i18nl10n`'s locale and `PL_unicode` per call, and the default layers for the standard handles, would let those calls start from the image too.
- **`SOURCE_DATE_EPOCH` for the build.** `perl.c`'s "Compiled at" comes from `__DATE__` and `__TIME__`, so every recompile of `perl.c` changes `perl.wasm` and `perl-reactor.wasm`. Setting `SOURCE_DATE_EPOCH` in the build steps, or a recipe define of `PERL_BUILD_DATE`, would make a full rebuild reproducible.

Open items from the CI of 019 (PR #29), for a later change:

- **An abort at interpreter exit on ubuntu-24.04-arm, not one-time.** The ARM wheel-test job's first step that runs perl exited 134 twice, on `f47b716` (job 138774884353) and on dev's `50e0298` (job 138792311744), after `run()` had returned its result: wasmtime-wasi `tokio-rt-worker` threads panicked with "panic in a function that cannot unwind". Over every run of `ci.yml`, the step failed in 2 of 58 ARM runs, both carrying 019; 0 of 45 before it (EXECUTED: the GitHub API, 2026-11-24). It is not 019's: on ARM, diagnostic pull request #31 saw the pre-019 wheel abort the same way (1 of 300 warm runs), and dev's without the runner's atexit hook 6 of 200 (EXECUTED: run 45510880573). The cause, reproduced on x86-64 under Python 3.11: wasmtime-py's free of each custom output stream runs in Python on a tokio worker after the store is closed; once Python is finalizing, CPython 3.11 ends that worker with `pthread_exit`, whose unwind aborts in Rust's thread entry. Specification 023, agreed and built (PR #33), gives wasmtime no Python free; the evidence is there. Watch the next ARM runs.
- **The guest's output written on its own thread.** After 023, a guest's write still runs Python (`_outputs.write`) on a wasmtime-wasi worker. A `run()` still writing on a daemon thread when Python 3.11 finalizes can still panic there (023, Decision 6; its review, M2). wasi-python's pipe capture, which wasmtime writes as a file on the guest's own thread, would close that case and make output cheaper. It is a specification of its own.
- **023's fix for wasi-python.** wasi-python's `_CallbackCapture`, its only capture on Windows, still gives wasmtime a Python free (`_output_free`), with the same hazard. Its `close` already drops the keys, so the change there is a NULL free. Take it to wasi-python with the checks that carry over (023, "For wasi-python"), for the unification of the shared components; neither API changes.
