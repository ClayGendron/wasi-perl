# Every lever on wasi-perl's speed under vfs, ranked, and the specifications they become

- **Status:** research synthesis, 2026-11-24. It proposes specifications; none is agreed yet. Section 5 lists what the owner decides.
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
- **Labels:** every number here is the study's own, cited by letter and section. Those studies label each one EXECUTED or READ. A number this memo derives by combining studies is marked INFERRED.

## 1. The answer

Under vfs, wasi-perl runs many short programs, each in a fresh instance, over files a provider serves. Five costs matter, in this order:

1. **The fixed cost per call is mostly the Python runner, not Perl.**
   - Perl's own start is 1.58 M instructions, about 0.2 ms (D §1.1).
   - A bare call costs 3.5–4.4 ms. Study A's prototype brings it to 1.3–1.5 ms, against wasmtime-py's floor of 1.0–1.1 ms. No check is weakened (A §1, §6).
   - A writable grant goes from 21–24 ms to 2.8–3.3 ms. A fresh process's first call goes from 138–153 ms to 34 ms (A §1).
2. **The cost per file is host calls.**
   - Under wasmtime-py, a WASI file-system call costs 15–60 µs, against 0.85 µs under the wasmtime CLI. The likely cause is a hand-off to another thread on every call (A §5.1, C §3.5).
   - Opening and reading one file takes nine such calls, about 300 µs (C §3.5).
   - A mount served in Python answers a call in 16–19 µs. So a mount is *faster* than a grant once files are read: 97 against 156 ms over 500 rows (E §2, §4.2).
3. **The provider's own round trips can dwarf everything above.** A grep over 500 rows makes 1,000–1,503 provider calls. A cache in vfs's provider, filled by one batched read, brings that to 1–2 round trips (E §5, §6).
4. **Line-oriented speed.** The guest runs 1.6–1.9× native's instruction count, with native's profile (C §3.1). Two WebAssembly-only costs are worth removing:
   - the 8 KiB PerlIO buffer;
   - wasi-libc's 4-bytes-a-step `memchr`.

   Fixing both cuts 23–52% of wall time on file workloads: `grep_log` 111 → 66 ms, `-pi` 203 → 112 ms (C §1).
5. **What agents do besides text.**
   - JSON::PP is 29× slower than Cpanel::JSON::XS, which also beats wasi-python's `json` (F §1).
   - Module loads cost: five common modules are 124 M instructions, 25 ms (D §1.2).
   - A preload snapshot cuts module loads 4–6×, but changes some behaviour (D §5).

Compiler flags are spent: wasi-sdk 34 at `-O2` is already the best measured. Only `wasm-opt -O3` gives a further 2.7% (B §1). Two engine settings must be pinned so that a future default cannot silently cost 2–3×: copy-on-write memory and parallel compilation (B §9).

**One security finding came out of this work.** wasi-perl 0.1.0's hash seed and `srand` come from the clock: the library has no `/dev/urandom`, so `Perl_seed` falls back to the time (D §6; also EXECUTED by the orchestrator: `-e "/dev/urandom"` is false in the guest). That weakens Perl's defence against hash flooding, an attack where crafted keys make every hash insert slow.

## 2. What the levers add up to (INFERRED)

These are the studies' numbers, combined. They are not one measured build.

| shape of call | 0.1.0 | after specs 1–4 below | where it comes from |
|---|---|---|---|
| bare call, process already running | 4.4 ms | about 1.4 ms | A |
| call with a writable grant | 31 ms | about 3 ms | A |
| fresh process, first call | 122–153 ms | about 34 ms | A |
| `-ne` regex over a 10 MB file | 107–111 ms | about 63–66 ms | C, A |
| `-pi` over a 10 MB file | 203–216 ms | about 110 ms | C, A |
| 500 small rows through a mount (vfs) | not possible | about 97 ms, before the provider's own cost | E |
| the same, with vfs's provider cache | | 1–2 provider round trips instead of 1,000–1,503 | E |
| JSON decode, 1.4 MB | 1,287–1,350 ms | about 46 ms | F |
| five common modules loaded | 25 ms in-guest | about 22 ms (flat `@INC`); about 4.3 ms with the preload snapshot | D |

## 3. The specifications, in the order proposed

Each is small enough for one branch. The order puts first what is safe, cheap and helps every call.

