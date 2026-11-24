# 022 — A preload image the embedder makes at install time

- **Status:** proposed, not yet agreed by the owner. Written by the orchestrator, 2026-11-24, for synthesis item 10. The owner gave two answers that bear on it, the same day (`../roadmap.md`):
  - "You can do all 10 actually, but do not change the API around perl to be different than wasi python, unless wasi python could adopt the same changes. We are going to eventually unify the shared components." That agrees to building the item, not this text.
  - "Install-time images only." The roadmap records what it means: the image may be built only as one the embedder makes itself, so that its fixed hash seed is never published. So wasi-perl ships no preloaded image.

  These choices are new here and await the owner: choice (c), which no study prototyped; the second binary `perl-reactor.wasm`; the default package list held in the runner's code (Decision 5); and the cap on data segments. They are to be asked with AskUserQuestion before the build starts. Amended the same day for the review `../reviews/specs-017-022-pr-2026-11-24.md` (H1, H2, M1–M4, M6, L5–L8).
- **Owner:** Clay Gendron
- **Kind:**
  - the build: a new `recipes/wasi-reactor.c`, its object compiled in `scripts/20_target.sh`, its link in step 4 of spec 019's `scripts/40_link.sh`, `scripts/assemble_artifacts.py`, and the `SHA256SUMS` that step 7 writes. `MANIFEST` is not changed;
  - the runner: new `src/wasi_perl/_snapshot.py`, `_snapshot_make.py` and `_image_module.py`; changes to `runner.py`, `_artifacts.py`, `_engines.py`, `cli.py` and `__init__.py`;
  - checks in `tests/snapshot/`, `tests/build/` and `tests/mounts/test_mount_api.py`; `docs/snapshots.md` and `docs/sandbox.md`.
- **Depends on:**
  - 013, because the image's hash seed must come from the host's randomness;
  - 014, for the engine's `memory_init_cow = True` pin, one `InstancePre` per module and the mapped `.cwasm`;
  - 017, so that `make_snapshot` and `make_package_snapshot` take `mounts` and `max_file_calls` as wasi-python's do (Decision 5), and so that `Completed.files` already stands before `image_sha256`;
  - 019, the library index and the build order of its Decision 5, which the roadmap puts first. The reactor links the same index.
  - 021 (`wasm-opt -O3`) in either order. If 021 is built first, this adds the reactor to 021's step. If this is built first, 021's pass covers both binaries.
- **Research it stands on:**
  - `../research/2026-11-24-perf-startup.md` (study D) and its evidence folder: `perl-reactor.c`, `fdtable-reset.c`, `build.sh`, `drift.py`, `results/`;
  - `../research/2026-11-24-perf-synthesis.md` §3 row 10 and §5 item 2;
  - wasi-python's snapshot API: `src/wasi_python/_snapshot.py`, `_snapshot_make.py`, `runner.py`, `recipes/reactor_main.c` and `docs/how-to/snapshots.md` (READ, 2026-11-24).

Labels: EXECUTED means run and seen. READ means read from a file. INFERRED means reasoned, not run. A number with no command beside it is study D's, with its label there.

## Intent

A call that loads modules spends most of its time compiling them. "Five" is `use strict; use warnings; use POSIX; use JSON::PP; use List::Util`. It costs 123.8 M instructions and 25.2 ms a call, and 85% of that is the modules' compile (D §1.2). A preload image is Perl's memory after those modules were loaded once. In study D's prototype, "five" ran in 4.32 ms from such an image, 5.8× faster. `-MPOSIX` ran in 4.25 ms against 15.55 ms (D §4.2, EXECUTED).

After this specification, an embedder can do this:

```python
from wasi_perl import Snapshot, make_package_snapshot, run

snap = make_package_snapshot()          # once, at install or service start
snap.save("modules.wpsnap")             # optional: keep it for the next process
done = run(["-e", "use POSIX; print floor(2.5)"], snapshot=snap)
```

The API is wasi-python's: `Snapshot`, `SnapshotOrigin`, `SnapshotFailed`, `SnapshotMismatch`, `make_snapshot`, `make_package_snapshot`, `Snapshot.save`, `Snapshot.load`, `Snapshot.check` and `run(..., snapshot=...)`. Every difference is listed in section "Where wasi-perl differs from wasi-python", with its reason and how wasi-python could adopt it.

wasi-perl ships no image. No image is in the wheel, the artifact set or a release, and the build makes none. An image is made only by the embedder's own call, with a hash seed drawn from the host's randomness then. So the seed is never published.

## Decisions

### 1. How a call starts from the image: a module the runner writes (choice c)

Study D measured or bounds three ways.

