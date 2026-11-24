# wasi-perl against Monty, on speed and on the wall

- **Status:** research, 2026-11-24. A comparison the owner asked for. It changes no code. Section 8 names the gaps and where each stands among the specifications. Amended the same day for the review `../reviews/research-monty-comparison-pr-2026-11-24.md` (H1, M1–M4, L1–L5), section 10.
- **Owner:** Clay Gendron
- **Researcher:** an agent (Claude Opus 5.5), on branch `research/monty-comparison`.
- **The question, in the owner's words:** "Can we compare to the speed of something like Monty? Have a sub agent study that project and find out if it is faster/more performant than our wasi perl implementation. If so, are there gaps we can close."
- **Stands on:**
  - the baseline, `2026-11-24-wasi-perl-against-wasi-python-on-speed.md`;
  - the synthesis, `2026-11-24-perf-synthesis.md`;
  - `2026-11-24-the-runners-fixed-cost-measured.md` (specs 014 and 015);
  - `2026-11-24-wasmtime-py-file-call-cost.md`;
  - spec 016's `2026-11-24-the-guests-reads-and-writes-measured.md`, on branch `spec/016-the-guests-reads-and-writes`.
- **Evidence:** `2026-11-24-wasi-perl-against-monty/`. Scripts:
  - `workloads.py`: the tasks, once per language;
  - `bench_perl.py`, `bench_monty.py`, `native.py`: the harnesses;
  - `hashprobe.py`: the library hash by file and as one file;
  - `measure.sh`, `counts.sh`: the runs;
  - `tables.py`: every table below, and the check that every runtime printed the same bytes.

  Raw results are in `results/`.

Labels: EXECUTED means run and seen. READ means read from a file, cited by path. INFERRED means the researcher's reasoning.

**How ranges are built.** Every timing cell is a range `a–b`: the lower and higher of the two sessions' medians (s1 and s2) of **one** harness, with one value when both sessions agree. Ratios are taken within a session, then ranged. A cell that mixes harnesses says so. `python3 tables.py` prints every table (EXECUTED).

## 1. The answer

**Monty is faster on most work. It is much faster on a call's fixed cost, and part of that lead is a lever we can copy. It is not faster on everything.**

- **A bare call** (§4.2):
  - Back to back, Monty takes 0.15–0.20 ms with its default reused worker, against wasi-perl's 1.9–2.1 ms.
  - With a pause before each call, as an agent's calls have, Monty with a **fresh worker process per session** takes 0.79–1.2 ms. wasi-perl takes 2.7–3.2 ms.
  - So even at our level of isolation Monty is about 3× faster (EXECUTED). It does the start-up work before the call arrives: the pool spawns the next worker as each session ends (READ: `crates/monty-pool/src/lib.rs:127-133`, `pool.rs:495-511`, `:562-602`).
  - Part of that can be done in wasi-perl too (§8, row 2).
- **A fresh process's first call** (§4.1):
  - Monty is ready about 4 ms after its import; wasi-perl takes 103–143 ms.
  - Spec 015 is built. It brings our first call to about 73 ms (READ: its landing notes).
  - Import to first answer would then be about 92–100 ms for wasi-perl, against Monty's 35–51 ms (INFERRED).
- **CPU-bound loops** (§4.3):
  - Monty's pool is 1.4–3.0× faster.
  - Natively, perl 5.42 and Monty are within about 1.5× of each other either way, in instructions.
  - On `fib25` and `loop`, native perl ties or beats Monty. There, Monty's lead is the price of the WebAssembly wall. On `strjoin` and `sort_ints`, Monty's interpreter is ahead natively too (EXECUTED).
- **String building with `+=`**: wasi-perl takes 16 ms. Monty's pool takes 5.8–7.0 s, 345–451× slower: a cliff (EXECUTED, §4.3).
- **Text** (§4.4):
  - On line-oriented one-liners, Monty's pool is 1.4–2.2× faster.
  - `re.sub` over a 10 MB string is a second Monty cliff: 5.8–8.5 s in its pool, against our 65–74 ms, 90–115× (EXECUTED).
- **JSON**: Monty's pool is 23–85× faster. Its `json` is Rust; ours is `JSON::PP`, pure Perl. Spec 018 is proposed, not yet agreed. It would close most of the gap (READ, §8).
- **Reading host files** (§4.6):
  - Today Monty's pool is 1.6–3.2× faster.
  - With wasmtime's `allow_blocking_current_thread` set on the host side, wasi-perl reads 100 small files in 2.3–3.0 ms, and one 10 MB file in 5.8–7.6 ms (EXECUTED).
  - That is 2–10× faster than **Monty's pool with a mount**, which pays a pipe round trip for every OS call.
  - It is level to 1.9× ahead of **the `monty` CLI**, which serves its mount in-process. The CLI takes 2.7–3.2 ms and 9.5–11.1 ms, a process start included.
- **Memory**: level. A trivial call needs 13.4 MiB of wasi-perl linear memory, and a fresh Monty worker peaks at 11.3–12.2 MiB (EXECUTED, §4.7).

**The gaps worth closing** (§8), largest first on this comparison:
1. **The file-call hand-off.** It is upstream's: a C API setter, then a wasmtime-py release. It is worth 7–16× on file-heavy calls.
2. **Start-up work done before the call** (new): take `Store.close` and the store's set-up off the caller's path. About 0.3–0.45 ms of a 1.3–1.9 ms call (INFERRED from the phase profile). It shares no state between calls and changes no API.
3. **JSON in XS, spec 018:** from 1.3 s to about 46 ms (INFERRED from study F).
4. **The guest's I/O, spec 016:** built, `grep_log` 113.6 → 61.3 ms (READ: its memo §1).
5. **The library check, as one file** (new): spec 015 leaves 43–47 ms of check in each first call. The bytes alone hash in 22 ms (EXECUTED).

What is not worth copying:
- Monty's 0.15 ms reuse of a worker across sessions. It weakens the wall (§7).
- Running perl natively in-process. It removes the wall.
- The CPU gap is the wall's price where native perl is level with Monty. Only `wasm-opt` (spec 021, −2.7%) is left there.

## 2. Which Monty, and what it is

**Identity (READ).** "Monty" is pydantic's Monty, `github.com/pydantic/monty`: "A secure Python sandbox, written in Rust, for code written by AI" (`README.md`). Nothing else by that name fits "a sandbox for agent-written code", so the request is not ambiguous.

