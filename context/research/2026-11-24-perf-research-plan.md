# A research phase on wasi-perl's speed, for use under vfs

- **Status:** a plan, 2026-11-24. The owner asked for it: "start a research phase where you use multiple sub agents to squeeze out every bit of performance out of wasi-perl … This package is designed to be used with vfs, so ensure strategies would align with using wasi perl as a code interpreter to run on the vfs file system APIs."
- **Stands on:** `2026-11-24-wasi-perl-against-wasi-python-on-speed.md`, the baseline.
- **What it produces:** six study memos, then one synthesis memo that ranks every lever and proposes specifications. Nothing in `src/`, `recipes/` or `scripts/` changes in this phase. Prototypes live on study branches and are evidence, not merges.

## What vfs needs, as far as is known at the start (READ)

- vfs (github.com/ClayGendron/vfs) gives an agent one namespace of files backed by the embedder's storage, such as database rows. It runs agent programs inside WebAssembly guests that reach nothing but vfs (vfs `context/specifications/active/172-the-wasi-python-runtime/spec.md`, and 169 and 169b for the host).
- The guests see vfs files through wasi-python's mount protocol (`wasi_python/mounts.py`):
  - A provider answers `stat`, `list` and `read`, plus `write`, `mkdir`, `remove`, `rmdir` and `rename`, on normalized paths.
  - The runner owns descriptors and offsets, and stages a file's bytes whole.
- wasi-perl has only host-folder grants today. A mount layer is a precondition of vfs use, and its cost is part of this phase.
- The call pattern is many short programs, each a fresh instance, over small-to-medium files, with captured output, under budgets. So the costs that matter most are, in order (INFERRED):
  1. the fixed cost per call;
  2. the cost per file opened and read through a provider;
  3. the guest's speed on line-oriented text.

  A process's first call and the compile matter once per host process.

## The studies

| study | branch | question |
|---|---|---|
| A. The runner's overhead | `research/perf-runner` | What does each call and each process cost on the host side, and how near 1 ms can a bare call get without weakening any sandbox check? |
| B. Code generation | `research/perf-codegen` | Which compiler, linker, `wasm-opt` and wasmtime settings make the guest faster or the module cheaper to compile and load? |
| C. The interpreter's configuration and hot paths | `research/perf-interp` | Which Perl build choices (`config.sh`, ccflags, malloc, hash function, PerlIO buffers) and which hot functions under a guest profile cost the most on WebAssembly? |
| D. Startup and snapshots | `research/perf-startup` | Where do Perl's startup and module loads spend their instructions, and can a snapshot of a started perl, or one with modules preloaded, keep per-call semantics and pay? |
| E. The vfs I/O path | `research/perf-vfs-io` | How should wasi-perl serve provider-backed files, and what do the WASI calls a one-liner makes per file cost when a provider answers them? |
| F. Libraries for agent workloads | `research/perf-libs` | Where is Perl slow on what agents do over vfs (JSON, CSV, sort, regex), and which statically linked modules would close it? |

## The rules every study keeps

- **Counts first.** Fuel (guest instructions), WASI calls, provider calls and bytes are deterministic, and they are the primary evidence. Wall time is secondary.
- **One machine, shared.** The container has 4 vCPUs. Any build and any wall-clock timing holds `flock /home/user/wasi-perl-wt/machine.lock`, so timings are not disturbed and builds do not starve one another.
- **No lever may weaken the sandbox.** A change that removes a check must show the check's guarantee is kept another way.
- **Every claim is labelled** EXECUTED, READ or INFERRED, with its command beside it, as `AGENTS.md` asks.
- **Each candidate is ranked** by its measured or estimated gain, its cost to build, its risk, its effect on security and its relevance to vfs.
- **Two strikes, then write it down.**
