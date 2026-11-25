# The preload image, prototyped: choice (c) on today's dev build

- **Status:** research, 2026-11-24. The prototype that spec 022 asked for before its build, the owner having asked that 022 be built last and stop with a report if the approach fails. It changes nothing under `src/`, `recipes/` or `scripts/`. The prototype is evidence, not a merge.
- **Researcher:** an agent (Claude Opus 5.5).
- **Built on:** `origin/dev` at 49fd412 (specs 013–016 and 018–021 merged), in the worktree `022-preload-image`, with 021's full build in `build/`. wasmtime-py 51.0.0 (EXECUTED: `importlib.metadata.version("wasmtime")`). Spec 017 is not merged; nothing here needs it.
- **The question:** do spec 022's riskiest choices hold? That is: the second link of Decision 2; the reactor entry of Decision 3; and choice (c) of Decision 1, which no study prototyped: the runner writes the image into a new module, which wasmtime then maps copy-on-write.
- **Evidence:** `2026-11-24-preload-image-prototype/`. It holds the C files, the build script, the Python scripts and every raw result (`results/`). The binaries stay in the ignored `build/proto022/`. Their hashes and sizes are in `results/binaries.txt`.

Labels: EXECUTED means run and seen. READ means read from a file. INFERRED means the researcher's reasoning.

## 0. The answer

**Go.** Every item held, on the second attempt at worst. Each fix the prototype needed is a change to spec 022's text, listed in §6.

| item | verdict | in one line |
|---|---|---|
| 1. The reactor links | **go** | `make perl` with the three variables links it. After `-O3` it has exactly Decision 1's form, and its code holds no `memory.init` and no `data.drop` |
| 2. Choice (c) under wasmtime-py 51 | **go** | 36 programs give the same stdout, stderr and exit code from the image as from perl.wasm. Two instances share no state. Each making draws its own seed |
| 3. As fast as claimed, on Linux | **go** | "five" ran in 4.2–5.2 ms against 21.4–24.7 ms. The image is mapped copy-on-write both when compiled in the process and when loaded from a `.cwasm` |
| 4. Security reading | **go**, with notes | The eight sections are byte-identical to the checked reactor's. Decision 1's argument misses three things a prelude leaves behind, and one trust point |

| decision | verdict | amendments (§6) |
|---|---|---|
| 1. Choice (c), the module the runner writes | **go** | A8–A12 |
| 2. A second binary, `perl-reactor.wasm` | **go** | A1, A2, A13 |
| 3. The reactor entry is a recipe file | **go** | A3–A7 |

Three prototype fixes took a second attempt each, under the two-strikes rule:
- errno before the second parse (§3.3);
- the working folder (§3.4);
- the build host's path (§2.2).

Each second attempt worked. No hypothesis reached a third.

## 1. What was built

All four files are in `2026-11-24-preload-image-prototype/`. They are written for this project, after study D's prototype.

- **`wasi-reactor.c`.** It includes Perl's generated `perlmain.c` for `xs_init`, with `main` renamed away. It does not compile unless the Perl is 5.42.3 (`#error`). It exports `prepare`, `run` and `wasi_perl_open_files`, and the link gives `_initialize`, `wasi_perl_exit_status` and `memory`.
  - `prepare` does Decision 3's steps 1–10.
  - `run` does Decision 4's resets, then the second `perl_parse` and `perl_run`, then `perl_destruct` and `exit`.
  - The second parse keeps the preload through six link wraps. Each wrap aborts if it does not find what `perl.c` 5.42.3 makes at that point.
- **`fdtable-reset.c`.** It includes `recipes/wasi-fd-table.c` and adds the two functions Decision 3 puts in that file:
  - `wasi_perl_fd_table_open_files`;
  - `wasi_perl_fd_table_reset`, which also forgets `devdir` and `libdir`.

  A build would add both to the recipe itself.
- **`build.sh`** does the link as Decision 2 says, then 021's `-O3`.
- **`image_module.py`** does Decision 1's steps 1–5.
  - It checks the form.
  - It writes the module, with the segments cut into 4 KiB runs and the 50,000 cap applied.
  - It has `with_global_export`, which adds an export of the mutable global. The making runs in that copy, so the host can read the stack pointer after `prepare`. The copy's code is the reactor's.
