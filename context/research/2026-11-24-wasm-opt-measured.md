# wasm-opt -O3 after the link, measured

- **Status:** research, 2026-11-24. The numbers specification 021 asks for: what binaryen's `wasm-opt -O3` does to the shipped `perl.wasm`'s size, sections and guest instructions, and what it does to the figures of specifications 019 and 020.
- **Researcher:** the implementer of 021 (Claude Opus 5.5), on `spec/021-wasm-opt-after-the-link`.
- **Evidence:** `2026-11-24-wasm-opt-measured/`: `fuel.py` (study B's ten workloads on two modules), `ratios.py` (`docs/fast-perl.md`'s ratios, as its checks measure them), `sections.py` (a section listing), and `results/`, the raw output of each.

Labels: EXECUTED means run and seen. READ means read from a file. INFERRED means the implementer's reasoning. "Fuel" is wasmtime's count of executed WebAssembly instructions: a count, the same on any machine.

## 1. The answer

- **The pass removes 2.0–3.4% of the guest's instructions on every workload**, as study B found (`2026-11-24-perf-codegen.md` §5): from −1.98% (`subcall`) to −3.44% (`sort`). EXECUTED, `results/fuel.txt`.
- **`perl.wasm` is 8.1% smaller**: 9,443,860 → 8,675,094 bytes. The code section is 11.5% smaller, and 909 of its 4,078 functions are gone, inlined or merged. EXECUTED, `results/sections.txt`.
- **019's fuel cost is paid back.** The five-module program now runs 120,184,111 instructions, 1.25% fewer than dev before 019 (121,707,825), where 019 alone had added 1.25%. EXECUTED, `results/library-index-measure.txt`.
- **020's ratios all stay inside the page's 20% window.** Each ratio's two sides shrink by about the same share, so the ratios move by 0.1 to 2 points. The keyed sort, the one nearest an edge, moves from +10.2% to +11.5% of the page's 5×. EXECUTED, `results/ratios-*.txt`.

## 2. Fuel, the ten workloads

The two modules of one build: `build/link/perl.wasm`, wasm-ld's output, and `build/artifacts/perl.wasm`, the pass's. One run each, with `PERL_HASH_SEED=0`, so each count is exact. The inputs are made by `fuel.py`: a CSV and a log of 200,000 lines each, and a JSON array of 20,000 objects. Each workload gave the same output on both modules.

| workload | linked | shipped | change |
|---|---:|---:|---:|
| hello | 1,621,593 | 1,578,455 | −2.66% |
| sum_csv (`-F, -lane`) | 1,090,687,914 | 1,058,384,972 | −2.96% |
| grep_log (`-ne` regex count) | 618,521,264 | 598,842,795 | −3.18% |
| inplace (`-pi`) | 813,848,669 | 794,396,872 | −2.39% |
| json (JSON::PP) | 6,027,724,019 | 5,856,990,458 | −2.83% |
| loop | 603,688,355 | 587,643,898 | −2.66% |
| subcall | 532,119,780 | 521,574,684 | −1.98% |
| eval_die | 149,972,434 | 146,277,401 | −2.46% |
| destroy | 410,839,448 | 401,993,951 | −2.15% |
| sort | 905,557,995 | 874,362,282 | −3.44% |

The check of the build (`tests/build/test_wasm_opt.py`) asks for at least 1.5% fewer on `hello`, `sum_csv`, `grep_log` and `loop`, on smaller inputs of its own. It passes.

## 3. Size and sections

`sections.py` over both modules (EXECUTED, `results/sections.txt`):

| | linked | shipped |
|---|---:|---:|
| the file | 9,443,860 | 8,675,094 (−768,766, −8.1%) |
| code | 3,178,203, 4,078 functions | 2,812,992, 3,169 functions (−11.5%) |
| data | 5,533,386, 2 segments | 5,318,597, 11,958 segments |
| datacount | none | 2 bytes |
| name | 88,055 | none |
| `.debug_line` | 215,785 | 116,122 |
| the six `.debug_*` | 634,162 | 534,499, 6.2% of the file |
| producers, target_features | 142, 193 | 142, 193, unchanged |

