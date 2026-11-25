# The preload image, measured as built

- **Status:** research, 2026-11-25. The acceptance memo that spec 022 asks for (its Acceptance 4), on the branch `spec/022-a-preload-image`, measured on the final build.
- **Researcher:** an agent (Claude Opus 5.5).
- **Built on:** the branch at `05a4653` (dev at 388ddd3, with spec 017, merged in), after a clean in-place rebuild of steps 00–40 under the machine lock with `WASI_PERL_JOBS=4`. wasmtime-py 51.0.0.
- **Evidence:** `2026-11-24-the-preload-image-measured/`: `measure.py` (the runner's own API, under the lock), `on_image.py` (Perl's `t/` from an image), `oneliners_on_image.py` (the one-liner corpus from an image), and `results/`: `run1.json` and `run2.json`, `binaries.txt`, `wheel.txt`, `perl_t.txt`.
- **Prior figures:** the prototype memo, `2026-11-24-preload-image-prototype.md`, measured the same things through a harness that patched the runner. Where its figures still hold on the final code, this memo says so.

Labels: EXECUTED means run and seen. READ means read from a file. INFERRED means reasoned, not run.

## 0. In one table

| | perl.wasm | from the package image | prototype (§4.1 there) |
|---|---|---|---|
| "five", one call | 22.8–24.2 ms | **4.9–5.3 ms** | 21.4–24.7 → 4.21–5.22 ms |
| `-MPOSIX -e 1` | 12.9–14.5 ms | **4.3–5.1 ms** | 13.0–13.2 → 4.11–4.19 ms |
| `-e 1` | 1.0–1.2 ms | **1.65–1.96 ms** | 1.04–1.20 → 1.48–1.68 ms |
| "five", fuel | 119.9–120.7 M | **17.8–18.2 M** | 120.3–120.7 → 18.3–18.5 M |
| `-MPOSIX -e 1`, fuel | 70.5–70.6 M | **17.2–17.4 M** | 70.1–70.9 → 17.1–17.5 M |
| `-e 1`, fuel | 1.56 M | **1.28 M** | 1.56 → 1.27 M |
| minor faults, "five" / `-MPOSIX` / `-e 1` | 569 / 368 / 81 | 461–475 / 450–464 / 291–299 | 569 → 470–480; 368 → 459–469; 81 → 292–300 |

"five" is `use strict; use warnings; use POSIX; use JSON::PP; use List::Util; 1`. The prototype's figures hold on the final code: the A6 and A10 resets, and the emptying of `%ENV`, add nothing measurable.

## 1. Whole calls (EXECUTED)

```
WASI_PERL_JOBS=4 flock /home/user/wasi-perl-wt/machine.lock uv run python \
    context/research/2026-11-24-the-preload-image-measured/measure.py \
    context/research/2026-11-24-the-preload-image-measured/results/run1.json 60
```

The same command wrote `run2.json`. Each run alternates perl.wasm and the image call by call through `wasi_perl.run`, 60 of each after two warm-up calls, and takes medians. Faults are the process's minor faults across one call (`getrusage`). The machine is shared; the ranges are the two runs.

| program | in process: perl.wasm → image | from a `.cwasm` cache: perl.wasm → image |
|---|---|---|
| "five" | 24.16 → 5.31; 22.76 → 4.86 ms | 24.27 → 5.36; 22.94 → 4.87 ms |
| `-MPOSIX -e 1` | 12.86 → 4.30; 13.48 → 4.62 ms | 14.53 → 5.13; 13.37 → 4.66 ms |
| `-e 1` | 1.02 → 1.65; 1.23 → 1.96 ms | 0.99 → 1.68; 1.15 → 1.91 ms |

- The cache columns load both modules from `.cwasm` files through spec 014's sealed `memfd`: the counts across those calls are 2 loaded, 0 compiled, 0 written (`cwasm_counts` in both files).
- **The one figure for a bare one-liner:** from the image, `-e 1` costs **0.64–0.76 ms more** than from perl.wasm (the four medians above: 0.64, 0.69, 0.73, 0.76), and about 215 more faults. `docs/snapshots.md` and the changelog quote this figure. `docs/snapshots.md`'s rule, pass the image only to calls that load modules, stands.

## 2. Copy-on-write on Linux (EXECUTED, the same runs)

Instantiation alone, from the runner's own `InstancePre`, median of 30. "Mapped" is what `/proc/self/maps` gained while the first instance lived.

| module | instantiate | faults | mapped |
|---|---|---|---|
| perl.wasm, in process | 0.0075–0.0080 ms | 0 | `memfd:wasm-memory-image`, 5,533,696 B |
| image, in process | 0.0071 ms | 0 | `memfd:wasm-memory-image`, 8,224,768 B |
| perl.wasm, from `.cwasm` | 0.0063–0.0072 ms | 0 | `memfd:wasi-perl-module`, 5,533,696 B |
| image, from `.cwasm` | 0.0062–0.0066 ms | 0 | `memfd:wasi-perl-module`, 8,224,768 B |