| | (a) a wizer-made module | (b) a run-time memory image, copied per call | (c) the image written into a new module by the runner |
|---|---|---|---|
| restore per call | instantiation 0.044 ms; the 7.3 MB of data is mapped copy-on-write (D §4.1, EXECUTED) | about 2.7 ms to copy 15 MiB (INFERRED: D §4.1, the same image with copy-on-write off, 2.745 ms) | about 0.04 ms (INFERRED: the module has (a)'s shape, data segments over the image, so wasmtime maps it the same way) |
| "five" per call | 4.32 ms (D §4.2, EXECUTED) | about 7.0 ms (INFERRED: 4.3 + 2.7) | about 4.3 ms (INFERRED, as above) |
| memory per live call | the pages it writes, about 294 page faults on `-e 1` (D §4.2) | a private 15 MiB, since the copy writes every page | as (a) |
| install-time step | `wasmtime wizer`, which needs the wasmtime CLI. The wheel carries none, and it is not a Python dependency. Up to 21.5 s with the machine lock (D §4.3) | the prelude's run, then a capture | the prelude's run, a capture, writing the module, and one compile, cached as a `.cwasm` |
| who runs the making | the wasmtime CLI, with its own WASI set-up: none of the runner's grant rules or limits apply | the runner, under `run`'s rules | the runner, under `run`'s rules |
| new code to build | a wizer step for each embedder | the reactor and the runner's capture | the same as (b), plus the module writer, about 200 lines |
| what wasi-python does | at its build, for `python-snapshot.wasm` (READ: `scripts/steps/80_snapshot.sh`) | for every image an embedder makes (READ: `_snapshot._restore`) | nothing yet |

**(c) is chosen.**
- It restores as cheaply as (a). The runner does the making itself, under the same rules as (b), with no tool beyond wasmtime-py.
- (a) is refused because the embedder's install step would need a binary the wheel does not carry. `make_snapshot(code)` could then not be a Python call checked by `run`'s rules.
- (b) is refused because it pays about 2.7 ms of copying on every call. That is 60% of the gain on "five". It also holds a private 15 MiB per live call.
- Security is the same in (b) and (c). Both start the reactor's checked code over memory the runner captured. In (c) the code section is copied byte for byte from the checked reactor. Only the memory's size, the data and two exports change, so the engine compiles no code that `SHA256SUMS` does not pin.
- What (c) adds is one compile per image: about 0.9 s (INFERRED: the code section is the reactor's, which is perl.wasm's size; study B §8 measured 0.9 s with parallel compilation). It is paid at the making and then cached. A `.cwasm` from the cache loads as any other does (spec 014).

**How the runner writes the image module** (`_image_module.py`):
1. It reads the reactor's bytes once and holds them to the checked digest, as `_engines` holds perl.wasm's.
2. It walks the section headers. It does not decode the code.
3. It refuses, with `ArtifactsMissing`, a reactor that does not have the form below. This is a build fault, not a caller's:
   - one memory, defined and exported as `memory`;
   - every data segment active, in memory 0, at an `i32.const` offset;
   - exactly one mutable global, the stack pointer;
   - no start section;
   - the exports `_initialize`, `prepare`, `run`, `wasi_perl_open_files`, `wasi_perl_exit_status` and `memory`.

   A data-count section is allowed. `wasm-opt` writes one when bulk memory is on, though the code uses no segment: over perl.wasm, `-O3` adds one, and the code holds no `memory.init` and no `data.drop` (EXECUTED: spec 021, Decision 5). The reactor is linked from the same objects, so it is expected to be the same (INFERRED; reactor check 1 confirms it).
4. It writes a module from those sections, in order:
   - The memory section's initial size is the image's pages, and its maximum is kept. An image past the maximum raises `SnapshotMismatch`.
   - The data section holds the image, as active segments (step 5).
   - A data-count section is dropped. `memory.init` and `data.drop` do not validate without one: wasmparser answers "data count section required" (READ: `wasmparser-0.263.0/src/validator/operators.rs:1905-1911`, the version wasmtime 51 pins at its `Cargo.toml:375`). So a reactor whose code indexes a segment fails to compile, and that error is raised as `ArtifactsMissing`. It never runs against segments that are gone.
   - The export section drops `_initialize` and `prepare`, so neither can run over the image.
   - Custom sections are dropped. Nothing reads them, and the bytes then depend only on the reactor and the image.
   - Every other section is copied byte for byte: types, imports, functions, table, globals, tags, elements and code.
5. **The segments, and their cap.**
   - **Runs.** The image is cut into 4 KiB blocks. A run is a longest stretch of blocks that each hold a non-zero byte. A block of zeros is left out, since fresh memory is zero.
   - **The cap.** wasmparser refuses a module with more than 100,000 data segments (READ: `wasmparser-0.263.0/src/limits.rs:29`, `MAX_WASM_DATA_SEGMENTS`). `_image_module.MAX_SEGMENTS` is 50,000, half of that limit. The margin is a choice made here, not one any engine asks for.
   - **Coalescing.** While there are more runs than the cap, the shortest gap of zero blocks between two runs is closed: the two runs become one segment, and the gap's zeros are written into it. Among gaps of equal length, the one at the lower address closes first. So exactly `runs − 50,000` gaps close, the zeros written are the fewest such a merge can give, and the result depends only on the image.
   - **Why the cap is reachable:** `memory_bytes` defaults to 1 GiB. An image that size with every other block zero has 131,072 runs (INFERRED). Coalesced, it is 50,000 segments, and no image is refused for its shape.
6. Why the one global is safe: the image holds memory only. The stack pointer is back at its first value whenever `prepare` returns. `prepare` checks this itself (decision 3), so the global's first value in the module is right.

**The cache key.** The image module's bytes are a pure function of the reactor and the image. So the in-process table and the `.cwasm` name are keyed by the SHA-256 of `"wasi-perl image module 1\0" + reactor digest + "\0" + image_sha256`. A process that finds the `.cwasm` never writes the module at all. The cache's own checks of owner, mode, one name and folder (specs 004, 012 and 014) apply unchanged. With `cache_dir=False`, the compiled module lives in the process only.

**When it compiles.**
- `make_snapshot` compiles the unmetered flavour before it returns, and the metered one too when it was given `fuel`. So the install step pays the compile.
- `Snapshot.load` compiles nothing, so it stays a read and a check.
- A call in a cold cache compiles its flavour once.

**If wasmtime declines copy-on-write** for an image past its dense-image limit (INFERRED: a larger prelude than the default), the call is still correct. It then pays an eager copy, as (b) would. The acceptance memo records which one the default image gets.

### 2. A second binary, `perl-reactor.wasm`, and no wizer anywhere

- `perl.wasm` stays a WASI command (a module that exports `_start`). Other hosts run it, and the wasmtime CLI runs it with no flags. Every call without an image still runs it.
- A command cannot also serve the image. Each of its other exports would run the C constructors again (INFERRED: wasm-ld wraps a command's other exports with constructor calls; not tried here). The image needs an entry that runs once, returns with the interpreter alive, and is then entered again.
- So the build links the same objects a second time, with Perl's `perlmain.o` replaced by `recipes/wasi-reactor.c`'s object. The result is `perl-reactor.wasm`, a WASI reactor: a module with no `_start` that the host enters through its exports.
- **Where, in spec 019's build order (its Decision 5):**
  - step 1, `20_target.sh`: the reactor's object is compiled after `Configure`, as `sandbox-refusals.o` is (Decision 3);
  - step 4, `40_link.sh`, after the index and before the relink of `perl.wasm`: `build/target/perl` is removed, and `make perl` runs with three variables set. `perlmain_objs` is the reactor's object, `libs` is the index object followed by the configured `libs`, and `CLDFLAGS` is the configured one plus `-mexec-model=reactor` and Decision 3's `--wrap` flags. The result is copied to `build/link/perl-reactor.wasm`. Step 5's relink then leaves the tree's `perl` the command;
  - step 6: 021's pass, once 021 is built, writes it to `build/artifacts/`. Until then, a copy;
  - step 7: `SHA256SUMS` pins it, with every other file of the set.
- **`MANIFEST` is unchanged.** It stays `<sha256>  <path>` for every file under `lib/` and `dev/` (`scripts/30_library.sh:62-69`; `tests/build/test_modules.py:529-550`, READ). The reactor is pinned by `SHA256SUMS` alone, as `perl.wasm` is.
- **`Artifacts` gains `reactor: Path | None = None`, as its last field.**
  - It has a default, so every embedder's `Artifacts(perl, lib, dev, manifest, sums)` still works.
  - `Artifacts.at(folder)` fills it with `folder / "perl-reactor.wasm"` when that file exists, else `None`.
  - The runner checks the reactor against `SHA256SUMS` with the set, as it checks perl.wasm, whenever a call or a making uses it.
  - `make_snapshot`, `make_package_snapshot`, `Snapshot.check` and a call with `snapshot=<Snapshot>` raise `ArtifactsMissing` when it is `None`. A call with `snapshot=True` or `False` never reads it.
  - wasi-python's `Artifacts` has two required reactor fields, `snapshot` and `plain`, and no command. The difference is in the table below.
- Its size is about perl.wasm's 9.3 MB (INFERRED: the same objects; D §4.3). The wheel grows by its compressed size, which the acceptance memo records.
- It is not a snapshot. Nothing is initialised in it, and no seed is in it.

### 3. The reactor entry is a recipe file, not a patch to Perl

`recipes/wasi-reactor.c` is written for this project. It follows study D's prototype, which needed no change to Perl's source (D §2). `AGENTS.md` allows a patch only for an unavoidable source change, and this one is avoidable. It includes Perl's generated `perlmain.c` for its `xs_init`, with `main` renamed away, and is compiled with the command Perl's own objects get.

Its exports:
- **`_initialize`**: wasi-libc's, which runs the C constructors.
- **`prepare() -> i32`**: the making. It runs once per instance, and a second call aborts.
  1. It reads argv and the environment from WASI. The runner gives the argv `perl -e <code>`.
  2. It pins the clocks: `__wasi_clock_time_get` is wrapped to answer a fixed realtime of 1767225600 s and a monotonic 0, as wasi-python's `prepare` does. Then no time the image holds lies in a later call's future. **Entropy is not pinned** (decision 7).
  3. It runs `PERL_SYS_INIT3`, `perl_alloc` and `perl_construct`. The hash seed is drawn here, from `/dev/urandom` (spec 013), which is `random_get`.
  4. It runs `perl_parse` with `xs_init` and the argv, then `perl_run`, with `PERL_EXIT_DESTRUCT_END` set. So the code's END blocks stay queued and run at the end of every call, as if the two were one script.
  5. It stores Perl's status. Anything other than 0 is returned at once.
  6. It unpins the clocks and flushes PerlIO.
  7. It counts the descriptors the code left open in the guest's table: not 0–2 and not a preopen. `wasi_perl_open_files` answers that count.
  8. It empties the guest's descriptor table (`wasi_perl_fd_table_reset`, added to `recipes/wasi-fd-table.c`) and wasi-libc's preopen table. The reset also forgets the two preopens the table finds by name: the `/dev` preopen of spec 013 and the library's preopen of spec 019. Each is found again, from this instance's preopens, at its first use. It drops wasi-libc's copy of the environment.
  9. It checks that the stack-pointer global, read with `global.get __stack_pointer`, equals its value at entry. If not, it aborts.
  10. It returns 0.
- **`run()`**: one call from the image (decision 4), then `perl_destruct`, `perl_free` and `exit`, as `perlmain.c` ends.
- **`wasi_perl_open_files() -> i32`** and the existing **`wasi_perl_exit_status`**.

The second `perl_parse` would undo the preload in three ways (D §2.1). Link wraps keep it, as in the prototype:
- `Perl_newSVpvn_share` puts the image's `main::` back in place of `init_main_stash`'s new one;
- `Perl_boot_core_PerlIO` puts `PL_isarev`, the inheritance map, back;
- `Perl_boot_core_UNIVERSAL`, `_builtin`, `_mro` and `Perl_newXS` skip what the image already defines. Without them, `-w` printed 140 "Subroutine redefined" lines.

**The coupling to `perl.c` 5.42.3's order of calls is guarded, not trusted.**
- The file fails to compile (`#error`) unless the Perl is 5.42.3. A Perl upgrade must re-read `S_init_main_stash` and `S_parse_body`, rerun the drift suite, and decide again between wrappers and a reviewed patch.
- The `newSVpvn_share` wrapper acts only when it is armed and finds `PL_defstash` a new, empty hash. Otherwise it aborts.
- The `boot_core_PerlIO` wrapper acts only when it finds `PL_isarev` new and empty. Otherwise it aborts.
- A call that aborts traps, and the drift suite fails, so a silent misorder cannot ship.

### 4. What is reset on every call, and how

| state | in the image | what `run` does | checked by |
|---|---|---|---|
| argv, `$0`, `@ARGV`, switches | the making's `-e <code>` | reads argv from this instance's WASI; the second `perl_parse` sets the rest | drift `argv_0`, every switch case |
| environment, `%ENV` | dropped at the end of `prepare` | `__wasilibc_initialize_environ`, then the parse builds `%ENV` | drift `env` |
| working folder | not kept | `chdir($PWD)` when `PWD` is absolute, as `wasi-stubs.c`'s constructor does for perl.wasm | drift `pwd` |
| time, `$^T` | the clocks were pinned, then unpinned | nothing: the parse reads the real clock | drift `basetime`; reset check 6 |
| the descriptor table and wasi-libc's preopens | emptied | filled again on first use, from this instance's preopens | drift `n_file`, `p_i`; reset check 4 |
| PerlIO's `STDIN`, `STDOUT`, `STDERR` | flushed; no other handle is open (decision 5) | clears each one's end-of-file and error flags | reset check 5 |
| `srand`, `PERL_RAND_SEED` | whatever the code did | `PL_srand_called` cleared; `PERL_RAND_SEED` read again | drift `rand`, `rand_seed_env`; reset check 7 |
| internal random state, hash-order bits | seeded at the making | seeded again in `perl_construct`'s order, from `Perl_seed` (so `random_get`) or `PERL_INTERNAL_RAND_SEED` | drift `hash_order`; seed check 3 |
| hash seed and hash state | the making's | **kept**: every stash and shared key in the image was hashed with it (D §2.1) | seed checks 1 and 2 |
| `@INC`, `PL_e_script` | the making's | cleared; the parse fills `@INC` as perl.wasm does | drift `inc_dirs` |
| `main::`, `PL_isarev`, `%INC`, compiled subs | the code's | kept, by design: that is the preload | drift's visibility cases |
| locale | set up at the making, under no `LC_*` | not redone; under wasi-libc's C and C.UTF-8 it agrees with perl.wasm (D §2.1) | drift `locale`, `lang_utf8` |
| the exit status that `wasi_perl_exit_status` keeps | −1, since `prepare` never exits | nothing | runner tests of exit codes |

### 5. The making: `make_snapshot` and `make_package_snapshot`

**`make_snapshot(code, *, grants=(), mounts=(), env=None, cwd=None, fuel=None, deadline_s=600.0, memory_bytes=UNSET, output_bytes=UNSET, cache_dir=None, artifacts=None, max_file_calls=UNSET) -> Snapshot`**

- Its keywords are `run`'s, checked by `run`'s rules before any work. It takes no `argv` and no `stdin`, because the code is the program.
- **The order is wasi-python's.** The keywords both runtimes have stand in wasi-python's order (`tests/public_api.txt:217-232`, READ): `grants`, `mounts`, `env`, `fuel`, `deadline_s`, `memory_bytes`, `output_bytes`, `cache_dir`, `artifacts`, `max_file_calls`. `cwd`, which only wasi-perl has, stands after `env`, where it stands in wasi-perl's `run`. wasi-python's `root`, `import_path`, `host` and `max_open_files` are absent, since wasi-perl has none of them. All are keyword-only, so the order changes no call.
- **`mounts` and `max_file_calls` are spec 017's**, which lands first. A mount serves the making as it serves any call. What the code read through a provider is then in the image, as a grant's files are. A keyword wasi-perl's `run` gains later joins `make_snapshot` in wasi-python's place.
- The deadline is 600 s, as wasi-python's, because the making is a setup step.
- The steps:
  1. a fresh store on `perl-reactor.wasm`;
  2. `_initialize`, then `prepare`, with the argv `perl -e <code>`;
  3. the image is the whole linear memory, copied once into `bytes` through wasmtime-py's pointer;
  4. the store is closed;
  5. the image module is written and compiled (decision 1).
- The code runs as a `-e` program in package `main`. Its subroutines, package variables, loaded modules and END blocks are in every call. Its `my` variables are not, since a call is a separate compilation unit.
- `code` is a `str` of at most 16 MiB as UTF-8 with no NUL. It travels as an argv string, and an argv cannot hold NUL.
- **`env` may not set `PERL_HASH_SEED`, `PERL_PERTURB_KEYS` or `PERL_JSON_PP_USE_B`**: `ValueError`, before any work. The first two would make the image's hash seed known to whoever knows the environment. The third would be frozen into JSON::PP for every call (D §5). The origin keeps the other names, never their values. The values stay in the image, as in wasi-python, so pass no secret.
- `SnapshotFailed` has wasi-python's three reasons:
  - `"exit"`: `prepare` returned a status other than 0. That covers a compile error, an uncaught `die` (255) and `exit N` with N > 0. The message's last line is the code's last line of stderr. `exit 0` is a normal end, as it is for `perl_run`;
  - `"trapped"`: fuel, the deadline, or a trap;
  - `"open file"`: `wasi_perl_open_files` is above 0. A module's `__DATA__` handle counts. A call cannot reopen it.

**`make_package_snapshot(packages=None, *, ...)`** has `make_snapshot`'s keywords, in the same order. It makes sense for Perl: it loads modules without importing them, which is study D's preload exactly.
- The code is one `require <Module>;` line per name, so no name lands in `main::`.
- **The default list is held in the runner's code, not in the set.** `_snapshot_make.PACKAGES` is a tuple of `(distribution, (Module, …))` pairs. With `packages=None`, it loads every pair's modules, in order, each once.
- **The pairs name study D's eight**, the measured set (D §3–§5). Each is named by its CPAN distribution where it has one, else by its module name:

  | distribution | modules |
  |---|---|
  | `strict` | `strict` |
  | `warnings` | `warnings` |
  | `POSIX` | `POSIX` |
  | `Scalar-List-Utils` | `List::Util` |
  | `JSON-PP` | `JSON::PP` |
  | `Data-Dumper` | `Data::Dumper` |
  | `Storable` | `Storable` |
  | `Encode` | `Encode` |

  - Each module added makes every image larger, and every call from it pays more first-touch page faults (D §4.2).
  - An embedder names more with `packages=[...]`.
  - The list changes only with a drift run and a review, and the review reads it in the code.
- **A name given** is resolved as wasi-python resolves one (`_snapshot_make._package_names`, READ):
  - a pair's distribution, matched without regard to case (`json-pp`), loads that pair's modules;
  - a pair's first module loads that pair's modules;
  - any other module name (`Data::Dumper`, `Text::Wrap`) is loaded as given;
  - a name that is neither a module name nor a pair's distribution raises `ValueError` ("this artifact set ships no package named …").
  - A `str` alone is refused with `TypeError`, as in wasi-python.
- **Why not in `MANIFEST`, as wasi-python keeps it.**
  - wasi-perl's `MANIFEST` has one format, `<sha256>  <path>` for each file under `lib/` and `dev/`. `30_library.sh` writes it (`scripts/30_library.sh:62-69`), and `tests/build/test_modules.py:529-550` asserts that every line is a digest and a path, and that the entries are exactly the files on disk (READ). A line of another kind would break that check, and changing the check would weaken it.
  - A separate file in the set would add a sixth path to `Artifacts` and to `assemble_artifacts.py`, for a list no build step computes.
  - wasi-python's list follows the packages each of its builds ships. wasi-perl's eight are core modules every set holds, chosen by measurement. So the list belongs with the code that was measured.
- **A set that lacks one of the eight**, which only a set passed as `artifacts=` can, fails the making with `SnapshotFailed("exit")`, whose message names the `require` that died.

### 6. Which calls use the image

- **The parameter.** `run` gains `snapshot: bool | Snapshot = True`, keyword-only, after `stdin` and before `cwd`. wasi-python's `run` has it after `stdin` and `host`, and before `fuel` (`tests/public_api.txt:242-245`, READ). wasi-perl has no `host`, and `cwd` is its own. So every parameter both runtimes have keeps wasi-python's order. After specs 017 and this, wasi-perl's `run` reads: `argv`, then `grants`, `mounts`, `env`, `stdin`, `snapshot`, `cwd`, `fuel`, `deadline_s`, `memory_bytes`, `output_bytes`, `cache_dir`, `artifacts`, `max_file_calls`.
- **`snapshot=True`**, the default, and **`snapshot=False`** both run `perl.wasm`. wasi-perl's own start is perl.wasm. A construct-only snapshot saves 0.12 M instructions and no measurable time: 1.039 against 1.045 ms (D §4.2). So the set ships no snapshot binary.
- **`snapshot=<Snapshot>`** runs the image module, with one exception: **if the call's environment has `PERL_HASH_SEED`, `PERL_PERTURB_KEYS` or `PERL_JSON_PP_USE_B`, with any value, the call runs `perl.wasm`.** An image cannot honour them: the seed is fixed, and JSON::PP's constant was set at the making. The call is then exactly perl.wasm's.
- `Completed.image_sha256: str | None = None` is the image's hash for a call that started from it, and `None` otherwise. That is wasi-python's field. It is how an embedder sees a fallback.
  - **Its place** is after spec 017's `files`, as the last field. wasi-python's order is `truncated`, `files`, `tools`, `image_sha256` (`tests/public_api.txt:14-17`, READ), and wasi-perl has no `tools`. So wasi-perl's `Completed` ends `truncated`, `files`, `image_sha256`.
  - 017 lands first, so `files` is already in place and nothing moves. Check A1 of 017, as this extends it, holds the order.
- A call from an image takes `mounts` as any call does. With mounts, it instantiates the image module from spec 017's mount linker, as a call with mounts and no image instantiates perl.wasm from it.
- **The runner never guesses from the program's text.** `use`, `require`, `do FILE` and `-M` in `PERL5OPT` cannot be judged reliably without running Perl. A guess would make a program's meaning depend on a heuristic: `POSIX::floor` without `use` would work or die according to the guess. So the embedder chooses per call.
  - `docs/snapshots.md` says to pass the image to calls that load modules, and not to bare one-liners.
  - On a one-liner the image costs about 0.38 ms more: 294 page faults against 78 (D §4.2).
- `SnapshotMismatch` is raised before any work in three cases:
  - the set's reactor or library is not the one the image was made with;
  - `memory_bytes` is below the image;
  - an image module would exceed the memory's maximum.
- The checks of a fallen-back call are perl.wasm's. The image is still checked against the set, so a wrong image fails the same way whatever the environment.

### 7. The hash seed

- The seed is drawn at the making, from `random_get`, through spec 013's `/dev/urandom`. It is frozen in the image, and every call from that image shares it.
- **The making's entropy is not pinned**, unlike wasi-python's `prepare`. If it were, every embedder's image of the same set would have the same seed, and anyone with the set could compute it. That would be a published seed, which the owner refused.
- Because the seed is drawn fresh, two makings of the same code give different images. `image_sha256` names an image; it does not predict one. wasi-python already says this of a checkpoint.
- A program running from the image can read the seed with `Hash::Util::hash_seed()`. So:
  - `docs/sandbox.md` says that a program that hashes untrusted keys should run with `snapshot=False` when other code from the same image may be hostile;
  - an embedder changes the seed by making the image again, at each install or each service start.
- **A saved file holds the seed.**
  - `save` writes it with mode 0600, as `mkstemp` makes it.
  - On POSIX, `load` also refuses a file that group or others can read, beside wasi-python's refusal of one they can write.
  - `docs/snapshots.md` says to keep the file as private as a key, and never to share it between embedders or machines.
- **The module cache holds the seed too.** The image module's `.cwasm` carries the image's data segments, and so the frozen seed.
  - The cache already keeps every file at 0600, in a folder at 0700, owned by this user, and refuses anything else (`_cache.py:139-148`, READ).
  - `docs/snapshots.md` says the cache folder holds the seed as the saved file does. A cache shared between embedders shares their images' seeds. With `cache_dir=False` nothing is written.

### 8. The drift, and where it is documented

Study D's preload differed from perl.wasm in 6 of 41 checks (D §5, EXECUTED, `results/drift.json`). After this specification:

| drift case | what differs | disposition |
|---|---|---|
| `no_use_posix` | `POSIX::floor(2.5)` without `use POSIX` prints 2; perl.wasm dies | accepted; `docs/snapshots.md`, "What is frozen" |
| `defined_sub` | `defined &POSIX::floor` is 1 | accepted; same place |
| `inc_keys` | `%INC` lists the image's 25 files | accepted; same place |
| `hash_seed` | `hash_seed()` is the same on every call; hash *order* still varies | accepted; `docs/snapshots.md` and `docs/sandbox.md` (decision 7) |
| `hash_seed_env` | `PERL_HASH_SEED=0` was ignored | removed: the call runs perl.wasm (decision 6) |
| `hash_order_env` | `PERL_HASH_SEED=0` and `PERL_PERTURB_KEYS=0` were ignored | removed: the call runs perl.wasm |

D also read one case without running it: JSON::PP fixes `PERL_JSON_PP_USE_B` at compile time (READ: `JSON/PP.pm` line 48). The fallback removes that too.

`docs/snapshots.md` follows wasi-python's how-to. It has sections on making, saving and loading, what is frozen, what is fresh, what to avoid and the costs. "What is frozen" lists the four accepted drifts in these words: a module the image loaded is usable without `use`, and is in `%INC`. "What to avoid in the code" adds: an open file or `__DATA__` handle; waiting for time to pass; `my` variables a call expects to see; secrets in `env` or the code.

## Where wasi-perl differs from wasi-python, and how wasi-python could adopt it

| # | wasi-perl | why | how wasi-python could adopt it |
|---|---|---|---|
| 1 | `snapshot=True` means the set's default start, which is perl.wasm. `True` and `False` run the same binary | wasi-perl ships no snapshot binary (decision 6) | say "the set's default start" instead of naming `python-snapshot.wasm`; its behaviour is unchanged |
| 2 | a call from a `Snapshot` instantiates a module the runner wrote, mapped copy-on-write; there is no per-call copy | about 2.7 ms and 15 MiB a call saved (decision 1) | adopt it inside the runner, with no change to the API or the file. It would pay one compile of its snapshot binary's code per image, cached. Its 203 MiB images exceed wasmtime's dense-image limit, so whether they still map is to be measured (INFERRED) |
| 3 | the making pins the clocks but not entropy | the hash seed is frozen in the image (decision 7) | draw real entropy in `prepare` as well. That would close its own pydantic note (READ: `docs/explanation/security-model.md`). It gives up identical images from identical code, which its docs do not promise for a prelude (INFERRED) |
| 4 | `make_snapshot`'s `env` refuses three names, and a call whose env has them runs the plain binary | an image cannot honour them (decision 6) | the same rule with its own list: for example `PYTHONHASHSEED`, if it ever honours one in an image |
| 5 | `make_package_snapshot` takes CPAN distribution names or `Module::Names`, loads with `require`, and has no `root` keyword. Its default list is `_snapshot_make.PACKAGES`, in the runner's code; wasi-python reads its set's `x-snapshot-package` lines in `MANIFEST` | Perl's names and loading; wasi-perl's `run` has no root. wasi-perl's `MANIFEST` holds only `<sha256>  <path>` lines, which its build check enforces, and its eight packages are core modules every set holds (Decision 5) | the shared component asks each runtime for its package list through one function: wasi-python's reads `MANIFEST`, wasi-perl's returns `PACKAGES`. The rule for `None` and for a name given is the same in both, and neither API changes |
| 6 | `make_snapshot` and `make_package_snapshot` take the keywords of wasi-perl's `run`: `grants`, `mounts`, `env`, `cwd`, `fuel`, `deadline_s`, `memory_bytes`, `output_bytes`, `cache_dir`, `artifacts`, `max_file_calls`, in wasi-python's order (Decision 5). wasi-python's `root`, `import_path`, `host` and `max_open_files` are absent | wasi-perl has none of the four; each joins, in wasi-python's place, when wasi-perl's `run` gains it | nothing to adopt for the four |
| 7 | `code` may not hold NUL | it travels as argv, so no new export is needed to copy it in | nothing to adopt |
| 8 | `SnapshotOrigin` has three neutral names, each in the place of wasi-python's: `binary_sha256` (perl-reactor.wasm) for `snapshot_sha256`; `library_sha256` (the SHA-256 of the `SHA256SUMS` lines that name `lib/`) for `stdlib_zip_sha256`; and `runtime_version` (wasi-perl's version) for `wasi_python`. `kind` is always `"prelude"`, `parent_sha256` is always `None`, and `home` is `""` | the binary is not a snapshot; the library is a folder, not a zip; a field named after one runtime cannot be the shared one. wasi-perl has no sessions and sets no `HOME` (spec 004) | rename the three to the neutral names, keeping `snapshot_sha256`, `stdlib_zip_sha256` and `wasi_python` as read-only aliases, and reading either spelling in a file's header. The shared component then has one shape |
| 9 | the file's first line is `wasi-perl snapshot\n` | neither runner may load the other's image | the same scheme: one magic line per runtime |
| 10 | `load` refuses a file others can read (POSIX) | the file holds a secret, the seed (decision 7) | adopt it: its checkpoints hold everything a session read |
| 11 | no `Session`, `checkpoint` or `run_async` | wasi-perl has none | they follow wasi-python's when wasi-perl gains sessions |
| 12 | the command line accepts `--no-snapshot`, which changes nothing | so a wasi-python command line runs | nothing to adopt |
| 13 | `make_snapshot` takes `cwd`, after `env` | wasi-perl's `run` has `cwd`, and the making takes `run`'s keywords | the same keyword, in the same place, if wasi-python's `run` gains `cwd` |
| 14 | `Artifacts` has `perl` (the command), `lib`, `dev`, `manifest`, `sums` and `reactor: Path \| None = None`, last. wasi-python's has `snapshot` and `plain`, both reactors and both required, with `stdlib_zip` and `manifest` | wasi-perl's start is a command, and its reactor serves only images; a set without one still runs every call but those from an image (Decision 2) | an optional binary as a last field with a default `None`, as here, if it ever ships one. The two classes already differ in every path but `manifest`, since each holds its own runtime's files |

Everything else is wasi-python's, adapted from its files and saying so in their docstrings:
- `Snapshot` is immutable, cannot be made directly, is not pickled, and has the same `repr`, `origin`, `save`, `load` and `check`;
- the file format is version 1: the magic line, one line of sorted JSON, then zlib slices, with the same size, hash and stream checks;
- `save` and `load` publish the folder so that no live or later writable grant can reach it;
- `SnapshotMismatch` is a `ValueError` and `SnapshotFailed` a `RuntimeError`, with `reason` and `completed`;
- `prelude_fuel` is recorded when `fuel` is given;
- the counts gain `snapshots_made` and `snapshots_restored`.

## Checks (written first, seen failing; they count operations, never time)

**The image module** (`tests/snapshot/test_image_module.py`)
1. It is built from a stand-in reactor made by the test and a known image. It holds the stand-in's code section byte for byte. Its memory's initial pages equal the image's. Its exports lack `_initialize` and `prepare`. Two builds give identical bytes.
2. Instantiated, its memory equals the image byte for byte, zero blocks included.
3. Stand-ins are refused, each with `ArtifactsMissing`: one with a passive segment, one with two mutable globals, and one with a start section. A stand-in whose code holds `memory.init`, with its data-count section, gives a module that fails to compile, raised as `ArtifactsMissing`.
4. A stand-in with a data-count section and no segment-indexing instruction is accepted. Its written module has no data-count section, and it compiles.
5. **The segment cap.**
   - Fast, on the coalescer alone, with the cap passed in: runs with gaps of 3, 1, 2 and 1 blocks under a cap of 3 close the two gaps of 1 block, the lower one first. Under a cap of 1, every gap closes. A run count at or under the cap closes none.
   - `slow`: a striped image of 100,002 blocks, every other one non-zero, so 50,001 runs. The written module has exactly 50,000 data segments; it compiles; and, instantiated, its memory equals the image byte for byte.
   - `_image_module.MAX_SEGMENTS` is at most half of wasmparser's 100,000.
6. Twenty calls from one image compile it once: the counts show one compile or one load. `snapshots_restored` counts 20. No host write into guest memory happens: `ctypes.memmove` and `Memory.write` are patched to count, and count 0.
7. A second process with the same cache loads the `.cwasm` and never writes the module: the writer is patched to count.

**The reactor** (`tests/build/`)
1. `perl-reactor.wasm` has the form of decision 1, step 3, and exactly the six exports. An image module written from it and a one-page image compiles, so its code indexes no data segment.
2. `SHA256SUMS` lists `perl-reactor.wasm`; `MANIFEST` does not, and `tests/build/test_modules.py`'s check of `MANIFEST` passes unchanged.
3. Compiling `recipes/wasi-reactor.c` against a stand-in `patchlevel.h` that says 5.42.4 fails on its `#error`.
4. The artifact set holds no file that begins with the snapshot magic. No script runs `wizer`.

**Drift** (`tests/snapshot/test_drift.py`, the 41 cases of D's `drift.py`, ported)
1. The image of `make_snapshot("")` differs from perl.wasm in exactly `{hash_seed}`.
2. The image of `make_package_snapshot()` differs in exactly `{no_use_posix, defined_sub, inc_keys, hash_seed}`.
3. The cases `hash_seed_env` and `hash_order_env` match perl.wasm's output, and their `Completed.image_sha256` is `None`. So do three new cases: `PERL_PERTURB_KEYS=0` alone, `PERL_JSON_PP_USE_B=1` with `-MJSON::PP`, and `PERL_HASH_SEED` set to empty.
4. The sets are exact. A new difference fails, and so does an expected one that disappears, so the docs and the test change together.

**No leak between calls** (`tests/snapshot/test_isolation.py`)
1. Study D's `leak_set` then `leak_check` sees neither the planted variable nor the new `POSIX::floor`.
2. Eight threads make ten calls each from one `Snapshot`. Each writes its own marker into a package variable, then prints it. Every output is its own marker.
3. After all of them, `Snapshot`'s image still hashes to its `image_sha256`.
4. A making with a grant at `/data`, then a call with no grant: `-e "/data/f.txt"` is false. A call with another folder granted at `/data` reads its own file.

**Per-call resets** (`tests/snapshot/test_resets.py`)
1. `$0`, `@ARGV` and the switches come from the call.
2. `%ENV` is exactly the call's.
3. `PWD` and `cwd` are the call's.
4. The descriptor table is the call's, as in isolation check 4.
5. The prelude `my @x = <STDIN>;` reads its empty stdin to the end. A call given `"a\n"` then prints `a` for `print scalar <STDIN>`.
6. A call's `$^T` is not 1767225600, and is at most `time`.
7. The prelude `srand(42)`: twenty calls give twenty different `rand()` values. A call with `PERL_RAND_SEED=7` gives perl.wasm's sequence for that seed.
8. The prelude `END { print "pre\n" }` with the call `END { print "call\n" }` prints `call`, then `pre`, as one script would.

**The seed** (`tests/snapshot/test_seed.py`)
1. Five makings give five different `hash_seed()` values. Twenty calls from one image give one value.
2. With a probe linker whose `random_get` returns fixed bytes, as in spec 013's check 1, two makings give the same seed. Other bytes give another seed. So the seed comes from the making's `random_get`, not from a constant or a zeroed source.
3. With that probe, two calls from one image print the same key order and `rand()`. Under the real runner, twenty calls show more than one key order. So the per-call bits come from the call's own `random_get`.
4. `make_snapshot` refuses each of the three names in `env` with `ValueError`, before any instance is made.
5. On POSIX, a saved file has mode 0600, and `load` refuses it at 0640 and at 0606 with `PermissionError`.

**The API** (`tests/snapshot/test_api.py`)
1. `Snapshot()` raises `TypeError`, pickling raises `TypeError`, and setting an attribute raises `AttributeError`.
2. `save`, then `load`, gives an equal origin and image.
3. A truncated file, a flipped byte, a newer format and another runtime's magic line each raise `SnapshotMismatch`.
4. A set with another reactor or library: `check()` raises `SnapshotMismatch`, and so does `run`.
5. `memory_bytes` below the image raises `SnapshotMismatch`.
6. `SnapshotFailed` has the reasons `"exit"` (`die`, `exit 3`, a compile error), `"trapped"` (fuel) and `"open file"`, each with its `completed`.
7. `exit 0` makes an image.
8. `make_package_snapshot("POSIX")` raises `TypeError`. `["JSON-PP"]`, `["json-pp"]` and `["JSON::PP"]` give images whose `%INC` holds `JSON/PP.pm`. `["No-Such-Dist"]` raises `ValueError` before any work. `make_package_snapshot()`'s origin `prelude` is the eight `require` lines of `PACKAGES`, in order.
9. **The listing, against wasi-python's** (spec 017's check A1, `tests/mounts/test_mount_api.py`, extended):
   - the blocks for `Snapshot`, `SnapshotFailed` and `SnapshotMismatch` equal wasi-python's, line for line;
   - `SnapshotOrigin`'s block is wasi-python's, with `binary_sha256`, `library_sha256` and `runtime_version` in the places of `snapshot_sha256`, `stdlib_zip_sha256` and `wasi_python`, and no other line changed;
   - `make_snapshot`'s and `make_package_snapshot`'s blocks are wasi-python's with the lines for `root`, `import_path`, `host` and `max_open_files` absent, one `cwd` line added after `env`, and no other line changed;
   - `run`'s `snapshot` line equals wasi-python's, and stands after `stdin` and before `cwd`;
   - `Completed`'s `image_sha256` line equals wasi-python's, and is the last field, after `files`;
   - `Artifacts`'s last line is `field reactor: Path | None = None`.

   Each allowed difference is a row of the table above, named in the test beside it.
10. `Artifacts(perl, lib, dev, manifest, sums)`, made by hand with five arguments, still runs `snapshot=False` calls. Given to `make_snapshot`, or to a call with a `Snapshot`, it raises `ArtifactsMissing`. `Artifacts.at` of a folder without `perl-reactor.wasm` gives `reactor=None`.
11. `make_snapshot` with a mount at `/work` whose provider serves `Mod.pm`, and the code `use lib "/work"; require Mod;`: a call from the image, with no mounts, sees `Mod` loaded and makes no provider call. The same making with `max_file_calls=1` raises `SnapshotFailed` with the reason `"trapped"`.

## Acceptance

1. The checks pass, and every existing suite stays green (EXECUTED).
2. The one-liner oracle runs on the package image with the same results as on perl.wasm, apart from the four accepted drifts, none of which it exercises (EXECUTED).
3. Perl's own `t/` suite runs on the image of `make_snapshot("")` and on the package image.
   - A test that fails only there goes in `tests/perl_t/exclusions-image.toml`, with its reason, in its own commit.
   - `tests/perl_t/exclusions.toml` is not touched.
4. A research memo beside study D records, with commands, under the machine lock:
   - "five", `-MPOSIX` and `-e 1` through `run()`, on perl.wasm and on the image;
   - the making's time, the module writer's share of it, and the compile;
   - the image module's `.cwasm` size;
   - the wheel's growth from `perl-reactor.wasm`;
   - whether wasmtime maps the default image copy-on-write (its page faults per instantiation).
5. `docs/snapshots.md` exists, and `docs/sandbox.md` has the seed's paragraph. `CHANGELOG.md` has its lines under "Unreleased", naming `perl-reactor.wasm` as a new artifact.
6. A fresh agent's review lands.