- **Source read:** a shallow clone of `main` at `bdf4f6e` (2026-11-24, workspace version 1.7.0-beta.1), in `context/prior_art/monty`.
- **Licence:** MIT (`LICENSE`). The core crate is "MIT AND Python-2.0.1", because it vendors some CPython stdlib modules under `crates/monty/stdlib` (`crates/monty/Cargo.toml:5`). It is recorded in `context/prior_art/README.md`.
- **Timed:** pydantic-monty 1.6.0, the newest release, with two wheels it depends on: pydantic-monty-runtime 1.6.0 (the `monty` worker binary, 32.4 MB) and pydantic-monty-client 1.6.0 (the 11.7 MB `_monty` extension). They were installed by `uv pip install pydantic-monty` into a scratch venv, not pinned by hash.

  Afterwards, the three wheels were fetched from PyPI. Their SHA-256 matched PyPI's published digests, and the binary and extension inside them matched what had run, byte for byte (EXECUTED: `sha256sum`, and the files' SHA-256 inside each zip):

  | wheel | SHA-256 |
  |---|---|
  | `pydantic_monty-1.6.0-py3-none-any.whl` | `cf968bc9eccc93cf9806842a2513b815019fd5b10cb559821bfd1bedd5ae37d6` |
  | `pydantic_monty_client-1.6.0-cp313-cp313-manylinux_2_28_x86_64.whl` | `e7624944528bdd462fded093a78641e7f7fdc6acee1223c6110ab00263b2d6ef` |
  | `pydantic_monty_runtime-1.6.0-py3-none-manylinux_2_28_x86_64.whl` | `f0d7d9eacbd94c7be6912fc30fbd60283fe407e4428562fa535dbb3e6ae47461` |

  The worker binary ran natively on the host, and under valgrind. It did not run under wasmtime. `AGENTS.md` asks that downloads run only as build tools or under wasmtime. This was a research tool in a scratch venv, outside the build. It is recorded here so the owner can judge it.

**Design (READ, `crates/monty/src`).**

| part | what Monty does | where |
|---|---|---|
| parser | Ruff's Python parser (`ruff_python_parser`) | `crates/monty/Cargo.toml`, `parse.rs` |
| execution | a stack-based **bytecode VM**: an operand stack, frames that each own their instruction pointer, raw `Vec<u8>` opcodes. It replaced an earlier tree-walker | `bytecode/mod.rs:1-13`, `bytecode/vm/mod.rs:1-4`, `bytecode/op.rs:1-7` |
| values | a tagged `enum Value`: immediates (`None`, `Bool`, `Int(i64)`, `Float`, short inline strings, interned literals) and `Ref`s into one heap arena | `value.rs:50-110` |
| memory | the arena counts references, and a cycle collector marks "gray" candidates whose count dropped without reaching zero | `heap/mod.rs:71-97`, `:820-919` |
| standard library | written in Rust inside the binary: `re` is `fancy-regex`, `json` parses with `jiter`, about 30 modules in all | `modules/`, `docs/limitations/index.md` |
| the host | three opt-in doors: **external functions** (a name the code leaves undefined is looked up in `external_lookup`; the VM suspends, the host runs it, the VM resumes with the result); an **`os` callback**; **mounts** of host folders | `docs/security.md` |
| files | the interpreter does no file I/O. Each `open()` and each read is a one-shot OS call: the host opens, acts and closes. A file's whole content is buffered in the sandbox heap on its first read. Mounts confine paths with `cap_std` descriptors | `docs/limitations/open.md:12-27`, `:126-132`; `docs/security.md` ("Confinement is structural") |
| limits | `max_memory`, counted by a global allocator in the worker; feed and turn durations, checked at interpreter checkpoints; recursion depth; `max_suspensions`. **All are off unless set, except recursion depth and `max_suspensions` (1,000 host round trips per checkout).** The pool's wall-clock `request_timeout` defaults to none | `docs/resource-limits.md:51-61`, `docs/security.md:466-469` |
| snapshots | `dump()` serializes the whole interpreter with serde to CBOR: globals, heap, and at a suspension the paused call stack. It is "a few kilobytes", restorable in another process or more than once, because the sandbox holds no OS resource | `docs/snapshots.md`, `dump_format.rs` |
| process model | the Python and JavaScript APIs run code **only** in `monty` worker subprocesses from a pool. A checkout dedicates a worker to one REPL session. Workers are reused for up to 1,000 checkouts by default, then recycled. With `min_processes > 0`, a retired worker's replacement is spawned in the background, so the next session usually finds it ready. There is no in-process mode in Python; the Rust `monty` crate runs in-process | `_monty.pyi:714-733`; `crates/monty-pool/src/lib.rs:72-74`, `:127-133`; `pool.rs:495-511`, `:562-602`; `docs/security.md:507-511` |
| WebAssembly | a separate build: a `wasm32-wasip1` core module wrapped as a WASI 0.2 component, for browsers and Node | `crates/monty-wasm-runtime`, `Makefile:67` |

**The subset (READ, `docs/limitations/index.md`).**
- It has functions, closures, simple classes, comprehensions, `try`, `match`, f-strings and `async`/`await`.
- It has about 30 stdlib modules, each "often a small part" of CPython's.
- Rejected at parse time: generators (`yield`), `del`, metaclasses, decorators on methods, and exception groups.
- Also missing: user-defined exception classes, subclassing built-in types, `enum`, `io`, `struct`, `logging`, `string`, and any third-party package.
- `re` lacks `subn` and `bytes` patterns (`docs/limitations/re.md:13`, `:29`).

**Monty's own performance claims (READ).** All of them are about start latency. None in the docs compares the interpreter's speed with CPython's.
- They are in `docs/index.md` and `docs/alternatives.md`, made by `scripts/startup_performance.py` on an Apple M3 Max:
  - a new sandbox from an open pool, after the pool sat idle 1 s: 0.77 ms (median of 20, 1.0.0b3);
  - the same, back to back: 0.04 ms;
  - a new pool: 4.5 ms on macOS, 2.4 ms in a Linux container;
  - ten REPL commands: 0.4 ms.
