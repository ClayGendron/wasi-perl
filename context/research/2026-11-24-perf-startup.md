# Startup and snapshots: where Perl's start goes, and what a snapshot buys

- **Status:** research, 2026-11-24. Study D of `2026-11-24-perf-research-plan.md`. It changes no code under `src/`, `recipes/` or `scripts/`. The prototype is evidence, not a merge.
- **Researcher:** an agent (Claude Opus 5.5).
- **The question:** where do Perl's startup and module loads spend their instructions? Can a snapshot of a started perl, or of one with modules preloaded, keep exact per-call semantics and pay for itself?
- **Evidence:** `2026-11-24-perf-startup/`. Every script, the prototype's C files and build commands, and every raw result (`results/`).

Labels: EXECUTED means run and seen. READ means read from a file. INFERRED means the researcher's reasoning.

## 0. The answer in five lines

1. Perl's own start is small. A bare `perl -e 1` is 1.58 M guest instructions, about 0.2 ms. A snapshot of a constructed perl saves 0.12 M of them and no measurable time (§2, §4).
2. Module loads are where the instructions go. `use strict; use warnings; use POSIX; use JSON::PP; use List::Util` is 124 M instructions and 25 ms a call. 85% of it is compiling the modules (§1.2).
3. A wizer snapshot with eight common modules preloaded works without touching Perl's source. It brings that program to 18 M instructions and 4.3 ms, 5.8× faster. `-MPOSIX` goes from 15.6 to 4.3 ms (§3, §4).
4. That snapshot's restore costs no instructions and no copy: wasmtime maps it copy-on-write. But the larger image costs about 216 more page faults per call, so a bare one-liner gets 0.38 ms *slower* on it (§4).
5. The preload snapshot differs from perl.wasm in 6 of 41 behaviour checks. Three come from preloaded modules being visible without `use`. Three come from the hash seed being frozen. The construct-only snapshot differs in none (§5).

## 1. Where the start goes

### 1.1 A bare call (EXECUTED)

A research link of perl.wasm wraps 18 of Perl's functions with marks (`probes.c`, `link.sh`). At each mark the host reads the remaining fuel (`phases.py`). The link is otherwise the shipped one: with the custom sections stripped, a plain relink is byte-identical to `build/artifacts/perl.wasm`.

```
flock …/machine.lock context/research/2026-11-24-perf-startup/link.sh probe - build/research/probes.o -Wl,--wrap=…   (the list in build/research/probe-wraps.txt)
python phases.py build/research/probe.wasm build/artifacts/lib/perl5 build/artifacts/dev results/phases.json
```

`perl -e 1`: **1,580,976 instructions**.

| phase | instructions | share |
|---|---|---|
| C start-up, `perl_alloc` | 1,200 | 0.1% |
| `perl_construct` | **341,787** | 22% |
| · `PL_` set-up, stacks, arenas | 33,366 | |
| · internal random state (`seed()`) | 4,152 | |
| · hash seed (`Perl_get_hash_seed`) | 28,702 | |
| · hash function state (SBOX32) and `PL_strtab` pre-size | 216,212 | 14% |
| · locale (`init_i18nl10n`) | 48,845 | 3% |
| `perl_parse` | **1,133,646** | 72% |
| · main stash, switches, `@INC` set-up, script open | 65,006 | 4% |
| · `boot_core_PerlIO` | 14,876 | |
| · `boot_core_UNIVERSAL` (the core XSUBs) | 372,925 | 24% |
| · `boot_core_builtin` | 138,326 | 9% |
| · `boot_core_mro` | 8,176 | |
| · `xs_init` (45 static bootstraps), `%ENV`, `@ARGV`, std handles, lexer start | 485,968 | 31% |
| · compile of `1` | 32,138 | 2% |
| · CHECK, end of parse | 16,021 | |
| `perl_run` | 559 | |
| `perl_destruct`, `perl_free`, `exit` | 100,800 | 6% |

What this shows:

