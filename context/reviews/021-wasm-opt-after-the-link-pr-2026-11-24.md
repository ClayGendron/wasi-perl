# Review: 021, `wasm-opt -O3` after the link (PR #30)

Reviewer: a fresh agent (Claude Opus 5.5, `claude-opus-5-5`) that did not write the change. 2026-11-24.

- **Reviewed:** `git diff origin/dev...HEAD` on `spec/021-wasm-opt-after-the-link` at head `3d51777` (`3d517770d6a87c552b64435e4e575a8c8ddf5906`), base `50e0298`, twelve commits. Read against `AGENTS.md`, `context/constitution.md`, the specification with its landing notes, the memo `context/research/2026-11-24-wasm-opt-measured.md`, study B's §5 and §9, spec 016's Decision 2, and the PR body.
- **Modules:** the worktree's own build. `build/link/perl.wasm` is `d0dda31b…`, 9,443,860 bytes. `build/artifacts/perl.wasm` is `cde7600b…`, 8,675,094 bytes, and `SHA256SUMS` names that hash (EXECUTED: `sha256sum`, `ls -l`, `grep`). These are the memo's §4 hashes.
- **Mutations** ran in a scratch copy, `/home/user/wasi-perl-wt/review-021-scratch`, since removed. Probes are in `/tmp/claude-0/-home-user-wasi-perl/ecbe2564-8e44-50cd-9bc9-1c1b283f37e5/scratchpad/`. The worktree's code and build are unchanged (EXECUTED: the same two hashes and `.link.stamp` after the review, `git status` clean). Every build and fuel run held `flock /home/user/wasi-perl-wt/machine.lock`, with `WASI_PERL_JOBS=4`.

Labels: EXECUTED means I ran it and saw the result. READ means I read it in a file or a log. INFERRED means my reasoning.

## Verdict

**Lands.** The pass is pinned and verified. It runs once, after the guard, before `SHA256SUMS`. It is deterministic. The shipped bytes are exactly what the pinned tool makes from the linked module. Every figure I reran matched the memo to the instruction. The three changed checks keep their claims, and the shipped module is still held to the properties they guarded (judged below). The four folded follow-ups are correct, and each was seen failing without its fix. CI is green on all seven jobs, and the build gate counts `test_wasm_opt` with 0 skipped.

The findings are all Low. None is needed before the merge. Each can be fixed in a later change or recorded as an open item.

## The three changed checks

**1. `assert_exit_refused` (4406fc9) keeps its claim, and reads it from a stronger source.**

- The old check said that the innermost frame was the function `__wasi_proc_exit`. That is libc's one-line wrapper, which does nothing but call the import. The new check says that the innermost frame's offset holds `0x10` (call), and that the call's operand is the function index of `wasi_snapshot_preview1.proc_exit`. That is the instruction itself, so it says at least as much as the name did.
- On the linked module, frame 0 is `0x2e67b8 - perl!__wasi_proc_exit`. On the shipped module, it is `0x2958d7 - <unknown>!<wasm function 2982>`. In both, the byte there is `0x10` and the callee is 31, which is `proc_exit`'s import index (EXECUTED: `exit 200` through `_build_helpers.Guest` on each module, then the bytes read at the offset).
- wasmtime's frame offsets are offsets in the module's file, so the check reads the right bytes. The one-byte-off check shows that the check is not vacuous. Every caller passes a `Result` from a `Guest`, which now records its module, so the bytes read are the bytes that ran (READ: `test_refusals.py`, `test_descriptors.py`, `test_perl_wasm.py`).
- So it is proven of the shipped module that a status of 126 or more is refused at the exit import, with no trap on the way. The six checks that use it pass (EXECUTED: `tests/build`, 594 passed).

**2. The memchr checks (2b5b5d3) keep their claims on the module they are about. The shipped module keeps memchr's SIMD. Its one new SIMD store is acceptable.**

- Both checks were about what wasm-ld linked: the recipe's memchr, compiled alone with `-msimd128`, and nothing else compiled for SIMD. They now read `build/link/perl.wasm`, and they fail if it is missing; they do not skip. Their assertions are unchanged.
- The shipped module is held by two things together. The new check finds `i8x16.eq` and `i8x16.bitmask` in it. Check 1 proves that it is the pinned pass over the linked module, byte for byte.
- I listed the SIMD ops of both modules (EXECUTED: `llvm-objdump -d`, then `grep -oE` and `uniq -c`):
  - linked: 2 `i8x16.bitmask`, 2 `i8x16.eq`, 1 `i8x16.splat`, 2 `v128.load`, all in `<memchr>`;
  - shipped: the same seven in one function, plus one `v128.const 0` and one `v128.store` in another function, at `0x25d6fb`. That is the memo's §3, exactly. memchr is not inlined into any caller.