- **Their "WASI / wasmtime" row, 16 ms and 180 ms, is CPython compiled to WASI, not wasi-perl.**
- CodSpeed tracks Monty's regressions (`.github/workflows/codspeed.yml`). The benchmark harness compares with CPython only locally (`crates/monty-bench/benches/main.rs:30`).

## 3. How it was measured

- **Machine:** the shared 4-vCPU x86-64 Linux VM of the earlier studies.
  - Every timed script ran under `flock /home/user/wasi-perl-wt/machine.lock`, one script per hold.
  - The counting scripts held the lock too, so as not to disturb other agents' timing (EXECUTED: `measure.sh`, `counts.sh`).
- **wasi-perl:** the runner of `dev` at `8b81f84`, which is spec 014 merged.
  - It ran from this worktree's `.venv` (`uv sync`; CPython 3.13.18, wasmtime-py 51.0.0), with `WASI_PERL_ARTIFACTS=/home/user/wasi-perl-wt/perf-base/build/artifacts` (perl 5.42.3).
  - Every call is `wasi_perl.run` with a read-only grant of the data at `/data`, a fresh instance per call, and a warm module cache.
  - Its default limits were on: 1 GiB of memory, a 60 s epoch deadline and 16 MiB of output (READ: `src/wasi_perl/runner.py:57`).
  - Session s1's warm calls (`perl-calls-s1.json`) ran instead from the 014 worktree's `.venv` at `72738d2`, since cleared. Its `src/` differs from `8b81f84` by one comment (EXECUTED: `git diff 72738d2 8b81f84 -- src`). `measure.sh` says so beside `PERL_PY`.
- **Monty:** pydantic-monty 1.6.0 in its own venv (CPython 3.13.18), run with `python -I`.
  - The data is mounted read-only at `/data` with `MountDir`, and output is collected with `CollectString`.
  - **No limit was set**, as the defaults leave them (§2). §9 measures what setting them costs.
  - Four modes:

  | mode | what a call is | what persists between calls |
  |---|---|---|
  | `checkout` | a new `checkout()` and one `feed_run` on an open pool | the worker process, reused (the default, up to 1,000 sessions) |
  | `session` | one more `feed_run` on one checkout | the process and the REPL's globals |
  | `fresh` | as `checkout`, with `max_checkouts_per_worker=1` | nothing: a new worker process per session, spawned in the background after the last one ends |
  | `cli` | `monty -m DATA::/data -c CODE` as a subprocess, its mount served in that process | nothing |

- **Native references:** perl 5.42.3, built natively by perf-base (`build/native-install/bin/perl`), and the `monty` CLI. Each ran as a subprocess with an empty environment (`native.py times`).
- **The work:** 15 tasks, each written once in Perl and once in Monty's Python (`workloads.py`).
  - **Every task printed the same bytes on wasi-perl, Monty, native perl and the Monty CLI, and in both flag arms** (EXECUTED: `tables.py`, "same stdout on every runtime and arm: True").
  - The data is the baseline memo's, byte for byte: `t.csv` 4.6 MB, `t.log` 10.2 MB, `t.json` 1.4 MB. Added to it: 100 files of 1 KiB.
- **Statistics:**
  - Each cell is a median of 10 calls (100 for `hello`), after 2 warm-ups, in each of two sessions. Where a call took over 2 s, it got 1 warm-up and 3 timed calls (`strcat` and `sub_log` on Monty).
  - The paced runs (§4.2) are 15 calls after 3 warm-ups, for each pause.
  - Sessions differ by up to about 15%, as in the earlier studies.
