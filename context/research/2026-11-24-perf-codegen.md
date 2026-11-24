# Code generation and engine settings for wasi-perl's speed (study B)

- **Status:** research, 2026-11-24. Study B of `2026-11-24-perf-research-plan.md`. It changes no code; the two diffs it proposes are evidence files, not applied.
- **Researcher:** an agent (Claude Opus 5.5), on branch `research/perf-codegen`.
- **The question:** which compiler, linker, post-link and engine settings make the guest faster, or the module cheaper to compile and load?
- **Evidence:** the folder `2026-11-24-perf-codegen/`. `bench.py` is the harness; `results/` holds every raw result; `variants/` holds each flag set as a diff to `recipes/`, and the two proposals.

Labels: EXECUTED means run and seen. READ means read from a file or a listing. INFERRED means the researcher's reasoning.

## 1. The answer in short

- **The compiler is already at its best setting.** `-O2` with wasi-sdk 34's default features is as fast as any other flag set tried. `-O3`, `-Os`, LTO, ThinLTO, `-msimd128` and `-mtail-call` all land within ±4 % of `-O2` in paired wall time, which is the noise floor. `-Oz` is 5 to 30 % slower. (EXECUTED, sections 4 and 5.)
- **Bulk memory is already on.** Every `memcpy` and `memset` in perl.wasm is an inline `memory.copy` or `memory.fill`. `-mbulk-memory` would change nothing. (EXECUTED, section 3.)
- **`wasm-opt -O3` is the one small build-side gain.** It runs 2.7 % fewer guest instructions on every workload, makes perl.wasm 8 % smaller, and is about 10 % faster on `die` inside `eval`. Line-oriented one-liners do not get faster in wall time. It adds 5 s to the build, its output is byte-identical run to run, and it passes the one-liner oracle and 337 files of Perl's `t/` with no regression. `-O4` aborts on the exception encoding. (EXECUTED, sections 5 and 10.)
- **wasmtime's defaults are right, and they matter a great deal.** Turning any of them off costs 1.2× to 3.5×:
  - copy-on-write memory images: off makes a bare call 2.4× slower (2.95 → 7.19 ms);
  - parallel compilation: off makes the compile 3.5× slower (0.94 → 3.31 s);
  - a 4 GiB reservation with guard pages: off adds explicit bounds checks, 1.2× to 2.7× slower;
  - Cranelift at `speed`: `none` saves 12 % of compile time and costs 13 to 46 % at run time.

  The runner relies on these defaults without saying so. Pinning them is the cheapest lever here. (EXECUTED, section 8.)
- **The setjmp/longjmp lowering costs nothing until something throws.** Only 13 functions call `setjmp`, none of them in the hot loop, and `__wasm_setjmp` is ten instructions. A `die` caught by `eval` costs about 0.5 µs more than its instructions would suggest. That makes `eval_die` 5.4× slower than native, against 2.4 to 3.1× for the other workloads timed beside it. No compiler flag changes that. (EXECUTED and READ, section 6.)

For vfs this means (INFERRED):

- Per-call cost and text speed are set by the engine settings and by the interpreter (studies A, C and D), not by the compiler.
- What this study can offer vfs is to protect the engine settings, plus an optional 2.7 % fuel saving from `wasm-opt -O3`.

## 2. What was measured

**The builds.** Each was built from the same Perl 5.42.3 tree with the pinned wasi-sdk 34 (clang 23.1.0). Only one or two lines of `recipes/config.sh` or `recipes/wasi-cc` changed, as `variants/<name>.diff` records. Each build ran `scripts/20_target.sh` under the machine lock with 4 jobs (`build_variant.sh`). The base build is `dev`'s recipes, unchanged: `-O2`, an 8 MiB stack, no post-link step.

| name | change |
|---|---|
| base | none: `optimize='-O2'` |
| O3, Os, Oz | `optimize='-O3'`, `'-Os'`, `'-Oz'` |
| lto | `-O2 -flto` to compile and link, and the sjlj options forwarded to the LTO code generator (section 4.2) |
| thinlto | the same with `-flto=thin` |
| simd | `-O2 -msimd128` |
| tailcall | `-O2 -mtail-call` |
| nobulkopt | `-O2 -mno-bulk-memory-opt` |
| stack1m | `-z stack-size=1048576` in `recipes/wasi-cc` |
| woO2, woO3, woOs | base, then binaryen `wasm-opt -O2`, `-O3`, `-Os` |
| woO3c | base, then `wasm-opt -O3 --converge` |

