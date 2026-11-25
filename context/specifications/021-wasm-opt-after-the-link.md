# 021 — `wasm-opt -O3` after the link

- **Status:** agreed by the owner 2026-11-24, through AskUserQuestion. Asked to agree 019 and 021 together, the owner answered "Agree both (Recommended)". That agrees this text's new choices: binaryen `version_133` as a pinned build tool, the feature flags, and the check by a second run. Written by the orchestrator, 2026-11-24, for synthesis item 9. The owner agreed to build the ten items that day: "You can do all 10 actually, but do not change the API around perl to be different than wasi python, unless wasi python could adopt the same changes." Amended the same day for the review `../reviews/specs-017-022-pr-2026-11-24.md` (H1, H2, H4, M6, L8, L9; round 2: N3, N5, N8).
- **Owner:** Clay Gendron
- **Kind:** `scripts/lib.sh`, `scripts/00_download.sh`, step 6 of spec 019's `scripts/40_link.sh`, `downloads/SHA256SUMS`, checks in `tests/build/`, and the move of `test_no_wasm_opt` in `tests/build/test_perl_wasm.py` (Decision 7). `MANIFEST` is not changed.
- **Depends on:** 019, which makes the last link and the step this fills. Spec 016 comes before 019, so its SIMD `memchr` is linked.
  - If 022 is built first, this pass runs over `perl-reactor.wasm` too.
  - If this is built first, 022 adds the reactor to the same step.
- **Research it stands on:** `../research/2026-11-24-perf-codegen.md` (study B) §5 and §9, with `variants/proposed-wasm-opt-O3.diff` and `results/static.txt`.

Labels: EXECUTED means run and seen. READ means read from a file. INFERRED means reasoned.

## Intent

binaryen's `wasm-opt -O3`, run once over the linked module, removes 2.0–3.4% of the guest's instructions on every workload (B §5). It also makes `die` inside `eval` 9–10% faster in wall time, and `sort` 4–7% faster. vfs caps every call's fuel, so fewer instructions means more work per budget. After this specification, the shipped `perl.wasm` is the optimised one.

## Decisions

### 1. binaryen version_133, pinned in `downloads/SHA256SUMS`

- `downloads/SHA256SUMS` gains one line, in its own three-field format (`<sha256>  <name>  <url>`, read by `00_download.sh`):

  ```
  2dc9c7813f5375db93d96ead4b78222fcc3e2677bbb832297af4797782a37489  binaryen-version_133-x86_64-linux.tar.gz  https://github.com/WebAssembly/binaryen/releases/download/version_133/binaryen-version_133-x86_64-linux.tar.gz
  ```

  The hash matches the release's `.sha256` file (READ: B's `results/static.txt:78-79`). It is also the hash of the tarball study B fetched (EXECUTED: `sha256sum /home/user/wasi-perl-downloads/binaryen-dl/b.tgz`, 2026-11-24).
- `lib.sh` names it twice, since the tarball's name and its folder's differ: `BINARYEN_TARBALL=binaryen-version_133-x86_64-linux.tar.gz`, and `BINARYEN_NAME=binaryen-version_133`, the one top folder it unpacks to (EXECUTED: `tar -tzf`, 2026-11-24). `00_download.sh` fetches and verifies it like every other input.
- **The unpack.** `00_download.sh`'s loop (`00_download.sh:37-44`, READ) unpacks `$name.$ext` and requires the folder `$name`, which fits wasi-sdk and wasmtime only. binaryen gets its own lines after the loop, written the same way: unless `build/tools/$BINARYEN_NAME/.unpacked` exists, remove that folder, untar `$DOWNLOADS/$BINARYEN_TARBALL` under `build/tools/`, require `build/tools/$BINARYEN_NAME/bin/wasm-opt`, and touch `.unpacked`. It runs only as a build tool.
- **Why not `MANIFEST`:** `MANIFEST` is strictly `<sha256>  <path>`, one line for each file under `lib/` and `dev/` (`scripts/30_library.sh:62-69`; `tests/build/test_modules.py:529-550`, READ). A tool is an input, and every input is pinned in `downloads/SHA256SUMS` (constitution §4.2).

### 2. `-O3`, once, after the last link

