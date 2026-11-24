# Study C: the interpreter's configuration and hot paths on WebAssembly

- **Status:** research, 2026-11-24. Study C of the speed research phase (`2026-11-24-perf-research-plan.md`). It changes nothing in `src/`, `recipes/` or `scripts/`; the prototypes and their diffs are evidence.
- **Researcher:** an agent (Claude Opus 5.5).
- **The question:** which Perl build choices and which hot functions cost the most on WebAssembly, and what would remove that cost, keeping every behaviour `tests/oneliners` and `tests/perl_t` check?
- **Evidence:** the folder `2026-11-24-perf-interp/`. Its scripts, the profile tables (`profiles/`), the raw results (`results/`) and the diffs to `recipes/` (`recipes-diffs/`).

Labels: EXECUTED means run and seen. READ means read from a file. INFERRED means the researcher's reasoning. "Fuel" is wasmtime's count of executed WebAssembly instructions: a count, the same on any machine.

## 1. The answer in brief

The guest's time on line-oriented text goes to three places. Only the first two are particular to WebAssembly.

1. **The host's cost of each WASI call, paid per 8 KiB.** Perl reads and writes files in 8 KiB pieces. Under wasmtime-py each `fd_read` costs about 15 to 50 µs of host time, so a 10 MB file costs 1,248 reads and about 40 ms. The guest's instructions do not show it: fuel hardly moves. A 64 KiB PerlIO buffer cuts the calls by 8× and the wall time of the one-liners over large files by 23 to 44%.
2. **wasi-libc's `memchr`**, which compares four bytes a step. Perl calls it once per line read and once per candidate in a substring search. A sixteen-byte SIMD `memchr` cuts 10 to 20% of the instructions of the text one-liners.
3. **The interpreter itself**, whose profile under WebAssembly is native perl's profile: the same functions in the same order. What is left there is code generation (study B) or Perl's own algorithms. `NO_TAINT_SUPPORT` is the only build choice that buys a measurable 1 to 1.6%, and it removes a feature.

A third lever is in our own wrappers: Perl asks `isatty` of every file it opens, and that host call costs about 60 µs. The fd-table can answer it for a file it has already looked at.

The recommendation is all three: `recipes-diffs/buffer-and-memchr.diff` and `recipes-diffs/fd-table-isatty.diff`. Together they make the text one-liners 20 to 53% faster than the base, and an agent's grep over 200 small files 25% faster (EXECUTED: section 4):

| ms per call | base | 64 KiB buffer, SIMD `memchr`, `isatty` |
|---|---:|---:|
| sum_csv | 146 | 117 |
| grep_log | 111 | 66 |
| inplace | 203 | 112 |
| count_lines | 78 | 36 |
| many_files | 67 | 50 |
| loop | 75 | 74 |

The native perl's times for the first three, from the baseline, are 58, 26 and 41 ms. So the guest goes from 2.5×, 4.3× and 5.0× native to 2.0×, 2.5× and 2.7× (INFERRED: the ratio of the two measurements, made on the same machine under different load).

## 2. How it was measured

**The base.** The base build of the `perf-base` worktree, the same sources as this branch's base (`32dfb73`), copied with `cp -a`. Every variant below is linked from that build's objects, so a variant differs from the base only in what it names. The base relinked by `relink.sh` gives the same fuel as the shipped `perl.wasm` within 0.3% (EXECUTED: `measure.py fuel`, `results/fuel-relink.json`; the remaining difference is the hash seed, below).

**The workloads** are the baseline's six (`2026-11-24-wasi-perl-against-wasi-python-on-speed/bench.py`), with three more that isolate line I/O, in `measure.py`:

| workload | program |
|---|---|
| `e1` | `-e 1`: startup alone |
| `count_lines` | `-ne 'END { print $. }'` over the 10.2 MB log: line reading alone |
| `cat_log` | `-ne print` over the log: reading and writing every line |
| `many_files` | `-ne '$n++ if /ERROR/; END { print $n }'` over 200 files of 50 lines (about 2.5 KB each): an agent's grep over a folder |

**Fuel** runs through the `wasi_perl` runner with `fuel=10**13` and `PERL_HASH_SEED=0`. Without a fixed seed, `sum_csv` varies by 0.3% between calls, because the hash order changes the work (EXECUTED: two base runs, 1,269 M and 1,272 M). With it, repeated runs agree within 0.02%. Every call's output is checked against the expected answer.

