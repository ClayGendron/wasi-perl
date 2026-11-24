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