- **Where.** Step 6 of spec 019's build order, in `scripts/40_link.sh`. It reads `build/link/perl.wasm`, and `build/link/perl-reactor.wasm` once 022 is built. It writes each to `build/artifacts/` under the same name. Step 7, `SHA256SUMS`, follows it, so `SHA256SUMS` hashes the optimised bytes.
- **The stamp.** `.link.stamp` also covers `BINARYEN_NAME`, `BINARYEN_TARBALL`, the tarball's pinned SHA-256 and the flag list, so a change to any of them reruns the pass.
- **Not `-O4`:** it aborts, because its flatten pass does not take `try_table` (B §5).
- **Not `--converge`:** it adds nothing (B §5).

### 3. The feature flags are exactly the module's features

- **What the module uses.** `perl.wasm`'s `target_features` section lists ten features. `wasm-opt --print-features` gives the same ten. Each was read both from study B's base module (`results/static.txt:11-21`, EXECUTED there) and from the built set's `perl.wasm` (EXECUTED: `wasm-opt --print-features` and a section listing of `/home/user/wasi-perl-wt/perf-base/build/artifacts/perl.wasm`, 2026-11-24).
- **The flags.** `40_link.sh` passes exactly these, written out in the script:

  | feature in `target_features` | flag |
  |---|---|
  | `mutable-globals` | `--enable-mutable-globals` |
  | `nontrapping-fptoint` | `--enable-nontrapping-float-to-int` |
  | `bulk-memory` | `--enable-bulk-memory` |
  | `bulk-memory-opt` | `--enable-bulk-memory-opt` |
  | `sign-ext` | `--enable-sign-ext` |
  | `exception-handling` | `--enable-exception-handling` |
  | `reference-types` | `--enable-reference-types` |
  | `multivalue` | `--enable-multivalue` |
  | `extended-const` | `--enable-extended-const` |
  | `call-indirect-overlong` | `--enable-call-indirect-overlong` |
  | `simd128`, from spec 016's `memchr` | `--enable-simd` |

  - The SIMD row is INFERRED: wasm-ld lists every feature that an object it links uses, and 016 compiles its `memchr` with `-msimd128`.
  - `try_table` and `exnref` need reference types, which the earlier text left out.
- **The guard.** Before the pass, the script compares `wasm-opt --print-features` of the input with its own list, and stops with one line naming the difference if they are not equal. So a feature that a later change adds, or drops, cannot be passed over unseen.
- **Why name them, not let wasm-opt read the section:** the list is then in the script, where a review sees it. The guard makes the two the same, and check 3 holds the output to it.

### 4. It is deterministic

Two runs on the same input give byte-identical output (B §5, EXECUTED). The build's reproducibility rules apply to it as to the compiler.

### 5. What the pass changes beside the code

These were seen by running the pinned `wasm-opt -O3` over the built set's `perl.wasm` (EXECUTED: into the scratch folder, then a section listing, 2026-11-24):

- **The `name` section is dropped**, since no `-g` is given. A trap's backtrace then names functions by index. The runner keeps a few words of an error, its cause's last line or else its first line, and never the backtrace (`_error_words`, `runner.py:156-162`, READ). So no result changes. The unoptimised module in `build/link/` keeps the names for debugging.
- **The `producers` section is unchanged.** binaryen does not add itself to it. So the producers section cannot show that the pass ran (check 1).
- **A data-count section appears, and the 2 data segments become 11,955.** binaryen packs memory around runs of zeros. The code holds no `memory.init` and no `data.drop` (EXECUTED: `wasm-dis`, then `grep -c`, 0 each). Spec 022's image module writer accepts such a reactor; its Decision 1 says how.
- **The `target_features` section is kept**, with the same ten features.
- **The DWARF sections survive.** The `.debug_*` sections stay, and binaryen rewrites `.debug_line` from 216,375 to 116,380 bytes (EXECUTED: the section listing before and after). Together they are 635,642 bytes before the pass and 535,647 after, about 6% of the optimised module's 8.5 MB (EXECUTED: the same listing), and nothing in the runner reads them. Stripping them is a separate choice, for every build at once, and is not made here.

### 6. The unoptimised modules are kept in `build/link/` for comparison only

They are never shipped.

### 7. The test that says wasm-opt never ran moves to the unoptimised module