wasmtime maps the default image copy-on-write from both sources spec 022's Decision 1 names, as the prototype found (its §4.2). The image's segments are 10–14 runs holding 8.1 MB, inside a span of 8.2 MB, so it qualifies by density (spec 022's amendment A9).

## 3. The making's one-time costs (EXECUTED, median of five makings, two runs)

| step | cost |
|---|---|
| the reactor's own compile, a process's first making | 0.99–1.58 s |
| the reactor's `_initialize` and `prepare` (the prelude) | 28–32 ms; 150.7 M instructions metered |
| the capture, 16,580,608 bytes | 3.8–5.8 ms |
| the module writer (`_image_module.write`) | 47–52 ms |
| the image module's compile, unmetered | 0.97–1.56 s |
| the image module's compile, metered | 1.23–2.08 s |
| the whole `make_package_snapshot()`, reactor compiled already | 2.1–3.2 s |

- The image is 253 pages; the written module is 10.9 MB.
- **The `.cwasm` sizes**: 17,583,288 bytes for the image module unmetered, 19,598,704 metered, against 14,879,336 for perl.wasm. A cache that holds both flavours of one image holds 37.2 MB.
- The compile is most of the making, and the first making also pays the reactor's. Run 1's compiles were about 1.6× run 2's: the machine was busier then.

## 4. The wheel and the binaries (EXECUTED)

```
uv run --no-project python -I scripts/assemble_artifacts.py
uv build --wheel --out-dir <scratch>/dist
mv src/wasi_perl/artifacts/perl-reactor.wasm <aside>; uv build --wheel --out-dir <scratch>/dist-without
```

| | bytes |
|---|---|
| the wheel without `perl-reactor.wasm` | 10,068,237 |
| the wheel with it | 13,298,264 |
| **growth** | **3,230,027 (+32.1%)** |
| `perl-reactor.wasm`'s entry in the wheel, deflated | 3,229,822 |
| `perl-reactor.wasm`, after `-O3` / as linked | 8,677,592 / 9,447,212 |
| `perl.wasm`, after `-O3` / as linked | 8,675,094 / 9,443,860 |

`results/wheel.txt` and `results/binaries.txt` hold the hashes. The prototype's estimate of the growth, 3.25 MB from deflate −6, holds.

## 5. The suites from the images (EXECUTED)

**Perl's own `t/`:**

```
uv run python context/research/2026-11-24-the-preload-image-measured/on_image.py empty perl_t \
    --quiet --list-failures --jobs 4 --results build/perl_t/results-image-empty.json
```

and the same with `packages`. Every one of the 616 files started from the image (the driver counts the calls whose `image_sha256` is the image's: 616 each).

| start | scoreboard |
|---|---|
| perl.wasm | 616 run, 439 passed, 177 excluded, 0 failed, 0 stale, 0 regressed |
| `make_snapshot("")` | the same, exactly |
| `make_package_snapshot()` | the same, except 1 regressed: `lib/deprecate.t` |

- `lib/deprecate.t` is excluded already ("bug": all 10 tests pass, then an END block's File::Path cannot load Carp). From the package image it fails the same way, exit 2 after the same 10 oks, but with another message: Carp is already loaded, so File::Path goes further and stops at a `chmod` WASI lacks ("cannot make directory read+writeable … Function not implemented"). The entry's `shows` pattern then does not match. It is the accepted drift `inc_keys`: a module the image loaded is loaded in every call.
- No file fails only from an image, so `tests/perl_t/exclusions-image.toml` is not needed, and `tests/perl_t/exclusions.toml` is not touched.

**The one-liner corpus:**

```
uv run python context/research/2026-11-24-the-preload-image-measured/oneliners_on_image.py empty
uv run python context/research/2026-11-24-the-preload-image-measured/oneliners_on_image.py packages
```

The oracle's own tests set `PERL_HASH_SEED=0` and `PERL_PERTURB_KEYS=0`, so a call there runs perl.wasm whatever image it is given (Decision 6): run unchanged, the oracle never starts from an image. So the driver runs each of the 226 cases with the corpus's environment minus those two names, twice from perl.wasm and once from the image, and compares exit, output, trap and files.

| image | equal | differ | hash-order dependent (not compared) |
|---|---|---|---|
| `make_snapshot("")` | 226 | 0 | 0 |
| `make_package_snapshot()` | 226 | 0 | 0 |

## 6. macOS and Windows

The memo's driver reads `/proc/self/maps` and was not run on CI. So the macOS and Windows rows of spec 022's Decision 1 stay INFERRED: there wasmtime has no file to map, and each call writes the image's 8.1 MB of data into fresh memory (READ: wasmtime v51.0.0, `cow.rs`, as the spec cites). The image's checks pass on CI's macos-15 and windows-2025 legs, which is behaviour, not speed (EXECUTED: PR #32's runs).

## 7. Limits

- One shared x86-64 Linux machine. Times are medians of alternated calls; the two runs differ by up to 60% on the compiles and 15% on the calls. Instruction counts and faults are the primary evidence.
- **The build is not reproducible across full rebuilds of step 20 here.** `perl.wasm`'s SHA-256 changed between rebuilds whose only changed input was `recipes/wasi-reactor.c`, which `perl.wasm` does not link: `3493714…` and then `fa24c9d…`, and after the final clean rebuild from an empty `build/`, `5556f69…` (EXECUTED: `sha256sum` after each). A relink alone, as 021's review found, gives the same bytes.
  - *The cause (added 2026-11-25, from the review, L2):* `perl.c` builds `PERL_BUILD_DATE` from `__DATE__` and `__TIME__` (READ: `build/target/perl.c:2117-2119`), which `perl -V` prints as "Compiled at". Both modules of the final build hold `Compiled at Nov 25 2026 00:21:50` (EXECUTED: `grep -a -o "Compiled at [A-Za-z0-9 :]*" build/artifacts/perl.wasm build/artifacts/perl-reactor.wasm`). So every recompile of `perl.c` changes both modules, on dev too, whatever else changed. `SOURCE_DATE_EPOCH`, which clang honours for these macros, or a recipe define, is a roadmap item.
- The default image's data is 8.1 MB here and 8.1 MB in the prototype; another prelude may be sparser and may not be mapped (amendment A9).