- **`harness.py`** makes an image and runs calls through **`wasi_perl.run` itself**. It patches two things:
  - the runner's module lookup, which answers the image module;
  - `_start`, which is read as `run`.

  So the runner's checks, limits, grants, streams and exit-status handling apply to the image's calls exactly as to perl.wasm's.

## 2. Item 1: the reactor links, and has the form

### 2.1 The link (EXECUTED)

```
WASI_PERL_JOBS=4 flock /home/user/wasi-perl-wt/machine.lock \
    context/research/2026-11-24-preload-image-prototype/build.sh
```

- The reactor's object is compiled with `sh cflags "optimize='-O2'" perlmain.o`, the command Perl gives `perlmain.o`.
- The link is `make perl` with:
  - `perlmain_objs="wasi-reactor.o fdtable-reset.o"`;
  - `libs="build/index/lib-index.o <configured libs>"`;
  - `CLDFLAGS="<configured> -mexec-model=reactor -Wl,--wrap=Perl_newSVpvn_share,--wrap=Perl_boot_core_PerlIO,--wrap=Perl_boot_core_UNIVERSAL,--wrap=Perl_boot_core_builtin,--wrap=Perl_boot_core_mro,--wrap=Perl_newXS,--wrap=__wasi_clock_time_get"`.
- `build/target/perl` is moved aside for the link and put back. Its hash was unchanged after every run (EXECUTED: `sha256sum build/target/perl`, d0dda31b…).
- **Control:** the same `make perl` call with `perlmain_objs=perlmain.o` gives bytes identical to `build/link/perl.wasm`: d0dda31b…, 9,443,860 bytes (`results/binaries.txt`). So the method reproduces 019's link exactly.
- One quirk belongs to this machine, not to the spec. This `build/` was made in another worktree and moved here, so its `Makefile` names that worktree's compiler and headers. `build.sh` names `CC` and `libs` again for this worktree, and holds the rule's prerequisites old with `make -o`. A tree built in place needs neither (INFERRED from the control link).
- The link takes about 6.7 s for both binaries and the pass (EXECUTED: `time`, under the lock).

| file | bytes | deflate -6 | sha256 |
|---|---|---|---|
| `perl-reactor.wasm` (wasm-ld) | 9,445,627 | 3,387,808 | f4076345… |
| `perl-reactor.opt.wasm` (`-O3`) | 8,676,076 | **3,252,601** | ca0327c6… |
| `build/artifacts/perl.wasm`, for comparison | 8,675,094 | 3,256,737 | cde7600b… |