- **Counts:**
  - wasm fuel for wasi-perl (`bench_perl.py fuel`);
  - native x86-64 instructions (callgrind's `Ir`) for native perl and the Monty CLI, under valgrind (`native.py instr`). Monty offers no count of its own. The CLI is the same binary as the pool's worker (EXECUTED: `find_monty_binary()`);
  - the least linear memory each wasi-perl call completes in, by bisection in 64 KiB pages (`bench_perl.py memory`);
  - the `VmHWM` (peak RSS) of a fresh Monty worker after each task (`bench_monty.py memory`).

The commands:

```
S=<scratch>; E=context/research/2026-11-24-wasi-perl-against-monty
UV_CACHE_DIR=$S/monty-dl uv venv $S/venvs/monty-venv --python 3.13
UV_CACHE_DIR=$S/monty-dl uv pip install --python $S/venvs/monty-venv/bin/python pydantic-monty   # 1.6.0
python3 -I $E/workloads.py $S/mdata
$E/measure.sh $S s1 && $E/counts.sh $S && $E/measure.sh $S s2      # every step under the lock
for L in s1 s2; do PATCHED_WT=$S/patched-wt $E/measure.sh $S $L flag; done
for L in s1 s2; do $E/measure.sh $S $L paced phases; done; $E/measure.sh $S s1 cap limits
flock /home/user/wasi-perl-wt/machine.lock python3 $E/hashprobe.py <artifacts>/lib $S $E/results/hashprobe.json
python3 $E/tables.py
```

Session s1 ran in parts. Its Monty `session` mode first stopped at Monty's 1,000-suspension default (§6), and the 014 worktree was cleared under it. So `measure.sh` takes the steps to run as arguments.

## 4. Results

### 4.1 (a) A fresh process's first call

Seven fresh processes per session, two sessions, warm module cache (EXECUTED: `results/*-first-s{1,2}.jsonl`). Each range is over all 14 processes:

| ms | wasi-perl (dev, 014) | Monty |
|---|---|---|
| `import` | 19.4–27.0 | 31.4–46.7 |
| making the pool | none | 1.4–2.0 |
| first call (hello) | **103–143** | **2.2–2.5** |
| second call | 1.6–2.4 | 0.21–0.52 |
| host process peak RSS | 39.5–39.7 MiB | 28.4–28.6 MiB, plus the worker's 11.3–12.2 MiB |

- wasi-perl's first call is mostly the library check of 1,455 files: 78–81 ms before spec 015 (READ: the fixed-cost memo §8.2).
- Monty has no library to check: its standard library is inside the worker binary. Its pool spawns the worker in the background, so the first call includes the wait for it.
- **From `import` to the first answer:** Monty 35–51 ms, wasi-perl 122–170 ms (INFERRED: the sums of the rows).
- **Spec 015, built:** its first call is about 73 ms (73.2 ms, median of five), against 104 ms before. Its check is 42.7–47.0 ms (READ: the fixed-cost memo §8.2, on branch `spec/015-the-runners-checks-kept-across-calls`).
  - The record that would have made it 34 ms was dropped, because a write through a mapping can change a file and move no stamp. The owner agreed (READ: spec 015's Status line).
  - Our import-to-answer would then be about 92–100 ms, about twice Monty's (INFERRED).
- **What the check is made of:** SHA-256 over the library's 22.6 MB takes 22.5 ms read file by file, and 21.9 ms as one file (EXECUTED: `hashprobe.py`, median of 9, `results/hashprobe.json`). So the hashing itself is about half of 015's 43–47 ms check. The rest is per-file work around the hash (INFERRED). §8 row 6 takes this up.

### 4.2 (b) A bare call, a trivial program

`hello`, medians of 100 calls back to back, two sessions (EXECUTED: `results/*-calls-*.json`, `native-times-*.json`):

| | back to back |
|---|---|
| wasi-perl, a fresh instance per call | 1.5–2.0 ms |
| Monty `checkout`, worker reused | 0.13–0.14 ms |
| Monty `session`, the same REPL | 0.08 ms |
| Monty `fresh`, a new worker per session | 1.5–1.6 ms: a wait on the spawn (below) |
| native perl 5.42.3, a subprocess | 1.3–1.7 ms |
| `monty -c`, a subprocess | 1.8–2.1 ms |

**With a pause before each call.** An agent pauses between calls. Monty's `fresh` mode starts the next worker as each session ends. So back to back, the next call waits on a spawn still under way. With a pause, the worker is ready.

`hello`, 15 calls for each pause, after 3 warm-ups, in two sessions (EXECUTED: `results/*-paced-*-s{1,2}.json`, `tables.py`):

| side | back to back | 50 ms between calls | 1 s between calls |
|---|---:|---:|---:|
| wasi-perl, a fresh instance per call | 1.9–2.1 | 2.7–2.9 | 3.2 |
| Monty `fresh`, a new worker process per session | 2.1 | **0.79–0.94** | **1.0–1.2** |
| Monty `checkout`, worker reused | 0.15–0.20 | 0.45–0.90 | 0.62–0.78 |

- **At equal isolation, after a pause, Monty is about 3× faster than us:** 0.79–1.2 ms against 2.7–3.2 ms. Back to back the two are level, but only because Monty's next worker is not ready yet.
  - The back-to-back `fresh` figure is a race: its calls run from 0.40 ms to 4.3 ms within one series (EXECUTED: the `min_ms` and `max_ms` of `monty-paced-fresh-*.json`).
  - It was 1.5–1.6 ms in the first sessions, and the review measured 2.16 ms.
- Monty's `checkout` after 1 s, 0.62–0.78 ms, matches its published 0.77 ms (READ, §2). So this machine is comparable to theirs for this measure.
- A pause makes our call slower too: 1.9–2.1 → 2.7–3.2 ms. That is caches and the CPU going cold, which happens on both sides (INFERRED).
- **Guest work is not the difference.**
  - wasi-perl's hello is 1.60 M wasm instructions (`results/perl-fuel.json`).
  - Natively it is 1.29 M x86 instructions for perl, and 1.17 M for Monty's whole CLI process (`results/native-instr.json`).
- **Where our bare call goes.** Study A's per-phase profile, run here (EXECUTED: `phases.py bare 100`, `results/perl-phases-bare-s{1,2}.json`; the timers add about 0.1 ms):
  - the guest's `_start`, `Func.__call__`: 783–804 µs, for about 0.2 ms of instructions (INFERRED from the fuel at study C's rate);
  - `Store.close`: 205–206 µs;
  - `runner._set_up`: 40–47 µs;
  - the rest of `_call`, which holds the unwrapped `InstancePre.instantiate`: 100–140 µs;
  - in all, `run()`: 1,316–1,401 µs.

  With a read-only grant, `Store.close` is 232–242 µs, `_set_up` 54–56 µs and `run()` 1,855–1,907 µs (`perl-phases-ro-*.json`).

### 4.3 (c) CPU-bound loops

Medians in ms. The time columns are EXECUTED. The instruction columns are counts (EXECUTED): fuel is wasm instructions, and Ir is x86-64 instructions, so the two are proxies for each other, not one unit.

| task | wasi-perl | Monty checkout | Monty fresh | native perl | monty CLI | wasi-perl fuel | native perl Ir | monty Ir |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `fib25` (recursive) | 56.7–57.5 | 19.1–20.4 | 21.8–22.4 | 21.4–22.8 | 21.2 | 613 M | 350 M | 259 M |
| `loop` (Σ i², 10⁶) | 69.3–73.3 | 39.5–41.7 | 43.4–43.7 | 23.8–24.2 | 40.9–43.3 | 604 M | 335 M | 583 M |
| `strcat` (`.=` / `+=`, 200 k) | **15.5–16.8** | 5,790–7,013 | 10,820–13,238 | 7.3–8.0 | 22,749–24,573 | 183 M | 118 M | not run |
| `strjoin` (join 200 k) | 31.7–35.8 | 22.3 | 24.5–25.8 | 31.3–31.9 | 26.6–27.1 | 419 M | 264 M | 221 M |

- **Natively, the two interpreters are within about 1.5× of each other, either way.** Over the nine tasks counted that are not library-bound, perl's Ir over Monty's runs from 0.52 (`grep_log`) to 1.49 (`split_join`) (EXECUTED: `native-instr.json`).
- **Where native perl ties or beats Monty, the wall is Monty's lead.** That is `fib25` (21.4–22.8 against 21.2 ms) and `loop` (23.8–24.2 against 40.9–43.3 ms).
  - On those two, wasi-perl's wall time is 2.5–3.1× native perl's, and its fuel 1.75–1.80× native perl's Ir, as study C found (1.6–1.9×). So Monty's lead there is the WebAssembly wall's price (INFERRED).
  - Native perl also beats Monty on `sum_csv` and `grep_log` (§4.4). There our cost adds the file-call hand-off to the wall (§4.6).
  - On `strjoin` and `sort_ints`, Monty is ahead natively too, so part of its lead is its interpreter.
- **`strcat` is a cliff in Monty.** `s += str(i) + ','` over 200,000 numbers takes 5.8–7.0 s in the pool and 22.7–24.6 s through the CLI.
  - It grows faster than linearly: 20,000 steps took 49 ms and 40,000 took 385 ms through the CLI (EXECUTED, once each, an unlocked probe).
  - CPython and Perl append in place. Monty seems to copy the string at each step (INFERRED from the growth).
  - The CLI is 4× slower than the pool on this task, for a reason not studied. So on `strcat` the CLI does not stand in for the worker, and it was not counted under valgrind.

### 4.4 (d) Text

| task | wasi-perl | Monty checkout | Monty fresh | native perl | monty CLI | wasi-perl fuel | native perl Ir | monty Ir |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `sum_csv` (split, 200 k lines) | 138–150 | 92.3–94.1 | 94.7–96.7 | 57.6–58.4 | 90.0–90.1 | 1,269 M | 770 M | 1,016 M |
| `grep_log` (`/ERROR \d+/` per line) | 104–124 | 74.6–78.6 | 73.1–74.4 | 25.8–26.4 | 59.7–59.8 | 644 M | 353 M | 679 M |
| `sub_log` (`s///g` on 10 MB, then count) | **64.6–74.3** | 5,829–8,512 | 7,101–9,235 | 17.9–18.6 | 7,133–9,246 | 274 M | 78 M | not run |
| `split_join` (whole file) | 215–246 | 105–110 | 129–141 | 151–158 | 111–121 | 1,870 M | 1,286 M | 864 M |

- **Per-line work:** natively, perl runs fewer instructions than Monty and takes less time. Through the wall it loses by 1.4–1.6× (EXECUTED).
- **`re.sub` over a 10 MB string is a second Monty cliff**, 5.8–8.5 s in the pool.
  - On the same string, `re.findall` takes 13 ms and `str.replace` 16 ms. `re.sub` on its first 1 MB takes 43 ms (EXECUTED, once each, an unlocked probe).
  - So the cost grows faster than the input (INFERRED).
- `sub_log`'s fuel is 3.5× native perl's Ir, against 1.6–1.9× elsewhere. That is the regex engine scanning for literals, which study C found slow through wasi-libc's `memchr`. Spec 016's SIMD `memchr` targets it (INFERRED).
- `split_join` is the one text task where Monty's interpreter is ahead natively: 864 M against 1,286 M.
- **Spec 016 is built**, not yet merged. On its own branch and workloads it takes `grep_log` from 113.6 to 61.3 ms (READ: `2026-11-24-the-guests-reads-and-writes-measured.md` §1, worktree `/home/user/wasi-perl-wt/016-the-guests-reads-and-writes`). That would put this row's `grep_log` level with or ahead of Monty's pool (INFERRED).

### 4.5 (e) Dicts, lists and JSON

| task | wasi-perl | Monty checkout | Monty fresh | native perl | monty CLI | wasi-perl fuel | native perl Ir | monty Ir |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `wordfreq` (a dict of 203 k words, sorted) | 1,044–1,136 | 490–504 | 548–557 | 767–814 | 500–537 | 8,348 M | 4,870 M | 5,606 M |
| `sort_ints` (200 k) | 73.1–73.5 | 25.1–26.3 | 30.9–34.7 | 42.1–42.6 | 29.6–32.3 | 906 M | 489 M | 390 M |
| `json_decode` (1.4 MB) | 1,293–1,310 | 15.4 | 24.4–25.2 | 535–541 | 20.4–21.3 | 11,136 M | 6,865 M | 181 M |
| `json_encode` (20 k objects) | 355–359 | 15.5–15.8 | 22.0–24.3 | 154–163 | 20.6–21.9 | 3,232 M | 1,928 M | 184 M |

- **`wordfreq`:** perl runs fewer native instructions than Monty (4.9 B against 5.6 B), yet takes more time.
  - Perl's `sort { $c{$b} <=> $c{$a} || $a cmp $b }` makes two hash lookups per comparison, n log n times. Python's `key=` computes each key once (INFERRED from the programs).
  - That is the "keyed sort" idiom of spec 020, proposed, not yet agreed (READ: synthesis, spec 8).
- **JSON is the widest gap: 23–85×.** It is a library gap, not an interpreter one: `JSON::PP` runs 6.9 B native instructions, against 0.18 B for `jiter` and Monty's Rust encoder.

### 4.6 (f) Reading host-provided data

Medians in ms, each column one harness over two sessions (EXECUTED: `tables.py`).
- The "flag" columns are wasi-perl through a copy of wasmtime-py 51.0.0 whose C API carries the file-call memo's 19-line patch (`/home/user/wasi-perl-downloads/target-c-api/release/libwasmtime.so`, sha256 `a604ff29…`), with `allow_blocking_current_thread` off or on (`bench_perl.py flag`, `results/perl-flag-{off,on}-s{1,2}.json`).
- "Flag off" is the control: the same patched library, unchanged behaviour.

| task | wasi-perl, grant | flag off | **flag on** | Monty checkout, mount | `monty -c`, mount, a process per call | native perl, a process per call |
|---|---:|---:|---:|---:|---:|---:|
| `read_big` (one 10.2 MB file) | 47.3–50.2 | 52.2–53.9 | **5.8–7.6** | 15.0–15.8 | 9.5–11.1 | 4.9–6.4 |
| `read_small` (100 files of 1 KiB) | 36.0–38.1 | 35.7–39.5 | **2.3–3.0** | 22.6–23.0 | 2.7–3.2 | 1.7–1.8 |
| `grep_log` | 104–124 | 106–113 | **64.1–79.8** | 74.6–78.6 | 59.7–59.8 | 25.8–26.4 |
| `sum_csv` | 138–150 | 134 | **112–114** | 92.3–94.1 | 90.0–90.1 | 57.6–58.4 |
| `split_join` | 215–246 | 241–243 | **189–203** | 105–110 | 111–121 | 151–158 |
| `hello` | 1.5–2.0 | 1.6–1.7 | 1.6–1.7 | 0.13–0.14 | 1.8–2.1 | 1.3–1.7 |

The other host paths (EXECUTED):
- wasi-perl `read_big` through stdin: 20.9–21.6 ms (`perl-stdin-*.json`).
- Monty, on a checkout (`monty-hostdata-*.json`):
  - `read_big` as an `inputs=` string: 19.7–21.9 ms;
  - `read_big` from a host function: 20.6–24.5 ms;
  - `read_small` as 100 host-function calls: 11.4–12.0 ms.

- **Today the hand-off dominates our reads.** `read_big` runs 13 M wasm instructions, about 1.5 ms (INFERRED at study C's 8.5 B/s), but takes about 50 ms. That is about 1,250 `fd_read` calls of 8 KiB at about 27 µs each (INFERRED from the file-call memo §3.2). `read_small` makes about 9 calls per file (READ: synthesis §1).
- **With the flag on:**
  - against **Monty's pool**, wasi-perl reads host files 2–10× faster: ratios 0.10–0.50 (EXECUTED, §5);
  - against **the `monty` CLI**, it is level on small files (2.3–3.0 against 2.7–3.2 ms) and 1.3–1.9× ahead on one big file. The CLI serves its mount in its own process, and its figures include a process start of 1.8–2.1 ms (its `hello`).

  So Monty's slow reads in the pool come from serving the mount across the worker's pipe, not from its interpreter (INFERRED). A Monty host call in the pool costs about 0.11–0.12 ms: 100 calls took 11.4–12.0 ms. With the flag on, a WASI file call costs 0.42–1.49 µs, by kind of call (READ: the file-call memo §3.2).
- Monty can hand the program structured values (`inputs=`, host functions), and wasi-perl cannot. That is a feature, not speed (§6). Spec 017's mounts, proposed, are the WASI-only way to serve files from the embedder's code.

### 4.7 (g) Memory per call

`results/perl-memory.json` and `results/monty-memory.json` (EXECUTED). Monty's range is over three fresh workers:

| task | wasi-perl: least linear memory | Monty: fresh worker's peak RSS |
|---|---:|---:|
| `hello` | 13.4 MiB | 11.5–12.1 MiB |
| `grep_log` | 13.5 MiB | 33.2–33.5 MiB |
| `wordfreq` | 46.8 MiB | 53.8–54.2 MiB |
| `json_decode` | 25.3 MiB | 26.4–26.7 MiB |
| `split_join` | 120.5 MiB | 84.5–86.2 MiB |
| `read_big` | 23.2 MiB | 30.6–31.9 MiB |

- The units differ. Ours is the guest's linear memory, which bounds what it may touch. Monty's is a whole process's resident pages, the binary included (INFERRED).
- They are level:
  - A streaming one-liner stays at our floor, while Monty buffers a mounted file whole in its heap (`grep_log`: 13.5 against 33 MiB).
  - Whole-file work costs us more (`split_join`).
  - The host side is level too: 39.5 MiB for a wasi-perl process, against 28.5 MiB plus a worker of about 12 MiB for Monty (§4.1).

## 5. Summary of speed

wasi-perl's median over Monty's, within each session, then ranged; below 1 means wasi-perl is faster (EXECUTED: `tables.py`, `ratio_table`). "Flag on" is wasi-perl with the file-call flag, on the tasks it was run for. The CLI column compares a warm wasi-perl call with a `monty -c` process, start included.

| group | tasks | against Monty checkout | flag on | against the monty CLI | flag on |
|---|---|---:|---:|---:|---:|
| (b) bare call | hello | 10.4–15.8× | 12.3–12.4× | 0.814–0.974× | 0.759–0.966× |
| (c) CPU | fib25, loop, strjoin | 1.42–3.01× | | 1.17–2.71× | |
| (c) string building | strcat | 0.00221–0.0029× | | 0.000683× | |
| (d) text | sum_csv, grep_log, split_join | 1.4–2.24× | 0.859–1.85× | 1.53–2.08× | 1.07–1.71× |
| (d) big regex substitution | sub_log | 0.00873–0.0111× | | 0.00804–0.00906× | |
| (e) dict and sort | wordfreq, sort_ints | 2.07–2.92× | 1.99–2.12× (wordfreq) | 2.09–2.48× | 1.94–2× |
| (e) JSON | json_decode, json_encode | 22.8–84.8× | | 16.2–63.5× | |
| (f) host files | read_big, read_small | 1.57–3.17× | 0.103–0.504× | 4.51–13.4× | 0.524–1.12× |

The bare call against Monty `fresh` after a pause is about 3× (§4.2).

## 6. What each side cannot do

A feature gap is not a speed win.

**Monty, not wasi-perl (READ, §2):**
- external functions with structured values;
- pausing at a host call, and dumping a paused run to kilobytes, to resume elsewhere or more than once;
- a REPL session whose state persists between feeds;
- a type checker before running;
- async host calls.

**wasi-perl, not Monty (READ, §2; EXECUTED where noted):**
- **All of Perl 5.42 and its core library:** XS modules, `local`, formats, `tr`, `sprintf`, Perl's own regex engine. Monty runs a subset of Python with no generators, no `del`, no user exceptions, no `re.subn` and no bytes regex.
- **Writable files with real descriptors:** `-pi -e` edits a granted file in place.
- **Any number of file reads in one call.**
  - Monty's default budget of 1,000 host round trips binds within one call, and each `open()` and each read is one.
  - EXECUTED, one default checkout per call (`bench_monty.py cap`, `results/monty-cap-s1.json`): 400, 450 and 500 small files opened and read succeed; 550 and 600 fail with "RuntimeError: suspension limit 1000 exceeded". So one Monty call reads at most about 500 files unless `max_suspensions` is raised.
  - The budget is per checkout, so a REPL session fed many file-reading snippets reaches it too. Our first `session` run stopped there.
- **No cliff on `+=` string building or on `re.sub` over large strings** (§4.3, §4.4).

## 7. The two walls

| | Monty (OSS pool, as timed) | wasi-perl |
|---|---|---|
| what holds the code in | the interpreter itself: a language-level sandbox, "no container, no seccomp filter and no virtual machine" (READ: `docs/security.md:13`) | WebAssembly: bounds-checked linear memory, and only the WASI preview1 imports |
| what an escape needs | a memory-safety or logic bug in the interpreter, which runs the attacker's program directly, or in its parser or libraries. Then it has the worker process, with the application's OS privileges (READ: `docs/alternatives.md`, "Execution environment") | a bug in perl's C first, to run code of the attacker's choosing inside linear memory. Then a bug in wasmtime, Cranelift or wasmtime-wasi, or in the host-side checks of wasmtime-py and our runner (INFERRED) |
| the code on the trusted side, as counted (EXECUTED: `wc -l` over `*.rs` outside `tests/`, and `grep -rn "unsafe {"`; third-party crates not counted on either side) | core `crates/monty/src` 147,970 lines (39 `unsafe` blocks); `monty-pool` 11,533, `monty-proto` 11,377, `monty-types` 9,033, `monty-fs` 4,903, `monty-runtime` 1,405 (0 each); `monty-alloc` 141 (4) | wasmtime v51 (`/home/user/wasi-perl-downloads/src/wasmtime-v51.0.0`, `32656dc`): `crates/wasmtime` 125,064 lines (974), `cranelift/codegen` 139,589 (3), `crates/environ` 41,692 (7), `crates/cranelift` 33,687 (0), `crates/wasi` 29,394 (20), `crates/c-api` 9,829 (67); wasmtime-py's Python layer 9,788 lines; our runner `src/wasi_perl` 2,488 lines (grants, the hard-link and cache rules, limits). The fd-table and refusal recipes (1,412 lines of C) run inside the guest |
| between callers | a worker is reused for up to 1,000 sessions by default. "A compromised worker could see later sessions until it is replaced"; set the count to 1 when callers do not trust each other (READ: `docs/security.md:435-437`) | a fresh instance per call; nothing carries over |
| crashes | a worker panic kills only the worker, a subprocess | a guest trap ends the call; a wasmtime abort would take the host process (INFERRED) |
| host access | external functions, the `os` callback and mounts, all opt-in. Mounts are confined with `cap_std`, and workers start with an empty environment (READ: `docs/security.md:427`) | preopened grants only, confined by wasmtime-wasi's `cap-std`; nothing else |
| limits, by default | **off**, except recursion depth and 1,000 host round trips per checkout. Memory, both durations and `request_timeout` are unset unless the embedder sets them (READ: `docs/resource-limits.md:59-61`, `docs/security.md:466-469`) | **on**: 1 GiB of linear memory, a 60 s deadline, 16 MiB of output per stream (READ: `src/wasi_perl/runner.py:57`) |

- Line counts measure size, not risk. Monty's interpreter is reachable directly by the attacker's program. Most of wasmtime's code is reached only through `perl.wasm`, which we build, and through WASI calls (INFERRED).
- **So Monty's 0.15 ms bare call is not like for like.** It is a reused process, behind a wall of Rust code rather than a virtual machine.
- Its like-for-like mode, a fresh worker per session, costs 0.79–1.2 ms after a pause, about a third of our 2.7–3.2 ms. It costs that much because the start-up is done before the call (§4.2). That part is a lever we can copy (§8, row 2).
- Pydantic sells OS-level isolation separately, as Commercial Support (READ: `docs/server.md`).

## 8. The gaps, and whether we can close them

Each gain is on wasi-perl, measured here (EXECUTED) or estimated (INFERRED, with its source). "Fits" asks four things:
- (1) WASI preview1 only;
- (2) real Perl 5.42;
- (3) no custom import;
- (4) wasi-python's API kept.

The rows are ordered by gain on this comparison. That is not the synthesis's build order (§3 there), which puts safe, cheap, every-call work first.

**Status of the specifications cited (READ: their Status lines on `origin/dev`):**
- 015 and 016 are agreed. 015 is built on its branch, 016 on `spec/016-the-guests-reads-and-writes`; neither is merged.
- 017 to 022 are proposed, not yet agreed by the owner.

| # | gap | mechanism | gain | cost | risk | fits | already ranked? |
|---|---|---|---|---|---|---|---|
| 1 | **host reads** (f) | set wasmtime-wasi's `allow_blocking_current_thread` before the first preopen, once wasmtime's C API and wasmtime-py expose it | EXECUTED, flag off → on: `read_small` 35.7–39.5 → 2.3–3.0 ms; `read_big` 52.2–53.9 → 5.8–7.6 ms; `grep_log` 106–113 → 64–80 ms; `sum_csv` 134 → 112–114 ms; bare call unchanged. Against Monty's pool with mounts, our worst row becomes our best (0.10–0.50×). Against the `monty` CLI we go from 4.5–13× behind to level or 1.9× ahead | S on our side (one property in the runner); the patch is upstream's to take | low: the same bytes in every arm | yes: host-side only | **yes**, as "outside this repository" (synthesis §3). The owner will file it later (`../roadmap.md`; the drafts in `../upstream/wasmtime-allow-blocking-current-thread.md`). The largest lever measured here |
| 2 | **the warm call's fixed cost** (b): start-up done before the call | as Monty's `fresh` pool does: make the next call's `Store` ahead of time, and close the last one off the caller's path, on a helper thread or after the result is handed back. Each call still gets its own fresh store and instance; nothing is shared. The `WasiConfig` (argv, environment, grants) still depends on the call, so only the parts that do not can move | INFERRED from the phase profile (§4.2): `Store.close` 205–242 µs, plus most of `_set_up` (40–56 µs) and perhaps the instantiation (part of the 100–140 µs rest), so about 0.3–0.45 ms of 1.3–1.9 ms. That holds if instantiation can come before the call's WASI context is set, which was not checked. The guest's `_start`, 0.78–0.97 ms, remains, so after a pause we would stay about 2–2.5× Monty `fresh` (INFERRED) | S–M: a helper thread, one prepared store per process | medium: wasmtime-py stores across threads need care; a prepared store holds about 13 MiB of reserved, mostly untouched memory; the deadline and fuel must still start at the call | yes, all four: internal to the runner, and wasi-python's runner has the same shape and could do the same | **new** (the review's H1). Worth a prototype before a specification |
| 3 | **JSON** (e) | Cpanel::JSON::XS with JSON::MaybeXS, statically linked | INFERRED from study F §3.1: decode 1,293–1,310 → about 46 ms; encode 7×, 355 → about 50 ms. Monty's pool: 15 ms | S–M | low | yes | yes: spec 018, **proposed, not yet agreed** |
| 4 | **line-oriented text** (d) | a 64 KiB PerlIO buffer, a SIMD `memchr`, `isatty` from the fd-table | READ, spec 016's memo §1: `grep_log` 113.6 → 61.3 ms, `count_lines` 72.8 → 32.2 ms; overlaps row 1 on the count of `fd_read` calls | S–M | low; SIMD narrows the hosts | yes | yes: spec 016, **agreed and built**, not yet merged |
| 5 | **idioms** (e) | docs: keyed sorts, `-0777` for whole-file work | READ, study F §6: 4–6× on the programs they fit; `wordfreq` is the case here | S | none | yes | yes: spec 020, proposed |
| 6 | **first call after 015** (a): the library check as one file | spec 015 keeps hashing the whole library in every new process: about 73 ms of first call, with a 43–47 ms check. Hashing the same 22.6 MB costs 22 ms by file or as one file (EXECUTED, §4.1), so about half of the check is per-file work. One archive, read by `require` through a guest-side `@INC` hook, would leave one file to check | INFERRED: the check from 43–47 ms to about 22 ms, so the first call from about 73 to about 50 ms. Stripping POD, 4.4 MB of the 22.6 MB (EXECUTED: a scan for `=pod`…`=cut` blocks), would save about 4 ms more | M | medium: `%INC` and `__FILE__` read differently; tools that read a module's file need it from the archive | yes: an `@INC` hook is plain Perl, and the archive is read through WASI | **new.** Larger than first thought, now that 015 keeps the per-process hash |
| 7 | **reusing one interpreter across calls** (b) | Monty's default: one live process for many sessions | INFERRED: up to about 1.2 ms of a call (the guest's `_start` and `Store.close`) | M–L | **high**: Perl reads its own linear memory (`unpack "P"`), so a call would see what an earlier call left there. That is the reuse Monty itself warns about (§7). Safe only within one caller | (1)–(3) yes; (4) no: wasi-python has no session API | **new; not recommended.** If wanted, a joint wasi-python feature ("a session"), for the owner |
| 8 | **CPU loops** (c) | the wall's price: fuel 1.65–1.82× native perl's Ir on `fib25`, `loop`, `sum_csv` and `grep_log`, and 2.5–3.1× wall time on `fib25` and `loop` | READ: `wasm-opt -O3`, −2.7% (study B); nothing else measured gains (synthesis §4) | S | low–medium | yes | yes: spec 021, proposed. The rest cannot be closed: running perl natively in-process removes the wall |
| 9 | **snapshots of a running call** | Monty serializes its own heap and frames | none in speed | not feasible: wasmtime keeps the wasm call stack outside linear memory, so a paused call cannot be dumped and resumed without Asyncify, which the constitution forbids (INFERRED) | | no | spec 022 (proposed) covers start images made at install time; mid-run snapshots are ruled out |
| 10 | **host functions** | structured calls to the embedder | a feature, not speed | | | a custom import would break (3) | spec 017's mounts (proposed) are the preview1 form |

