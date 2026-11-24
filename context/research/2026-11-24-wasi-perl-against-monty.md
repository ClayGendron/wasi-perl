# wasi-perl against Monty, on speed and on the wall

- **Status:** research, 2026-11-24. A comparison the owner asked for. It changes no code. Section 8 names the gaps and where each stands among the specifications.
- **Owner:** Clay Gendron
- **Researcher:** an agent (Claude Opus 5.5), on branch `research/monty-comparison`.
- **The question, in the owner's words:** "Can we compare to the speed of something like Monty? Have a sub agent study that project and find out if it is faster/more performant than our wasi perl implementation. If so, are there gaps we can close."
- **Stands on:** the baseline, `2026-11-24-wasi-perl-against-wasi-python-on-speed.md`; the synthesis, `2026-11-24-perf-synthesis.md`; `2026-11-24-the-runners-fixed-cost-measured.md` (spec 014); `2026-11-24-wasmtime-py-file-call-cost.md`.
- **Evidence:** `2026-11-24-wasi-perl-against-monty/`. Scripts: `workloads.py` (the tasks, once per language), `bench_perl.py`, `bench_monty.py`, `native.py`, `measure.sh`, `counts.sh`, `tables.py`. Raw results: `results/`.

Labels: EXECUTED means run and seen. READ means read from a file, cited by path. INFERRED means the researcher's reasoning.

## 1. The answer

**Monty is faster on most work, and much faster on the fixed cost of a call. It is not faster on everything, and much of its lead is bought with a different wall.**

- **A bare call:** Monty 0.13–0.14 ms against wasi-perl's 1.5–2.0 ms, 10–16× (EXECUTED, §4.2). Monty reuses a live worker process for up to 1,000 sessions. When Monty gives every session a fresh process, as wasi-perl gives every call a fresh instance, its bare call is 1.5–1.6 ms: the same as ours.
- **A fresh process's first call:** Monty is ready in about 4 ms after its import; wasi-perl takes 103–143 ms, most of it the library check that spec 015 cuts (EXECUTED, §4.1).
- **CPU-bound loops:** Monty is 1.4–3.0× faster (EXECUTED, §4.3). Natively, perl 5.42 and Monty are level: perl wins `loop`, they tie on `fib25`, Monty wins `sort_ints` (EXECUTED, §4.3). So Monty's lead here is the price of the WebAssembly wall, not a better interpreter (INFERRED).
- **Text:** Monty is 1.4–2.2× faster on line-oriented one-liners. But wasi-perl is **90–450× faster** on two common patterns where Monty falls off a cliff: building a string with `+=` (16 ms against 5.8–24.6 s) and `re.sub` over a 10 MB string (65–74 ms against 5.8–9.4 s) (EXECUTED, §4.4).
- **JSON:** Monty is 23–85× faster. Its `json` is Rust; ours is `JSON::PP`, pure Perl. Spec 018 closes most of it (READ, §8).
- **Reading host files:** Monty is 1.6–3.2× faster today. That flips with one host-side setting, wasmtime's `allow_blocking_current_thread`. With it on, wasi-perl reads 100 small files in 2.3–3.0 ms against Monty's 22.6–23.0 ms, and one 10 MB file in 5.8–7.6 ms against 15.0–15.8 ms (EXECUTED, §4.6).
- **Memory:** the two are level. A trivial call needs 13.4 MiB of wasi-perl linear memory, and a Monty worker peaks at 11.3–12.2 MiB (EXECUTED, §4.7).

**The gaps worth closing** (section 8), largest first:
1. The file-call hand-off. It is upstream's, and already known. Measured here it is worth 6–15× on file-heavy calls, and it turns host-data work from our worst row into our best.
2. JSON in XS, spec 018: from 1.3 s to about 46 ms (INFERRED from study F).
3. The first call, spec 015: from about 110 ms to about 35 ms (READ, study A). That draws level with Monty's import-plus-first-call of 35–51 ms.
4. The guest's I/O, spec 016.

The fixed cost of a warm call cannot be closed the way Monty closes it without weakening the wall: Monty's 0.13 ms comes from reusing a process across sessions (section 7). The CPU gap is the WebAssembly wall's price. Only `wasm-opt` (spec 021, −2.7%) remains there.

## 2. Which Monty, and what it is

**Identity (READ).** "Monty" is pydantic's Monty, `github.com/pydantic/monty`: "A secure Python sandbox, written in Rust, for code written by AI" (`README.md`). Nothing else by that name fits "a sandbox for agent-written code", so the request is not ambiguous.

