# 021 — `wasm-opt -O3` after the link

- **Status:** written by the orchestrator, 2026-11-24, under the owner's agreement of the speed work ("You can do all 10 actually", 2026-11-24).
- **Owner:** Clay Gendron
- **Kind:** `scripts/lib.sh`, `scripts/00_download.sh`, `scripts/20_target.sh` (and the relink step of spec 019), `downloads/SHA256SUMS`, `MANIFEST`, checks in `tests/build/`.
- **Depends on:** 019, whose relink is the last link, so it runs after it.
- **Research it stands on:** `../research/2026-11-24-perf-codegen.md` (study B) §5 and §9, with `variants/proposed-wasm-opt-O3.diff`.

## Intent

binaryen's `wasm-opt -O3`, run once over the linked module, removes 2.0–3.4% of the guest's instructions on every workload (B §5). It also makes `die` inside `eval` 9–10% faster in wall time and `sort` 4–7% faster. vfs caps every call's fuel, so fewer instructions means more work per budget. After this specification, the shipped `perl.wasm` is the optimised one.

## Decisions

1. **binaryen version_133, pinned.**
   - The pin is `https://github.com/WebAssembly/binaryen/releases/download/version_133/binaryen-version_133-x86_64-linux.tar.gz` with its SHA-256, which study B recorded in `results/static.txt`. It goes into `downloads/SHA256SUMS`, and `00_download.sh` fetches it.
   - It unpacks under `build/tools/`, as the other tools do, and runs only as a build tool.
   - `MANIFEST` records its version and hash.
2. **`-O3`, once, after the last link.**
   - `-O4` aborts: its flatten pass does not take `try_table` (B §5).
   - `--converge` adds nothing.
   - The feature flags passed are exactly those perl.wasm uses: exception handling with the new encoding, bulk memory, sign extension, non-trapping float-to-int and mutable globals, plus SIMD after spec 016. No others are enabled.
3. **It is deterministic.** Two runs on the same input give byte-identical output (B §5, EXECUTED). The build's reproducibility rules apply to it as to the compiler.
4. **The unoptimised module is kept in `build/` for comparison only**, never shipped.

## Checks (written first, seen failing)

1. The shipped `perl.wasm` carries binaryen's mark. Its `producers` section, or a recorded hash pair in `MANIFEST`, shows it went through `wasm-opt`.
2. Fuel on the baseline workloads (`hello`, `sum_csv`, `grep_log`, `loop`) is at least 1.5% below the unoptimised module's. This is a count.
3. The import list and the export list are unchanged.
4. Every `die` and `eval` case of spec 002 gives the same output.

## Acceptance

1. The checks pass (EXECUTED).
2. The one-liner oracle on every platform in CI, the escape tests and the whole of Perl's `t/` suite pass, with no new exclusion.
3. `CHANGELOG.md` under "Unreleased".
4. A fresh agent's review lands.
