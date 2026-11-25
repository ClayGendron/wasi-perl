# 022 — A preload image the embedder makes at install time

- **Status:** agreed by the owner, 2026-11-24, and being built. The owner answered through AskUserQuestion: "Build it last (Recommended)", with a prototype first and a stop with a report if the prototype failed. The prototype says go (`../research/2026-11-24-preload-image-prototype.md`, §0). That answer agrees to the four new choices named below. Written by the orchestrator, 2026-11-24, for synthesis item 10. The owner gave two answers that bear on it, the same day (`../roadmap.md`):
  - "You can do all 10 actually, but do not change the API around perl to be different than wasi python, unless wasi python could adopt the same changes. We are going to eventually unify the shared components." That agrees to building the item, not this text.
  - "Install-time images only." The roadmap records what it means: the image may be built only as one the embedder makes itself, so that its fixed hash seed is never published. So wasi-perl ships no preloaded image.

  These choices are new here and await the owner: choice (c), which no study prototyped; the second binary `perl-reactor.wasm`; the default package list held in the runner's code (Decision 5); and the cap on data segments. They were asked with AskUserQuestion before the build started, and agreed as above. Amended the same day for the review `../reviews/specs-017-022-pr-2026-11-24.md` (H1, H2, M1–M4, M6, L5–L8; round 2: N1, N2).
- **Owner:** Clay Gendron
- **Kind:**
  - the build: a new `recipes/wasi-reactor.c`, its object compiled in `scripts/20_target.sh`, its link in step 4 of spec 019's `scripts/40_link.sh`, `scripts/assemble_artifacts.py`, and the `SHA256SUMS` that step 7 writes. `MANIFEST` is not changed;
  - the runner: new `src/wasi_perl/_snapshot.py`, `_snapshot_make.py` and `_image_module.py`; changes to `runner.py`, `_artifacts.py`, `_engines.py`, `cli.py` and `__init__.py`;
  - checks in `tests/snapshot/`, `tests/build/` and `tests/mounts/test_mount_api.py`; `docs/snapshots.md` and `docs/sandbox.md`.