- `test_no_wasm_opt` (`tests/build/test_perl_wasm.py:186-188`, READ) says "The module is what wasm-ld wrote: binaryen's wasm-opt never ran on it", and asserts that `b"binaryen"` is not in the shipped `perl.wasm`.
- After this specification its words are false for the shipped module, and its assertion would still pass, since `-O3`'s output holds no such string (Decision 5). A test that passes while its claim is false guards nothing.
- So it moves to `build/link/perl.wasm`, wasm-ld's own output. There it still guards what it was for: that clang never runs a `wasm-opt` of its own at the link (`wasi-cc`'s `--no-wasm-opt`, `recipes/wasi-cc:64`). Its assertion is unchanged.
- The shipped module is held instead by check 1, which proves it is exactly the pass's output.
- The move is its own commit, with this reason in its message, so the review sees it. It is not a weakened check: its assertion and its claim are kept, on the file the claim is about.

## Checks (written first, seen failing)

1. **The shipped module is the pass's output.** A build check runs the pinned `wasm-opt` with the script's flags over `build/link/perl.wasm`, and the output equals the shipped `perl.wasm` byte for byte. It also checks that the two `perl.wasm` files differ. The same holds for `perl-reactor.wasm` once 022 is built. This is possible because the pass is deterministic (Decision 4), and needed because the producers section does not change (Decision 5).
2. **Fuel** on the baseline workloads (`hello`, `sum_csv`, `grep_log`, `loop`) is at least 1.5% below the unoptimised module's. This is a count.
3. **Features.** `wasm-opt --print-features` gives the script's list for the input and for the output, and the output's `target_features` section names the same features.
4. **The import list and the export list are unchanged.**
5. **Every `die` and `eval` case of spec 002** gives the same output.

## Acceptance

1. The checks pass (EXECUTED).
2. The one-liner oracle on every platform in CI, the escape tests and the whole of Perl's `t/` suite pass, with no new exclusion.
3. `CHANGELOG.md` under "Unreleased", naming binaryen `version_133` as a new pinned input.
4. A fresh agent's review lands.

## Landing notes

Built 2026-11-24 by the implementer (Claude Opus 5.5) on `spec/021-wasm-opt-after-the-link`, from dev at `50e0298`, Linux x86-64, 4 vCPUs, CPython 3.13, wasmtime-py 51.0.0, as root. **Not yet reviewed.** Every claim below was run (EXECUTED) unless it says otherwise. Every build and every fuel run held `flock /home/user/wasi-perl-wt/machine.lock`, with `WASI_PERL_JOBS=4`. The numbers are in `../research/2026-11-24-wasm-opt-measured.md`.

### What was built

- **Decision 1.** The pin line, as written above, in `downloads/SHA256SUMS`. `lib.sh` names `BINARYEN_TARBALL` and `BINARYEN_NAME`. `00_download.sh` fetches and verifies it in its loop, then unpacks it on its own lines after the loop, as written above: `build/tools/binaryen-version_133/bin/wasm-opt`, then `.unpacked`. CI's download cache is keyed by the hash of `downloads/SHA256SUMS`, so the new pin makes a new key; the tarball comes from github.com, as wasi-sdk's and wasmtime's do.
- **Decision 2.** Step 6 of `40_link.sh`: `wasm-opt -O3 <features> build/link/perl.wasm -o build/artifacts/perl.wasm`, then `SHA256SUMS`. The script holds `PASS='-O3'` and `FEATURES='…'`, one flag a line. `.link.stamp` is the SHA-256 of the four files 019 named, then one line each for `BINARYEN_NAME`, `BINARYEN_TARBALL`, the tarball's pin (`pin_of`), the pass and each feature.
- **Decision 3.** The eleven flags of the table. The guard is `same_features <wasm-opt> <module> <flags…>` in `lib.sh`, run just before the pass: it compares `--print-features` with the list, sorted, and stops with one line naming what the module uses and the pass does not name, and what the pass names and the module does not use. The SIMD row is now EXECUTED: `--print-features` of this build's linked module gives eleven, `--enable-simd` among them, and so does the section.
- **Decision 4.** Deterministic: one input gave the same bytes with 1 thread, 4 threads and every core. Check 1 runs the pass again on every build of the checks.
- **Decision 5.** Seen on this build: no name section; producers and target_features unchanged; a datacount section and 11,958 data segments (the text's 11,955 was another build's); `.debug_line` 215,785 → 116,122 bytes.
- **Decision 6.** `build/link/perl.wasm` is kept, and the checks read it.
- **Decision 7.** `test_no_wasm_opt` reads `build/link/perl.wasm`, in its own commit, with its assertion unchanged.

### Choices the specification left to the implementer