- **About 1.0 M of the 1.58 M is fixed work.** That is the core XSUBs, `builtin::`, the static bootstraps and the hash function's state. Each `newXS` costs about 8,300 instructions: 48 calls cost 400,017.
- **About 0.3 M depends on the call's environment:** the seeds and the locale. They read `PERL_HASH_SEED`, `PERL_PERTURB_KEYS`, `PERL_RAND_SEED`, `PERL_INTERNAL_RAND_SEED` and `LC_*`/`LANG` (READ: `perl.c` 268–310, `util.c` 4757–4860, `locale.c`).
- At the guest's speed of about 8.5 B instructions/s on plain code (baseline memo §4.2), the whole start is **about 0.19 ms** (INFERRED). The fixed part is about 0.12 ms. The runner's bare call is 4.0 to 4.4 ms (baseline memo §4.2), so the rest is host work.

`-ne 'print if /x/' /dev/null` is 1,665,321 and `-lane` 1,708,218. The switches add 0.1 M (EXECUTED, same run).

### 1.2 Module loads (EXECUTED)

Same run (`results/phases.json`), one `-M` each, total instructions with `-e 1`:

| program | instructions | above `-e 1` |
|---|---|---|
| `-Mstrict` | 2.85 M | 1.3 M |
| `-Mwarnings` | 16.2 M | 14.6 M |
| `-MList::Util` | 24.3 M | 22.8 M |
| `-MStorable` | 46.6 M | 45.0 M |
| `-MData::Dumper` | 63.0 M | 61.4 M |
| `-MPOSIX` | 72.0 M | 70.4 M |
| `-MEncode` | 73.7 M | 72.1 M |
| `-MJSON::PP` | 87.9 M | 86.3 M |
| `use strict; use warnings; use POSIX; use JSON::PP; use List::Util` ("five") | 123.8 M | 122.2 M |
| `-MPOSIX -MList::Util -MData::Dumper -MJSON::PP -MStorable -MEncode` ("six") | 173.0 M | 171.4 M |

- **In "five", 121.0 M of 123.8 M is the compile of the main program.** That includes the 108 `require`s it runs at BEGIN time. The outermost `require`s alone account for 104.6 M.
- **POSIX's default import is 16.4 M on its own.** It exports 587 names (EXECUTED: `scalar(@POSIX::EXPORT)`). `-mPOSIX` (no import) costs 55.9 M against 72.1 M; `-MPOSIX=floor` costs 60.7 M (`results/import_fuel.txt`).
- Counts vary by about 0.5% between runs, because the hash seed changes the hashes' layout. For example, `-MPOSIX` read 72.0, 72.1 and 72.3 M in three runs.

At the measured wall time, compile-heavy code runs at about 4 B instructions/s. `-MPOSIX -e 1` is 15.6 ms against 1.1 ms for `-e 1` in the same harness (`results/ab.json`; INFERRED rate). That is half the speed of plain loops.

### 1.3 The `@INC` search (EXECUTED)

wasmtime's own trace of preview1 counts the calls (`wasi_calls.sh`, `results/wasi_calls.txt`):

```
wasi_calls.sh build/artifacts/perl.wasm build/artifacts five -- -e 'use strict; use warnings; use POSIX; use JSON::PP; use List::Util; 1'
```

| WASI call | `-e 1` | "five" |
|---|---|---|
| `path_filestat_get` | 1 | **155, of which 118 fail** |
| `path_open` | 1 (`/dev/null`) | 19 (18 module files) |
| `fd_fdstat_get` | 11 | 47 |
| `fd_read` | 0 | 38 (238,168 bytes) |
| `fd_tell` / `fd_seek` / `fd_close` | 4 / 0 / 1 | 36 / 14 / 19 |

Why 8.6 stats per file found:

- `@INC` has four entries: `5.42/site_perl/wasm32-wasi`, `5.42/site_perl`, `5.42/wasm32-wasi` and `5.42`. The two `site_perl` folders **do not exist in the library** (READ: `ls build/artifacts/lib/perl5/5.42`).
- For each entry, `require` stats `X.pmc`, then `X.pm`. So every file pays 4 failing stats for the two absent folders. A file in `5.42` pays 7 failing stats, then one that succeeds.
- The descriptor table (`recipes/wasi-fd-table.c`) stats once more before the open. It does this to refuse special files.