- Source read: a shallow clone of `main` at `bdf4f6e` (2026-11-24, workspace version 1.7.0-beta.1), in `context/prior_art/monty`.
- Licence: MIT (`LICENSE`). The core crate is "MIT AND Python-2.0.1", because it vendors some CPython stdlib modules under `crates/monty/stdlib` (`crates/monty/Cargo.toml`). It is recorded in `context/prior_art/README.md`.
- Timed: `pydantic-monty` 1.6.0 from PyPI, the newest release (EXECUTED: `uv pip install pydantic-monty` in a scratch venv). It pulls in `pydantic-monty-runtime` 1.6.0, which carries the `monty` worker binary (32.4 MB), and `pydantic-monty-client` (the 11.7 MB `_monty` extension).

**Design (READ, `crates/monty/src`, about 148,000 lines of Rust).**

| part | what Monty does | where |
|---|---|---|
| parser | Ruff's Python parser (`ruff_python_parser`) | `crates/monty/Cargo.toml`, `parse.rs` |
| execution | a stack-based **bytecode VM**: an operand stack, frames that each own their instruction pointer, raw `Vec<u8>` opcodes. It replaced an earlier tree-walker | `bytecode/mod.rs:1-13`, `bytecode/vm/mod.rs:1-4`, `bytecode/op.rs:1-7` |
| values | a tagged `enum Value`: immediates (`None`, `Bool`, `Int(i64)`, `Float`, short inline strings, interned literals) and `Ref`s into one heap arena | `value.rs:50-110` |
| memory | the arena counts references, and a cycle collector marks "gray" candidates whose count dropped without reaching zero | `heap/mod.rs:85-97`, `:820-919` |
| standard library | written in Rust inside the binary: `re` is `fancy-regex`, `json` parses with `jiter`, and there are about 30 modules in all | `modules/`, `docs/limitations/index.md` |
| the host | three opt-in doors: **external functions** (a name the code leaves undefined is looked up in the host's `external_lookup`; the VM suspends, the host runs it, the VM resumes with the result); an **`os` callback** for OS-shaped calls; **mounts** of host folders | `docs/security.md` |
| files | the interpreter does no file I/O. Each `open()` and each read is a one-shot OS call: the host opens, acts and closes, and a file's whole content is buffered in the sandbox heap on its first read. Mounts confine paths with `cap_std` descriptors | `docs/limitations/open.md:12-27,126-132`; `docs/security.md` ("Confinement is structural") |
| limits | `max_memory`, counted by a global allocator in the worker; feed and turn durations, checked at interpreter checkpoints; recursion depth; and `max_suspensions` (default 1,000 host round trips per session). The pool's wall-clock `request_timeout` defaults to none | `docs/resource-limits.md:51-61`, `docs/security.md` ("Resource exhaustion") |
| snapshots | `dump()` serializes the whole interpreter with serde to CBOR: globals, heap, and at a suspension the paused call stack. It is "a few kilobytes", restorable in another process or more than once. This works because the sandbox holds no OS resource | `docs/snapshots.md`, `dump_format.rs`, `Cargo.toml` (`minicbor-serde`) |
| process model | the Python and JavaScript APIs run code **only** in `monty` worker subprocesses from a pool. A checkout dedicates a worker to one REPL session. Workers are reused for up to 1,000 checkouts by default, then recycled. There is no in-process mode in Python. The Rust `monty` crate runs in-process | `_monty.pyi` (`Monty`, `max_checkouts_per_worker`), `docs/security.md` ("Crash isolation", "In-process execution") |
| WebAssembly | a separate build: a `wasm32-wasip1` core module wrapped as a WASI 0.2 component, for browsers and Node | `crates/monty-wasm-runtime`, `Makefile:67` |

**The subset (READ, `docs/limitations/index.md`).**
- It has functions, closures, simple classes, comprehensions, `try`, `match`, f-strings, `async`/`await`, and about 30 stdlib modules, each "often a small part" of CPython's.
- Rejected at parse time: generators (`yield`), `del`, metaclasses, decorators on methods (`@property`, `@staticmethod`), and exception groups.
- Also missing: user-defined exception classes, subclassing built-in types, `enum`, `io`, `struct`, `logging`, `string`, and any third-party package.
- `re` lacks `subn` and `bytes` patterns (`docs/limitations/re.md:13,29`).

**Monty's own performance claims (READ).** All of them are about start latency. None compares the interpreter's speed to CPython's in the docs.
- They are in `docs/index.md` and `docs/alternatives.md`, made by `scripts/startup_performance.py` on an Apple M3 Max:
  - a new sandbox from an open pool, after the pool sat idle 1 s: 0.77 ms (median of 20, 1.0.0b3);
  - the same, back to back: 0.04 ms;
  - a new pool: 4.5 ms on macOS (0.0.21), 2.4 ms in a Linux container;
  - ten REPL commands: 0.4 ms.
- **Their "WASI / wasmtime" row, 16 ms and 180 ms, is CPython compiled to WASI, not wasi-perl.** wasi-perl's bare call is 1.5–2.0 ms (§4.2).
- CodSpeed tracks Monty's regressions (`.github/workflows/codspeed.yml`). The benchmark harness compares against CPython only locally (`crates/monty-bench/benches/main.rs:30`).

## 3. How it was measured

- **Machine:** the shared 4-vCPU x86-64 Linux VM of the earlier studies. Every timed script ran under `flock /home/user/wasi-perl-wt/machine.lock`, one script per hold. A counting script held it too, so as not to disturb another agent's timing (EXECUTED: `measure.sh`, `counts.sh`).
- **wasi-perl:** the runner of `dev` at `8b81f84`, which is spec 014 merged. It ran from this worktree's `.venv` (`uv sync`; CPython 3.13.18, wasmtime-py 51.0.0), with `WASI_PERL_ARTIFACTS=/home/user/wasi-perl-wt/perf-base/build/artifacts` (perl 5.42.3).
  - Session s1's warm calls ran from the 014 worktree at `72738d2`, before it was cleared. Its `src/` differs from `8b81f84` by one comment in `_cache.py` (EXECUTED: `git diff 72738d2 8b81f84 -- src`).
  - Every call is `wasi_perl.run` with a read-only grant of the data at `/data`: a fresh instance per call, and a warm module cache.
- **Monty:** pydantic-monty 1.6.0 in its own venv (CPython 3.13.18), run with `python -I`. The data is mounted read-only at `/data` with `MountDir`, and output is collected with `CollectString`. Four modes:

  | mode | what a call is | what persists between calls |
  |---|---|---|
  | `checkout` | a new `checkout()` and one `feed_run` on an open pool | the worker process, reused (the default, up to 1,000 sessions) |
  | `session` | one more `feed_run` on one checkout | the process and the REPL's globals |
  | `fresh` | as `checkout`, with `max_checkouts_per_worker=1` | nothing: a new worker process per session |
  | `cli` | `monty -m DATA::/data -c CODE` as a subprocess | nothing |

- **Native references:** perl 5.42.3, built natively by perf-base (`build/native-install/bin/perl`), and the `monty` CLI. Each ran as a subprocess, with an empty environment (`native.py times`).
- **The work:** 15 tasks, each written once in Perl and once in Monty's Python (`workloads.py`). **Every task printed the same bytes on wasi-perl, Monty, native perl and the Monty CLI** (EXECUTED: `results/perl-stdout.json` = `results/monty-stdout.json`, and the `stdout` fields of `native-times-*.json`). The data is the baseline memo's (`t.csv` 4.6 MB, `t.log` 10.2 MB, `t.json` 1.4 MB, byte for byte), plus 100 files of 1 KiB.
- **Statistics:** each cell is a median of 10 calls (100 for `hello`), after 2 warm-ups, in each of two sessions, s1 and s2. Tables give the range of the two medians. Where a call took over 2 s, it got 1 warm-up and 3 timed calls (`strcat`, `sub_log` on Monty). Sessions differ by up to about 15%, as in the earlier studies.
- **Counts:**
  - wasm fuel for wasi-perl (`bench_perl.py fuel`);
  - native x86-64 instructions (callgrind's `Ir`) for native perl and the Monty CLI (`native.py instr`, under valgrind). Monty offers no instruction count of its own;
  - the least linear memory each wasi-perl call completes in, by bisection on `memory_bytes` in 64 KiB pages (`bench_perl.py memory`);
  - the `VmHWM` (peak RSS) of a fresh Monty worker after each task (`bench_monty.py memory`).

The commands:

```
S=<scratch>; E=context/research/2026-11-24-wasi-perl-against-monty
UV_CACHE_DIR=$S/monty-dl uv venv $S/venvs/monty-venv --python 3.13
UV_CACHE_DIR=$S/monty-dl uv pip install --python $S/venvs/monty-venv/bin/python pydantic-monty   # 1.6.0
python3 -I $E/workloads.py $S/mdata
$E/measure.sh $S s1 && $E/counts.sh $S && $E/measure.sh $S s2      # every step under the lock
PATCHED_WT=$S/patched-wt $E/measure.sh $S s1 flag; PATCHED_WT=$S/patched-wt $E/measure.sh $S s2 flag
python3 $E/tables.py
```

Session s1 ran in three parts. Its Monty `session` mode first stopped at Monty's 1,000-suspension default (§6), and then the 014 worktree was cleared under it. `measure.sh` takes the steps to run as arguments for that reason.

## 4. Results

### 4.1 (a) A fresh process's first call

Seven fresh processes per session, two sessions, warm module cache (EXECUTED: `results/*-first-s{1,2}.jsonl`):

| ms | wasi-perl | Monty |
|---|---|---|
| `import` | 19.4–27.0 | 31.4–46.7 |
| making the pool | none | 1.4–2.0 |
| first call (hello) | **103–143** | **2.2–2.5** |
| second call | 1.6–2.4 | 0.21–0.52 |
| host process peak RSS | 39.5–39.7 MiB | 28.4–28.6 MiB, plus the worker's 11.3–12.2 MiB |

- wasi-perl's first call is the library check of 1,452 files, the module load, and the call, as study A and spec 014's memo found (READ).
- Monty has no library to check: its standard library is compiled into the worker binary (READ, §2). Its pool spawns the worker in the background, so the first call includes waiting for it.
- From `import` to the first answer: Monty 35–51 ms, wasi-perl 122–170 ms (INFERRED: the sums of the rows).
- Spec 015 brings our first call to about 35 ms, by study A's L6 prototype (READ: the fixed-cost memo §4). Our import-to-answer would then be about 55–60 ms, near Monty's (INFERRED).

### 4.2 (b) A bare call, a trivial program

`hello`, medians of 100 back-to-back calls, two sessions (EXECUTED: `results/*-calls-*.json`, `native-times-*.json`), plus 15 calls each after 1 s asleep, Monty's own method (EXECUTED: `results/*-idle-s2.json`):

| | back to back | after 1 s idle (median, range) |
|---|---|---|
| wasi-perl, fresh instance per call | **1.5–2.0 ms** | 2.9 ms (2.2–5.1) |
| Monty `checkout`, worker reused | **0.13–0.14 ms** | 0.76 ms (0.59–1.29) |
| Monty `session`, the same REPL | 0.08 ms | |
| Monty `fresh`, a new worker process per session | 1.5–1.6 ms | |
| native perl 5.42.3, a subprocess | 1.3–1.7 ms | |
| `monty -c`, a subprocess | 1.8–2.1 ms | |

- Monty's after-idle figure here, 0.76 ms, matches its published 0.77 ms (READ, §2). So this machine is comparable to theirs for this measure. Our own call is also slower after an idle second.
- Guest work is not the difference. wasi-perl's hello is 1.60 M wasm instructions (`results/perl-fuel.json`). Natively it is 1.29 M x86 instructions for perl and 1.17 M for Monty's whole CLI process (`results/native-instr.json`). All EXECUTED.
- Spec 014's phase table puts our 1.5 ms in wasmtime: the guest's `_start` (about 0.96 ms, for about 0.2 ms of instructions) and `Store.close` (about 0.25 ms) (READ: the fixed-cost memo §3.1). Monty pays neither: its worker is already running and makes a new interpreter state in its own heap (INFERRED from `monty-pool/README.md`, "Model").
- **Like for like:** a fresh, empty process per session costs Monty 1.5–1.6 ms, the same as a fresh instance costs us.

### 4.3 (c) CPU-bound loops

Medians in ms. The time columns are EXECUTED from `results/`. The instruction columns are counts (EXECUTED): fuel is wasm instructions, Ir is x86-64 instructions, so the two are a proxy for each other, not one unit.

| task | wasi-perl | Monty checkout | Monty fresh | native perl | monty CLI | wasi-perl fuel | native perl Ir | monty Ir |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `fib25` (recursive) | 56.7–57.5 | 19.1–20.4 | 21.8–22.4 | 21.4–22.8 | 21.2 | 613 M | 350 M | 259 M |
| `loop` (Σ i², 10⁶) | 69.3–73.3 | 39.5–41.7 | 43.4–43.7 | 23.8–24.2 | 40.9–43.3 | 604 M | 335 M | 583 M |
| `strcat` (`.=` / `+=`, 200 k) | **15.5–16.8** | 5,790–7,013 | 10,820–13,238 | 7.3–8.0 | 22,749–24,573 | 183 M | 118 M | not run |
| `strjoin` (join 200 k) | 31.7–35.8 | 22.3 | 24.5–25.8 | 31.3–31.9 | 26.6–27.1 | 419 M | 264 M | 221 M |

- **Natively the two interpreters are level.** Perl takes 0.6× Monty's instructions on `loop` and 1.35× on `fib25`. In wall time native perl wins `loop` by 1.7× and ties `fib25` (EXECUTED).
- **The wasm wall costs us 2.5–3.1× in wall time** against native perl: 56.7–57.5 against 21.4–22.8 ms, and 69.3–73.3 against 23.8–24.2 ms. Fuel runs 1.75–1.80× native perl's Ir, as study C found (1.6–1.9×). So Monty's 1.4–3.0× lead on loops is the wall's price, not a better interpreter (INFERRED).
- **`strcat` is a cliff in Monty.** `s += str(i) + ','` over 200,000 numbers takes 5.8–24.6 s. It grows faster than linearly: 20,000 steps took 49 ms and 40,000 took 385 ms through the CLI (EXECUTED, once each, unlocked probe). CPython and Perl append in place; Monty, it seems, copies the string at each step (INFERRED from the growth).

### 4.4 (d) Text

| task | wasi-perl | Monty checkout | Monty fresh | native perl | monty CLI | wasi-perl fuel | native perl Ir | monty Ir |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `sum_csv` (split, 200 k lines) | 138–150 | 92.3–94.1 | 94.7–96.7 | 57.6–58.4 | 90.0–90.1 | 1,269 M | 770 M | 1,016 M |
| `grep_log` (`/ERROR \d+/` per line) | 104–124 | 74.6–78.6 | 73.1–74.4 | 25.8–26.4 | 59.7–59.8 | 644 M | 353 M | 679 M |
| `sub_log` (`s///g` on 10 MB, then count) | **64.6–74.3** | 5,829–8,512 | 7,101–9,235 | 17.9–18.6 | 7,133–9,246 | 274 M | 78 M | not run |
| `split_join` (whole file) | 215–246 | 105–110 | 129–141 | 151–158 | 111–121 | 1,870 M | 1,286 M | 864 M |

- On per-line work, natively, perl runs fewer instructions than Monty: 770 M against 1,016 M on `sum_csv`, 353 M against 679 M on `grep_log`. Through the wall it loses by 1.4–1.6× (EXECUTED).
- **`re.sub` over a 10 MB string is a second Monty cliff**, 5.8–9.4 s. `re.findall` on the same string takes 13 ms and `str.replace` 16 ms; `re.sub` on the first 1 MB takes 43 ms (EXECUTED, once each, unlocked probe). So the cost grows faster than the input (INFERRED).
- `sub_log`'s fuel is 3.5× native perl's Ir, against 1.6–1.9× elsewhere. That is the regex engine's scanning for literals, which study C found slow through wasi-libc's `memchr`, and which spec 016's SIMD `memchr` targets (INFERRED).
- `split_join` is the one text task where Monty's interpreter is better natively (864 M against 1,286 M).

### 4.5 (e) Dicts, lists and JSON

| task | wasi-perl | Monty checkout | Monty fresh | native perl | monty CLI | wasi-perl fuel | native perl Ir | monty Ir |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `wordfreq` (a dict of 203 k words, sorted) | 1,044–1,136 | 490–504 | 548–557 | 767–814 | 500–537 | 8,348 M | 4,870 M | 5,606 M |
| `sort_ints` (200 k) | 73.1–73.5 | 25.1–26.3 | 30.9–34.7 | 42.1–42.6 | 29.6–32.3 | 906 M | 489 M | 390 M |
| `json_decode` (1.4 MB) | 1,293–1,310 | 15.4 | 24.4–25.2 | 535–541 | 20.4–21.3 | 11,136 M | 6,865 M | 181 M |
| `json_encode` (20 k objects) | 355–359 | 15.5–15.8 | 22.0–24.3 | 154–163 | 20.6–21.9 | 3,232 M | 1,928 M | 184 M |

- **`wordfreq` is level natively in instructions** (4.9 B against 5.6 B), yet Monty is faster in wall time. Perl's `sort { $c{$b} <=> $c{$a} || $a cmp $b }` makes two hash lookups per comparison, n log n times. Python's `key=` computes each key once (INFERRED from the programs). That is the "keyed sort" idiom spec 020 documents (READ: synthesis, spec 8).
- **JSON is the widest gap: 23–85×.** It is a library gap, not an interpreter one: `JSON::PP` runs 6.9 B native instructions, against 0.18 B for `jiter` and Monty's Rust encoder.

### 4.6 (f) Reading host-provided data

Medians in ms (EXECUTED). The "flag" arms are wasi-perl through a copy of wasmtime-py 51.0.0 whose C API carries the file-call memo's 19-line patch (`/home/user/wasi-perl-downloads/target-c-api/release/libwasmtime.so`, sha256 `a604ff29…`), with `allow_blocking_current_thread` off or on (`bench_perl.py flag`, `results/perl-flag-{off,on}-s{1,2}.json`):

| task | wasi-perl, grant | wasi-perl, stdin | **wasi-perl, grant, flag on** | Monty, mount (checkout) | Monty, `inputs=` | Monty, host function | native perl |
|---|---:|---:|---:|---:|---:|---:|---:|
| `read_big` (one 10.2 MB file) | 47.3–53.9 | 20.9–21.6 | **5.8–7.6** | 15.0–15.8 | 19.7–21.9 | 20.6–24.5 | 4.9–6.4 |
| `read_small` (100 files of 1 KiB) | 35.7–39.5 | n/a | **2.3–3.0** | 22.6–23.0 | n/a | 11.4–12.0 (100 calls) | 1.7–1.8 |
| `grep_log` | 104–124 | | **64–80** | 74.6–78.6 | | | 25.8–26.4 |
| `sum_csv` | 134–150 | | **112–114** | 92.3–94.1 | | | 57.6–58.4 |
| `split_join` | 215–246 | | **189–203** | 105–110 | | | 151–158 |
| `hello` | 1.5–2.0 | | 1.6–1.7 | 0.13–0.14 | | | 1.3–1.7 |

- **Today the hand-off dominates our reads.** `read_big` runs 13 M wasm instructions, about 1.5 ms (INFERRED at study C's 8.5 B/s), but takes about 50 ms. That is about 1,250 `fd_read` calls of 8 KiB at about 27 µs each (INFERRED from the file-call memo §3.2). `read_small` makes about 9 calls per file (READ: synthesis §1).
- **With the flag on, wasi-perl reads host files 2–8× faster than Monty** (EXECUTED). Stdout was the same in both arms.
- A Monty host call costs about 0.11–0.12 ms: 100 external-function calls took 11.4–12.0 ms (EXECUTED). Each is a round trip over the worker's pipe. A mount read is an OS call answered the same way, so it pays the same round trip (READ: `docs/limitations/open.md`). With the flag on, a WASI file call costs 0.5–1.5 µs (READ: the file-call memo §3.2).
- Monty can hand the program structured values (`inputs=`, host functions) and wasi-perl cannot; that is a feature, not speed (§6). Spec 017's mounts are the WASI-only way to serve files from the embedder's code.

### 4.7 (g) Memory per call

`results/perl-memory.json`, `results/monty-memory.json` (EXECUTED):

| task | wasi-perl: least linear memory | Monty: fresh worker's peak RSS |
|---|---:|---:|
| `hello` | 13.4 MiB | 11.5–12.1 MiB |
| `grep_log` | 13.5 MiB | 33.2–33.5 MiB |
| `wordfreq` | 46.8 MiB | 53.8–54.2 MiB |
| `json_decode` | 25.3 MiB | 26.4–26.7 MiB |
| `split_join` | 120.5 MiB | 84.5–86.2 MiB |
| `read_big` | 23.2 MiB | 30.6–31.9 MiB |

- The units differ. Ours is the guest's linear memory, which bounds what it may touch. Monty's is a whole process's resident pages, binary included (INFERRED).
- They are level. A streaming one-liner stays at our floor, while Monty buffers a mounted file whole in its heap (`grep_log`: 13.5 against 33 MiB). Whole-file work costs us more (`split_join`).
- The host side is level too: 39.5 MiB for a wasi-perl process, against 28.5 MiB plus a worker of about 12 MiB for Monty (§4.1).

## 5. Summary of speed

wasi-perl's median over Monty `checkout`'s, in each of the two sessions; below 1 means wasi-perl is faster (INFERRED from `results/`, the ratio of the medians):

| group | tasks | wasi-perl ÷ Monty | with the file-call flag on |
|---|---|---|---|
| (b) bare call | hello | 10–16× (about 1× against Monty `fresh`) | unchanged |
| (c) CPU | fib25, loop, strjoin | 1.4–3.0× | |
| (c) string building | strcat | **0.002–0.003×** | |
| (d) text | sum_csv, grep_log, split_join | 1.4–2.2× | 0.86–1.85× |
| (d) big regex substitution | sub_log | **0.008–0.013×** | |
| (e) dict and sort | wordfreq, sort_ints | 2.1–2.9× | 2.0–2.1× (wordfreq) |
| (e) JSON | decode, encode | 23–85× | |
| (f) host files | read_big, read_small | 1.6–3.2× | **0.10–0.50×** |

## 6. What each side cannot do

A feature gap is not a speed win.

**Monty, not wasi-perl (READ, §2):**
- external functions with structured values, pausing at them, and dumping a paused run to kilobytes, to resume elsewhere or more than once;
- a REPL session whose state persists between feeds;
- a type checker before running;
- async host calls.

**wasi-perl, not Monty (READ, §2, and EXECUTED where noted):**
- all of Perl 5.42 and its core library: XS modules, `local`, formats, `tr`, `sprintf`, Perl's own regex engine. Monty runs a subset of Python with no generators, no `del`, no user exceptions, no `re.subn` and no bytes regex;
- writable files with real descriptors: `-pi -e` edits a granted file in place;
- any number of host reads per call. A Monty session stops at 1,000 host round trips unless `max_suspensions` is raised, and every mount read is one. EXECUTED: our first `session` run failed with "RuntimeError: suspension limit 1000 exceeded" while feeding `read_small`, which opens and reads 100 files per feed, after the session's earlier feeds had read files too;
- no cliff on `+=` string building or `re.sub` over large strings (§4.3–4.4).

## 7. The two walls

| | Monty (OSS pool, as timed) | wasi-perl |
|---|---|---|
| what holds the code in | the interpreter itself: a language-level sandbox, "no container, no seccomp filter and no virtual machine" (READ: `docs/security.md`) | WebAssembly: bounds-checked linear memory, and only the WASI preview1 imports |
| what an escape needs | a memory-safety or logic bug in about 148,000 lines of Rust (39 `unsafe` blocks in the core, `unsafe_code = "deny"` elsewhere) or its dependencies: `fancy-regex`, `jiter`, Ruff's parser (READ, EXECUTED: `grep -rn "unsafe {"`). Then it has the worker process, with the application's OS privileges (READ: `docs/alternatives.md`, "Execution environment") | a bug in wasmtime, Cranelift or wasmtime-wasi. A memory bug in perl's own C stays inside linear memory (INFERRED) |
| between callers | a worker is reused for up to 1,000 sessions by default. "A compromised worker could see later sessions until it is replaced"; set the count to 1 when callers do not trust each other (READ: `docs/security.md`) | a fresh instance per call; nothing carries over |
| crashes | a worker panic kills only the worker (subprocess) | a guest trap ends the call; a wasmtime abort would take the host process (INFERRED) |
| host access | external functions, the `os` callback and mounts, all opt-in; mounts confined with `cap_std`; workers start with an empty environment | preopened grants only, confined by wasmtime-wasi's `cap-std`; nothing else |
| limits | allocator-counted memory; time at checkpoints; the wall-clock backstop is off by default | linear memory capped; fuel or an epoch deadline; output capped; all on by default |

**So Monty's 0.13 ms bare call is not like for like.** It is a reused process, behind a wall of Rust code rather than a virtual machine. Its like-for-like mode, a fresh worker per session, costs 1.5–1.6 ms (§4.2), the same as ours. Pydantic sells OS-level isolation separately, as Commercial Support (READ: `docs/server.md`).

## 8. The gaps, and whether we can close them

Each gain is on wasi-perl, measured here (EXECUTED) or estimated (INFERRED, with its source). "Fits" asks:
- (1) WASI preview1 only;
- (2) real Perl 5.42;
- (3) no custom import;
- (4) wasi-python's API kept.

| # | gap | mechanism | gain | cost | risk | fits | already ranked? |
|---|---|---|---|---|---|---|---|
| 1 | **host reads** (f) | set wasmtime-wasi's `allow_blocking_current_thread` before the first preopen, once wasmtime's C API and wasmtime-py expose it | EXECUTED: `read_small` 36–39 → 2.3–3.0 ms, `read_big` 52–54 → 5.8–7.6 ms, `grep_log` 106–113 → 64–80 ms, `sum_csv` 134 → 112–114 ms; bare call unchanged | S on our side (one property in the runner); the patch is upstream's to take | low: same bytes in every arm, here and in the file-call memo | yes: host-side only | **yes**, as "outside this repository" (synthesis §3, owner question 3). **This comparison raises it to first**: it is the only lever that turns a row from a loss against Monty into a 2–8× win |
| 2 | **JSON** (e) | Cpanel::JSON::XS with JSON::MaybeXS, statically linked | INFERRED from study F §3.1: decode 1,293–1,310 → about 46 ms; encode 7×, 355 → about 50 ms. Monty: 15 ms | S–M | low | yes | yes, spec 018 |
| 3 | **first call** (a) | keep the library check across processes, by tree stamp (L6) | READ, study A §4: first call about 110 → about 35 ms; import to answer then about 55–60 ms against Monty's 35–51 | M | medium | yes | yes, spec 015 |
| 4 | **line-oriented text** (d) | 64 KiB PerlIO buffer, SIMD `memchr`, `isatty` from the fd-table | READ, study C §1: `grep_log` 111 → 68 ms; `sub_log`'s 3.5× fuel ratio is the `memchr` target (§4.4). Overlaps gap 1 on `fd_read` counts | S–M | low; SIMD narrows the hosts | yes | yes, spec 016 |
| 5 | **idioms** (e) | docs: keyed sorts, `-0777` for whole-file work | READ, study F §6: 4–6× on the programs they fit; `wordfreq` is the case here | S | none | yes | yes, spec 020 |
| 6 | **the warm call's fixed cost** (b) | Monty's way: keep one live interpreter and start each call from it | INFERRED: up to about 1.2 ms of 1.5 ms (the guest's `_start` and `Store.close`, spec 014's memo §3.1) | M–L | **high**: Perl reads its own linear memory (`unpack "P"`), so a call would see what an earlier call left there. That is the reuse Monty itself warns about (§7). Safe only within one caller | (1)–(3) yes; (4) no: wasi-python has no session API, and the owner's rule is no API change that wasi-python could not adopt | **new.** Not recommended as a default. If wanted, it is a joint wasi-python feature ("a session"), for the owner |
| 7 | **first call, what 015 leaves** (a) | check one file instead of a 1,452-file tree: the library as one archive, or inside `perl.wasm`, served to `require` by a guest-side `@INC` hook | INFERRED: about 10–15 ms of 015's remaining first call (study A's L6 cost), plus some stats per module load that spec 019's index already removes | M | medium: `%INC` and `__FILE__` read differently; the hash still covers the same bytes | yes: an `@INC` hook is plain Perl, and the archive is read through WASI | **new**, but small once 015 and 019 land. Low rank |
| 8 | **CPU loops** (c) | the wasm wall's price: 1.75–1.8× instructions, 2.5–3.1× wall against native perl | READ: `wasm-opt -O3`, −2.7% (study B); nothing else measured gains (synthesis §4) | S | low–medium | yes | yes, spec 021. The rest cannot be closed: running perl natively in-process would remove the wall |
| 9 | **snapshots of a running call** | Monty serializes its own heap and frames | none in speed | not feasible: wasmtime keeps the wasm call stack outside linear memory, so a paused call cannot be dumped and resumed without Asyncify, which the constitution forbids (INFERRED). A start image is spec 022 | | no | spec 022 covers start images; mid-run snapshots are ruled out |
| 10 | **host functions** | structured calls to the embedder | a feature, not speed | | | a custom import would break (3) | spec 017's mounts are the preview1 form |

**What is new here:** rows 6 and 7. Neither is worth building now: row 6 weakens the wall, and row 7 is small after 015 and 019. **What changes rank:** row 1, which deserves the owner's answer on going upstream first (synthesis §5, question 3).

**What we should not copy:** the process reuse that gives Monty its 0.13 ms (§7). We should not copy its cliffs either. A wasi-perl docs page may say plainly that `.=` and `s///g` over large strings are linear in Perl.

## 9. Limits

- One shared VM, two sessions. Sessions differ by up to about 15%. Every comparison is within one session's run, and the counts are the primary evidence.
- Monty's workers, natively compiled, ran on Linux x86-64 only. Its published numbers are from macOS arm64.
- Fuel (wasm) and `Ir` (x86-64) are different units. Their ratio is a proxy only.
- Monty's CLI and pool differ on `strcat` by 4× (5.8 s against 24 s), for a reason not studied. Both are cliffs.
- The probes of Monty's growth on `strcat` and `re.sub` (§4.3–4.4) ran once each, without the lock. They show shape, not timing.
- The flag arms use an unreleased, patched wasmtime C API. They show what an upstream release would give. They are not something the runner can ship today.
- The `monty-wasm-runtime` component was not built: it needs Rust 1.98 (1.97 is here) and a component host. So Monty's instructions under wasm were not counted.