- **`BINARYEN_CORES`.** The pass runs with `BINARYEN_CORES=$JOBS`, so `WASI_PERL_JOBS` caps its threads as it caps make's. The output does not depend on it (Decision 4).
- **The guard is a `lib.sh` function**, so a check can run it on the real module with a list that drops or adds a flag, without running the whole step.
- **Check 5's cases.** Spec 002's three `die`-and-`eval` cases of `test_modules.py` (`storable-corrupt`, `posix-strtol-junk`, `die-through-first`), with `test_perl_wasm.py`'s `DIE_IN_EVAL` and `NESTED`, an uncaught `die`, and an uncaught `die` with an `END` block. Each runs on both modules and must give the same exit code, stdout, stderr, exit-status export and error cause.
- **Check 2's inputs** are made by the check: a CSV and a log of 50,000 lines. `PERL_HASH_SEED=0`, so each count is exact.

### Findings

- **Three more checks read function names** (Decision 5 says no result changes; that holds for the runner, not for `tests/build`). Each was seen failing on the optimised module, and each change is its own commit:
  - `assert_exit_refused` read the innermost frame's name, `perl!__wasi_proc_exit`. Without names, it is `<unknown>!<wasm function 2982>`, and six checks failed. It now reads the module's byte at that frame's offset, which must be a call of the `proc_exit` import: the same claim, read from the instruction itself. A new check shows that a frame one byte off is refused.
  - `test_the_memchr_linked_compares_sixteen_bytes_a_step[perl.wasm]` and `test_only_memchr_uses_simd` disassembled `<memchr>` by name, and both failed. Their claims are about the link, so they read `build/link/perl.wasm`, as Decision 7 does. A new check holds the shipped module to `i8x16.eq` and `i8x16.bitmask`. "Only memchr uses SIMD" is not true of the shipped module: the pass writes one 16-byte store of zeros as `v128.store`.
  - Keeping names with `-g` was tried: 9,172,757 bytes, and every frame of the exit path kept, so far less inlining. It would change Decision 5, and was not taken.
    - *Corrected 2026-11-24 (review L3).* That run kept the DWARF sections, and the lost inlining comes from them, not from the names: `--strip-dwarf -O3 -g` keeps a 246 KB name section at the same fuel, and is 8.36 MB (EXECUTED in the review). It is an open item in `../roadmap.md`, for the owner's later choice about DWARF.
- **The `.debug_*` sections** stay, 534,499 bytes, 6.2% of the shipped module, as Decision 5 says.

### The checks

| check | before the pass | after |
|---|---|---|
| 1: the shipped module is the pass over the linked one; the two differ | failed: they were the same file | passed |
| 1: the pass is `-O3` alone | failed: no `PASS` in the script | passed |
| 2: four workloads at least 1.5% fewer instructions | failed: +0.00% each | passed |
| 3: the script's features are both modules' `--print-features` and sections | failed: no `FEATURES` | passed |
| 3: the guard passes the list; stops on a list without `--enable-simd` or with `--enable-tail-call` | failed: no guard | passed |
| 3: the guard runs before the pass, the pass before `SHA256SUMS` | failed | passed |
| 4: imports and exports unchanged | passed (the same module) | passed |
| 5: seven `die` and `eval` cases | passed (the same module) | passed |
| the link stamp covers binaryen and the flags | failed | passed |

### The acceptance runs

| command | result |
|---|---|
| `flock …machine.lock scripts/build.sh` from an empty `build/` | 00–40 ok; 1,462 files in `MANIFEST`, 1,464 in `SHA256SUMS`; a second run: 00 ok, every other step "up to date" |
| `uv run pytest tests/build -ra` | 594 passed, 0 skipped; CI's build-job gate over its JUnit file: "154 checks of the CPAN modules, the library index and wasm-opt (at least 54), 0 skipped" |
| `WASI_PERL_ORACLE=build/native-install/bin/perl uv run pytest tests/runner tests/escapes tests/oneliners -ra` | 761 passed, 2 skipped ("root can write every folder"; "macOS, run in CI"), 020's fuel-ratio checks among the passed |
| `uv run python tests/perl_t/run_suite.py --quiet --list-failures --jobs 4` | 616 run, 439 passed, 177 excluded, 0 failed, 0 stale exclusions, 0 regressed (19,052 guarded oks), as 019's; `tests/perl_t/exclusions.toml` unchanged |
| `uv run pytest -m "not slow"` | 528 passed, 2 skipped ("root can write every folder"; "macOS, run in CI"), 273 deselected |
| `uv run ruff check`; `uv run ruff format --check` | all checks passed; 43 files already formatted |