The host cost of each call, from a loop of 20,000 inside one call, less the empty loop (`statcost.py`, `results/statcost.json`, under the lock). The guest instructions come from `results/statfuel.txt`.

| operation | host µs each | guest instructions each |
|---|---|---|
| stat of a missing file | **28.7** | 2,719 |
| stat of a file found | 58.5 | 2,520 |
| open and close | 214.8 | 11,936 |

So the 118 failed stats of "five" cost about **3.4 ms of its 25 ms** (INFERRED: 118 × 28.7 µs). The search is cheap in the guest and dear in the host.

**What a flatter layout would save** (INFERRED from the counts above):

| layout | stats per module file | "five" stats | saved |
|---|---|---|---|
| today | 8.6 | 154 | |
| drop the two absent `site_perl` entries | 4.6 | 82 | 72 (≈ 2.1 ms) |
| one library folder (arch merged into `5.42`) | 3 | 54 | 100 (≈ 2.9 ms) |
| one folder and `-DPERL_DISABLE_PMC` | 2 | 36 | 118 (≈ 3.4 ms) |

A precomputed index would save the same calls without changing `@INC`. It would be a sorted list of the library's 1,453 paths, built from `MANIFEST`. A guest-side `path_filestat_get` wrapper would then answer ENOENT under `/usr/local/lib/perl5` without asking the host. The runner already checks the library against its digest, so the index cannot drift from the files (READ: `_artifacts.checked()`). Under vfs, a library served by a provider would turn each failed stat into a provider `stat`, so this matters more there (INFERRED).

## 2. Is a snapshot possible?

**Yes. `wasmtime wizer` is built into the wasmtime 51 CLI** (EXECUTED: `wasmtime wizer --help`). It is the tool wasi-python's `80_snapshot.sh` uses. During initialisation it gives the module WASI (`--dir`, `-S cli=y`), so the init function can read the library. The output keeps `run` and drops both `_initialize` and the init export (EXECUTED: exports `memory`, `run`, `wasi_perl_exit_status`).

The prototype is one recipe-style C file. It needs no change to Perl's source (`perl-reactor.c`, `fdtable-reset.c`, `build.sh`):

```
flock /home/user/wasi-perl-wt/machine.lock context/research/2026-11-24-perf-startup/build.sh
```

- **(a) construct snapshot** (`snap-construct.wasm`). `wizer.initialize` runs `PERL_SYS_INIT3`, `perl_alloc` and `perl_construct`. `run` reads argv and the environment from WASI, then runs `perl_parse`, `perl_run`, `perl_destruct` and `exit`, as `perlmain.c` does.
- **(b) preload snapshot** (`snap-preload.wasm`). It does the same, then runs a first `perl_parse` of `-mstrict -mwarnings -mPOSIX -mList::Util -mJSON::PP -mData::Dumper -mStorable -mEncode -e 0`. Modules are loaded by `require` with no import, so nothing lands in `main::`. `run` then runs a second `perl_parse` with the call's argv.

### 2.1 State that must not be frozen, and how the prototype undoes it