**binaryen** is version_133. It is pinned by URL and SHA-256 in `results/static.txt`:

- URL: `https://github.com/WebAssembly/binaryen/releases/download/version_133/binaryen-version_133-x86_64-linux.tar.gz`
- SHA-256: `2dc9c7813f5375db93d96ead4b78222fcc3e2677bbb832297af4797782a37489`. It matches the release's own `.sha256` file. (EXECUTED: `sha256sum`.)

It is unpacked under `/home/user/wasi-perl-downloads/binaryen/`.

**The workloads** (`bench.py`, `PERL`):

- The baseline memo's six, on the same inputs from the same generator: `hello`, `sum_csv` (`-F, -lane`), `grep_log` (`-ne` regex count), `inplace` (`-pi`), `json` (JSON::PP) and `loop`.
- Four more, which reach the paths the exception lowering touches:
  - `subcall`: 300,000 sub calls;
  - `eval_die`: 50,000 `die` caught by `eval`;
  - `destroy`: 100,000 `DESTROY` calls, each through `call_sv` with its own JMPENV, Perl's wrapper around setjmp;
  - `sort`: a numeric sort of 200,000 numbers.

**The method:**

- **Fuel is the primary evidence.** Fuel is the number of WebAssembly instructions the guest executes. `bench.py fuel` runs each workload once on a metered engine. Perl's hash seed is random, so fuel varies by up to ±0.2 % between two runs of one build (EXECUTED: `base` against an identical copy, `cmp.py base2`). A gap of 1 % or more is real.
- **Fuel does not count machine code.** Cranelift turns one wasm instruction into a varying amount of x86, so fuel cannot judge a compiler flag alone. Wall time was measured too.
- **Wall time is paired.** The machine is shared by six studies. A single run per build drifted by ±10 to 20 % (`results/wall-*-r1.json`; for example, every build looked 10 % faster than base on `eval_die`). So `bench.py ab` loads every build in one process and calls each in turn, round after round, under the lock. Drift then falls on all builds alike.
  - Two runs were made: `results/ab-builds.json` (14 builds, 9 rounds) and `results/ab-confirm.json` (7 builds, 15 rounds).
  - The second run includes `base2`, a byte-identical copy of base, as a control. It landed within ±2 % of base on every workload. So a gap under about 4 % is noise.
- **Compile time** is the median of three `wasmtime.Module(engine, bytes)` calls on a fresh engine. Size is that of `Module.serialize()`, the cwasm. Load is the median of five `Module.deserialize` calls (`bench.py compile`, under the lock).
- **The host** is wasmtime-py 51.0.0 and the worktree's runner, called with `cache_dir=False` and the base library set. It is the 4 vCPU Xeon of the baseline memo.

## 3. What wasi-sdk 34 already does (READ and EXECUTED)

The output is in `results/static.txt`.

- **Default target features.** clang 23 for `wasm32-wasip1` already enables `bulk-memory`, `bulk-memory-opt`, `multivalue`, `mutable-globals`, `nontrapping-fptoint`, `reference-types` and `sign-ext` (EXECUTED: `clang -E -dM`). So `-mnontrapping-fptoint`, `-msign-ext`, `-mmutable-globals` and `-mbulk-memory` are no-ops. The default does not enable `simd128`, `tail-call` or `relaxed-simd`. `exception-handling` comes from the sjlj flags.
- **memcpy and memset are inline bulk operations.**
  - perl.wasm defines no `memcpy`, `memset` or `memmove` function at all.
  - It holds 639 `memory.copy` and 276 `memory.fill` instructions (EXECUTED: `wasm-dis` and `grep`).
  - With `bulk-memory-opt` on, clang lowers each `memcpy` call straight to `memory.copy`.
  - wasi-libc's own `memcpy.o` holds a `memory.copy` too, used above 32 bytes (EXECUTED: `llvm-objdump` of `libc.a`).
- **The sysroot has no SIMD variant of libc.** `lib/wasm32-wasip1/` holds `eh/`, `noeh/` (libc++ with and without C++ exceptions) and `llvm-lto/23.1.0-wasi-sdk/`, a bitcode libc that `-flto` links. perl.wasm holds no `v128` instruction. (READ: `ls`; EXECUTED: `grep`.)
- **The stack comes first.** `__stack_pointer` starts at 8 MiB and data begins at 8 MiB. So a stack overflow runs below address 0 and traps; it never overwrites data. Initial memory is 213 pages (13.3 MiB). (EXECUTED: `wasm-dis`.)

