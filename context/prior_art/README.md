# Prior art

Shallow clones, git-ignored, read-only. Studied, never copied.

| Project | Where from | Commit | Licence | What we study |
|---|---|---|---|---|
| zeroperl | https://github.com/uswriting/zeroperl | c28db5d | MIT (checked 2026-11-24, `LICENSE`) | where Perl's build and runtime break under WASI; notes in `../research/2026-11-24-what-zeroperl-teaches.md` |
| wasi-python | https://github.com/ClayGendron/wasi-python | (sister project, same owner) | Apache-2.0 | the runner, the command line, the module cache, the sandbox tests, the way of working |
| wasi-libc | https://github.com/WebAssembly/wasi-libc | 2e6fb9d (the commit wasi-sdk 34 names) | Apache-2.0 WITH LLVM-exception, Apache-2.0, MIT (checked 2026-11-24, `LICENSE`) | which `__wasi_*` functions take or return a descriptor, the lazy preopen scan, `close`, `fcntl`, `dup`, `pselect`; notes in specification 009's landing notes |
| monty | https://github.com/pydantic/monty | bdf4f6e (main, 1.7.0-beta.1; timed: pydantic-monty 1.6.0 from PyPI) | MIT, with selected CPython stdlib modules under Python-2.0.1 in `crates/monty/stdlib` (checked 2026-11-24, `LICENSE`, `crates/monty/Cargo.toml`) | a Rust Python-subset sandbox for LLM code: its start-up, pool, host calls and snapshots; notes in `../research/2026-11-24-wasi-perl-against-monty.md` |