- **Under the constitution and spec 016, this is acceptable.** Spec 016 accepted that the module needs SIMD128, and documented it (`docs/sandbox.md`). Its rule "Only this file is compiled with `-msimd128`" is a rule about compiler flags, and the linked module still obeys it. A `v128.store` of zeros adds no host requirement and no new behaviour (INFERRED). The check "only memchr uses SIMD" cannot be true of the shipped module, so moving it is not a weakening. One gap remains: nothing would notice a future binaryen that vectorises much more widely (L1).

**3. `test_no_wasm_opt` (7c6fb98) moves as Decision 7 says, with its assertion unchanged.** Its words are true of `build/link/perl.wasm`, and that file is what it guards: no `wasm-opt` of clang's own at the link. The move adds a skip when the linked module is missing. That skip sits outside the CI gate's five files (L2).

**On `-g`:** I see no reason to revisit Decision 5 for this PR. The runner keeps only an error's cause, never a backtrace (READ: spec Decision 5; the wheel jobs pass on all four platforms). The three checks now read instructions, not names. But the memo's account of `-g` is incomplete, and the owner should have the full account before a later choice (L3). `-g` loses inlining only while the DWARF sections are present. `--strip-dwarf -O3 -g` keeps a name section and loses nothing (EXECUTED; the figures are in L3).

## Findings

### Low

**L1. The shipped module's SIMD is held only to a minimum.**
- **Evidence.** `test_the_shipped_module_keeps_memchr_s_sixteen_byte_compare` asserts that `{i8x16.eq, i8x16.bitmask}` is a subset of the shipped module's ops. Check 1 pins today's bytes. But after a binaryen bump, any amount of new SIMD in the shipped module would pass. Today the pass adds exactly `v128.const` and `v128.store`, in one function (EXECUTED, above).
- **Fix.** Assert the exact set of SIMD ops in the shipped module: the linked module's set, plus `v128.const` and `v128.store`. Also assert that at most two functions use them. Then a future pin bump that widens SIMD is seen in review.

**L2. `test_no_wasm_opt` can now skip, and no gate counts it.**
- **Evidence.** It skips when `build/link/perl.wasm` is missing (READ: `test_perl_wasm.py:197-203`). `test_perl_wasm` is not one of the CI gate's five files (READ: `ci.yml`). The memchr checks in the same change `assert LINKED.exists()` instead. In CI, a missing linked module would still fail the gate through `test_wasm_opt`'s `linked` fixture. So this is a gap in consistency, not in coverage (INFERRED).
- **Fix.** Replace the skip with `assert linked.exists(), "… scripts/40_link.sh builds it"`, as the memchr checks do. `40_link.sh` always writes the file.

**L3. The memo's account of `-g` attributes the lost inlining to the names. The cause is the DWARF.**
- **Evidence** (EXECUTED: the pinned `wasm-opt` with the script's eleven flags over `build/link/perl.wasm`, then `sections.py` and the memo's `fuel.py` limited to some workloads):

  | variant | bytes | functions | name section | fuel against shipped |
  |---|---:|---:|---:|---|
  | `-O3` (shipped) | 8,675,094 | 3,169 | none | — |
  | `-O3 -g` | 9,172,901 | 4,077 | 264,329 bytes | `hello` +0.55%, `sort` +1.07% |
  | `--strip-dwarf -O3 -g` | 8,360,960 | 3,169 | 245,961 bytes | `hello`, `grep_log`, `eval_die`, `sort`: +0.00% each |

  While DWARF is present, binaryen with `-g` runs only the passes that preserve DWARF (INFERRED from the table: once the DWARF is stripped, `-g` costs no fuel). The memo's §4 says "it keeps every frame of the exit path, so it inlines far less". The landing notes say the same. The observation is right. The cause it implies is wrong.
- **Fix.** In memo §4 and the landing notes, say that the `-g` figure was taken with DWARF present. Add that `--strip-dwarf -O3 -g` keeps the names at no fuel cost, and is 314 KB smaller than the shipped module. Record it as an open item in `context/roadmap.md`, beside Decision 5's separate choice about stripping DWARF. Either choice is the owner's.