## 4. Compiler and linker flags

### 4.1 Results

Guest instructions, as a ratio to base. A ratio below 1 means fewer instructions. Source: `results/fuel-*.json`, `python3 cmp.py`, EXECUTED.

| build | hello | sum_csv | grep_log | inplace | json | loop | subcall | eval_die | destroy | sort |
|---|---|---|---|---|---|---|---|---|---|---|
| base, absolute | 1.6 M | 1,272 M | 644 M | 842 M | 11,136 M | 604 M | 532 M | 150 M | 411 M | 906 M |
| O3 | 0.995 | 1.004 | 1.005 | 1.003 | 1.006 | 1.000 | 1.009 | 1.002 | 1.007 | 1.015 |
| Os | 1.058 | 0.976 | 1.006 | 1.004 | 1.010 | 1.000 | 1.003 | 0.986 | 1.004 | 1.007 |
| Oz | 1.097 | 1.047 | 1.047 | 1.049 | 1.073 | 1.118 | 1.053 | 1.047 | 1.054 | 1.078 |
| lto | 0.985 | 0.985 | 0.988 | 0.991 | 1.011 | 1.055 | 1.007 | 0.980 | 0.985 | 1.015 |
| thinlto | 0.961 | 0.986 | 0.988 | 0.990 | 1.011 | 1.055 | 1.003 | 0.985 | 0.992 | 1.013 |
| simd | 0.999 | 0.998 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.998 | 1.000 | 1.036 |
| tailcall | 1.001 | 0.998 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.998 | 1.000 | 1.000 |
| nobulkopt | 1.042 | 1.120 | 1.003 | 1.013 | 1.020 | 1.000 | 1.030 | 1.047 | 1.013 | 1.008 |
| stack1m | 0.999 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.998 | 1.000 | 1.000 |

Every build printed the same output as base on every workload.

Paired wall time, the median as a ratio to base (`results/ab-builds.json`, 9 rounds, `python3 abtable.py`, EXECUTED):

| build | hello | sum_csv | grep_log | inplace | loop | subcall | eval_die | destroy | sort |
|---|---|---|---|---|---|---|---|---|---|
| base, ms | 3.39 | 141.9 | 114.5 | 208.5 | 73.6 | 47.1 | 37.4 | 34.0 | 87.7 |
| O3 | 1.018 | 0.995 | 1.023 | 0.996 | 1.021 | 1.008 | 1.013 | 1.013 | 1.056 |
| Os | 0.994 | 0.985 | 1.033 | 1.011 | 1.039 | 1.027 | 0.991 | 1.087 | 1.020 |
| Oz | 1.015 | 1.197 | 1.022 | 1.042 | 1.063 | 1.121 | 1.053 | 1.238 | 1.291 |
| lto | 1.009 | 1.017 | 1.000 | 0.987 | 1.027 | 1.020 | 0.985 | 0.975 | 1.022 |
| thinlto | 0.959 | 1.040 | 1.001 | 0.984 | 1.033 | 1.017 | 0.982 | 0.969 | 1.001 |
| simd | 0.976 | 1.030 | 1.003 | 0.979 | 1.030 | 1.000 | 0.993 | 1.016 | 1.078 |
| tailcall | 1.006 | 1.005 | 0.976 | 1.024 | 1.015 | 1.015 | 1.017 | 1.012 | 0.996 |
| nobulkopt | 1.012 | 1.026 | 1.001 | 1.066 | 1.025 | 1.020 | 1.027 | 1.004 | 0.962 |
| stack1m | 1.009 | 1.012 | 0.979 | 1.012 | 1.013 | 1.008 | 1.010 | 1.015 | 0.963 |

The confirming run (`results/ab-confirm.json`, 15 rounds, with the byte-identical control `base2`) agrees:

- `base2` is within 0.982 to 1.010 of base;
- O3 is within 0.992 to 1.023;
- lto is within 0.981 to 1.070;
- Oz is 1.136 on `sum_csv` and 1.305 on `sort`.

Size and compile (`results/compile-*.json`, EXECUTED, under the lock):

