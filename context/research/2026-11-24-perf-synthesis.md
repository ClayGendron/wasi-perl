# Every lever on wasi-perl's speed under vfs, ranked, and the specifications they become

- **Status:** research synthesis, 2026-11-24. The owner agreed the speed work the same day. Specifications 013–022 supersede section 3's rows: for example, 013 serves `/dev/urandom` in the guest rather than wrapping `Perl_seed`, and 014 reads the `.cwasm` on Windows rather than stamping it. Section 5 lists what the owner was asked.
- **Owner:** Clay Gendron
- **Researcher:** an agent (Claude Opus 5.5), the orchestrator of the six studies.
- **Stands on:**
  - the plan, `2026-11-24-perf-research-plan.md`;
  - the baseline, `2026-11-24-wasi-perl-against-wasi-python-on-speed.md`;
  - the six studies, each with its evidence folder:
    - A `2026-11-24-perf-runner.md`
    - B `2026-11-24-perf-codegen.md`
    - C `2026-11-24-perf-interp.md`
    - D `2026-11-24-perf-startup.md`
    - E `2026-11-24-perf-vfs-io.md`
    - F `2026-11-24-perf-libs.md`
- **Labels:** every number here is the study's own, cited by letter and section, and carries the label it has there. Most are EXECUTED. Some are INFERRED in their study, and those are marked INFERRED here too: the 300 µs per file (C §3.5), the 0.2 ms start (D §1.1), the host-stat savings (D §1.3) and the thread hand-off as the cause (A §5.1). A number this memo derives by combining studies is marked INFERRED as well.

## 1. The answer

Under vfs, wasi-perl runs many short programs, each in a fresh instance, over files a provider serves. Five costs matter, in this order:

1. **The fixed cost per call is mostly the Python runner, not Perl.**
   - Perl's own start is 1.58 M instructions, about 0.2 ms (INFERRED, D §1.1).
   - A bare call costs 3.5–4.4 ms. Study A's prototype brings it to 1.3–1.5 ms, against wasmtime-py's floor of 1.0–1.1 ms. No check is weakened (A §1, §6).
   - A writable grant goes from 21–24 ms to 2.8–3.3 ms. A fresh process's first call goes from 133–157 ms to 34 ms (A §1).
2. **The cost per file is host calls.**
   - Under wasmtime-py, a WASI file-system call costs 15–60 µs, against 0.85 µs under the wasmtime CLI. A §5.1 infers the cause from futex counts: a hand-off to another thread on every call (INFERRED). C §3.5 measured the costs.
   - Opening and reading one file takes nine such calls, about 300 µs (INFERRED, C §3.5).
   - A mount served in Python answers a call in 16–19 µs. So a mount is *faster* than a grant once files are read: 97 against 156 ms over 500 rows (E §2, §4.2).
3. **The provider's own round trips can dwarf everything above.** A grep over 500 rows makes 1,000–1,503 provider calls. A cache in vfs's provider, filled by one batched read, brings that to 1–2 round trips (E §5, §6).
4. **Line-oriented speed.** The guest runs 1.6–1.9× native's instruction count, with native's profile (C §3.1). Two WebAssembly-only costs are worth removing:
   - the 8 KiB PerlIO buffer;
   - wasi-libc's 4-bytes-a-step `memchr`.

   Fixing both: `grep_log` 111 → 68 ms and `-pi` 203 → 114 ms. Adding the fd-table's `isatty` answer as well gives 66 and 112 ms, 20–53% of wall time on file workloads (C §1, §4).
5. **What agents do besides text.**
   - JSON::PP is 29× slower than Cpanel::JSON::XS, which also beats wasi-python's `json` (F §1).
   - Module loads cost: five common modules are 124 M instructions, 25 ms (D §1.2).
   - A preload snapshot makes module loads 3.7–5.8× faster, but changes some behaviour (D §4.2, §5).

Compiler flags are spent: wasi-sdk 34 at `-O2` is already the best measured. Only `wasm-opt -O3` gives a further 2.7% (B §1).

Four engine settings must be pinned in the runner so that a future default cannot silently cost 1.2–3.5× (B §9):
- copy-on-write memory images;
- parallel compilation;
- signals-based traps;
- Cranelift's `speed` level.

**One security finding came out of this work.** wasi-perl 0.1.0's hash seed and `srand` come from the clock: the library has no `/dev/urandom`, so `Perl_seed` falls back to the time (D §6; also EXECUTED by the orchestrator: `-e "/dev/urandom"` is false in the guest). That weakens Perl's defence against hash flooding, an attack where crafted keys make every hash insert slow.

## 2. What the levers add up to (INFERRED)

These are the studies' numbers, combined. They are not one measured build.

Each row takes its "today" figure from one study's own base measurement at the plan's commit, and names that study. The baseline memo was measured at another commit and is not mixed in. The third column names the specifications of section 3 the row needs.