**L4. binaryen's unpack has no refusal of links and no check for one top folder. Only the pin stops them.**
- **Evidence.** The new lines in `00_download.sh` untar straight into `build/tools/`, as root, without `--no-same-owner`. They require only `bin/wasm-opt`. 018's `unpack_pinned` checks a copy against the pin, unpacks it with `--no-same-owner --no-same-permissions`, refuses any entry that is not a file or a folder, and requires exactly one top folder (READ: `lib.sh:100-133`). The pinned tarball is safe as it is: 21 entries, 17 regular files and 4 folders, all under `binaryen-version_133/`, owned by root, with no setuid bit and no link (EXECUTED: `tar -tvzf`; `find build/tools/binaryen-version_133 ! -type f ! -type d` is empty). It is verified in the same run, just before the unpack (READ: `00_download.sh:9-33`). This is what Decision 1 specifies, and it matches how wasi-sdk and wasmtime are unpacked. Also, `.unpacked` does not record the pin, so a new pin under the same name would keep the old tool (INFERRED; all three tools share this).
- **Fix.** Keep the unpack as specified. Add one cheap build check to `test_wasm_opt.py`: the unpacked binaryen folder holds only files and folders, with no setuid bit. Then a future pin bump cannot bring a link in silently. For the shared `.unpacked` gap, add an open item, or write the pin into `.unpacked` and compare it.

**L5. Small slips of wording and rounding.**
- `-e 1`'s change is −2.676%. That rounds to −2.68%, not the −2.67% given in memo §7 and in the 019 memo's §1.2 (EXECUTED: `python3 -c 'print(1557752/1600585-1)'`).
- The CHANGELOG words for follow-up 1 ("a name Windows rewrites or cannot hold") are in `1a3cebb`, the memo commit, not in `30fc232`, the commit that changed the index.
- `139d39f` turns on the pass two commits before `4406fc9` and `2b5b5d3` adapt the checks that read names. So `tests/build` is red at `139d39f`, `4406fc9` and `7c6fb98`. Each commit message records the failure, so the history explains itself. But a bisect over this range would stop there.
- **Fix.** Correct the two percentages when the memo is next touched. The other two need no action.

## What was checked and held

- **The pin.** The line in `downloads/SHA256SUMS` is the one in Decision 1, character for character. The tarball in `downloads/` hashes to `2dc9c781…7489` (EXECUTED: `sha256sum`). `00_download.sh` verifies every line before the unpack, and refuses a mismatch (READ). CI's download cache missed on this head (key `downloads-ff0042d9…`). So CI fetched binaryen from github.com, verified it and unpacked it, and `00_download: ok` (READ: the build job's log, job 138810347751).
- **The step.** In `40_link.sh` the order is guard, then pass, then `SHA256SUMS` (READ). `SHA256SUMS` names the shipped hash (EXECUTED). A rerun in the scratch copy relinked and gave the same `d0dda31b…` and `cde7600b…`, and a `SHA256SUMS` identical to the worktree's. The next run said "up to date" (EXECUTED).
- **The stamp.** I took the stamp lines from the real script and ran them. Their stamp equals the worktree's `.link.stamp` (`1c09072d…`). I then changed one input at a time and restored it. Each change gave a new stamp (EXECUTED):
  - the pin in `downloads/SHA256SUMS`, which no script file contains;
  - `BINARYEN_NAME`;
  - `BINARYEN_TARBALL`;
  - `PASS`;
  - each of the eleven feature flags;
  - `lib_index.sh`;
  - `.library.stamp`.
- **The guard.** I ran the real step in scratch with `--enable-simd` dropped from `FEATURES`. It stopped with one line: "FAILED: the features of perl.wasm differ from the pass's: it uses --enable-simd, which the pass does not name". No `perl.wasm` and no `SHA256SUMS` were left. With `--enable-tail-call` added, it stopped with "the pass names --enable-tail-call, which it does not use" (EXECUTED).
- **Determinism.** `BINARYEN_CORES=1`, `BINARYEN_CORES=4` and every core each gave `cde7600b…`, the shipped bytes (EXECUTED). Passing `WASI_PERL_JOBS` as `BINARYEN_CORES` keeps the shared machine's cap without changing the output.
- **Fuel.** I reran `grep_log`, `eval_die` and `sort` with the memo's `fuel.py` on both modules: 618,521,264 → 598,842,795; 149,972,434 → 146,277,401; 905,557,995 → 874,362,282. Each is the memo's figure to the instruction (EXECUTED).
- **020's ratios.** `ratios.py` on the shipped set gave output byte for byte that of `results/ratios-shipped.txt` (EXECUTED: `diff`). `test_fast_perl.py` passes on the shipped module, 14 passed (EXECUTED).
  - JSON::MaybeXS is at 26.101, −10.0% of 29×. That leaves 2.90 above the window's floor of 23.20, 10 of the 20 points of margin.
  - The keyed sort, at +11.5%, is nearest an edge, with 8.5 points left.
  - Neither is a finding. A later change that moves either ratio by that much will fail a check, as it should.