**Wall time** is the median of 5 to 30 calls in one warm process, under `flock machine.lock`. The machine is shared by six studies, so two runs of one binary differ by up to 15% (EXECUTED: `grep_log` 101.9 and 121.2 ms for the same base, `time2.out`). Wall-time claims below rest on three interleaved rounds (`results/time3-*.json`) and are given as ranges.

**Profiles.** `wasmtime run --profile=guest,<file>,100us` (wasmtime 51 CLI), with the runner's preopens (`wt.sh`). The CLI writes a profile only when `_start` returns, and perl always leaves through `exit()`, so profiling used a relinked `perl.wasm` whose `exit` jumps back into `main` (`prof-exit.c`, never shipped). Natively, `valgrind --tool=callgrind` on the base build's native perl 5.42.3 (`native.sh`). Tables: `profiles/guest-top.md` and `profiles/native-callgrind-top.md`.

A caution on reading the guest profile (INFERRED from wasmtime's design and confirmed by the callers below): its sampler fires at epoch checks, which sit at function entries and loop heads. So a sample lands in the first such check *after* the time passed. Two consequences:

- `Perl_runops_standard` shows 17 to 50% "self" time: it is the loop that calls every op.
- Time spent in the host during a WASI call is charged to the next function the guest enters after the call returns. That is how the host's cost of `fd_read` and `fd_write` shows up below.

**WASI calls** were counted from the CLI's trace, `WASMTIME_LOG=wasmtime_wasi=trace` (`wasicount.py`). Each call's arguments and result are one line each. **The host's cost per call** was timed in two ways: a C program that calls each WASI function 5,000 times under wasmtime-py with the runner's preopens and times itself (`proto/wasicost.c`, `wasicost.py`), and Perl loops that differ by one call per iteration (`callcost.py`).

## 3. Where the time goes

### 3.1 Guest against native: the same interpreter

The guest runs 1.6 to 1.9× as many WebAssembly instructions as native perl runs x86-64 instructions (EXECUTED: fuel against callgrind's `Ir`):

| workload | native x86-64 instructions | guest instructions | ratio |
|---|---:|---:|---:|
| sum_csv | 771 M | 1,269 M | 1.65 |
| grep_log | 354 M | 644 M | 1.82 |
| inplace | 438 M | 843 M | 1.92 |
| loop | 336 M | 604 M | 1.80 |
| json | 6,865 M | 11,136 M | 1.62 |
| hello | 1.9 M | 1.6 M | 0.84 |

The top functions agree. On `loop`, both are `pp_iter`, `pp_gvsv`, `pp_multiply`, `pp_add`, `pp_unstack`, `pp_padsv` around the runops loop. On `sum_csv` both are `pp_split`, `sv_clear`, `newSV_type`, the allocator, `sv_gets`, `newSVpvn_flags`. On `json` both are the ops of JSON::PP's Perl code and the regex engine. So the guest has no interpreter-level hot spot of its own on those three. The general 1.6 to 1.9× is code generation, which is study B's.

### 3.2 What is hot only under WebAssembly

On the text workloads, two things are hot in the guest and not natively: `memchr`, and the host's side of `fd_read` and `fd_write` (EXECUTED: `profsum.py`, `profsum.py callers`):

| workload | function | guest, % of samples | native, % of instructions | what it is |
|---|---|---:|---:|---|
| grep_log | `memchr` | 18.4 | 5.8 (AVX2) | wasi-libc's `memchr`, called by `sv_gets` (69%) and `fbm_instr` (31%) |
| inplace | `memchr` | 10.3 | 3.1 | the same |
| sum_csv | `memchr` | 3.4 | 0.5 | the same |
| grep_log | `PerlIOBase_read` | 6.3 | under 0.1 | the host's `fd_read`, charged to the first loop after it returns |
| inplace | `PerlIOBase_noop_ok` | 6.6 | under 0.1 | the host's `fd_write`: 99% of its samples are under `PerlIOBuf_flush < PerlIOBuf_write`, and it is the first function entered after the write returns |
| inplace | `PerlIOBase_read` | 4.1 | under 0.1 | the host's `fd_read` |

What is *not* hot, of the candidates the plan named (EXECUTED, the same tables):

- **The fd-table wrappers**: `__wrap___wasi_*` and `is_open` are each under 0.2% of samples, on every workload. They add host *calls* (section 3.4), not guest time.
- **Locale code**: absent from every table but startup's, where all of `e1` is 1.6 M instructions, about 0.2 ms.
- **`memcpy`, `memmove`, `memset`**: the module has bulk memory (READ: its `target_features` section), so these compile to `memory.copy` and `memory.fill` and do not appear.
- **setjmp**: `__wasm_setjmp_test` is at most 0.1%.
- **The allocator**: dlmalloc's `malloc` and `free` are 1.5 to 3% of `sum_csv`'s samples. Natively glibc's are 13% of its instructions. The guest's allocator is no worse than native.

### 3.3 Why `memchr` is slow here

wasi-libc's `memchr` is musl's: it aligns byte by byte, then tests one `size_t` word a step for the byte (READ: `llvm-objdump -d --disassemble-symbols=memchr perl.wasm`, no `v128` instruction). On wasm32 a `size_t` is four bytes. Perl calls it with the rest of the 8 KiB buffer and stops at the line's end, so each call scans about 50 bytes in 13 steps. glibc's AVX2 version scans 32 bytes a step.

### 3.4 The WASI calls each workload makes

Counted from the trace (EXECUTED: `wasicount.py`, `results/wasi-counts.md` and `results/wasi-calls-base-*.txt`), with the 8 KiB buffer of the base and with larger ones:

| workload | buffer | total WASI calls | fd_read | fd_write | other |
|---|---:|---:|---:|---:|---:|
| hello | 8 KiB | 42 | 0 | 1 | 41 |
| sum_csv | 8 KiB | 609 | 560 | 1 | 48 |
| sum_csv | 64 KiB | 120 | 71 | 1 | 48 |
| sum_csv | 256 KiB | 68 | 19 | 1 | 48 |
| grep_log | 8 KiB | 1297 | 1248 | 1 | 48 |
| grep_log | 64 KiB | 206 | 157 | 1 | 48 |
| grep_log | 256 KiB | 89 | 40 | 1 | 48 |
| inplace | 8 KiB | 2548 | 1248 | 1244 | 56 |
| inplace | 64 KiB | 369 | 157 | 156 | 56 |
| inplace | 256 KiB | 135 | 40 | 39 | 56 |
| json | 8 KiB | 465 | 196 | 1 | 268 |
| json | 64 KiB | 306 | 37 | 1 | 268 |
| json | 256 KiB | 291 | 22 | 1 | 268 |
| loop | 8 KiB | 42 | 0 | 1 | 41 |
| many_files | 8 KiB | 1,838 | 400 | 1 | 1,437 |
| many_files | 64 KiB | 1,838 | 400 | 1 | 1,437 |

`many_files` reads 2.5 KB files, so every buffer size makes the same two reads per file (EXECUTED: the trace for 8 KiB; INFERRED for 64 KiB from the identical fuel and the `PerlIOBuf_fill` code).

Where the fixed calls of every program come from (READ: the call order in `results/wasi-seq-base-json.txt`, against `recipes/wasi-fd-table.c`, wasi-libc's preopen scan and `perlio.c`):

| calls | count | made by |
|---|---:|---|
| `fd_fdstat_get` on 0 to 7, `fd_prestat_get` on 3 to 6 | 8 + 4 | **our fd-table**, `start()`, scanning the host's descriptors on first use |
| `fd_prestat_get`, `fd_prestat_dir_name` on each grant | 4 + 4 | wasi-libc's preopen scan |
| `fd_fdstat_get` and `fd_tell` on 0, 1, 2 | 3 + 3 | Perl: `isatty` and `tell` as PerlIO pushes its layers on the standard handles |
| `clock_time_get`, `environ_*`, `args_*` | 7 | wasi-libc and Perl's startup (the hash seed, `$^T`) |

And for each file opened for reading, 9 calls (READ: the same sequence, `results/wasi-seq-base-many-head.txt`):

| call | made by | host cost, µs (section 3.5) |
|---|---|---:|
| `fd_fdstat_get` on the grant | wasi-libc's `open`, for the folder's inheritable rights | 0.2 |
| `path_filestat_get` | **our fd-table**: the look that refuses a special file before the host could wait in it | 61.6 |
| `path_open` | wasi-libc | 31.8 (with the close) |
| `fd_fdstat_get` on the file | Perl: `isatty` in `PerlIOBuf_pushed` | 60.5 |
| `fd_tell` | Perl: `PerlIOBuf_pushed` takes the position | 0.05 |
| `fd_filestat_get` | Perl: `fstat` after the open in `doio.c` | 57.4 |
| `fd_read` × 2 | Perl: the data, then the end of file | 31.5 + 54.8 |
| `fd_close` | Perl | (with open) |

Each `require` adds 8 failed or found `path_filestat_get` calls before its open: 4 `@INC` folders, and in each a `.pmc` before the `.pm` (READ: `results/wasi-seq-base-json.txt`, 7 `Noent` then a hit, per module; perl `pp_ctl.c`). JSON::PP and the modules it loads make 124 of them.

### 3.5 What each call costs the host

From inside the guest, 5,000 calls of each kind (625 to 1,250 for the large reads), under the lock (EXECUTED: `wasicost.py proto/wasicost.wasm … 5000`, `results/wasicost.txt`), in µs per call:

| call | µs | | call | µs |
|---|---:|---|---|---:|
| `fd_fdstat_get`, a regular file | **60.5** | | `fd_fdstat_get`, a folder or stdout | 0.2 |
| `fd_filestat_get` | **57.4** | | `fd_tell` | 0.05 |
| `path_filestat_get`, found | **61.6** | | `fd_seek` | 0.2 |
| `path_filestat_get`, missing | 30.7 | | `fd_prestat_get` | 0.06 |
| `fd_read` 1 B | 29.2 | | `clock_time_get` | 0.1 |
| `fd_read` 8 KiB | 31.5 | | `fd_write` 1 B to stdout | 0.6 |
| `fd_read` 64 KiB | 45.7 | | `path_open` + `fd_close` | 31.8 |
| `fd_read` 256 KiB | 83.8 | | | |
| `fd_read` at end of file | 54.8 | | | |

An earlier, shorter run outside the lock (200 calls each; EXECUTED, printed and not kept) gave a half to three quarters of each large number: `fd_fdstat_get` on a file 43.8, `fd_filestat_get` 27.0, `path_filestat_get` found 27.9, `fd_read` 1 B 13.3, 8 KiB 14.7, 64 KiB 21.7, 256 KiB 45.6 µs. So the host's cost per call moves by up to 2× with the machine's load; the ordering does not.

What this says (INFERRED from the two kinds of numbers): wasmtime-wasi answers a call from memory when it can (positions, a folder's or a pipe's flags, preopens, clocks) in well under a microsecond. A call that must ask the host's file system, a `read`, a `stat`, a file's flags, takes 15 to 60 µs, which is far more than the system call itself. That fits a call handed to a blocking thread and waited for, once per call. The per-call part dominates up to 64 KiB: reading 64 KiB costs 1.5× reading 8 KiB, for 8× the bytes.

The Perl-loop probe agrees (EXECUTED: `callcost.py`, `time2.out`): 27 µs per 1-byte `sysread`, 50 µs per 64 KiB `sysread`, 56 µs per `-e` (a `stat`), 53 µs per `-t` (an `isatty`, so `fd_fdstat_get` on a file), 0.3 µs per `sysseek`, and 193 µs per `open` and `close` of a small file. The guest's own share of each is 200 to 1,700 instructions, about 0.02 to 0.2 µs.

So a file opened and read whole costs about 300 µs of host time in nine calls, before Perl reads a line: the look 62, open and close 32, `isatty` 60, `fstat` 57, the data 31, the end of file 55 (INFERRED: the sum of the rows). `many_files` opens 200 files: that is most of its 55 to 65 ms.

## 4. The candidates, measured

Fuel change against the base, per workload (EXECUTED: `compare.py results …`; `results/fuel-*.json`):

| variant | hello | e1 | sum_csv | grep_log | inplace | count_lines | cat_log | many_files | json | loop |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| base (instructions) | 1.6 M | 1.6 M | 1,269.4 M | 643.8 M | 843.4 M | 304.9 M | 426.9 M | 31.9 M | 11,135.7 M | 603.7 M |
| memchr-simd | -0.0% | -0.0% | -2.4% | -10.5% | -12.8% | -19.6% | -14.0% | -16.2% | -0.0% | -0.0% |
| memchr-swar | +0.5% | +0.3% | -0.2% | -3.0% | -2.9% | -6.4% | -4.6% | -4.0% | -0.0% | +0.0% |
| buf16384 | +0.0% | +0.0% | -0.0% | -0.2% | -0.1% | -0.2% | -0.2% | +0.0% | -0.0% | +0.0% |
| buf65536 | +0.0% | +0.0% | -0.0% | -0.3% | -0.2% | -0.3% | -0.3% | +0.0% | +0.0% | +0.0% |
| buf262144 | +0.0% | +0.0% | -0.0% | -0.4% | -0.2% | -0.3% | -0.3% | +0.0% | +0.0% | +0.0% |
| buf65536-memchr | -0.0% | -0.0% | -2.5% | -10.8% | -13.0% | -19.9% | -14.3% | -16.2% | -0.0% | -0.0% |
| buf65536-memchr-isatty | -0.0% | +0.0% | -2.5% | -10.8% | -13.0% | -19.9% | -14.3% | -16.2% | -0.0% | -0.0% |
| notaint | -0.1% | -0.1% | -1.4% | -1.4% | -1.0% | -1.2% | -1.0% | -1.3% | -1.6% | -0.5% |
| zaphod | -0.0% | -0.0% | -0.0% | -0.0% | -0.0% | -0.0% | -0.0% | -0.0% | -0.0% | -0.0% |

Wall time, median ms per call, three interleaved rounds (EXECUTED: `time3.sh`, `results/time3-*.json`):

| variant | hello | sum_csv | grep_log | inplace | count_lines | many_files | loop |
|---|---:|---:|---:|---:|---:|---:|---:|
| relink (3 rounds) | 3.5 (3–4) | 146.0 (139–149) | 110.8 (109–129) | 202.7 (198–230) | 77.6 (76–80) | 66.8 (64–72) | 74.5 (70–76) |
| buf65536 (3 rounds) | 3.9 (3–4) | 112.0 (111–118) | 71.6 (70–73) | 119.3 (118–128) | 43.4 (38–48) | 71.3 (59–105) | 74.8 (71–77) |
| memchr-simd (3 rounds) | 3.4 (3–4) | 134.0 (132–142) | 104.3 (96–110) | 200.1 (176–202) | 77.6 (64–79) | 67.9 (58–69) | 71.0 (71–75) |
| buf65536-memchr (3 rounds) | 3.9 (4–4) | 116.0 (116–117) | 68.1 (62–69) | 114.2 (106–117) | 36.3 (34–39) | 63.0 (63–73) | 74.6 (71–75) |
| buf65536-memchr-isatty (3 rounds) | 3.0 (3–3) | 116.8 (110–118) | 66.0 (62–72) | 112.2 (107–117) | 36.4 (35–37) | 50.4 (46–53) | 74.3 (71–77) |

Each cell is the median of the three rounds' medians, with the lowest and highest round in brackets. `relink` is the base. The single runs before (`time2.out`) agree, and add 256 KiB: `grep_log` 67.4 ms, `inplace` 102.5 ms, `count_lines` 33.5 ms, but `many_files` 65.6 ms (and 55.9, 104.7, 31.7 and 66.8 ms with the SIMD `memchr`).

### 4.1 A larger PerlIO buffer (`PERLIOBUF_DEFAULT_BUFSIZ`)

- **What:** `perlio.h` sets the buffer of every `:perlio` handle to `BUFSIZ > 8192 ? BUFSIZ : 8192`, under `#ifndef` (READ: `perlio.h` line 152). wasi-libc's `BUFSIZ` is 1,024 (READ: `stdio.h`), so Perl reads and writes 8 KiB at a time. Only `perlio.c` uses the macro, so a `-D` in `ccflags` sets it.
- **Fuel:** unchanged, at most −0.4% (the 8× fewer refills).
- **WASI calls:** `fd_read` and `fd_write` fall by 8× at 64 KiB and 32× at 256 KiB, on every file larger than the buffer.
- **Wall time:** −23% `sum_csv` (146 → 112 ms), −35% `grep_log` (111 → 72), −41% `inplace` (203 → 119), −44% `count_lines` (78 → 43). `many_files` and `loop` do not change beyond the noise; `json` neither (1,339 → 1,314 ms in one run). The gain is the host time of the calls saved: `grep_log` saves 1,091 reads and 39 ms, so about 36 µs a read, inside the 31 to 46 µs that section 3.5 measured.
- **64 KiB, not 256 KiB.** `many_files` is slower at 256 KiB than at 64 KiB in both runs (EXECUTED: 65.6 and 66.8 ms against 60.3 and 54.5 ms, `time2.out`). Each open allocates the buffer and each close frees it; a 256 KiB block is past dlmalloc's small bins (INFERRED). The large files gain only a few ms more from 256 KiB.
- **Memory:** one buffer per open handle that has been read or written, allocated on first use (READ: `PerlIOBuf_get_base`, no zeroing). 64 KiB × a few handles is nothing against the runner's 1 GiB cap.
- **Behaviour:** none that the checks see. `tests/oneliners`: 448 passed, at 64 KiB and at 256 KiB. The `perl_t` slice (section 5): no change.
- **vfs:** the most relevant lever of the study. A provider-backed file is staged whole by the runner, so each `fd_read` is one host callback whose cost is per call, not per byte. Study E should take 64 KiB as the guest's read size.

### 4.2 A SIMD `memchr`

- **What:** a `memchr` in our own recipe file (`recipes/wasi-memchr.c` in the diff), linked before wasi-libc so the libc member is never pulled. With `-msimd128` it compares 16 bytes a step: aligned 16-byte loads, a compare, `i8x16.bitmask`, a count of trailing zeros. An aligned load holding at least one byte of the range never passes the end of linear memory, whose size is a multiple of 64 KiB; bytes outside the range are masked off. Without SIMD it compares 8 bytes a step in a 64-bit word (measured too, as "swar").
- **Checked:** `proto/memchr-test.c` compares it with a byte loop at every start offset 0 to 31, length 0 to 79 and target position, with a hit planted just before and just after the range, and a `c` above 255: 108,800 cases pass in both forms (EXECUTED: `wasmtime run memchr-test-simd.wasm`).
- **Fuel:** −10.5% `grep_log`, −12.8% `inplace`, −19.6% `count_lines`, −16.2% `many_files`, −2.4% `sum_csv`; nothing elsewhere. The 64-bit word form gets about a third of that.
- **Wall time:** with the 8 KiB buffer the gain is lost in the noise of the host calls (`grep_log` 111 → 104 ms, `count_lines` 78 → 78). On top of the 64 KiB buffer it shows: `count_lines` 43 → 36 ms (−16%), `grep_log` 72 → 68, `inplace` 119 → 114. The 64-bit word form, in one run on top of 64 KiB: `grep_log` 67.7, `count_lines` 36.0 ms (`time2.out`), close to SIMD, which is within that run's noise.
- **Cost and risk:** one small C file and one compile line. The module then needs the SIMD proposal, which wasmtime enables by default and the runner does not turn off (READ: `src/wasi_perl/_engines.py` sets only `wasm_exceptions`). Study B, which owns `-msimd128`, may choose to build everything with SIMD; this file needs it either way.
- **Security:** none; `memchr` reads only memory the guest owns, and a wasm load out of bounds traps.

### 4.3 The fd-table answers `isatty` for files it opened

- **What:** `PerlIOBuf_pushed` calls `isatty` on every handle it opens, and wasi-libc's `isatty` is an `fd_fdstat_get` (READ: `perlio.c` line 4250). The fd-table already looks at every name before opening it (`look`). The prototype keeps the look's answer: if it saw a regular file, `isatty` of that descriptor answers "not a terminal" without a host call. Everything else goes to the host as before (`recipes-diffs/fd-table-isatty.diff`, linked with `--wrap=isatty`).
- **WASI calls:** one fewer per file opened: `many_files` 1,838 → 1,637 (EXECUTED: the trace).
- **Fuel:** unchanged.
- **Wall time:** `many_files` 63 → 50 ms (−20%), all three rounds lower (46 to 53 against 63 to 73). That is 200 × 60 µs, the `fd_fdstat_get` on a file of section 3.5. The other workloads open one file and do not change.
- **Risk:** small. A name swapped between the look and the open is the race the look already accepts; the worst outcome is that a terminal is buffered as a file. No terminal can be reached through a grant in the sandbox anyway.
- **Security:** none.
- **vfs:** each `fd_fdstat_get` on a provider file is a provider call if the mount answers it per call (study E).

### 4.4 Build choices in `config.sh`

Each is a full rebuild with `scripts/20_target.sh` in a build root that differs from this checkout only in `recipes/config.sh` (`mkvariant.sh`; the edits are `recipes-diffs/config-*.diff`). A build takes 4 to 6 minutes at `-j4` (EXECUTED: `buildall.out`).

| choice | what was built | fuel | verdict |
|---|---|---|---|
| `-DNO_TAINT_SUPPORT` | `ccflags` | −1.0 to −1.6% on every workload that does work | real but small; it removes `-T` (section 6) |
| `PERL_HASH_FUNC_ZAPHOD32` in place of SIPHASH13 | `ccflags` | ±0.0% everywhere | no gain: keys of 24 bytes or fewer are hashed by SBOX32 under either function (READ: `hv_func.h`, `PERL_HASH_USE_SBOX32_ALSO`), and these workloads' keys are short |
TBD-NOSBOX
TBD-MYMALLOC
| `PERL_PERTURB_KEYS` | not built | | INFERRED no gain: it changes only the order of iteration after an insert, a few instructions per insert (READ: `hv.c` `PL_hash_rand_bits` updates); dropped to save machine time |
| `-DPERL_NO_COW` | not built | | INFERRED a loss: copy-on-write spares a copy on every `$x = $y` of a string, which `sv_gets`, `split` and `JSON::PP` do per line; dropped to save machine time |
| `d_memrchr` (undef today; wasi-libc has it) | not built | | no gain: musl's `memrchr` is a byte loop (READ: disassembly of `__memrchr`, one `i32.load8_u` per step), the same as Perl's own `my_memrchr`, and none of these workloads calls it (READ: `util.c` `rninstr`, `toke.c`) |
| other `d_` symbols | | | the only `d_*='undef'` functions that wasi-libc defines are `asctime_r ctime_r gmtime_r localtime_r crypt_r ftime lgamma_r memrchr pipe pipe2 strerror_r` (EXECUTED: `llvm-nm --defined-only libc.a` against `config.sh`). None but `memrchr` is on a hot path; `memmem` and `strnlen` are already defined. |
| `use64bitint` | not built | | INFERRED: keep. WebAssembly has native 64-bit integer arithmetic, so an 8-byte IV costs only memory (an IV body is 8 bytes instead of 4) and an occasional extra load. Turning it off changes behaviour the oracle checks: `loop`'s answer, 333333833333500000, does not fit in 32 bits. |
| `-DPERL_DISABLE_PMC` | not built | | 4 fewer failed `path_filestat_get` per `require` (READ: the trace, a `.pmc` probe in each of 4 `@INC` folders): about 4 × 31 µs = 0.12 ms per module (INFERRED from section 3.5). It changes behaviour (a `.pmc` beside a `.pm` is no longer preferred), which no shipped file uses. A startup matter for study D. |
| locale (`-DNO_LOCALE`) | not built | | INFERRED no gain on these workloads: no locale function is in any profile but startup's, and all of startup is 1.6 M instructions. |

The hash, taint and allocator choices were all measured by fuel only. None but taint moved it by more than the 0.02% noise, so their wall time was not measured.

## 5. Correctness of what is recommended

- **`tests/oneliners`**, the native oracle (EXECUTED: `WASI_PERL_ARTIFACTS=<set> uv run pytest tests/oneliners -q`, with each variant assembled as a full artifact set and its `SHA256SUMS` rewritten by `scripts/write_sums.py`): 448 passed for 64 KiB with SIMD `memchr`, for 256 KiB with SIMD `memchr`, and for 64 KiB with SIMD `memchr` and the `isatty` answer. Pointing the variable at a missing folder skips every slow case, so the tests do read it.
- **`tests/perl_t`, a slice** of 92 files on I/O, line reading, split, hashes, sort, regex, substitution, numbers, `-t` and stat (EXECUTED: `run_suite.py --only io/* --only op/readline.t … --jobs 2`, the list in section 8): base and the three-part candidate both "92 run, 50 passed, 42 excluded, 0 failed, 0 regressed (3266 guarded oks)" (`results/perl_t-slice.txt`). 256 KiB with `memchr` gave the same on the first 84 of them.
- **The `memchr` itself**: 108,800 cases against a byte loop (section 4.2).
- **The recipe diff builds**: TBD-CANDBUILD

## 6. Ranking

Gain is on the workloads above; cost is the work to land it; vfs relevance follows the plan's order (fixed cost, then cost per file, then line speed).

| rank | lever | gain | cost | risk | security | vfs |
|---|---|---|---|---|---|---|
| 1 | `PERLIOBUF_DEFAULT_BUFSIZ=65536` | 23 to 44% of wall time on files over 64 KiB; 8× fewer `fd_read`/`fd_write`; fuel ±0 | one `-D` in `config.sh` | low: no check changes | none | **high**: each read saved is a provider callback saved (study E) |
| 2 | SIMD `memchr` | 10 to 20% of instructions on line reading and substring search; 5 to 16% of wall time once rank 1 is in | one C file, one compile line, one `libs` entry | low: tested exhaustively on small ranges; needs SIMD, which wasmtime has on by default | none | high: every `-n`/`-p` line |
| 3 | the fd-table answers `isatty` for a regular file it opened | one host call (about 60 µs) per file opened; 20% on `many_files` | about 25 lines in `wasi-fd-table.c` and one `--wrap` | low | none: the look it reuses is the existing guard | **high**: per file opened, the plan's second cost |
| 4 | `-DNO_TAINT_SUPPORT` (or `SILENT_NO_TAINT_SUPPORT`) | 1.0 to 1.6% of instructions | one `-D`; but `-T` then dies (or is ignored, with the silent form), so `op/taint.t` and every `#!perl -T` test need exclusions, and the docs a line | medium: a feature leaves | taint is no sandbox boundary, but a program that relies on `-T` to check its inputs loses that check silently with the silent form | low |
| 5 | `-DPERL_DISABLE_PMC` | 4 failed stats (about 0.12 ms) per module loaded | one `-D` | low | none | low; study D's |
| — | hash function, SBOX32, key perturbation, copy-on-write, `memrchr`, `use64bitint`, locale | none measured or none expected | | | | |

Ranks 1 to 3 together are the recommendation, for one specification: they keep every behaviour the checks see, and they act on the two costs vfs cares most about. Rank 4 is the owner's call: 1.5% for a feature. Rank 5 belongs with study D's startup work.

Two findings for the other studies:

- **Study E and A:** under wasmtime-py, a WASI call that reaches the host's file system costs 15 to 60 µs, against well under 1 µs for one answered from memory (section 3.5). A file opened and read costs about 300 µs in nine calls; three of them could be answered in the guest (`isatty` here; the folder's `fd_fdstat_get`, already cheap; and the `fstat` after the look, which would need the look's answer to stay true). A provider-backed mount that answers from memory could make every one of them cheap.
- **Study B:** the guest runs 1.6 to 1.9× native's instruction count with the same profile, so what is left on the interpreter's side is code generation. The SIMD `memchr` is the one place where a libc routine, not the compiler, is the cost.

## 7. What was not done, and why

- **Perl's own malloc (`usemymalloc`).** The first build failed: `malloc.c` says "MYMALLOC is not defined", because `config.sh` also needs `d_mymalloc='define'` (EXECUTED: `v/mymalloc/build/logs/20_target.log`). TBD-MYMALLOC2
- **`PERL_PERTURB_KEYS`, `PERL_NO_COW`, `PERL_DISABLE_PMC`, 32-bit IVs**: variant roots were made (`mkvariant.sh`) but not built, to spend the shared machine on the larger levers; section 4.4 gives the reasoning for each.
- **A whole `tests/perl_t` run** of the candidate: only the slice of section 5. A specification that lands ranks 1 to 3 should run the whole suite.
- **Line reading itself.** `count_lines` spends about 1,200 instructions per line even with the SIMD `memchr` (EXECUTED: 244 M for 200,000 lines). `sv_gets`, `do_readline` and the `-n` loop's ops are the same functions natively; that cost is Perl's, not WebAssembly's.

## 8. Reproducing

From the evidence folder, with the base build's `build/` copied into the checkout and a data folder made by the baseline's `bench.py data DATA` (plus `DATA/many/`, 200 files of 50 lines each of `t.log`, `f000.log` to `f199.log`):

```
# a variant: relink with extra objects or another libperl.a (relink.sh), or rebuild (mkvariant.sh)
LIBPERL=<libperl.a with perlio.o built -DPERLIOBUF_DEFAULT_BUFSIZ=65536> \
  ./relink.sh <tree> <20_target.log> perl-x.wasm memchr-simd.o
uv run python measure.py fuel perl-x.wasm <artifact set> DATA results/fuel-x.json
flock /home/user/wasi-perl-wt/machine.lock uv run python measure.py time perl-x.wasm <artifact set> DATA results/time-x.json
python3 compare.py results <variant>...          # fuel table
python3 timesum.py results time3 <variant>...    # wall-time table
# WASI calls
WASMTIME_LOG=wasmtime_wasi=trace ./wt.sh <artifact set> DATA W grep_log 2>trace.txt; python3 wasicount.py trace.txt
# host cost per call
uv run python wasicost.py wasicost.wasm <artifact set> DATA 5000; uv run python callcost.py perl.wasm <artifact set> DATA out.json
# profiles
PERLWASM=perl-prof.wasm WTOPTS="--profile=guest,out.json,100us" ./wt.sh <artifact set> DATA W grep_log
python3 profsum.py 25 out.json; python3 profsum.py callers memchr out.json
PRE="valgrind --tool=callgrind --callgrind-out-file=cg.out" ./native.sh <native perl> DATA W grep_log
```

`perl-prof.wasm` is the base relinked with `prof-exit.o` and `-Wl,--wrap=exit,--wrap=__main_argc_argv`. The `perl_t` slice: `--only 'io/*' --only op/readline.t --only op/split.t --only 'op/hash*.t' --only op/taint.t --only 'op/each*.t' --only op/sort.t --only re/pat.t --only 'op/sprintf*.t' --only op/chop.t --only 'op/inc*.t' --only 'run/switch*.t' --only op/numconvert.t --only op/64bitint.t --only op/int.t --only op/arith2.t --only op/stat.t --only 'op/filetest*.t' --only op/index.t --only re/subst.t --only op/tr.t --only op/join.t`.