- **Depends on:**
  - 013, because the image's hash seed must come from the host's randomness;
  - 014, for the engine's `memory_init_cow = True` pin, one `InstancePre` per module, and its Decision 5: on Linux the checked `.cwasm` runs from a sealed `memfd` copy that wasmtime maps; elsewhere its bytes are read;
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
| restore per call | instantiation 0.044 ms; the 7.3 MB of data is mapped copy-on-write (D §4.1, EXECUTED) | about 2.7 ms to copy 15 MiB (INFERRED: D §4.1, the same image with copy-on-write off, 2.745 ms) | on Linux, about 0.04 ms (INFERRED: the module has (a)'s shape, data segments over the image, so wasmtime maps it the same way). On macOS and Windows, about 2.7 ms, as (b) (INFERRED: below) |
| "five" per call | 4.32 ms (D §4.2, EXECUTED) | about 7.0 ms (INFERRED: 4.3 + 2.7) | about 4.3 ms on Linux, about 7.0 ms on macOS and Windows (INFERRED, as above) |
| memory per live call | the pages it writes, about 294 page faults on `-e 1` (D §4.2) | a private 15 MiB, since the copy writes every page | as (a) on Linux; as (b) on macOS and Windows |
| install-time step | `wasmtime wizer`, which needs the wasmtime CLI. The wheel carries none, and it is not a Python dependency. Up to 21.5 s with the machine lock (D §4.3) | the prelude's run, then a capture | the prelude's run, a capture, writing the module, and one compile, cached as a `.cwasm` |
| who runs the making | the wasmtime CLI, with its own WASI set-up: none of the runner's grant rules or limits apply | the runner, under `run`'s rules | the runner, under `run`'s rules |
| new code to build | a wizer step for each embedder | the reactor and the runner's capture | the same as (b), plus the module writer, about 200 lines |
| what wasi-python does | at its build, for `python-snapshot.wasm` (READ: `scripts/steps/80_snapshot.sh`) | for every image an embedder makes (READ: `_snapshot._restore`) | nothing yet |

**(c) is chosen.**
- It restores as cheaply as (a). The runner does the making itself, under the same rules as (b), with no tool beyond wasmtime-py.
- (a) is refused because the embedder's install step would need a binary the wheel does not carry. `make_snapshot(code)` could then not be a Python call checked by `run`'s rules.
- (b) is refused because it pays about 2.7 ms of copying on every call. That is 60% of the gain on "five". It also holds a private 15 MiB per live call.
- Security is the same in (b) and (c). Both start the reactor's checked code over memory the runner captured. In (c) the code section is copied byte for byte from the checked reactor. Only the memory's size, the data and two exports change, so the engine compiles no code that `SHA256SUMS` does not pin.
- > **Amended 2026-11-24 (A11, `../research/2026-11-24-preload-image-prototype.md` §5.2 item 2).** The image is as trusted as its prelude. A prelude cannot escape the sandbox, but it decides every later call's Perl: it can redefine a core function through `CORE::GLOBAL::`, set `$SIG{__DIE__}` or `$SIG{__WARN__}`, queue END blocks, and leave any package variable set. Every call from the image runs whatever it left. So `make_snapshot` is to be given only code the embedder would run in every call, never a program from the party the sandbox guards against. `docs/snapshots.md` states this trust point, and so does `make_snapshot`'s docstring; `docs/sandbox.md` points to it.
- What (c) adds is one compile per image: about 0.9 s (INFERRED: the code section is the reactor's, which is perl.wasm's size; study B §8 measured 0.9 s with parallel compilation). It is paid at the making and then cached. A `.cwasm` from the cache loads as any other does (spec 014).

**How the runner writes the image module** (`_image_module.py`):
1. It reads the reactor's bytes once and holds them to the checked digest, as `_engines` holds perl.wasm's.
2. It decodes every section but the code, which it copies as bytes. That is the import, memory, global and export sections, and every data segment's header.

   > **Amended 2026-11-24 (A13, `../research/2026-11-24-preload-image-prototype.md` §6).** The text said "it walks the section headers; it does not decode the code". That undersold the work: the form check decodes the four sections named and each segment's header.
3. It refuses, with `ArtifactsMissing`, a reactor that does not have the form below. This is a build fault, not a caller's:
   - one memory, defined and exported as `memory`;
   - every data segment active, in memory 0, at an `i32.const` offset;
   - exactly one mutable global, the stack pointer;
   - no start section;
   - the exports `_initialize`, `prepare`, `run`, `wasi_perl_open_files`, `wasi_perl_exit_status` and `memory`.

   A data-count section is allowed. `wasm-opt` writes one when bulk memory is on, though the code uses no segment: over perl.wasm, `-O3` adds one, and the code holds no `memory.init` and no `data.drop` (EXECUTED: spec 021, Decision 5). The reactor is linked from the same objects, so it is expected to be the same (INFERRED; reactor check 1 confirms it).
4. It writes a module from those sections, in order:
   - The memory section's initial size is the image's pages, and its maximum is kept. An image past the maximum raises `SnapshotMismatch`.

     > **Amended 2026-11-24 (A12, `../research/2026-11-24-preload-image-prototype.md` §5.2 item 4).** Today's reactor has no maximum: wasm-ld writes none (EXECUTED in the memo: `memory_max: null`). So the image's only bound is the store's `memory_bytes`, which is `SnapshotMismatch`'s second case (Decision 6). The check of a maximum stays, for a reactor that gains one, and the image-module check covers it with a stand-in.
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
- > **Amended 2026-11-24 (A8, `../research/2026-11-24-preload-image-prototype.md` §4).** The prototype measured, on Linux, under the machine lock (EXECUTED there): the restore by instantiation is 0.006 ms; "five" runs in 4.2–5.2 ms against 21.4–24.7 ms on perl.wasm; `-MPOSIX -e 1` in 4.1 against 13.0 ms; `-e 1` costs 0.43–0.48 ms more from the image, with 292–300 page faults against 81; the image module's compile takes 0.85–0.88 s unmetered and 1.18–1.24 s metered. These replace the Linux INFERRED figures of the table above, and the acceptance memo measures them again on the final build. A making also pays the reactor's own compile once, about 1.0 s, on a cold cache; it is cached as perl.wasm's is. A cache that holds both flavours of one image holds about 35 MB: each `.cwasm` was 17,579,152 bytes.

**Copy-on-write is Linux's only.** wasmtime builds a copy-on-write memory image only from a source it can map (READ, in the v51.0.0 clone: `crates/wasmtime/src/runtime/vm/cow.rs:105-145`):
- a module loaded from a mapped file (`MemoryImageSource::from_file`);
- or, on Linux only, a `memfd` it fills itself from the module's data (`from_data`). Off Linux, `from_data` answers `None` (`sys/unix/vm.rs:120-123`). On Windows both answer `None` (`sys/windows/vm.rs:75-81`).

Spec 014's Decision 5 gives wasmtime a mapped file only on Linux, its sealed `memfd` copy; elsewhere the `.cwasm`'s bytes are read. So:

| platform | how a call from an image starts | per call |
|---|---|---|
| Linux | the image is mapped copy-on-write, from 014's sealed copy, or from wasmtime's own `memfd` when the module was compiled in the process | about 0.04 ms and about 294 page faults on `-e 1` (INFERRED from D §4.1–§4.2, which ran on Linux; the acceptance memo measures it) |
| macOS | no image source: wasmtime writes the data segments into fresh memory at each instantiation | about 2.7 ms for the 15 MiB default image, and a private copy of its written pages (INFERRED: D §4.1's copy-on-write-off run, 2.745 ms; not measured on macOS) |
| Windows | as macOS | as macOS (INFERRED, not measured) |

- (c) stays the choice. Per call it is never slower than (b), and on Linux, where a vfs service runs, it is as cheap as (a). On macOS and Windows it costs what (b) costs, plus one compile per image, paid at the making and cached.
- On those two, the image still pays where it saves more than the copy: "five" runs in about 7.0 ms against 25.2 ms (INFERRED, the table above). On a bare one-liner it costs about 2.7 ms more, so the rule of Decision 6, pass the image only to calls that load modules, matters more there.
- `docs/snapshots.md` says this, platform by platform.

**If wasmtime declines copy-on-write** on Linux for an image past its dense-image limit (INFERRED: a larger prelude than the default), the call is still correct. It then pays an eager copy, as (b) would. The acceptance memo records which one the default image gets.

> **Amended 2026-11-24 (A9, `../research/2026-11-24-preload-image-prototype.md` §4.2).** The trigger is sparseness, not size. wasmtime 51 builds a static image when the span from the first to the last initialised byte is under twice the segments' bytes, at any size, or under 16 MiB at any density (READ there: `crates/environ/src/compile/module_environ.rs:1340-1450`, `try_static_init`; `crates/wasmtime/src/config.rs:303`). The writer drops zero blocks, so a heap as dense as Perl's stays mapped: the default image's span is 8.2 MB and its segments hold 8.1 MB. Acceptance 4's evidence is the memo's method: `/proc/self/maps` gaining `memfd:wasm-memory-image` or `memfd:wasi-perl-module` while an instance lives, and 0 page faults at instantiation.

### 2. A second binary, `perl-reactor.wasm`, and no wizer anywhere

- `perl.wasm` stays a WASI command (a module that exports `_start`). Other hosts run it, and the wasmtime CLI runs it with no flags. Every call without an image still runs it.
- A command cannot also serve the image. Each of its other exports would run the C constructors again (INFERRED: wasm-ld wraps a command's other exports with constructor calls; not tried here). The image needs an entry that runs once, returns with the interpreter alive, and is then entered again.
- So the build links the same objects a second time, with Perl's `perlmain.o` replaced by `recipes/wasi-reactor.c`'s object. The result is `perl-reactor.wasm`, a WASI reactor: a module with no `_start` that the host enters through its exports.
- **Where, in spec 019's build order (its Decision 5):**
  - step 1, `20_target.sh`: the reactor's object is compiled after the final `make` (today `20_target.sh:75`), with the command Perl's own objects get (Decision 3). It cannot come earlier: it includes Perl's generated `perlmain.c`, which only `make` writes, by the rule `perlmain.c: $(MINIPERL_EXE) ext/ExtUtils-Miniperl/pm_to_blib` (READ: the built tree's `Makefile:347`);
  - step 4, `40_link.sh`, after the index and before the relink of `perl.wasm`: `build/target/perl` is removed, and `make perl` runs with three variables set. `perlmain_objs` is the reactor's object, `libs` is the index object followed by the configured `libs`, and `CLDFLAGS` is the configured one plus `-mexec-model=reactor`, the six Perl wraps of Decision 3, and `--wrap=__wasi_clock_time_get` for its clock pin. The result is copied to `build/link/perl-reactor.wasm`. Step 5's relink then leaves the tree's `perl` the command;
    > **Amended 2026-11-24 (A1, `../research/2026-11-24-preload-image-prototype.md` §2.1 and §6).** The text named only "Decision 3's `--wrap` flags", which are Perl's six. Decision 3's step 2 pins the clocks through a seventh, `--wrap=__wasi_clock_time_get`, so `CLDFLAGS` names it too.
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

> **Amended 2026-11-24 (A2, `../research/2026-11-24-preload-image-prototype.md` §2.2).** The compile adds `-fmacro-prefix-map=<build/target>/=` and `-fmacro-prefix-map=<recipes>/=`. `perlmain.c`'s `xs_init` names its own file through `__FILE__` in every `newXS`. Perl's make compiles it as `perlmain.c`, from the tree; included from `recipes/`, it is found by its absolute `-I` path, and the build host's folders would be in `perl-reactor.wasm`, which ships. The prefix maps keep the relative name (EXECUTED there: one occurrence of the build root, then none). Reactor check 1 adds that the reactor holds no byte string of the build root.

Its exports:
- **`_initialize`**: wasi-libc's, which runs the C constructors.
- **`prepare() -> i32`**: the making. It runs once per instance, and a second call aborts.
  1. It reads argv and the environment from WASI. The runner gives the argv `perl -e <code>`.
  2. It pins the clocks: `__wasi_clock_time_get` is wrapped to answer a fixed realtime of 1767225600 s and a monotonic 0, as wasi-python's `prepare` does. Then no time the image holds lies in a later call's future. **Entropy is not pinned** (decision 7).
  3. It runs `PERL_SYS_INIT3`, `perl_alloc` and `perl_construct`. The hash seed is drawn here, from `/dev/urandom` (spec 013), which is `random_get`.
  4. It runs `perl_parse` with `xs_init` and the argv, then `perl_run`, with `PERL_EXIT_DESTRUCT_END` set. So the code's END blocks stay queued and run at the end of every call, as if the two were one script.
  5. It unpins the clocks, and flushes PerlIO and C's stdio (`PerlIO_flush(NULL)`, `fflush(NULL)`).
  6. It stores Perl's status. Anything other than 0 is returned now.

     > **Amended 2026-11-24 (A3, `../research/2026-11-24-preload-image-prototype.md` §6).** Steps 5 and 6 are swapped, and C's `fflush(NULL)` joins `PerlIO_flush`. Returning a status first could lose, still buffered, the text `SnapshotFailed("exit")` quotes. The prototype flushed first, and the `die` making's message arrived (EXECUTED there).
  7. It counts the descriptors the code left open in the guest's table: not 0–2 and not a preopen. `wasi_perl_open_files` answers that count.
  8. It empties the guest's descriptor table (`wasi_perl_fd_table_reset`, added to `recipes/wasi-fd-table.c`) and wasi-libc's preopen table. The reset also forgets the two preopens the table finds by name: the `/dev` preopen of spec 013 and the library's preopen of spec 019. Each is found again, from this instance's preopens, at its first use. It zeroes the environment's strings, then drops wasi-libc's copy of the environment. It sets wasi-libc's working folder back to `/`.

     > **Amended 2026-11-24 (A4, `../research/2026-11-24-preload-image-prototype.md` §3.4).** Decision 4 said the working folder is "not kept", but its mechanism kept it: `run` repeats `chdir($PWD)` only when the call sets `PWD`, so a call with no `cwd` started in the making's folder. `chdir("/")` cannot undo it, since no grant serves `/` (EXECUTED there). So step 8 replaces wasi-libc's `__wasilibc_cwd` with an allocated `"/"` and frees the making's copy, which `chdir` allocated; wasi-libc's next `chdir` frees the new one. This couples the reactor to wasi-libc's `chdir.c` and `getcwd.c` (READ: wasi-libc 2e6fb9d, the commit wasi-sdk 34 names: `__wasilibc_cwd` is global, and `chdir` frees what it pointed to once it has allocated one). The coupling is pinned as the Perl coupling is: `wasi-reactor.c` does not compile unless wasi-sdk is 34 (`__wasi_sdk_major__`). A new reset check: a making with `cwd="/data"`, then a call with no `cwd`, prints `/` for `Cwd::getcwd`.

     > **Amended 2026-11-24 (A10, `../research/2026-11-24-preload-image-prototype.md` §5.2 item 1).** Freed is not erased. The making's environment value was in the image twice: once in `%ENV`, and once in the environment block that wasi-libc's `__wasilibc_deinitialize_environ` dropped but did not zero (EXECUTED there). wasi-libc keeps the strings of the environment it read in one block it never frees, and its `clearenv` frees only what `setenv` made (READ: wasi-libc 2e6fb9d, `__wasilibc_initialize_environ.c:60-79,146-153`). So `prepare` zeroes every string `environ` names before it drops the environment. The value then stays once, in `%ENV`, where Decision 5 already says it stays. `docs/snapshots.md` says that anything the prelude read can stay in the image and its `.cwasm`, even after the prelude dropped it. A new seed check: a making with a marker in `env` gives an image that holds the marker exactly once.
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
- After the second parse, `run` aborts if either wrapper has not acted, but only when the parse returned 0.

  > **Amended 2026-11-24 (A7, `../research/2026-11-24-preload-image-prototype.md` §6).** The rule "both wrappers have acted" holds for a parse that returned 0 only. A parse that ends early, as for `-Q` or `-v`, never reaches `boot_core_PerlIO`. Aborting there would trap where perl.wasm exits 29, or 0 for `-v`. The prototype aborts only after a parse that returned 0, and both cases are equal (EXECUTED there).

### 4. What is reset on every call, and how

| state | in the image | what `run` does | checked by |
|---|---|---|---|
| argv, `$0`, `@ARGV`, switches | the making's `-e <code>` | reads argv from this instance's WASI; the second `perl_parse` sets the rest | drift `argv_0`, every switch case |
| environment, `%ENV` | dropped at the end of `prepare`, its strings zeroed (A10) | `__wasilibc_initialize_environ`; `%ENV` emptied without its magic (A14); then the parse builds it | drift `env`; reset check 2 |
| working folder | not kept: `prepare`'s step 8 sets wasi-libc's back to `/` (A4) | `chdir($PWD)` when `PWD` is absolute, as `wasi-stubs.c`'s constructor does for perl.wasm | drift `pwd`; reset check 9 |
| time, `$^T` | the clocks were pinned, then unpinned | nothing: the parse reads the real clock | drift `basetime`; reset check 6 |
| the descriptor table and wasi-libc's preopens | emptied | filled again on first use, from this instance's preopens | drift `n_file`, `p_i`; reset check 4 |
| PerlIO's `STDIN`, `STDOUT`, `STDERR` | flushed; no other handle is open (decision 5) | clears each one's end-of-file and error flags | reset check 5 |
| `srand`, `PERL_RAND_SEED` | whatever the code did | `PL_srand_called` cleared; `PERL_RAND_SEED` read again | drift `rand`, `rand_seed_env`; reset check 7 |
| internal random state, hash-order bits | seeded at the making | seeded again in `perl_construct`'s order, from `Perl_seed` (so `random_get`) or `PERL_INTERNAL_RAND_SEED` | drift `hash_order`; seed check 3 |
| hash seed and hash state | the making's | **kept**: every stash and shared key in the image was hashed with it (D §2.1) | seed checks 1 and 2 |
| `@INC`, `PL_e_script` | the making's | cleared; the parse fills `@INC` as perl.wasm does | drift `inc_dirs` |
| `main::`, `PL_isarev`, `%INC`, compiled subs | the code's | kept, by design: that is the preload | drift's visibility cases |
| locale, `PERL_UNICODE`, `PERLIO` | set up at the making, with none of their names set (A15) | not redone: a call that names one runs perl.wasm (A15) | drift `locale`, `lang_utf8`, `lc_all_c`, `perl_unicode`, `perlio_crlf`; seed checks 7 and 4 |
| the exit status that `wasi_perl_exit_status` keeps | −1, since `prepare` never exits | nothing | runner tests of exit codes |
| errno at the second parse (A5) | what the making's last call left | set to what `perl_construct` left, then `isatty` and `lseek` asked of descriptors 0–2, before `perl_parse` | drift `bad_switch`, `no_code_e` |
| punctuation and per-handle variables the parse does not set (A6) | the code's | set to perl.wasm's start values before the parse: the list below | drift `punctuation` |

> **Amended 2026-11-24 (A5, `../research/2026-11-24-preload-image-prototype.md` §3.3).** A croak while `perl_parse` reads the switches exits with errno. perl.wasm opens its three standard PerlIO handles at their first use, inside that croak's write to stderr, and the buffer layer asks each descriptor `isatty` and `lseek`, which leave `ESPIPE` (Linux 29). The image's handles were opened at the making. So `run` sets errno to what `perl_construct` left, then asks the same two questions of this instance's descriptors 0–2. Without it, `perl -Q -e 1` exited 2 or 255 where perl.wasm exits 29 (EXECUTED there). The drift suite adds `bad_switch` (`-Q -e 1`) and `no_code_e` (a bare `-e`), both expected equal.

> **Amended 2026-11-24 (A6, `../research/2026-11-24-preload-image-prototype.md` §3.4).** The second parse resets `$@`, `$!`, `$?`, `$/`, `$"`, `$0`, `@ARGV`, `$^W`, `$^T`, `$^X` and `select`, but not every variable a prelude can set (READ: `perl.c` 5.42.3, `perl_parse`, `S_init_main_stash`, `S_init_predump_symbols`; EXECUTED there for `$, $\ $_`, which kept the prelude's values in every `print`). The owner's rule is that a call from an image behaves as a call from perl.wasm. So `run` sets, before the parse, each variable of `perlvar` that has a built-in meaning, that perl.wasm starts with a fixed value, and that the parse does not set:
>
> - `$_` and `@_` (main's) to undef and empty; `$,` and `$\` to undef; `$;` to `"\034"`;
> - `$^I` (in-place editing) to undef; `$^R` to undef; `$^A` (the format accumulator) to empty; `$^L` to `"\f"`; `$:` to `" \n-"`; `$^F` to 2; `$^P`, `$^C` and `$^D` to 0;
> - `$.` and `${^LAST_FH}`, by forgetting the last read handle; the last successful match, so `$&`, `$1` and `${^LAST_SUCCESSFUL_PATTERN}` start unset;
> - `$ARGV` to undef, and the `ARGV` handle to its first state;
> - the per-handle state of `STDIN`, `STDOUT` and `STDERR`: `$|`, the line count of `$.`, and the format variables `$%`, `$-`, `$=`, `$~` and `$^`;
> - `${^UTF8CACHE}`, `${^RE_TRIE_MAXBUF}`, `${^RE_COMPILE_RECURSION_LIMIT}` and `${^MAX_NESTED_EVAL_BEGIN_BLOCKS}` to their defaults, where a prelude made them.
>
> *Made exact at the build, 2026-11-25:* `${^UTF8CACHE}` is set back to 1. The other three are left unset, as a fresh interpreter has them: the regular-expression engine and the compiler set each to its default when they find it unset (READ: `regcomp.c:3127-3130`, `regcomp_trie.c:617-620`, `op.c:11622-11627`).
>
> Variables with no built-in meaning, `$a`, `$b` and `@F` among them, are package variables, and stay frozen as every package variable does (Decision 5). So do `%SIG`'s `__DIE__` and `__WARN__` hooks and `%{^HOOK}`, which are code, under A11's trust point. A drift case, `punctuation`, has a prelude that sets every variable of the list, and calls that print each; they are expected equal to perl.wasm's.

> **Amended 2026-11-25, at the build (A14).** The parse does not empty `%ENV`: `S_init_postdump_symbols` stores each variable into the hash it finds, and the image's still holds the making's. So a making with `env={"MAKING": "1"}` gave every call a `%ENV` with `MAKING` beside the call's own (EXECUTED: reset check 2 failed with `CALL,MAKING,OTHER` where `CALL` was due; the prototype's drift case `env` had no variables at the making, so it did not see it). `run` now takes the hash's environment magic off, empties it, and leaves the parse to put the magic back and fill it. The magic comes off first because its clear would clear the C environment just read.

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

> **Amended 2026-11-25, from the review of PR #32 (H1): A15, the names read only at start-up.** The text said that under wasi-libc's C and C.UTF-8 the locale "agrees with perl.wasm". It does not for C. Perl reads the locale's names and `PERL_UNICODE` once, in `perl_construct`, and `PERLIO` at the first open of a handle, and `run` redoes neither. So a call from an image got the making's locale, Unicode switches and layers, silently, with `image_sha256` set: under `env={"LC_ALL": "C"}`, `${^UTF8LOCALE}` was 1 from the image and 0 from perl.wasm; under `PERL_UNICODE=S`, `print chr(233)` gave `e9` against `c3 a9`; under `PERLIO=:crlf` a file got `61 0a` against `61 0d 0a` (EXECUTED in the review, and again by the new drift cases on the unfixed head, which failed). The decision, the orchestrator's: **fall back, do not re-read.** `_snapshot.FROZEN_ENV` holds every name Perl reads before or outside what `run` redoes. A making refuses each (so an image is always made with them absent), and a call whose environment names any of them, with any value, runs perl.wasm with `image_sha256` `None`, as for the first three names. Re-reading them in `run`, so those calls keep the image, is a roadmap item.
>
> The audit, of every environment read in Perl 5.42.3's sources as built (READ: `grep -n 'PerlEnv_getenv\|getenv(\|my_getenv'` over `build/target/*.c`, with the enclosing function of each):
>
> | name | read in | when | here |
> |---|---|---|---|
> | `PERL_HASH_SEED`, `PERL_PERTURB_KEYS` | `util.c:4770`, `:4817`, `Perl_get_hash_seed` | `perl_construct` | falls back (Decision 6) |
> | `PERL_JSON_PP_USE_B` | `JSON/PP.pm` line 48 | when JSON::PP compiles | falls back (Decision 6) |
> | `LC_ALL`, `LANG` | `locale.c:9176-9177`, `Perl_init_i18nl10n`; `:3260`, `:3313`, `S_find_locale_from_environment` | `perl_construct` | **falls back** |
> | `LC_CTYPE`, `LC_NUMERIC`, `LC_COLLATE`, `LC_TIME`, `LC_MESSAGES`, `LC_MONETARY` | `locale.c:3303`, `:8875`, through `category_names` (`:819`), one per category wasi-libc defines (READ: its `locale.h`, 23-29) | `perl_construct` | **falls back** |
> | `LANGUAGE`, `PERL_BADLANG` | `locale.c:9002`, `:9009`, `Perl_init_i18nl10n` | `perl_construct` | **falls back** |
> | `PERL_DEBUG_LOCALE_INIT`, `PERL_SKIP_LOCALE_INIT` | `locale.c:9023`, `:9227`, `Perl_init_i18nl10n` | `perl_construct` | **falls back** |
> | `PERL_UNICODE` | `locale.c:9518`, `Perl_init_i18nl10n` | `perl_construct`; the parse reads only `-C` (`perl.c:3731`) | **falls back** |
> | `PERLIO` | `perlio.c:1486`, `PerlIO_default_layers` | once, at the first open | **falls back** |
> | `PERLIO_DEBUG` | `perlio.c:346`, `PerlIO_debug` | once, while `PL_perlio_debug_fd` is 0 | **falls back** |
> | `PERL_RE_COLORS` | `regcomp.c:352`, `Perl_reginitcolors` | once per interpreter, at the first regex debug output | **falls back** |
> | `PERL_RAND_SEED`, `PERL_INTERNAL_RAND_SEED` | `perl.c:278`, `:298`, `perl_construct` | `perl_construct`, and again in `run`'s `reseed` | per call |
> | `PERL_DESTRUCT_LEVEL` | `perl.c:648`, `perl_destruct` | each call's `perl_destruct` | per call |
> | `PERL5OPT`, `PERL_SIGNALS` | `perl.c:2405`, `:2667`, `S_parse_body` | each call's parse | per call |
> | `PERL5LIB`, `PERLLIB`, `PERL_USE_UNSAFE_INC` | `perl.c:4903`, `:4907`, `:4955`, `S_init_perllib` | each call's parse | per call |
> | `PERL_HASH_SEED_DEBUG` | `util.c:4870`, `Perl_debug_hash_seed`, from `perl_parse` (`perl.c:1792`) | each call's parse | per call (it prints the image's seed, the accepted drift) |
> | `PERL5DB` | `toke.c:9173`, `yyl_try` | a call's `-d` | per call |
> | `PATH`, `TMPDIR` | `util.c:3525`, `Perl_find_script`; `perlio.c:5464`, `PerlIO_tmpfile_flags` | each use | per call |
> | `%ENV`'s values | `perl.c:4837`, `S_init_postdump_symbols` | each call's parse (A14) | per call |
> | `PERL_TRACE_OPS`, `PERL_DUMP_RE_MAX_LEN` | `perl.c:723`; `regcomp.c:13943` | only in a `DEBUGGING` perl, which this is not | not compiled |
> | `PERL_MEM_LOG`, `PERL_C_BACKTRACE_ON_ERROR` | `util.c:4952`; `:1595` | only with `PERL_MEM_LOG` or `USE_C_BACKTRACE`, neither defined (READ: `config.h`) | not compiled |
> | `PERL_MALLOC_OPT`, `PERL_BADFREE`, `PERL_DEBUG_MSTATS` | `malloc.c:1667`, `:1830`; `perl.c:2717` | only with Perl's malloc; `usemymalloc='n'` (READ: `config.sh:1221`) | not compiled |
>
> - The names are exact. No prefix rule is needed: Perl reads only the `LC_*` categories `locale_table.h` lists under an `#ifdef`, and wasi-libc defines exactly six.
> - The checks: drift `perl_unicode`, `perl_unicode_argv`, `perlio_crlf`, `lc_all_c`, `lc_all_c_csl`, `lc_all_c_locale_w`, `lang_c`, and the ported `env`, `locale` and `lang_utf8`, now fallback cases (byte equality with perl.wasm, `image_sha256` `None`); `env_plain` keeps `%ENV` from the image; seed check 7, a call naming each of the 19 names runs perl.wasm and answers as it does; seed check 4, a making refuses each of the 19. Each failed on the head before the fix (EXECUTED: 34 failed).
> - wasi-python could adopt the same rule with its own list: the `PYTHON*` variables CPython reads once at start-up, such as `PYTHONHASHSEED`, `PYTHONIOENCODING`, `PYTHONUTF8` and `LC_*` for its locale coercion (INFERRED; row 4 of the table below). The API does not change.

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
| 2 | a call from a `Snapshot` instantiates a module the runner wrote. On Linux it is mapped copy-on-write, and there is no per-call copy. On macOS and Windows, wasmtime has no source to map, so each call writes the image into fresh memory, as wasi-python's copy does (decision 1) | on Linux, about 2.7 ms and 15 MiB a call saved; elsewhere about the same cost as a copy (decision 1, INFERRED) | adopt it inside the runner, with no change to the API or the file. It would pay one compile of its snapshot binary's code per image, cached. Its 203 MiB images exceed wasmtime's dense-image limit, so whether they still map is to be measured (INFERRED) |
| 3 | the making pins the clocks but not entropy | the hash seed is frozen in the image (decision 7) | draw real entropy in `prepare` as well. That would close its own pydantic note (READ: `docs/explanation/security-model.md`). It gives up identical images from identical code, which its docs do not promise for a prelude (INFERRED) |
| 4 | `make_snapshot`'s `env` refuses the names Perl reads only at start-up (A15: 19 of them), and a call whose env has one runs the plain binary | an image cannot honour them (decision 6) | the same rule with its own list: for example `PYTHONHASHSEED`, `PYTHONIOENCODING`, `PYTHONUTF8`, if it ever honours one in an image |
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
1. `perl-reactor.wasm` has the form of decision 1, step 3, and exactly the six exports. An image module written from it and a one-page image compiles, so its code indexes no data segment. It holds no byte string of the build root (A2).
2. `SHA256SUMS` lists `perl-reactor.wasm`; `MANIFEST` does not, and `tests/build/test_modules.py`'s check of `MANIFEST` passes unchanged.
3. Compiling `recipes/wasi-reactor.c` against a stand-in `patchlevel.h` that says 5.42.4 fails on its `#error`.
4. The artifact set holds no file that begins with the snapshot magic. No script runs `wizer`.

**Drift** (`tests/snapshot/test_drift.py`, the 41 cases of D's `drift.py`, ported)
1. The image of `make_snapshot("")` differs from perl.wasm in exactly `{hash_seed}`.
2. The image of `make_package_snapshot()` differs in exactly `{no_use_posix, defined_sub, inc_keys, hash_seed}`.
3. The cases `hash_seed_env` and `hash_order_env` match perl.wasm's output, and their `Completed.image_sha256` is `None`. So do three new cases: `PERL_PERTURB_KEYS=0` alone, `PERL_JSON_PP_USE_B=1` with `-MJSON::PP`, and `PERL_HASH_SEED` set to empty.
4. The sets are exact. A new difference fails, and so does an expected one that disappears, so the docs and the test change together.
5. `bad_switch` (`-Q -e 1`) and `no_code_e` (a bare `-e`) are equal on both images (A5).
6. `punctuation`: an image whose prelude sets every variable of A6's list gives calls equal to perl.wasm's (A6).

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
9. A making with `cwd="/data"`, then a call with no `cwd`: `Cwd::getcwd` prints `/` (A4).

**The seed** (`tests/snapshot/test_seed.py`)
1. Five makings give five different `hash_seed()` values. Twenty calls from one image give one value.
2. With a probe linker whose `random_get` returns fixed bytes, as in spec 013's check 1, two makings give the same seed. Other bytes give another seed. So the seed comes from the making's `random_get`, not from a constant or a zeroed source.
3. With that probe, two calls from one image print the same key order and `rand()`. Under the real runner, twenty calls show more than one key order. So the per-call bits come from the call's own `random_get`.
4. `make_snapshot` refuses each of the three names in `env` with `ValueError`, before any instance is made.
5. On POSIX, a saved file has mode 0600, and `load` refuses it at 0640 and at 0606 with `PermissionError`.
6. A making with a marker in `env` gives an image that holds the marker exactly once, in `%ENV` (A10).
7. A call from an image whose `env` names any of A15's 19 names runs perl.wasm: `image_sha256` is `None`, and its exit and output equal perl.wasm's. Check 4 refuses each of the 19 at a making (A15).

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
   - on Linux, whether wasmtime maps the default image copy-on-write: its page faults per instantiation, from 014's sealed copy and from an in-process compile;
   - on macOS, the same counts from CI's macOS runner, if it can run the memo's driver; otherwise the macOS and Windows rows of Decision 1's table stay INFERRED, and the memo says so.
5. `docs/snapshots.md` exists, and `docs/sandbox.md` has the seed's paragraph. `CHANGELOG.md` has its lines under "Unreleased", naming `perl-reactor.wasm` as a new artifact.
6. A fresh agent's review lands.

## Landing notes, 2026-11-25

Built on `spec/022-a-preload-image`, with dev at 388ddd3 merged in. The prototype's code was adapted into `recipes/wasi-reactor.c`, `recipes/wasi-fd-table.c` and `src/wasi_perl/_image_module.py`, each saying so.

### What was built, by decision

- **1. The image module.** `_image_module.py`: `check_form`, `runs`, `coalesce`, `write`. A form fault is `FormError`, an `ArtifactsMissing`. An image past a maximum or below the reactor's own pages is `SnapshotMismatch`. `_engines.image_module` keys the module by `SHA-256("wasi-perl image module 1\0" + reactor digest + "\0" + image_sha256)`, in the process and as the `.cwasm` name, through the same `_miss` and `_cache` as perl.wasm. A compile refused by wasmtime is `ArtifactsMissing`, with wasmtime's words ("data count section required").
- **2. The second binary.** `20_target.sh` compiles the entry after the final `make`, with Perl's `perlmain.o` command, `-Werror -Wno-c++-keyword` and the two prefix maps (A2). `40_link.sh` links it before the command, with `perlmain_objs`, `libs` and `CLDFLAGS` set (A1), then runs the pass over both. `assemble_artifacts.py` requires it. `Artifacts.reactor` is the last field. The reactor is also a release file (`ci.yml`'s release files, `release.yml`'s six assets): the spec named the set and the wheel, and `SHA256SUMS`, a release file, lists it.
- **3. The entry.** `prepare` and `run` as the decision and A3–A7 say, with A6's list in `fresh_special_variables`, A10's zeroing and A14's `fresh_environment_hash`. It is pinned to Perl 5.42.3 and to wasi-sdk 34 (`<wasi/version.h>`). Perl's `nostdio.h` hides `fflush` behind a macro, so C's is called as `(fflush)(NULL)`.
- **4. The resets** are checked by `tests/snapshot/test_resets.py` 1–9 and `test_drift.py`'s eleven special-variable cases.
- **5. The making.** `_snapshot_make.py`, adapted from wasi-python's. The making is `runner._run(..., making=True)`: `run`'s own checks, grants, mounts, limits, streams and stop, with `_prepare` as the entry and `_capture` after a status of 0. `make_snapshot` compiles the unmetered flavour before it returns, and the metered one with `fuel`.
- **6. The calls.** `run(snapshot=)` stands after `stdin`. The set is checked against the image before the fallback is judged, so a wrong image fails the same way whatever the environment. `Completed.image_sha256` is the last field.
- **7. The seed.** As the decision says; `save` writes 0600 and `load` refuses a file group or others may read or write.
- **8. The drift.** `test_drift.py` ports the 41 cases and adds three fallback cases, `bad_switch`, `no_code_e` and `-v`. The sets are exactly `{hash_seed}` and `{no_use_posix, defined_sub, inc_keys, hash_seed}`.

### Choices made in the build

- `--no-snapshot` passes `snapshot=False`, which runs perl.wasm as `True` does.
- The process's counts gain `snapshots_made` and `snapshots_restored`. The module cache's checks in `tests/runner` compared the whole counts dict; they now read the cache's three counts by name, which asserts the same of the cache.
- Spec 017's check A1 now holds `files` second to last and `image_sha256` last, as this decision's check 9 says it extends.
- A save or load publishes its folder in `_grants`, under the lock a starting call holds while it registers its grants, so of a call and a save at once one sees the other.

### Seen failing first

- The new Python checks failed to collect without the new modules. On a reactor built without A6's and A10's code, the eleven special-variable cases and the environment-once check failed (12 failed); with them, all passed (EXECUTED). Reset check 2 failed on the first reactor and found A14.
- The listing checks, the link-step checks and `--no-snapshot` each failed on the old code. `test_the_public_api` and the cache checks failed in CI's lint job before the counts change.
- On CI's windows-2025 leg, three image checks failed because `Path.write_text` writes CRLF there (job 138875087731); they now write bytes.

### Acceptance

1. The suites, EXECUTED on this machine after a clean in-place rebuild of steps 00–40 of the final code (`rm -rf build`, then `scripts/build.sh` under the lock): `tests/build` 605 passed (an earlier run had one failure of `test_perturb_keys_and_the_internal_seed_work_as_natively`, a check of perl.wasm alone that compares three random orders, which passed when run again); `tests/runner tests/escapes tests/mounts tests/oneliners tests/snapshot` with `WASI_PERL_ORACLE=build/native-install/bin/perl`, 1045 passed, 2 skipped; `-m "not slow"`, 647 passed, 3 skipped; ruff clean.
2. The one-liner oracle's tests pin `PERL_HASH_SEED=0` and `PERL_PERTURB_KEYS=0`, so by Decision 6 every one of their calls runs perl.wasm even when given an image. So acceptance 2 ran the 226 cases with those two names removed, from perl.wasm twice and from each image once: 226 equal on both images, none hash-order dependent (EXECUTED: the memo, §5).
3. Perl's `t/`: from `make_snapshot("")`, the scoreboard equals perl.wasm's exactly; from the package image, too, but for `lib/deprecate.t`, already excluded, which fails the same way with another message because Carp is preloaded, so its `shows` pattern does not match. No file fails only from an image, so `exclusions-image.toml` is not needed; `exclusions.toml` is not touched (EXECUTED: the memo, §5).
4. `../research/2026-11-24-the-preload-image-measured.md`. The macOS and Windows rows of Decision 1 stay INFERRED.
5. `docs/snapshots.md`, `docs/sandbox.md`'s paragraph, the README's section and the changelog's lines.

### Review round 1, `../reviews/022-a-preload-image-made-at-install-time-pr-2026-11-25.md`

- **H1.** Fixed by falling back, the orchestrator's decision: amendment A15 above, with the audit of every environment read and the new checks, seen failing on `02e9994` (34 failed) and passing after. Decision 4's locale row, Decision 6 (through A15), row 4 of the table, `docs/snapshots.md` ("What is fresh") and the changelog say it. Re-reading the locale, `PERL_UNICODE` and `PERLIO` in `run` is on the roadmap.
- **M1 (a).** `tests/oneliners/test_oneliners.py::test_guest_from_the_package_image_matches_native` runs all 226 corpus cases again, each from the package image, with `PERL_HASH_SEED`, `PERL_PERTURB_KEYS` and `LC_ALL` left out of the base environment, against the same native answers, and asserts that each call started from the image. Its name starts `test_guest_`, so the wheel legs' gate counts it. 226 passed (EXECUTED).
- **M1 (b).** `WASI_PERL_TESTS_FROM_IMAGE=1` (`tests/conftest.py`) sends every call that can start from the package image there: a call with `snapshot` left at its default, on the set the image was made from, with room in its memory limit. The one accepted drift these suites can see, `test_every_call_has_its_own_seeds`, is a strict expected failure in that mode. The wheel legs run `tests/escapes` and `tests/mounts` this way, and a gate of its own counts them and requires at least one call from the image. Here: 234 passed, 1 xfailed, 218 calls from the image (EXECUTED). `tests/runner` is not run this way: most of its checks use stand-in sets with no reactor, and many count the module cache's compiles and loads, which an image's module changes by design.
- **L1.** Damaged-file check: "another image", a sound zlib stream of the right size with one byte of the image changed, raises `SnapshotMismatch` "does not match its hash". It failed with the hash check removed (EXECUTED: 1 failed) and passes with it.
- **L2.** The memo names the cause of the rebuild that is not byte-reproducible: `PERL_BUILD_DATE` from `__DATE__` and `__TIME__` in `perl.c`, "Compiled at" in `perl -V`. `SOURCE_DATE_EPOCH` is on the roadmap.
- **L3.** `test_perturb_keys_and_the_internal_seed_work_as_natively` is a rare flake that predates 022: with `PERL_PERTURB_KEYS=1` its program has about 64 near-equal orders, so three equal runs come about once in 4,000 per side (the review's figures). It is left for a follow-up that takes more runs.
- **L4.** `release.yml`'s six assets run only on a tag; they are to be checked on the first release candidate.
- **L5.** `lib/deprecate.t` from the package image is an instance of the accepted drift `inc_keys`: it fails from perl.wasm too, so `exclusions-image.toml` does not apply.
- **N1.** `docs/snapshots.md`'s "What is fresh" names the start-up variables each call reads, and lists those that send a call to perl.wasm.
- **N2.** The memo states the `-e 1` overhead once, 0.64–0.76 ms (§1); the changelog and `docs/snapshots.md` quote it.