| shape of call | today (study's base) | after (INFERRED) | specs | source |
|---|---|---|---|---|
| bare call, process already running | 3.5–4.4 ms | about 1.4 ms | 2 | A §1 |
| call with a writable grant | 21–24 ms | about 3 ms | 2, 3 | A §1 |
| fresh process, first call | 133–157 ms | about 34 ms | 2, 3 | A §1 |
| `-ne` regex over a 10 MB file | 111 ms | about 63 ms | 2, 4 | C §1, minus A's 3 ms per call |
| `-pi` over a 10 MB file, writable grant | 203 ms | about 97 ms | 2, 3, 4 | C §1 (112 ms, measured on a writable grant, with the artifacts outside the package), minus A's L5 in that layout (about −12 ms: a writable call of 15.0 ms, A §2) and per-call levers (−3 ms) |
| 500 small rows | 156 ms through a grant | about 97 ms through a mount, before the provider's own cost | 5 | E §4.2 |
| the same, with vfs's provider cache | 1,000–1,503 provider calls | 1–2 round trips | vfs's side | E §6 |
| JSON decode, 1.4 MB | 1,350 ms | about 46 ms | 6 | F §3.1 |
| five common modules loaded | 25.2 ms in-guest | about 22 ms (spec 7, with D's single folder); about 4.3 ms with the preload snapshot | 7, 10 | D §4.2, §7 |

## 3. The specifications, in the order proposed

Each is small enough for one branch. The order puts first what is safe, cheap and helps every call.

| # | specification | levers | gain | cost | risk | vfs |
|---|---|---|---|---|---|---|
| 1 | **Seeds from WASI `random_get`** | wrap `Perl_seed` (D §6) | none in speed; the hash seed and `rand` stop being guessable | S | low | medium |
| 2 | **The runner's fixed cost, part 1** | A's L1 (version once), L2 (SHA256SUMS per stamp), L3 (rule inputs only when needed), L4 (`InstancePre`), L7 (map the checked `.cwasm`, with a `/dev/fd` path for macOS and a stamp check for Windows), L8 (the version beside the module); B's four engine pins | about −2.4 ms a call (INFERRED: the sum of L1 −1.0, L3 −0.85, L2 −0.5 and L4 −0.05). On a first call, −22 to −28 ms and −33 MiB from L7, and about −8 ms from L8 | S | low: no rule changes | high: every call |
| 3 | **The runner's fixed cost, part 2** | A's L5 (runtime-file identities per tree stamp) and L6 (a tree record in the module cache) | −19 ms on a writable call; −70 ms on a first call | M | medium: it keeps a rule by a new argument (A §4.1–4.2), which needs its own tests | medium (writable grants); low under mounts |
| 4 | **The guest's I/O** | C's 64 KiB `PERLIOBUF_DEFAULT_BUFSIZ`, the SIMD `memchr` recipe file, and the fd-table answering `isatty` for a file it opened | 20–53% on file workloads with all three; 8× fewer `fd_read`; one host call less per open | S–M | low. It needs a whole `perl_t` run (C §7). The `isatty` change has been tested only as a relink. The SIMD `memchr` makes the module need WebAssembly SIMD128. wasmtime has it on by default, but it narrows the other hosts that can run the module (B §4.2). Study B dropped `-msimd128` for giving no gain, not for this reason. The specification must decide whether to accept that narrowing, or to ship a fallback | high |
| 5 | **Mounts: files served from the embedder's code** | E's design: wasi-python's mount protocol adapted, trimmed to the calls perl.wasm imports, with `stat_reads`, `memo` and `defer_create` on, and `defer_close` a per-mount choice. It must carry wasi-python's ABI checks, budgets, fault boundary and embedder-thread hand-off | makes vfs possible; 1.6–1.9× faster than a grant on 500 rows; halves provider calls per file | L | medium: a large surface, though one wasi-python has already hardened. Under `stat_reads`, a `-s` or `-e` on a large file never opened fetches its body. The provider may instead answer `stat` with the body only when the body is small (E §6 rank 3) | **precondition** |
| 6 | **JSON and CSV in XS**, plus a diff | F: Cpanel::JSON::XS with JSON::MaybeXS, and Text::CSV_XS with Text::CSV, statically linked from pinned CPAN tarballs; Algorithm::Diff (pure Perl) in the library, which gives a real diff, 36 ms for 5,000 lines (F §8 rank 5) | JSON 29× decode, 7× encode; CSV 9× | S–M | low: +124 KB to perl.wasm, no new import; one output difference (`5.0`) | high |
| 7 | **A leaner `@INC`** | D: drop the absent `site_perl` entries and add `PERL_DISABLE_PMC` (C agrees on the latter). Then either merge the library into one folder, or add a guest-side index of its paths, which leaves `@INC` as it is (D §1.3, §8 rank 2) | dropping the entries and `.pmc` alone removes about 72 host stats per five-module call. The single folder or the index reaches −118. That is about 2–3.4 ms per module-loading call (INFERRED, D §1.3) | S (layout), S–M (index) | low: `@INC` prints differently with the single folder | medium; high if the library is ever served by a provider |
| 8 | **Docs for agents** | F §6 idioms (`-0777` for whole-file regex work, keyed sorts); D's `use POSIX ()`; and `File::Find` needs `cwd` (F §7.3) | 4–6× on the programs they fit | S | none | medium |
| 9 | **`wasm-opt -O3`** after the link | B: binaryen version_133, pinned | −2.7% instructions, −8% module size | S | low–medium: a post-link rewrite; `-O4` aborts on exceptions | low |
| 10 | **The preload snapshot, dispatched** (the owner decides) | D: a wizer image with common modules loaded, used only for programs that load them | 3.7–5.8× on module-loading calls; 0 on one-liners | M–L | medium: drift, and in that mode a fixed, unchangeable hash seed (section 5) | high for scripts; none for one-liners |

**Outside this repository:**

- **wasmtime-py's file-call cost.** 56 µs against 0.85 µs is upstream's to fix (A §6 rank 7). A reproduction and an issue upstream would help every embedder. Filing one is outward-facing, so it waits on the owner.
- **vfs's provider cache** (E rank 1) is vfs's to build. wasi-perl's mount must not assume it: wasi-python's rule is that the runtime is embedder-neutral, and this project follows it.

## 4. Measured and dropped

- **Compiler flags:**
  - `-O3`, `-Os`, LTO, ThinLTO, `-msimd128` and `-mtail-call` are all within noise; `-Oz` is 5–30% slower (B §9).
  - Bulk memory and the rest are already on.
  - The stack size has no effect.
- **Engine:** the pooling allocator, a smaller memory reservation and turning copy-on-write off gave no gain or a loss (A §4.4, B §8). Turning fuel or epochs off removes a limit and gains nothing on a bare call (A).
- **Interpreter build options:**
  - Perl's own malloc did not run (two strikes), and is capped at 3% anyway.
  - The hash function, SBOX32, copy-on-write and `use64bitint` showed no measured or expected gain (C §4.4, §7).
  - `NO_TAINT_SUPPORT` saves 1–1.6% but removes `-T`. That is the owner's call, and this memo does not recommend it.
- **Snapshot variants:**
  - A snapshot of a constructed perl saves 0.12 M instructions and no time (D §8).
  - A run-time memory image like wasi-python's costs about 2.7 ms to copy, against 0.04 ms for a wizer image mapped copy-on-write (D §4).
- **I/O designs:**
  - A custom whole-file import is no faster than 1 MiB `fd_read` calls, and would break the preview1-only rule (E §4.3).
  - Staging rows into a host folder is 2.2–2.4× slower, and leaves provider bytes on disk (E §6).
- **Libraries:** JSON::XS, List::MoreUtils::XS, Unicode::UTF8 and YAML::PP (F §8).
- **Libraries deferred, not dropped:**
  - YAML::XS, F's rank 4: 5× in time, 9× in fuel. It is left out of spec 6 because it is the largest addition (+158 KB) and a C parser of a complex format, F's only medium risk, while vfs's catalog names JSON and CSV far more often than YAML. It can join spec 6 if the owner wants YAML.
  - Sort::Key, F's rank 6: 2× in fuel, +15 KB. The docs in spec 8 get most of the same gain.
- **E's rank 6, a cheaper fd-table start and look:** E ranks it low once `stat_reads` (spec 5) makes the look free on mounts. It must keep the special-file refusal on grants. It is left for spec 5 to revisit.

## 5. What the owner decides

1. **The order of specifications 1–9**, or another order.
2. **The preload snapshot (spec 10).** Its drift, from D §5:
   - A preloaded module is usable without `use`: `POSIX::floor` works and `%INC` lists 25 files.
   - In that mode the hash seed is fixed for every call, and public if the image is published. It cannot be changed after the snapshot, because every stash and shared key in the image was hashed with it (D §2.1). So Perl's defence against hash flooding is gone in that mode. Fuel and the deadline still bound the harm.

   The dispatch rule would keep on `perl.wasm`:
   - every one-liner that loads no preloaded module;
   - every call whose environment sets `PERL_HASH_SEED`, `PERL_PERTURB_KEYS` or `PERL_JSON_PP_USE_B` (D §8).

   A further choice: the prototype's wrappers depend on the order of calls inside `perl.c` 5.42.3. A short, reviewed patch to `perl.c` might be clearer, and `AGENTS.md`'s rule on upstream patches governs that (D §8).

   Options:
   - accept the drift and the fixed seed for module-loading calls;
   - accept them only when the embedder makes its own image at install time, so the seed is never published;
   - not build it.
3. **Whether to report the wasmtime-py file-call cost upstream.**
4. **`NO_TAINT_SUPPORT`**: 1.5% against `-T`. Recommended: keep `-T`.

## 6. Limits

- Every number comes from one 4-vCPU x86-64 Linux VM, shared by six studies under one lock for wall time. Sessions differ by ±10–15% in most studies, and by up to about 25% in some of F's wall-time pairs (F §3.1). Comparisons are within a study, and instruction counts are the primary evidence.
- Section 2 adds the gains of different studies' prototypes. It is not a measurement of one combined build.
- macOS, arm64 Linux and Windows were not timed. Study A's L7 is Linux-only as prototyped.
- Every prototype lives on its study branch and in its evidence folder as a patch. None is in `src/`, `recipes/` or `scripts/` on this branch.