**What is new here:** rows 2, 6 and 7.
- Row 2 is the lesson of Monty's fast isolated mode. It fits every constraint and is worth a prototype.
- Row 6 grew when 015 dropped its record.
- Row 7 should not be built as a default.

**What changes rank:** row 1, which the owner has chosen to file upstream later.

**What we should not copy:** the process reuse behind Monty's 0.15 ms (§7), or its cliffs. A docs page may say plainly that `.=` and `s///g` over large strings are linear in Perl.

## 9. Limits

- One shared VM, two sessions. Sessions differ by up to about 15%. Every comparison is within one session's run, and the counts are the primary evidence.
- **Limits differ between the sides as run:** ours were on, Monty's off, as each side's defaults leave them. Does it move a number? Monty `checkout` calls with a 1 GiB `max_memory` and a 60 s `max_feed_duration_secs`, interleaved with calls without, moved:
  - `hello`, `loop`, `grep_log`, `wordfreq` and `json_decode` by −7% to +3%;
  - `fib25` by +11% (22.5 → 24.9 ms).

  That is one session only (EXECUTED: `bench_monty.py limits`, `results/monty-limits-s1.json`). Study A found that turning our fuel or epochs off gains nothing on a bare call (READ: synthesis §4). So no conclusion above moves.
- Monty's workers ran natively, on Linux x86-64 only. Its published numbers are from macOS arm64.
- Fuel (wasm) and `Ir` (x86-64) are different units; their ratio is a proxy.
- The probes of Monty's growth on `strcat` and `re.sub` ran once each, without the lock. They show shape, not timing.
- The flag arms use an unreleased, patched wasmtime C API. They show what an upstream release would give, not what the runner can ship today.
- Row 2's and row 6's gains in §8 are estimates from profiles and a probe, not prototypes.
- The `monty-wasm-runtime` component was not built: it needs Rust 1.98 (1.97 is here) and a component host. So Monty's instructions under wasm were not counted.
- Monty's wheels were installed without a hash pin. They were checked against PyPI's digests afterwards (§2).