These are the side effects specification 021's Decision 5 lists, seen on this build. Its figures were taken on another build's module, so they differ a little: 11,955 segments there, 11,958 here; 635,642 and 535,647 bytes of DWARF there.

- **Names.** Without the name section, wasmtime's backtraces name every frame `<unknown>!<wasm function N>` (EXECUTED: an `exit 200` under wasmtime-py, both modules). The runner keeps only an error's cause, so nothing it returns changes. Three checks of `tests/build` read names, and were changed (§5).
- **SIMD.** The pass keeps memchr's sixteen-byte compare (`i8x16.eq`, `i8x16.bitmask`). With SIMD128 enabled it also writes one 16-byte store of zeros as `v128.const` and `v128.store`, in another function (EXECUTED: `llvm-objdump -d` of both modules). The module needed SIMD128 already, since spec 016.

## 4. The pass itself

- **Time:** 5.8 s of wall time and 18.8 s of CPU on this machine, under the lock (EXECUTED: `time wasm-opt -O3 <flags> build/link/perl.wasm -o …`).
- **Determinism:** the same input gave the same bytes with `BINARYEN_CORES=1`, with `BINARYEN_CORES=4`, and with every core (EXECUTED: three runs over the build copied from 019's branch, SHA-256 `2249473a…` each). On this branch's build, the check of the build runs the pass again with every core and gets the shipped bytes (EXECUTED: `test_the_shipped_module_is_the_pass_run_over_the_linked_one`). `40_link.sh` passes `BINARYEN_CORES=$WASI_PERL_JOBS`.
- **`-g` was tried and not taken.** `wasm-opt -O3 -g` keeps the names, and its output is 9,172,757 bytes: it keeps every frame of the exit path, so it inlines far less (EXECUTED: the same `exit 200`, seven frames, as before the pass). The specification gives no `-g`.
  - *Corrected 2026-11-24, from the review of 021 (`../reviews/021-wasm-opt-after-the-link-pr-2026-11-24.md`, L3).* The lost inlining comes from the DWARF sections, not from the names. That `-g` run was taken with DWARF present, and while DWARF is present binaryen runs only the passes that keep it (INFERRED there, from the figures below). `--strip-dwarf -O3 -g` keeps a name section of 245,961 bytes (about 246 KB), loses no fuel (`hello`, `grep_log`, `eval_die`, `sort`: +0.00% each), and is 8,360,960 bytes, about 8.36 MB, 314 KB smaller than the shipped module (EXECUTED there: the pinned `wasm-opt` with the script's flags over `build/link/perl.wasm`, then `sections.py` and this memo's `fuel.py`). It is an open item for the owner's later choice about DWARF (`../roadmap.md`).
- **The hashes** of this build: linked `d0dda31b4f26f383a4d0f22bdfb8993d67e83bb723d3904d91e1f88a59db04f8`, shipped `cde7600bbc7b6e9426d3a23446637ae3fa59f6f364c168e19785f2e663df0901` (EXECUTED: `sha256sum`).

## 5. The checks that read names

The specification's Decision 5 says that dropping the names changes no result. That is true of the runner, but three checks of `tests/build` read function names, and failed on the shipped module (EXECUTED):

- `assert_exit_refused` (`tests/build/_build_helpers.py`) read the innermost frame's name, `perl!__wasi_proc_exit`. It now reads the byte at that frame's offset in the module, which must be a call of the `proc_exit` import. 6 checks had failed on a set with the pass's module.
- `test_the_memchr_linked_compares_sixteen_bytes_a_step[perl.wasm]` and `test_only_memchr_uses_simd` disassembled `<memchr>` by name. Their claims are about what was linked, so they now read `build/link/perl.wasm`, as Decision 7 moves `test_no_wasm_opt`. A new check holds the shipped module to memchr's SIMD compare.

Each change is its own commit, with its reason and the failure that showed it.

## 6. Specification 020's ratios

`ratios.py` measures each ratio exactly as `tests/oneliners/test_fast_perl.py` does, on the set `$WASI_PERL_ARTIFACTS` names: the linked set is this build's set with `build/link/perl.wasm` as its `perl.wasm` and its `SHA256SUMS` rewritten with `listed`; the shipped set is `build/artifacts` (EXECUTED, `results/ratios-linked.txt`, `results/ratios-shipped.txt`).

| ratio | page | linked | shipped | window |
|---|---:|---:|---:|---|
| count matches in a whole file | 9× | 9.068 (+0.8%) | 8.892 (−1.2%) | 7.20–10.80 |
| substitute in a whole file | 7.5× | 7.550 (+0.7%) | 7.414 (−1.2%) | 6.00–9.00 |
| keyed sort | 5× | 5.512 (+10.2%) | 5.576 (+11.5%) | 4.00–6.00 |
| JSON through JSON::MaybeXS | 29× | 26.215 (−9.6%) | 26.101 (−10.0%) | 23.20–34.80 |
| JSON through Cpanel::JSON::XS | 29× | 28.626 (−1.3%) | 28.503 (−1.7%) | 23.20–34.80 |
| CSV | 13× | 13.172 (+1.3%) | 13.269 (+2.1%) | 10.40–15.60 |
| `use POSIX` import | 16 M | 16.4 M | 16.0 M | 12.8–19.2 |

- Every ratio is inside its window on both modules. The page and the margins are unchanged.
- The keyed sort keeps 8.5 points of headroom, JSON::MaybeXS 10.
- The test suite's own run of these checks on the shipped module passed (EXECUTED: `WASI_PERL_ORACLE=build/native-install/bin/perl uv run pytest tests/runner tests/escapes tests/oneliners`, 761 passed, 2 skipped).

## 7. Specification 019's figures

`context/research/2026-11-24-the-library-index-measured/measure.py`, the linked set as "before", `build/artifacts` as "after", 3 rounds (EXECUTED, `results/library-index-measure.txt`):

| program | dev before 019 | 019, linked | 019 and 021, shipped | against dev |
|---|---:|---:|---:|---:|
| five modules | 121,707,825 | 123,212,541 | **120,184,111** | −1.25% |
| `-MPOSIX -e 1` | 71,062,047 | 72,001,857 | **70,195,305** | −1.22% |
| `-e 1` | 1,600,585 | 1,600,492 | **1,557,752** | −2.67% |

- The "dev before 019" column is READ from the 019 memo. The other two are this branch's build, whose index also sends names Windows cannot hold to the host. Its linked figures are 14,572 and 9,021 below 019's final ones (123,227,113 and 72,010,878), though the index now tests more bytes. The cause was not looked into; the compiler may lay out the longer test of each byte differently (INFERRED).
- The failed host stats under the library stay at 0, for both programs (EXECUTED: `tests/build/test_library_lookups.py`, passed).
- The wall-time rows in the results file are within the noise this memo's machine shows between rounds, and are not claimed.

## Reproduce

From the worktree, after `scripts/build.sh`:

```
E=context/research/2026-11-24-wasm-opt-measured
uv run python $E/sections.py build/link/perl.wasm build/artifacts/perl.wasm
flock /home/user/wasi-perl-wt/machine.lock uv run python $E/fuel.py build/artifacts build/link/perl.wasm build/artifacts/perl.wasm
# the linked set: build/artifacts copied, build/link/perl.wasm as its perl.wasm, SHA256SUMS from lib.sh's listed
flock /home/user/wasi-perl-wt/machine.lock env WASI_PERL_ARTIFACTS=<linked set> uv run python $E/ratios.py
flock /home/user/wasi-perl-wt/machine.lock env WASI_PERL_ARTIFACTS=$PWD/build/artifacts uv run python $E/ratios.py
flock /home/user/wasi-perl-wt/machine.lock uv run python \
    context/research/2026-11-24-the-library-index-measured/measure.py <linked set> build/artifacts 3
```