| state | where it is captured | how `run` undoes it | checked by (`drift.py`) |
|---|---|---|---|
| `environ` | wasi-libc reads it lazily; `perl_construct`'s `getenv` fills it at snapshot time | `__wasilibc_deinitialize_environ` at the end of init, then `__wasilibc_initialize_environ` in `run` | `env`, `pwd` |
| wasi-libc's preopen table | filled by the first path call at snapshot time | `__wasilibc_reset_preopens` at the end of init | every case with `/data` |
| the guest's descriptor table (`wasi-fd-table.c`) | filled at the first WASI call, with the snapshot host's grants | `wasi_perl_fd_table_reset`, a new function that empties it | `n_file`, `p_i` |
| working directory | `wasi-stubs.c`'s constructor runs once, at snapshot time | `run` repeats the `chdir($PWD)` | `pwd` |
| argv, `$0`, `@ARGV` | the fake argv of init | read from WASI in `run`; `perl_parse` sets the rest | `argv_0` |
| `$^T` | `perl_parse` calls `time()` | nothing needed: parse runs per call | `basetime` |
| srand, `PERL_RAND_SEED` | `PL_srand_called`, `PL_srand_override_next` | cleared and re-read from the env | `rand`, `rand_seed_env` |
| internal random state, hash-order bits | `perl_construct` | re-seeded in `run`, in `perl_construct`'s order | `hash_order` |
| hash seed and hash state | `perl_construct` | (a): made again, because no hash holds a key yet (checked, `wasi_perl_hashes_empty`); (b): **cannot change**, because every stash and shared key was hashed with it | `hash_seed`, `hash_seed_env` |
| PerlIO std handles | opened by init's parse in (b) | flushed at the end of init; the fds 0–2 mean the same in each instance | all |
| locale | `init_i18nl10n` in `perl_construct` | not redone; `setlocale` and `-CS` agree with perl.wasm under `LC_ALL`/`LANG` | `locale`, `lang_utf8` |

A second `perl_parse` on one interpreter does three things that would undo the preload (READ: `perl.c` `S_init_main_stash`, `S_parse_body`). The link wraps the functions that follow each one, so no Perl file changes:

1. `init_main_stash` makes a new, empty `main::`, which drops every preloaded package. The wrapper on `Perl_newSVpvn_share` puts the snapshot's stash back. `init_main_stash` calls that function first, with `"main"`.
2. `PL_isarev = newHV()` drops the inheritance map. The wrapper on `Perl_boot_core_PerlIO`, which `parse_body` calls next, puts the map back.
3. The core boots and `xs_init` define every XSUB again. Under `-w` that printed 140 "Subroutine … redefined" lines. The four `boot_core_*` wrappers and a `Perl_newXS` wrapper skip them on the second parse, and `run` passes no `xs_init`.

`@INC` is cleared before the second parse, so it is filled exactly as before. `%INC` keeps the preloaded files, so their `require` is a no-op and only `import` runs.

These wrappers depend on the order of calls inside `perl.c` 5.42.3. A Perl upgrade must re-run the drift suite. A specification might prefer a reviewed patch of about ten lines to `perl.c`, so that both steps are skipped when a flag is set. That is clearer than three wrappers that guess at call order (INFERRED).

## 3. What the snapshots save, in instructions (EXECUTED)

`restore.py`, metered pass (`results/restore.json`, `fuel`):