So the wheel would grow by about 3.25 MB compressed (INFERRED: the deflate size stands in for a wheel's zip entry).

### 2.2 The form (EXECUTED)

```
uv run python context/research/2026-11-24-preload-image-prototype/item1_form.py \
    context/research/2026-11-24-preload-image-prototype/results/item1.json
```

`results/item1.json` holds the full output. After `-O3`:

| Decision 1, step 3 | found |
|---|---|
| one memory, defined, exported as `memory` | yes: memory 0, min 214 pages, **no maximum** |
| every segment active, memory 0, at `i32.const` | yes: **11,958 segments**. wasm-opt split wasm-ld's 2 segments around runs of zeros |
| exactly one mutable global | yes: global 0, `__stack_pointer`, first value 8,388,608 |
| no start section | yes |
| the six exports, and only them | yes: `memory`, `_initialize`, `wasi_perl_open_files`, `prepare`, `run`, `wasi_perl_exit_status` |
| a data-count section | present after `-O3`, absent from wasm-ld's output |
| `memory.init`, `data.drop` in the code | **0 and 0** (binaryen's `wasm-dis`, by grep), in both binaries |

- An image module written from the optimised reactor and a one-page image compiles. So the code indexes no segment.
- The stand-ins in WAT behave as Decision 1 says:
  - a passive segment, two mutable globals and a start section are each refused by the form check;
  - a stand-in whose code holds `memory.init` writes a module that wasmtime refuses: "data count section required". So does one with `data.drop`;
  - a stand-in with a data-count section and neither instruction writes a module that compiles.

**The build host's path.**
- On the first attempt, the reactor held one absolute path of the build host: `…/build/target/perlmain.c`. It comes from `__FILE__` in `xs_init`'s `newXS` calls. Included from a file outside the tree, `perlmain.c` is found by its absolute `-I` path.
- Perl's own make compiles it as `perlmain.c`, so perl.wasm holds no such path.
- The second attempt adds `-fmacro-prefix-map=<tree>/=` to the compile, and the path is gone (EXECUTED: `strings -n 8 … | grep -c /home/user`, 1, then 0).
- Study D §6 saw the same path. Amendment A2.

## 3. Item 2: choice (c) works under wasmtime-py 51

```
uv run python context/research/2026-11-24-preload-image-prototype/item2_semantics.py  …/results/item2.json
uv run python context/research/2026-11-24-preload-image-prototype/item2_resets.py     …/results/item2-resets.json
```

### 3.1 The making (EXECUTED)

The prelude is Decision 5's: one `require` line for each of `strict`, `warnings`, `POSIX`, `List::Util`, `JSON::PP`, `Data::Dumper`, `Storable` and `Encode`.

- `prepare` returned 0, and `wasi_perl_open_files` answered 0.
- **The stack pointer is back at its first value.** The host read global 0 through the probe export after `prepare`: 8,388,608 against 8,388,608 in the global section. The guest's own check (step 9) passed too.
- The image is 16,580,608 bytes, 253 pages. It grew 39 pages past the reactor's 214.
- Written, the module has 12–15 segments: 1,978 blocks of 4 KiB, about 8.1 MB of data. That is far under the cap of 50,000. Only 8 of the blocks are in the 8 MiB stack region (`results/item4.json`).
- The written module is 10.9 MB.

### 3.2 Calls from the image against perl.wasm (EXECUTED)

Each program ran through `wasi_perl.run` from the image and from perl.wasm. The comparison is of stdout, stderr, exit code and trap, byte for byte. **36 of 36 are equal** (`results/item2.json`, `programs`). They cover:

- the brief's six:
  - `-e 'use POSIX; print floor(2.5)'`;
  - "five", printing JSON through `List::Util`, `POSIX` and `JSON::PP`, and "five" bare;
  - `-e 1`;
  - `die` inside `eval`, with a string and with a hash ref, then an uncaught `die` (255);
  - a granted file read by `-ne`, `-lane` and `-0777`, and a missing one (exit 2);
  - `%ENV` with three variables, one empty, then a set and a delete, and `@ARGV` with a space and an empty argument;
- also:
  - `exit 3`, stdin, `-w`, `use warnings`, `use strict` refs, and `$x = 1` with no strict;
  - the BEGIN/INIT/END order and `${^GLOBAL_PHASE}`;
  - the sandbox's refusals of `system` and `fork`;
  - `$^T` against the pinned instant;
  - `@INC`, Data::Dumper, `@ISA`, Encode, Storable and `PERL_RAND_SEED=7`;
  - `-v`, a bad switch, `-e` with no code, a missing script, a script file with arguments, a missing module, and a syntax error.

**Isolation (EXECUTED).** The first instance set `$main::leak = 42` and redefined `POSIX::floor` to return 99, and printed 99. The second, a fresh instance of the same image, printed `clean 2`.

**The seed (EXECUTED).**
- Three makings gave three different image hashes and three different `hash_seed()` values.
- Ten calls from one image gave one `hash_seed()` value.
- Ten calls of the same key-order program gave 9 or 10 distinct orders (two runs).
- perl.wasm's three calls gave three seeds.

**The accepted drifts are there, and only they** (EXECUTED): `no_use_posix`, `defined_sub` and `inc_keys` differ, as Decision 8 lists. The fourth, `hash_seed`, is the seed result above.

**The making's failures (EXECUTED).**
- A prelude that leaves `our $F` open: status 0, and `wasi_perl_open_files` answers 1.
- `die "nope\n"`: status 255, with "nope" on stderr.
- `exit 0`: status 0, open files 0.

**A prelude with `use strict; use warnings; END { print "pre\n" }`** (EXECUTED):
- a call's `$y = 2` runs, so strict does not leak;
- the call's `END` prints `call`, then `pre`, as one script would.

### 3.3 errno before the second parse: found, fixed in two attempts

On the first run, one case differed: `perl -Q -e 1`.
- perl.wasm exits 29. The image exited 2.
- **Why** (READ: `perl.c` `perl_parse`, `perlio.c:1537-1545` and `4246-4260`, `recipes/sandbox-refusals.c:322-339`):
  - a croak during switch parsing exits with errno, turned into its Linux number;
  - perl.wasm opens its three standard PerlIO handles lazily, inside that croak's write to stderr;
  - the buffer layer asks each descriptor `isatty` and `lseek`, which leave `ESPIPE` (Linux 29);
  - the image's handles were opened at the making, so errno was whatever `run`'s own preamble had left.
- **First attempt:** restore the errno that `perl_construct` left. That gave 255, since that errno was 0.
- **Second attempt:** also ask `isatty` and `lseek` of descriptors 0–2 before the parse, in this instance. Then `-Q` and a bare `-e` both exit 29, as perl.wasm does.
- Amendment A5.

### 3.4 State Decision 4's table does not name (EXECUTED)

`item2_resets.py` prints Perl's special variables, the working folder and the default output handle. It does so after three preludes, and once after a making that has a working folder.

| prelude | `$@ $! $? $/` | `$, $\ $_` | `select` | working folder |
|---|---|---|---|---|
| Decision 5's | as perl.wasm | as perl.wasm | as perl.wasm | as perl.wasm |
| one that sets all of them | reset by the parse | **kept**: every `print` of every call gets the prelude's separators | reset | as perl.wasm |
| `select STDERR` | | | reset | |
| made with `PWD=/data`, called with no `cwd` | | | | **first attempt: `/data`**; perl.wasm: `/` |

**The working folder.**
- Decision 4 says the working folder is "not kept", but the mechanism it gives keeps it. `run` repeats `chdir($PWD)` only when the call sets `PWD`. A call with no `cwd` then starts in the making's folder.
- That is no escape, since every path still resolves through the call's own grants. But a relative path then names a file that perl.wasm would not open.
- **First attempt:** `run` does `chdir("/")` when there is no `PWD`. It fails, because no grant serves `/`, and the folder stayed `/data`.
- **Second attempt:** `prepare`'s step 8 sets wasi-libc's `__wasilibc_cwd` back to `"/"`, freeing the copy that `chdir` allocated. Then the call sees `/`.
- This couples the reactor to wasi-libc's internals: the symbol is global in wasi-sdk 34's `getcwd.c`, and `chdir.c` frees what it points to (EXECUTED: `llvm-nm` over `libc.a`). Amendment A4.

**The separators** are amendment A6.

## 4. Item 3: the speed, on Linux, under the lock

```
WASI_PERL_JOBS=4 flock /home/user/wasi-perl-wt/machine.lock uv run python \
    context/research/2026-11-24-preload-image-prototype/item3_speed.py …/results/item3.json 60
```

The script ran twice, giving `results/item3.json` and `results/item3-run2.json`.
- Calls go through `wasi_perl.run`, alternating perl.wasm and the image call by call: 60 of each after 2 warm-up calls, taking the median.
- Faults are the process's minor faults across one call (`getrusage`).
- The machine is shared, and the plain "five" moved 15% between the runs. The ranges below are the two runs, inside one of which the two sides alternate.

### 4.1 Whole calls (EXECUTED)

| program | perl.wasm | image | × | spec 022 (INFERRED there) | faults, perl.wasm → image |
|---|---|---|---|---|---|
| "five", module compiled in process | 21.4–24.7 ms | **4.21–5.22 ms** | 4.7–5.1 | 25.2 → 4.3 ms | 569 → 470–480 |
| "five", module from a `.cwasm` (sealed memfd) | 21.9–22.9 ms | 4.40–4.60 ms | 5.0 | | 569 → 470–480 |
| `-MPOSIX -e 1`, in process | 13.0–13.1 ms | **4.12–4.14 ms** | 3.1–3.2 | 15.55 → 4.25 ms | 368 → 459–469 |
| `-MPOSIX -e 1`, `.cwasm` | 13.2 ms | 4.11–4.19 ms | 3.2 | | |
| `-e 1`, in process | 1.04–1.09 ms | **1.48–1.53 ms** | 0.71 | +0.38 ms | **81 → 292–300** |
| `-e 1`, `.cwasm` | 1.13–1.20 ms | 1.51–1.68 ms | 0.71–0.75 | | |

- So the image costs a bare one-liner **0.43–0.48 ms** and about 215 page faults. Decision 6's rule, to pass the image only to calls that load modules, stands.
- Through today's `run()`, a bare call is about 1.05 ms, not study D's 4.0 ms. So the image's added cost is now 40% of a bare call, not 9%.

**Fuel** (EXECUTED, metered once each; the two runs agree to 1%):

| program | perl.wasm | image |
|---|---|---|
| "five" | 120.3–120.7 M | 18.3–18.5 M |
| `-MPOSIX -e 1` | 70.1–70.9 M | 17.1–17.5 M |
| `-e 1` | 1.56 M | 1.27 M |

### 4.2 Copy-on-write (EXECUTED)

This is instantiation alone, through `InstancePre`, median of 30. "Mapped" is what `/proc/self/maps` gained while the first instance lived.

| module | instantiate | faults | mapped |
|---|---|---|---|
| perl.wasm, compiled in process | 0.0074–0.0076 ms | 0 | `memfd:wasm-memory-image`, 5,533,696 B |
| perl.wasm, from `.cwasm` (sealed memfd, `_cache.load`) | 0.0068–0.0070 ms | 0 | `memfd:wasi-perl-module`, 5,533,696 B |
| **image, compiled in process** | **0.0064 ms** | 0 | **`memfd:wasm-memory-image`, 8,224,768 B** |
| **image, from `.cwasm` (sealed memfd)** | **0.0063–0.0064 ms** | 0 | **`memfd:wasi-perl-module`, 8,224,768 B** |
| perl.wasm, copy-on-write off (control) | 1.53–1.96 ms | 1,351 | none |
| image, copy-on-write off (control) | 2.20–2.75 ms | 1,981–1,984 | none |

So on Linux wasmtime maps the default image copy-on-write from both sources that Decision 1 names. The restore costs no copy.

Why the default image qualifies (READ: wasmtime v51.0.0 `crates/environ/src/compile/module_environ.rs:1340-1450`, `try_static_init`):
- wasmtime builds a static image when the span from the first to the last initialised byte is under twice the bytes of the segments;
- or when that span is under `memory_guaranteed_dense_image_size`, which is 16 MiB by default (`crates/wasmtime/src/config.rs:303`).
- Here the span is 8.2 MB and the segments hold 8.1 MB.

Amendment A9.

### 4.3 The making's one-time costs (EXECUTED, median of 5 makings, two runs)

| step | cost |
|---|---|
| the reactor's own compile, on the first making of a process | about 1.0 s (EXECUTED once, outside the lock: `harness.make`'s first call, 1.04 s; a runner caches it as it caches perl.wasm) |
| `_initialize` and `prepare` (the prelude's run) | **30.2–30.3 ms**; 150.7 M instructions metered |
| the capture, 16.6 MB through `data_ptr` | 3.4–3.8 ms |
| writing the module (Python, no code decoded) | 37.8–38.3 ms |
| compile, unmetered | **0.85–0.88 s** |
| compile, metered | 1.18–1.24 s |
| serialise | 5–11 ms |
| `.cwasm` size, per flavour | **17,579,152 B**, against 14,879,336 B for perl.wasm |

## 5. Item 4: the security reading

```
uv run python context/research/2026-11-24-preload-image-prototype/item4_security.py …/results/item4.json
```

### 5.1 Byte-identical sections (EXECUTED)

The test takes one making with an env marker and a granted file, and compares the SHA-256 of each section's payload in the reactor (ca0327c6…) and in the image module.

| section | reactor | image module | identical |
|---|---|---|---|
| type, import, function, table, global, tag, element, code | 1 each | 1 each | **yes, all eight** |
| memory | 1 | 1 | no: initial pages only |
| export | 1 | 1 | no: `_initialize` and `prepare` dropped |
| data | 1 | 1 | no: the image |
| data count | 1 | 0 | dropped |
| custom | 8 (DWARF, producers, target features) | 0 | dropped |

So Decision 1's central claim holds: the engine compiles no code that `SHA256SUMS` does not pin.
- The image is data only. Its segments' offsets and lengths are the writer's, from block numbers. No length comes from the guest.
- A tampered image, or a hostile prelude, can change only the guest's memory. That is the power a guest already has inside one call (INFERRED).

### 5.2 What the argument misses

1. **What the image holds is wider than the spec says** (EXECUTED: `results/item4.json`, `image_holds`).
   - The making's env value is in the image **twice**: once in `%ENV`, and once in the environment block that `__wasilibc_deinitialize_environ` freed but did not zero.
   - The granted file's text is there because the prelude kept it.
   - No host path of the grant is there, and no build-host path.
   - Decision 5 says the values "stay in the image", but freed memory is not erased. Anything the prelude read, even what it dropped, can be in the image and in the `.cwasm`. Amendment A10.
2. **The image is as trusted as its prelude.**
   - A prelude cannot escape the sandbox. But it decides every later call's Perl: it can redefine `CORE::GLOBAL::open`, queue END blocks, or set `$,` and `$\` (§3.4).
   - Every call from the image runs whatever it left (INFERRED; the separators EXECUTED).
   - The spec documents the seed, but not that `make_snapshot` must be given code the embedder would run in every call. Amendment A11.
3. **The working folder was kept**, against Decision 4's table (§3.4). It cannot escape a grant, but it changes what a relative path names. Amendment A4.
4. **Decision 1's dead check.** The reactor's memory has no maximum, since wasm-ld writes none (EXECUTED: `item1.json`, `memory_max: null`). So "an image past the maximum raises SnapshotMismatch" never triggers today. The store's `memory_bytes` limit is the only bound. Amendment A12.

## 6. What spec 022 should change

Each amendment names its evidence.

- **A1 (Decision 2, step 4):** `CLDFLAGS` must also carry `-Wl,--wrap=__wasi_clock_time_get`.
  - Decision 3's step 2 pins the clocks through that wrap, but Decision 2 names only "Decision 3's `--wrap` flags", and those are Perl's six.
  - Proposed text: "…plus `-mexec-model=reactor`, the six Perl wraps of Decision 3, and `--wrap=__wasi_clock_time_get` for its clock pin."
- **A2 (Decisions 2 and 3, the reactor's compile):** compile `recipes/wasi-reactor.c` with `-fmacro-prefix-map=<build/target>/=` and `-fmacro-prefix-map=<recipes>/=`.
  - Otherwise `perlmain.c`'s `__FILE__` puts the build host's absolute path in `perl-reactor.wasm`, which ships. The reactor would then not be reproducible across folders (EXECUTED: 1 occurrence, then 0; §2.2).
  - Add to reactor check 1: the reactor holds no byte string of the build root.
- **A3 (Decision 3, steps 5–6):** unpin the clocks and flush PerlIO *before* returning a non-zero status, not after.
  - Otherwise the text that `SnapshotFailed("exit")` quotes can be lost while still buffered.
  - The prototype does this, and the `die` making's message arrived (EXECUTED).
  - Proposed: swap the two steps, and add C stdio's `fflush(NULL)` beside `PerlIO_flush` (INFERRED: nothing in the prototype wrote through C stdio).
- **A4 (Decision 3, step 8; Decision 4, the "working folder" row):** step 8 also sets wasi-libc's working folder back to `/`.
  - In the prototype that is `__wasilibc_cwd` replaced by an allocated `"/"`, with the old copy freed.
  - `run`'s `chdir($PWD)` cannot do it: `chdir("/")` fails, since no grant serves `/` (EXECUTED, §3.4).
  - Name the coupling to wasi-libc's `chdir.c`/`getcwd.c` beside the coupling to `perl.c`, and pin it the same way: `wasi-reactor.c` checks the wasi-sdk version, as it checks Perl's.
  - Add a reset check: a making with `cwd="/data"`, then a call with no `cwd`, prints `/` for `Cwd::getcwd`.
- **A5 (Decision 4, a new row: errno at the second parse):** `run` sets errno to what `perl_construct` left, then asks `isatty` and `lseek` of descriptors 0–2, before `perl_parse`.
  - perl.wasm's lazy opening of its standard handles asks the same two questions. Without this, a croak during switch parsing exits 2 or 255 where perl.wasm exits 29 (EXECUTED, §3.3).
  - Add `bad_switch` (`-Q -e 1`) and `no_code_e` (`-e`) to the drift suite, both expected equal.
- **A6 (Decision 4, a new row: punctuation variables):** `$@ $! $? $/` and `select` are reset by the parse, but `$,`, `$\` and `$_` keep the prelude's values (EXECUTED, §3.4).
  - Proposed: `run` sets `$,`, `$\` and `$_` to undef, and `$"` and `$;` to Perl's defaults, before the parse.
  - Add a drift case with a prelude that sets all of them, expected equal.
  - If the owner prefers them frozen, as package variables, then `docs/snapshots.md`'s "What to avoid in the code" must name them. Either way the default package image is unaffected (EXECUTED).
- **A7 (Decision 3, the wrappers' guard):** make the rule "both wrappers have acted" explicit for a parse that returned 0 only.
  - A parse that ends early (`-Q`, `-v`) never reaches `boot_core_PerlIO`. Aborting there would trap, where perl.wasm exits 29, or 0 for `-v`.
  - The prototype aborts only after a parse that returned 0. Both cases are equal (EXECUTED).
- **A8 (Decision 1, the table and the platform table):** replace the Linux INFERRED figures with this memo's EXECUTED ones:
  - restore 0.006 ms;
  - "five" 4.2–5.2 ms against 21.4–24.7 ms;
  - `-MPOSIX` 4.1 against 13.0 ms;
  - `-e 1` +0.43–0.48 ms and 292–300 against 81 faults;
  - the compile, 0.85–0.88 s unmetered and 1.18–1.24 s metered.

  Add to "When it compiles" the reactor's own compile, about 1.0 s on a cold cache, cached as perl.wasm is. Note that a cache holding both flavours of one image holds about 35 MB.
- **A9 (Decision 1, "If wasmtime declines copy-on-write"):** the trigger is sparseness, not size.
  - wasmtime 51 maps an image whose initialised span is under 2× its segment bytes, at any size, or under 16 MiB at any density (READ, §4.2).
  - The writer drops zero blocks, so a heap as dense as Perl's stays mapped. The default image has a span of 8.2 MB and 8.1 MB of segments.
  - Acceptance 4 can use this memo's method as its evidence: `/proc/self/maps` gaining `memfd:wasm-memory-image` or `memfd:wasi-perl-module`, and 0 faults at instantiation.
- **A10 (Decisions 5 and 7, docs):** say "freed is not erased".
  - The env value was found twice in the image (EXECUTED, §5.2).
  - Proposed: `prepare` zeroes the environment's strings before `__wasilibc_deinitialize_environ`, and `docs/snapshots.md` says that anything the prelude read can stay in the image and its `.cwasm`, even after it was dropped.
- **A11 (Decision 1's security paragraph and `docs/sandbox.md`):** add the Perl-level trust point.
  - The image runs its prelude's leftovers in every call. So `make_snapshot` is to be given only code the embedder would run in every call, never a program from the party the sandbox guards against.
- **A12 (Decision 1, step 4):** say that today's reactor has no maximum, so the image's only bound is the store's `memory_bytes`. That is `SnapshotMismatch`'s second case. Keep the check for a reactor that gains one.
- **A13 (Decision 1, step 2, wording):** "it walks the section headers; it does not decode the code" undersells the work. The form check decodes:
  - the import, memory, global and export sections;
  - every data segment's header.

  Proposed: "it decodes every section but the code, which it copies as bytes".

Two minor confirmations need no change. The fd table reset also forgets `devdir` and `libdir`, as Decision 3 says. `wasi_perl_open_files` counts a left-open handle (EXECUTED).

## 7. Limits

- One shared x86-64 Linux machine. Times are medians, alternating perl.wasm and the image within each run, and the two runs differ by up to 15%. Instruction counts and page faults are the primary evidence.
- macOS and Windows were not run. Decision 1's rows for them stay INFERRED.
- The harness calls `wasi_perl.run` with two module-lookup functions patched. It is not the spec's `_snapshot.py`, and it has no `Snapshot` file, cache key or `make_package_snapshot`.
- Not tried:
  - spec 017's mounts;
  - the probe linker of seed check 2;
  - eight threads on one image;
  - Perl's own `t/` on the image;
  - a prelude that reads stdin (reset check 5);
  - `PERL_JSON_PP_USE_B`;
  - the 50,000-segment striped image (the writer's coalescer exists and is unexercised here).
- The fd-table functions sit in a separate object that includes the recipe, not in the recipe.

## 8. To reproduce

From the worktree root, in this order (the build and item 3 under the lock):

```
WASI_PERL_JOBS=4 flock /home/user/wasi-perl-wt/machine.lock context/research/2026-11-24-preload-image-prototype/build.sh
P=context/research/2026-11-24-preload-image-prototype
uv run python $P/item1_form.py $P/results/item1.json
uv run python $P/item2_semantics.py $P/results/item2.json
uv run python $P/item2_resets.py $P/results/item2-resets.json
WASI_PERL_JOBS=4 flock /home/user/wasi-perl-wt/machine.lock uv run python $P/item3_speed.py $P/results/item3.json 60
uv run python $P/item4_security.py $P/results/item4.json
```

`results/binaries.txt` lists the hash, size and deflate size of each binary that `build.sh` writes, with `sha256sum`, `wc -c` and `gzip -6 -c | wc -c`. Its control row equals `build/link/perl.wasm`. Image hashes differ on every making, by design (§3.2).