| # | specification | levers | gain | cost | risk | vfs |
|---|---|---|---|---|---|---|
| 1 | **Seeds from WASI `random_get`** | wrap `Perl_seed` (D §6) | none in speed; the hash seed and `rand` stop being guessable | S | low | medium |
| 2 | **The runner's fixed cost, part 1** | A's L1 (version once), L2 (SHA256SUMS per stamp), L3 (rule inputs only when needed), L4 (`InstancePre`), L7 (map the checked `.cwasm`, with a `/dev/fd` path for macOS and a stamp check for Windows), L8; B's four engine pins | about −3 ms a call; −22 to −28 ms and −33 MiB on a first call | S | low: no rule changes | high: every call |
| 3 | **The runner's fixed cost, part 2** | A's L5 (runtime-file identities per tree stamp) and L6 (a tree record in the module cache) | −19 ms on a writable call; −70 ms on a first call | M | medium: it keeps a rule by a new argument (A §4.1–4.2), which needs its own tests | medium (writable grants); low under mounts |
| 4 | **The guest's I/O** | C's 64 KiB `PERLIOBUF_DEFAULT_BUFSIZ`, the SIMD `memchr` recipe file, and the fd-table answering `isatty` for a file it opened | 23–52% on file workloads; 8× fewer `fd_read`; one host call less per open | S–M | low; needs a whole `perl_t` run (C §7) | high |
| 5 | **Mounts: files served from the embedder's code** | E's design: wasi-python's mount protocol adapted, trimmed to the calls perl.wasm imports, with `stat_reads`, `memo` and `defer_create` on, and `defer_close` a per-mount choice. It must carry wasi-python's ABI checks, budgets, fault boundary and embedder-thread hand-off | makes vfs possible; 1.6–1.9× faster than a grant on 500 rows; halves provider calls per file | L | medium: a large surface, though one wasi-python has already hardened | **precondition** |
| 6 | **JSON and CSV in XS** | F: Cpanel::JSON::XS with JSON::MaybeXS, and Text::CSV_XS with Text::CSV, statically linked from pinned CPAN tarballs | JSON 29× decode, 7× encode; CSV 9× | S–M | low: +124 KB to perl.wasm, no new import; one output difference (`5.0`) | high |
| 7 | **A leaner `@INC`** | D: drop the absent `site_perl` entries and add `PERL_DISABLE_PMC`. C agrees on the latter | −72 to −118 host stats, about 2–3.4 ms, per module-loading call | S | low: `@INC` prints differently | medium; high if the library is ever served by a provider |
| 8 | **Docs for agents** | F §6 idioms (`-0777` for whole-file regex work, keyed sorts); D's `use POSIX ()`; and `File::Find` needs `cwd` (F §7.3) | 4–6× on the programs they fit | S | none | medium |
| 9 | **`wasm-opt -O3`** after the link | B: binaryen version_133, pinned | −2.7% instructions, −8% module size | S | low–medium: a post-link rewrite; `-O4` aborts on exceptions | low |
| 10 | **The preload snapshot, dispatched** (the owner decides) | D: a wizer image with common modules loaded, used only for programs that load them | 3.7–5.8× on module-loading calls; 0 on one-liners | M–L | medium: drift (section 5) | high for scripts; none for one-liners |

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
- **Libraries:** JSON::XS, List::MoreUtils::XS, Unicode::UTF8 and YAML::PP (F §8). YAML::XS (5× in time, +158 KB) and Sort::Key (2× in fuel, +15 KB) are worth it only if agents ask for them.

## 5. What the owner decides

1. **The order of specifications 1–9**, or another order.
2. **The preload snapshot (spec 10).** Its drift, from D §5:
   - A preloaded module is usable without `use`: `POSIX::floor` works and `%INC` lists 25 files.
   - In that mode the hash seed is fixed for every call, and public if the image is published.

   The dispatch rule would keep one-liners, and any call that sets `PERL_HASH_SEED`, on `perl.wasm`. Options: accept it; build only the dispatch plus a seed re-randomisation (which the prototype has not shown to be possible); or not build it.
3. **Whether to report the wasmtime-py file-call cost upstream.**
4. **`NO_TAINT_SUPPORT`**: 1.5% against `-T`. Recommended: keep `-T`.

## 6. Limits

- Every number comes from one 4-vCPU x86-64 Linux VM, shared by six studies under one lock for wall time. Sessions differ by ±10–15%. Comparisons are within a study, and instruction counts are the primary evidence.
- Section 2 adds the gains of different studies' prototypes. It is not a measurement of one combined build.
- macOS, arm64 Linux and Windows were not timed. Study A's L7 is Linux-only as prototyped.
- Every prototype lives on its study branch and in its evidence folder as a patch. None is in `src/`, `recipes/` or `scripts/` on this branch.
