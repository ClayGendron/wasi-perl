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

- **Order: "1–4 first."** These are 013–016 below. They are drafts for the owner to agree before they are built.
- **The preload snapshot: "Install-time images only."** It may be built only as an image the embedder makes itself, so that its fixed hash seed is never published. It is specified after the leaner `@INC`.
- **wasmtime-py's 56 µs file call: "No, clone the library and determine the root cause first. Another option is we could build our own rust crate that compiles wasmtime."** That root-cause study is `research/2026-11-24-wasmtime-py-file-call-cost.md`, in progress.
- **`NO_TAINT_SUPPORT`: "Keep -T."** It is not built.

| Spec | What it delivers | After |
|---|---|---|
| 013 | Seeds from the host's randomness: a guest-side `/dev/urandom` and `/dev/random` from WASI `random_get`, so the hash seed and `rand` are no longer guessable from the clock | 009, 010 |
| 014 | The runner's fixed cost: version once, `SHA256SUMS` per stamp, rule inputs only when needed, one `InstancePre`, the `.cwasm` mapped, the version from `dist-info`, and four engine pins | 004, 012 |
| 015 | The runner's checks kept across calls: runtime-file identities per folder stamp, and a tree record in the module cache | 014 |
| 016 | The guest's reads and writes: a 64 KiB PerlIO buffer, a SIMD `memchr`, and `isatty` answered for files the table looked at | 009, 010, 013 |

Proposed next, not yet written:
- mounts, wasi-python's protocol adapted (synthesis spec 5);
- JSON and CSV in XS (6);
- a leaner `@INC` (7);
- docs for agents (8);
- `wasm-opt -O3` (9);
- the install-time preload image (10).