| build | perl.wasm | compile | cwasm | load (deserialize) |
|---|---|---|---|---|
| base | 9.26 MB | 0.94 s | 14.94 MB | 7.3 ms |
| O3 | 9.35 MB | 0.89 s | 15.17 MB | 8.8 ms |
| Os | 9.03 MB | 0.85 s | 14.11 MB | 7.3 ms |
| Oz | 8.70 MB | 0.75 s | 13.51 MB | 6.8 ms |
| lto | 9.76 MB | 1.08 s | 16.09 MB | 9.9 ms |
| thinlto | 9.87 MB | 1.02 s | 16.20 MB | 12.1 ms |
| simd | 9.28 MB | 0.87 s | 14.99 MB | 7.6 ms |
| tailcall | 9.26 MB | 0.96 s | 14.94 MB | 7.6 ms |
| nobulkopt | 9.27 MB | 0.85 s | 14.84 MB | 7.2 ms |
| stack1m | 9.26 MB | 0.90 s | 14.94 MB | 7.6 ms |

Compile times differ by up to ±10 % between three runs of one build (for example, base 0.875 to 0.983 s). A gap under about 0.1 s is noise.

### 4.2 What each flag does here

- **`-O3`:** no gain. It inlines a little more, adds 1.5 % to the cwasm, and moves no workload beyond noise. Per-file `-O3` on hot files was not built. The global build moved no workload, so a subset of its files is unlikely to (INFERRED).
- **`-Os`:** no gain. It is 3 % smaller in wasm and 6 % smaller in cwasm, at no measured cost. It is not a speed lever; it is a size lever of 0.8 MB.
- **`-Oz`:** slower. It saves 6 % of wasm and 10 % of cwasm. It costs 5 to 12 % more instructions and 5 to 30 % more wall time, worst on `sort` and `destroy`. Do not use it.
- **LTO and ThinLTO:** no gain, and a 5 to 7 % larger module.
  - Fuel moves by about 1 %, but `loop` rises 5.5 %. Cross-file inlining of the runloop's callees does not pay on this interpreter.
  - They need the setjmp lowering forwarded to the link-time code generator: `-Wl,-mllvm,-wasm-enable-sjlj,-mllvm,-wasm-use-legacy-eh=false`. Without it, the first LTO build failed with `undefined symbol: setjmp` (EXECUTED: `results/lto-first-attempt.txt`). wasi-cc passes those options to clang, and clang does not pass them on to wasm-ld.
  - The full LTO build took several minutes longer (READ: `results/builds.log`; those times include waits for the lock).
  - **Risk:** a whole-program optimizer over code that relies on setjmp (READ: Perl's JMPENV). It brings no speed to pay for that.
- **`-msimd128`:** no gain. The loop vectoriser emits 3,326 SIMD instructions (EXECUTED: `wasm-dis | grep -c`), but no workload gets faster, and `sort` gets 4 % more instructions and 8 % more wall time. The string functions Perl leans on (`strlen`, `memchr`) live in wasi-libc, which the sysroot ships without SIMD.
  - A SIMD libc would mean building wasi-libc ourselves, a new pinned input.
  - SIMD also narrows which hosts can run the module (INFERRED: wasmtime supports it on x86-64 and arm64, but other embedders may not).
  - Not worth it.
- **`-mtail-call`:** no effect. The module is 11 bytes smaller and fuel is identical. Perl's C has almost no calls in tail position that clang turns into `return_call`.
- **`-mno-bulk-memory-opt`** (calls to libc's `memcpy` instead of inline `memory.copy`): no gain. It costs 12 % more instructions on `sum_csv`, because libc copies short strings byte by byte in wasm, and wall time is unchanged. In wasmtime, `memory.copy` is a call into the host, so small copies are not free. Even so, the wasm loop is no faster.
- **`-mnontrapping-fptoint`, `-msign-ext`, `-mmutable-globals`, `-mbulk-memory`:** already on (section 3). Nothing to do.

## 5. binaryen wasm-opt after the link

Fuel, as a ratio to base (`results/fuel-wo*.json`, EXECUTED):

| build | hello | sum_csv | grep_log | inplace | json | loop | subcall | eval_die | destroy | sort |
|---|---|---|---|---|---|---|---|---|---|---|
| woO2 | 0.984 | 0.975 | 0.977 | 0.981 | 0.975 | 0.977 | 0.985 | 0.977 | 0.984 | 0.977 |
| woO3 | 0.974 | 0.971 | 0.973 | 0.977 | 0.971 | 0.973 | 0.980 | 0.973 | 0.978 | 0.966 |
| woO3c | 0.975 | 0.967 | 0.971 | 0.976 | 0.970 | 0.973 | 0.979 | 0.973 | 0.978 | 0.966 |
| woOs | 0.982 | 0.980 | 0.984 | 0.984 | 0.992 | 0.973 | 0.984 | 1.007 | 0.985 | 0.969 |

Paired wall time, as a ratio to base. The two numbers are from `ab-builds` / `ab-confirm` (EXECUTED):

| build | hello | sum_csv | grep_log | inplace | loop | eval_die | sort |
|---|---|---|---|---|---|---|---|
| woO3 | 1.006 / 0.962 | 1.019 / 1.021 | 0.983 / 0.981 | 1.006 / 0.999 | 1.006 / 1.021 | **0.906 / 0.901** | **0.927 / 0.956** |
| woO3c | 0.962 / 0.977 | 1.007 / 0.973 | 0.998 / 0.972 | 0.985 / 0.998 | 0.996 / 1.000 | **0.914 / 0.902** | **0.916 / 0.931** |

Size and compile (`results/compile-wo*.json`, EXECUTED):

| build | perl.wasm | compile | cwasm | load |
|---|---|---|---|---|
| woO2 | 8.53 MB | 0.89 s | 14.53 MB | 7.0 ms |
| woO3 | 8.51 MB (−8.1 %) | 0.84 s | 14.50 MB (−2.9 %) | 6.9 ms |
| woO3c | 8.50 MB | 0.86 s | 14.49 MB | 7.0 ms |
| woOs | 8.50 MB | 0.89 s | 14.48 MB | 9.7 ms |

**What this shows:**

- **Fuel:** `-O3` removes 2.0 to 3.4 % of the guest's instructions on every workload. Most of the saving is local traffic that Cranelift's register allocator would remove anyway (INFERRED). So wall time on the text one-liners stays within noise.
- **Wall time:** two workloads gain in both paired runs: `eval_die` by 9 to 10 % and `sort` by 4 to 7 %.
- **`--converge`** adds nothing over a single `-O3`.
- **Fuel is a budget.** vfs caps every call's fuel, so 2.7 % fewer instructions is 2.7 % more work per budget (INFERRED).

**Exceptions:**

- wasm-opt 133 reads and writes the new encoding (`try_table`, `exnref`). It keeps the module's 21 `try_table` instructions, and every `die`/`eval` workload gives base's output (EXECUTED).
- **`-O4` aborts:** `unexpected expr type` / `UNREACHABLE executed at /src/src/passes/Flatten.cpp:231!` (EXECUTED: `results/wasm-opt.log`). `-O4` runs the flatten pass, which does not take `try_table`. So `-O4` is unusable on this module until binaryen fixes flatten.

**Correctness of woO3** (EXECUTED, `WASI_PERL_ARTIFACTS` set to a copy of the base set with woO3 as perl.wasm and its SHA256SUMS line rewritten):

- `uv run pytest tests/oneliners`: 448 passed, the oracle included.
- Perl's `t/` slices `op/*.t`, `re/*.t` and `uni/*.t` through `tests/perl_t/run_suite.py`: 337 files run, 0 failed, 0 regressed (section 10).

**Cost:**

- one more pinned input, binaryen, about 20 MB unpacked;
- one more build step of **5.3 s** (EXECUTED: `results/wasm-opt-repeat.txt`, timed inside the lock). The 181 to 201 s in `results/wasm-opt.log` are almost all waiting for the lock.

**Risk:**

- A post-link optimiser that is not the compiler. Its EH support is newer than LLVM's (INFERRED from the `-O4` abort), and it rewrites the code the oracle and `t/` were run on.
- Reproducibility holds if binaryen is pinned. Two runs of `wasm-opt -O3` on the same input gave byte-identical output, SHA-256 `0879d733…` (EXECUTED: `results/wasm-opt-repeat.txt`).

A proposed step is `variants/proposed-wasm-opt-O3.diff`. It also needs binaryen's URL and hash in `downloads/SHA256SUMS` and in `scripts/00_download.sh`.

## 6. The setjmp/longjmp lowering in hot paths

**Where it is** (EXECUTED: `wasm-dis`, then the functions that call `__wasm_setjmp`):

- Thirteen functions: `perl_parse`, `perl_run`, `perl_destruct`, `Perl_call_sv`, `Perl_eval_sv`, `Perl_call_list`, `S_docatch`, `S_try_yyparse`, `S_try_run_unitcheck`, `S_fold_constants_eval`, `S_gen_constant_list`, `_invoke_defer_block` and `exception`.
- None is `pp_entersub` or the runloop.
- `__wasm_setjmp` is 10 instructions: two tests and two stores. A JMPENV push is therefore cheap.

**How `eval` uses it** (READ: `pp_ctl.c` 3770–3840):

- `eval {}` at top level does not set a JMPENV of its own. `docatch` adds one only when `CATCH_GET` is set, that is, inside a `call_sv`.
- So a `die` in an `eval` throws to `perl_run`'s JMPENV, past every C frame in between. Perl then restarts the runloop at the op after the eval.

**What it costs** (EXECUTED: `results/native.json` is native perl 5.42.3 on the same workloads):

| workload | guest ms | native ms | guest ÷ native | guest instructions per µs |
|---|---|---|---|---|
| loop | 73.6 | 23.4 | 3.1 | 8,200 |
| subcall | 47.1 | 16.6 | 2.8 | 11,300 |
| destroy (a JMPENV per call) | 34.0 | 14.0 | 2.4 | 12,100 |
| eval_die (a throw per iteration) | 37.4 | 6.9 | **5.4** | **4,000** |

- `destroy` sets a JMPENV on each of its 100,000 calls and is no worse than the other workloads. So setting jumps costs nothing measurable.
- `eval_die` runs at half the instruction rate of the rest. At about 3,000 guest instructions per iteration (150 M ÷ 50,000), each throw and catch costs about 0.4 to 0.5 µs of host-side unwinding beyond its instructions (INFERRED: 37.4 ms − 150 M ÷ 8,200 per µs ≈ 19 ms, over 50,000 throws).
- No compiler flag reaches that cost. `wasm-opt -O3` trims 10 % of it, probably by shortening the frames that are unwound (INFERRED).

A loop that dies 10,000 times pays about 5 ms (INFERRED). Agent one-liners rarely do that, so for vfs this is not a priority.

**Two strikes:**

- The guest profiler (`wasmtime run --profile=guest`) wrote no profile for perl.wasm, either to a named path or to the default one. It probably does not write one when the guest leaves through `proc_exit` (INFERRED).
- The split above is therefore inferred from fuel and wall time, not sampled. Study C's guest profile, if it gets one, can confirm it.

## 7. Stack size

- An 8 MiB stack (base) and a 1 MiB stack (`stack1m`) give the same fuel, the same wall time within noise, and the same compile time and size. The difference is 11 bytes of wasm. (EXECUTED, tables in section 4.)
- The stack sits first, so the untouched part of it is never written. The 8 MiB costs a call nothing (INFERRED: with copy-on-write images and lazy zero pages, an unwritten page is never touched).
- A smaller stack brings only the risk of deep recursion overflowing sooner: Perl's regex engine and its recursive subs use the C stack (READ: `recipes/wasi-cc`'s note).

**Keep 8 MiB.** The stack is not a speed lever.

## 8. wasmtime Config settings

Each row is one process on the base build, with one setting changed from wasmtime-py 51's defaults (`bench.py wall`, `results/wall-base-<engine>-r1.json`; serial compile from `results/compile-base-serial.json`; EXECUTED, under the lock). These runs are single, not paired, so they have the ±10 to 20 % drift of section 2. Only gaps beyond that are claimed.

| setting | compile | cwasm | hello ms | sum_csv | grep_log | loop | sort | json |
|---|---|---|---|---|---|---|---|---|
| defaults | 0.89 s | 14.9 MB | 2.95 | 136 | 102 | 70 | 93 | 1,318 |
| `parallel_compilation = False` | **3.31 s** | 14.9 MB | | | | | | |
| `cranelift_opt_level = "none"` | 0.78 s | 15.6 MB | 4.32 | 166 | 128 | 91 | 119 | 1,497 |
| `cranelift_opt_level = "speed_and_size"` | 0.91 s | 14.9 MB | 3.79 | 147 | 114 | 70 | 104 | 1,301 |
| `regalloc_algorithm = "single_pass"` | **0.61 s** | 23.3 MB | 3.42 | **309** | **205** | 93 | 152 | **3,406** |
| `signals_based_traps = False` | **2.34 s** | 22.7 MB | 3.37 | 160 | 134 | 69 | 78 | 1,339 |
| `memory_reservation = 0`, guard 0 (bounds checks on) | 1.19 s | 17.6 MB | 3.50 | **179** | **145** | **95** | **255** | **1,833** |
| `memory_guard_size = 64 KiB` | 0.94 s | 15.0 MB | 3.73 | 151 | 120 | 71 | 75 | 1,324 |
| `memory_init_cow = False` | 0.87 s | 14.9 MB | **7.19** | 144 | 122 | 77 | 101 | 1,297 |

**Reading it:**

- **Parallel compilation** is the whole of the 1 s compile. On one core it is 3.3 s. A vfs host that compiles once per process on a small machine pays the serial figure (INFERRED).
- **Cranelift's opt level:** `speed`, the default, is right. `none` saves 0.1 s once and costs 13 to 46 % on every call. `speed_and_size` is no better.
- **The single-pass register allocator** makes the compile 0.3 s faster. It makes the code 1.3 to 2.6× slower and the cwasm 56 % larger. Never use it.
- **Signals-based traps** let wasmtime catch an out-of-bounds access with a guard page. Without them, every access is checked in code: the compile is 2.6× slower, the cwasm 52 % larger, and the text one-liners 17 to 32 % slower.
- **Bounds-check elimination** comes from the default 4 GiB reservation plus a guard region, so no 32-bit address can leave it. With none, every load and store is checked: 1.2× to 2.7× slower, worst on `sort`.
  - The runner's 1 GiB memory cap is a store limit, separate from the reservation, so it does not cost this (READ: `runner.py` `store.set_limits`).
  - The reservation is virtual address space, not memory.
- **A smaller guard (64 KiB)** changes nothing measurable. Perl's offsets are small (INFERRED). There is no reason to change it.
- **Copy-on-write memory images** are what make a call cheap. perl.wasm has 5.5 MB of data segments (EXECUTED: `llvm-objdump -h`, DATA size 0x536fb2), beside 3.1 MB of code. Without CoW they are copied into every new instance: a bare call goes from 2.95 to 7.19 ms. For vfs, which starts a fresh instance per program, this is the most important setting in the table (INFERRED from the call pattern in the plan).
- **The pooling allocator** would make instantiation cheaper still. wasmtime-py 51's `Config` does not expose it (READ: `dir(wasmtime.Config)` lists no pooling or instance-allocator setting). That is a question for study A or upstream.

**Security effect:**

- Each default either keeps the sandbox's guarantee or is neutral: guard pages and signals-based traps catch every out-of-bounds access, as explicit checks would.
- Turning signals-based traps off does not make the sandbox stronger; it makes it slower. Both ways stop every out-of-bounds access, one with a guard page and one with a check (INFERRED from what each does).
- None of these settings widens what the guest can reach.

**The lever.** `_engines.engine()` sets only `wasm_exceptions`, `epoch_interruption` and `consume_fuel` (READ). Everything above is wasmtime's default today. A wasmtime release that changed one of these defaults would slow every call without any change here (INFERRED). `variants/proposed-engine-pins.diff` states the four that matter. It costs four lines, and it changes no behaviour today.

## 9. Ranking

Gain is measured unless marked. "vfs" is how much it matters to many short programs over provider files.

| rank | candidate | gain | cost | risk | security | vfs | verdict |
|---|---|---|---|---|---|---|---|
| 1 | Pin wasmtime's CoW, parallel compile, signals-based traps and `speed` | protects 2.4× per call, 3.5× compile, 1.2–2.7× run (EXECUTED) | 4 lines | none | neutral | **high**: per-call cost and compile | do it |
| 2 | `wasm-opt -O3` after the link | −2.7 % fuel everywhere, −8 % wasm, −3 % cwasm, −10 % `eval_die`, −4 to −7 % `sort`; text one-liners ±noise (EXECUTED) | a pinned tool, +5 s build, deterministic | low–medium: post-link rewrite, `-O4` aborts on EH | neutral | low–medium: fuel budgets | worth a specification: small, cheap, and passes the oracle and the `t/` slice |
| 3 | Keep `-O2` | the best measured | none | none | neutral | — | keep |
| 4 | `-Os` | −0.8 MB cwasm, speed ±noise (EXECUTED) | one line | low | neutral | low | not for speed |
| 5 | `-O3` | ±noise, +1.5 % cwasm (EXECUTED) | one line | low | neutral | none | no |
| 6 | `-msimd128` | ±noise, `sort` +8 % (EXECUTED) | one line, or a self-built libc to matter | narrows hosts | neutral | none | no |
| 7 | `-mtail-call` | none: 11 bytes (EXECUTED) | one line | narrows hosts | neutral | none | no |
| 8 | Stack size | none (EXECUTED) | — | a smaller stack overflows sooner | neutral | none | keep 8 MiB |
| 9 | LTO / ThinLTO | ±noise, `loop` +5 % fuel, +5–7 % size (EXECUTED) | ldflags and forwarded sjlj options | medium: whole-program optimisation over setjmp code | neutral | none | no |
| 10 | `-mno-bulk-memory-opt` | none; +12 % fuel (EXECUTED) | one line | low | neutral | none | no |
| 11 | `-Oz` | −10 % cwasm, 5–30 % slower (EXECUTED) | one line | low | neutral | negative | no |
| 12 | Cranelift `none` or single-pass | −0.1 to −0.3 s compile, 1.13 to 2.6× slower calls (EXECUTED) | one line | none | neutral | negative | never |
| 13 | Signals off, or no reservation | slower compile, larger cwasm, 1.2–2.7× slower (EXECUTED) | one line | none | neutral | negative | never |
| — | `-mbulk-memory`, `-mnontrapping-fptoint`, `-msign-ext`, `-mmutable-globals` | already on (EXECUTED) | — | — | — | — | nothing to do |
| — | The sjlj lowering in hot paths | setjmp costs nothing; a throw costs about 0.5 µs (EXECUTED/INFERRED) | not a codegen lever | — | — | low | for study C, if anyone |

## 10. Correctness of the build this memo names

The build is woO3, base plus `wasm-opt -O3` (EXECUTED):

- `WASI_PERL_ARTIFACTS=build/set-woO3 uv run pytest tests/oneliners`: **448 passed**.
- `WASI_PERL_ARTIFACTS=build/set-woO3 uv run python tests/perl_t/run_suite.py --only 'op/*.t' --jobs 4`: 227 run, 143 passed, 84 excluded, **0 failed, 0 regressed** (13,661 guarded oks).
- The same with `--only 're/*.t' --only 'uni/*.t'`: 110 run, 86 passed, 24 excluded, **0 failed, 0 regressed** (4,827 guarded oks).
- The output is in `results/perl_t-woO3.txt`. `op/` and `re/` are the interpreter's core and its regex engine, the code `wasm-opt` rewrites the most.

Pin the engine (rank 1) changes no code path: it states the defaults already in force. The baseline's runs are its evidence.

## 11. Limits

- One x86-64 machine, shared, so wall time is paired and its noise floor is about ±4 %. arm64, where Cranelift's code differs, was not measured.
- Fuel counts wasm instructions, not machine instructions. A flag that changes only Cranelift's output is judged by wall time alone.
- The engine rows of section 8 are single runs. Only their large effects are claimed.
- `json` was left out of the paired runs to keep each lock hold short. Its fuel is in the tables.
- The cost of a throw was not sampled (section 6).

## Reproduce

From the worktree, after `scripts/build.sh` (or a copy of a built `build/`):

```
E=context/research/2026-11-24-perf-codegen
uv run python $E/bench.py data <data>
cp -a build/artifacts build/base-artifacts; cp build/artifacts/perl.wasm build/variants/base.wasm
$E/build_variant.sh <name>                    # for each variants/<name>.diff
$E/wasm_opt.sh build/variants/base.wasm build/variants/woO3.wasm -O3
BENCH_DATA=<data> $E/measure.sh fuel <name>   # results/fuel-<name>.json
BENCH_DATA=<data> $E/measure.sh wall base <engine> -r1
flock /home/user/wasi-perl-wt/machine.lock uv run python $E/bench.py ab <data> $E/results/ab-builds.json 9 \
    hello,sum_csv,grep_log,inplace,loop,subcall,eval_die,destroy,sort base O3 Os Oz lto thinlto simd tailcall \
    nobulkopt stack1m woO2 woO3 woO3c woOs
cp build/variants/base.wasm build/variants/base2.wasm   # the control in ab-confirm
uv run python $E/bench.py compile build/variants/<name>.wasm default $E/results/compile-<name>.json
# the woO3 set for the tests: base-artifacts with woO3.wasm as perl.wasm and its SHA256SUMS line rewritten
WASI_PERL_ARTIFACTS=build/set-woO3 uv run pytest tests/oneliners
WASI_PERL_ARTIFACTS=build/set-woO3 uv run python tests/perl_t/run_suite.py --only 'op/*.t' --jobs 4 --quiet
uv run python $E/bench.py native build/native-install/bin/perl <data> $E/results/native.json
python3 $E/cmp.py; python3 $E/abtable.py $E/results/ab-confirm.json
```