- **The memo's arithmetic.** I recomputed −8.1% size, −11.5% code, 6.2% DWARF, −1.25% and −1.22% (EXECUTED). Only the slip in L5 differs.
- **Follow-up 1 (30fc232).** I put back dev's `recipes/wasi-fd-table.c` in scratch. Exactly the 14 new host rows failed: four device stems with blanks after them, the six bytes, `x*/A.pm`, and three control bytes. The 4 rows that stay with the index passed under both versions, as they should (EXECUTED). The C trims only the blanks after the stem, before the device test. The byte test adds `< 0x20` and the six bytes Windows refuses (READ).
- **Follow-up 2 (3d51777).** 123,227,113 and 72,010,878 are spec 019's landing-note figures (READ: `019-the-library-index.md:243`). The figures after `-O3` match `results/library-index-measure.txt`. The differences are right, apart from L5.
- **Follow-up 3 (614168c).** In scratch I swapped the page's two figures, so that "count … 7.5×" and "substitution 9×". The new check failed: "9× (count matches in a whole file) follows 'substitut', not 'count'". dev's version of `test_fast_perl.py` passed all 13 of its checks on the same swapped page (EXECUTED).
- **Follow-up 4 (2cbc372).** Job 138774884353's log ends in "the tests did not run, so there is nothing to check here", as an abort in the first step that runs perl would leave it (READ: its last 40 lines). The item is worded as suspected, and its cause is labelled INFERRED.
- **No weakened test, no exclusion.** `tests/perl_t/` is untouched. Every removed `assert` in the diff has a replacement that says at least as much (READ). The stamp check now computes 40_link's longer formula. I tested that formula separately, above.
- **No spec number in code.** The added lines of `src/`, `tests/`, `scripts/`, `recipes/` and `.github/` name no specification or review (EXECUTED: `grep` over the diff).
- **CI on the head.** All seven jobs pass (READ: `get_check_runs`). The build job: "594 passed", and the gate printed "154 checks of the CPAN modules, the library index and wasm-opt (at least 54), 0 skipped". `tests/build/test_wasm_opt.py` ran 19 checks (READ: its log). The gate's own script over my local JUnit file printed the same line, and exited 0 (EXECUTED).

## Runs

| command | result |
|---|---|
| `uv run pytest tests/build -p no:cacheprovider -ra --junitxml=…` (EXECUTED) | 594 passed |
| the CI gate's script over that JUnit file (EXECUTED) | 154 checks, at least 54, 0 skipped, exit 0 |
| `WASI_PERL_ORACLE=build/native-install/bin/perl uv run pytest tests/oneliners/test_fast_perl.py` (EXECUTED) | 14 passed |
| `wasm-opt -O3 <flags>` with `BINARYEN_CORES` set to 1, 4 and unset (EXECUTED) | `cde7600b…` three times |
| the stamp lines of `40_link.sh` under 17 mutations (EXECUTED) | each gives a new stamp |
| `40_link.sh` in scratch, with a flag dropped, a flag added, then restored (EXECUTED) | FAILED twice, in one line each; then ok, with the same bytes; then up to date |
| `fuel.py` on `grep_log`, `eval_die` and `sort` (EXECUTED) | the memo's counts exactly |
| `ratios.py` on the shipped set (EXECUTED) | identical to `results/ratios-shipped.txt` |
| `test_lib_index.py` with dev's `wasi-fd-table.c`, in scratch (EXECUTED) | 14 failed, 26 passed |
| `test_fast_perl.py`, new and dev's, on a swapped page, in scratch (EXECUTED) | new: 1 failed; dev's: 13 passed |
| `-O3 -g` and `--strip-dwarf -O3 -g`: size, sections, fuel (EXECUTED) | L3's table |
| `get_check_runs`, and the build job's log (READ) | 7 of 7 green; binaryen fetched and verified; 0 skipped |