## 10. Changes after the review

`../reviews/research-monty-comparison-pr-2026-11-24.md`:
- **H1:**
  - new paced runs on both sides (§4.2);
  - §1, §4.2 and §7 corrected: Monty pre-spawns, and after a pause its fresh mode is about 3× faster than us;
  - a new §8 row 2, start-up done before the call;
  - a phase profile run for its estimate.
- **M1:** the `monty -c` column in §4.6 and §5. "2–10×" now says it is against Monty's pool with mounts, and that we are level with the CLI or up to 1.9× ahead. The roadmap line is qualified too.
- **M2:** the upstream draft's numbers are given per kind of call, as the file-call memo's tables give them. The CLI's flag is "unless a timeout is given".
- **M3:**
  - §7 gives both sides' limits as their defaults, and §9 what setting Monty's costs;
  - both sides get line and `unsafe` counts, our runner and wasmtime-py included.
- **M4:**
  - every range says how it is built;
  - §4.6 shows one harness per column;
  - §5 is per-session ratios printed by `tables.py`;
  - §1 uses the pool's ranges, and `strcat` moved to CPU.
- **L1:** "within about 1.5× either way". The wall's price is claimed only where native perl ties or beats Monty.
- **L2:** a single-call cap probe (§6).
- **L3:** §1 and §4.1 agree, now with 015's 73 ms.
- **L4:** spec statuses (§8), and 016's measured 61.3 ms.
- **L5:** `PERL_PY`'s history noted in `measure.sh`; `check.py` replaced by `tables.py`'s `check`; the wheels' hashes; where Monty ran.
- **Facts added since:**
  - spec 016 is built (§4.4, §8 row 4);
  - spec 015's first call is about 73 ms, not 35, which grows §8 row 6.