| program | perl.wasm | construct snapshot | preload snapshot |
|---|---|---|---|
| `-e 1` | 1.58 M | 1.46 M | **1.25 M** |
| `-ne … /dev/null` | 1.66 M | 1.55 M | 1.33 M |
| `-MPOSIX -e 1` | 72.3 M | 71.8 M | **17.4 M** (16 M of it is POSIX's import) |
| `-MJSON::PP -e 1` | 87.9 M | 87.7 M | **1.37 M** |
| "five" | 123.8 M | 123.7 M | **18.0 M** |
| "six" | 173.0 M | 173.2 M | **18.1 M** |
| `use strict; use warnings` | 17.4 M | | **1.6 M** (`results/import_fuel.txt`) |

## 4. What the restore costs, in time (EXECUTED, under the lock)

### 4.1 Instantiation alone

`restore.py`, median of 30 (`results/restore.json`, `ms_median`):

| allocator | perl.wasm `instantiate` / `InstancePre` | preload snapshot `instantiate` / `InstancePre` |
|---|---|---|
| default (on demand, copy-on-write images) | 0.050 / 0.045 ms | 0.044 / 0.038 ms |
| copy-on-write off (`memory_init_cow = False`) | 1.762 / 1.768 ms | 2.745 / 2.718 ms |
| pooling | 0.041 / 0.035 ms | 0.047 / 0.039 ms |

- **The snapshot's 7.3 MB of data costs nothing to instantiate.** wasmtime maps it copy-on-write, as it already maps perl.wasm's 5.5 MB. This is unlike wasi-python's run-time image, which is copied into each instance (about 30 ms; baseline memo §4.1).
- Turning copy-on-write off shows what a copied image would cost: about 2.7 ms for this one (INFERRED: a run-time image of Perl copied the way wasi-python's is would pay about that).
- `InstancePre` saves about 5 µs over `Linker.instantiate`. The pooling allocator saves about 10 µs more. Both are negligible at this scale.

### 4.2 Whole calls

`ab.py` alternates the two modules call by call, median of 100 (`results/ab.json`):

| program | perl.wasm | preload snapshot | change |
|---|---|---|---|
| `-e 1` | 1.077 ms | 1.454 ms | **+0.38 ms** |
| `-ne … /dev/null` | 1.501 ms | 1.883 ms | +0.38 ms |
| `-MPOSIX -e 1` | 15.55 ms | 4.25 ms | **−11.3 ms (3.7×)** |
| "five" | 25.20 ms | 4.32 ms | **−20.9 ms (5.8×)** |

The pooling allocator gives the same picture: +0.34, −11.2 and −23.0 ms. These calls skip the runner's own checks, so `-e 1` reads 1.1 ms here against 4.0 ms through `run()`.

**Why a bare call gets slower with fewer instructions** (`faults.py`, `results/faults.json`). The minor page faults per `-e 1` call are 78 on perl.wasm, 64 on the construct snapshot and **294 on the preload snapshot**. A call writes to many pages of the preloaded heap: reference counts, the stash and the pads. Each write copies a page on first touch. That is about 216 extra faults, or 0.37 ms (INFERRED: 1.7 µs a fault).

Keeping 32 MiB of each pooled slot resident cuts the faults to 8. But resetting it costs more: 2.6 ms for `-e 1`. That is not a fix.

**The construct snapshot saves nothing measurable:** 1.039 ms against 1.045 ms (`results/faults.json`).

### 4.3 What the snapshot costs to make and ship (EXECUTED)

- `snap-preload.wasm` is 11.1 MB against perl.wasm's 9.3 MB. Its data is 7.3 MB against 5.5 MB, and its initial memory 15 MiB against 13 MiB. The code section is the same size, so compiling it costs the same (INFERRED).
- `wasmtime wizer` took at most 21.5 s, including the wait for the machine lock.
- The snapshot is **not reproducible as built**: two builds differ in 2.6 M bytes. The clock seeds the hash seed and the heap's layout. wasi-python pins the clock and entropy during init (READ: `reactor_main.c`, `PIN_CLOCKS`), and a specification would have to do the same. That would then fix the hash seed in the published binary (see §6).

## 5. Semantic drift (EXECUTED)

`drift.py` runs 41 programs in fresh instances of each module, twice each, with the runner's preopens and a writable `/data` (`results/drift.json`). The checks cover argv, `$0`, `-n`, `-p`, `-i`, `-l`, `-0`, `-a`, `-M` with import lists, `%ENV`, `PWD`, `$^T`, the BEGIN/INIT/END order, `die` and `exit` codes, `-w` and `use warnings`, `__FILE__`/`__LINE__`, `$/` and `$\`, JSON::PP, Data::Dumper, List::Util, Encode, Storable, `@ISA` and `mro`, the locale, and `PERL_RAND_SEED`. Hash order, `rand` and the hash seed must also vary between runs.

- **Construct snapshot: 0 of 41 differ.** Hash order, `rand` and the hash seed vary between runs as they do on perl.wasm. `PERL_HASH_SEED=0` is honoured.
- **Preload snapshot: 6 of 41 differ.**
  - *Visible preloads:*
    - `print POSIX::floor(2.5)` without `use POSIX` prints 2. perl.wasm dies with "Undefined subroutine".
    - `defined &POSIX::floor` is 1, not 0.
    - `keys %INC` lists 25 files, not none.
  - *Frozen hash seed:*
    - `Hash::Util::hash_seed()` is the same on every call.
    - `PERL_HASH_SEED=0` and `PERL_PERTURB_KEYS=0` are ignored, so the order differs from perl.wasm's under the same settings.
    - Hash *order* still varies by default, because the order bits are re-seeded.
  - *Read, not run:* JSON::PP fixes `use constant USE_B => $ENV{PERL_JSON_PP_USE_B}` at compile time (READ: `JSON/PP.pm` line 48). The snapshot keeps the value it saw then. No other preloaded file reads `%ENV` while loading (READ: grep over the 25 files).
- **Nothing written by one call reaches the next.** `leak_set` overwrites `POSIX::floor` and plants a package variable. `leak_check`, in the next instance, sees neither. Each call is a new instance from the same bytes.

## 6. Security

- **No state crosses calls.** Each call is a fresh instance, so the snapshot's image is read-only shared data (EXECUTED: `leak_set`/`leak_check`).
- **Nothing of the build host is in the image** except one absolute path: the prototype compiled `perlmain.c` by its absolute path, and `__FILE__` keeps it. Perl's own make uses relative paths. Init runs with no environment and no stdio, and its preopens and descriptor table are cleared (EXECUTED: `strings snap-preload.wasm | grep /home/user` finds that one path).
- **The preload snapshot freezes the hash seed** for every call. Any program can read the seed with `Hash::Util::hash_seed()`. If the snapshot is published, the seed is public. Inputs could then be crafted so that many keys share one bucket, which makes hashing quadratic. That is bounded by the call's fuel and deadline, but Perl's defence against hash flooding is gone in that mode. An embedder that makes its snapshot at install time would have a seed that is not published, but it would still be shared by every call (INFERRED).
- **A finding outside snapshots: wasi-perl's seeds come from the clock.** The library has no `/dev/urandom` (EXECUTED: `-e /dev/urandom` is false). So `Perl_seed` falls back to `gettimeofday` plus a constant pid and a stack address (READ: `util.c` 4683–4750). The hash seed and `srand` are therefore guessable from a call's start time. `Perl_seed` is called across objects and can be wrapped, so a recipe could take it from WASI `random_get` (INFERRED). That changes no speed, and it belongs to a specification of its own.

## 7. Per-call savings for vfs-shaped programs (INFERRED from §3 and §4)

| program shape | today (this harness) | with the preload snapshot | with a flat `@INC` |
|---|---|---|---|
| one-liner, no module (`-ne`, `-pi`, `-lane`) | 1.1 ms (4.0 ms through `run()`) | **+0.38 ms**: so dispatch these to perl.wasm | 0 |
| `use strict; use warnings` script | about 5 ms (17.4 M instructions) | saves about 4 ms (−15.8 M instructions) | about −0.4 ms (2 files, 14 failed stats) |
| `-MPOSIX` | 15.6 ms | **−11.3 ms** | about −2.1 ms (11 files, 73 failed stats) |
| five common modules | 25.2 ms | **−20.9 ms** | about −3.4 ms (18 files, 118 failed stats) |

Under vfs, a library served through a provider adds a provider `stat` per failed stat and a staged read per file. A preloaded module makes none of those (READ: vfs spec 172 says the Python snapshot "makes the first import moot" in the same way). The flat layout and the index remove only the failed stats.

The six-module baseline (48.7 ms through `run()`, 174 M instructions) would fall to about 15 ms through `run()` (INFERRED: 37.0 → 4.7 ms in `restore.py`, plus the runner's 3 ms).

## 8. Ranked candidates

| # | candidate | gain | cost to build | risk (drift first) | security | vfs relevance |
|---|---|---|---|---|---|---|
| 1 | **Preload snapshot, dispatched**: perl.wasm for programs that load no preloaded module; the snapshot for those that do; perl.wasm whenever the env sets `PERL_HASH_SEED`, `PERL_PERTURB_KEYS` or `PERL_JSON_PP_USE_B` | −11 to −21 ms (3.7–5.8×) on module-loading calls; 0 on one-liners | medium: one recipe C file of about 200 lines, a wizer step with pinned clocks, a second artifact, a dispatch rule in the runner, the drift suite as tests | medium: 3 visibility drifts appear when a program loads one preloaded module and uses another without `use`; wrappers coupled to `perl.c` 5.42.3's call order | frozen hash seed in that mode (public if published) | **high**: agent scripts that `use` modules; no provider reads for preloaded files |
| 2 | **Flat library**: one `@INC` folder, the absent `site_perl` entries dropped, optionally `PERL_DISABLE_PMC`; or a guest-side index of the library's paths | −72 to −118 host stats per five-module call, about 2–3.4 ms; −2.1 ms on `-MPOSIX`; 0 on one-liners; adds to (1) only for modules not preloaded | low (layout and `config.sh`) or low-medium (index wrapper) | low: `@INC` prints differently; `.pmc` stops working (none shipped). The index keeps `@INC` as it is | none | medium; high if the library is provider-served |
| 3 | **Import guidance**: `use POSIX qw(...)` or `use POSIX ()`, in `docs/` | POSIX's import is 16 M instructions, about 4 ms, even when preloaded | trivial | none | none | medium (agent prompts) |
| 4 | Construct snapshot | −0.12 M instructions, no measurable time | medium | none measured (0/41) | none | none |
| 5 | `InstancePre`, pooling allocator | about 5–15 µs per call | low (study A's) | none | none | low |
| 6 | Seeds from `random_get` (security, not speed) | none | low (a `Perl_seed` wrapper) | none: seeds stay random | **better**: seeds are no longer guessable from the clock | medium |

**Not recommended:**

- A construct snapshot (4): it saves no time.
- The preload snapshot for every call: it would cost one-liners, wasi-perl's main use, 0.38 ms (+35% in this harness, +9% through `run()`).
- A run-time memory image like wasi-python's: it would pay about 2.7 ms to copy, where a wizer image costs 0.04 ms.

**For the owner to decide** before a specification of (1):

- whether the visibility drift is acceptable;
- whether the frozen seed is acceptable in the dispatched mode;
- whether the main-stash and isarev steps are wrappers or a reviewed patch to `perl.c`.

## 9. Limits

- One shared x86-64 machine with other agents running fuel passes outside the lock. Wall times are medians, interleaved where compared (`ab.py`). Instruction counts are the primary evidence.
- The harness calls wasmtime-py directly, without the runner's checks, so absolute times are lower than through `run()`. The differences carry over (INFERRED).
- The per-stat host cost (28.7 µs) was measured inside one call on wasmtime-py's WASI. Study E measures the WASI path in depth.
- The drift suite has 41 cases. It is not Perl's own test suite run on the snapshot. A specification would run `tests/perl_t` on the snapshot, too.
- The preload list is one guess at "common modules". `Cwd`, `File::Spec`, `Getopt::Long`, `Text::CSV`-like code and `Time::Local` were not tried.

To reproduce, from the worktree root, with the bench venv's `python` (the timings under the lock):

```
flock /home/user/wasi-perl-wt/machine.lock context/research/2026-11-24-perf-startup/build.sh
python …/phases.py build/research/probe.wasm build/artifacts/lib/perl5 build/artifacts/dev …/results/phases.json
…/wasi_calls.sh build/artifacts/perl.wasm build/artifacts five -- -e 'use strict; use warnings; use POSIX; use JSON::PP; use List::Util; 1'
python …/drift.py build/research build/artifacts …/results/drift.json
flock …/machine.lock python …/restore.py build/research build/artifacts …/results/restore.json 30
flock …/machine.lock python …/statcost.py build/artifacts …/results/statcost.json
flock …/machine.lock python …/ab.py build/research build/artifacts …/results/ab.json 100
flock …/machine.lock python …/faults.py build/research build/artifacts …/results/faults.json 60
```

The probe build is `link.sh probe - build/research/probes.o "-Wl$(cat build/research/probe-wraps.txt)"`, after compiling `probes.c` with `recipes/wasi-cc -O2 -c`.
