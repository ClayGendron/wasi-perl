# Serving vfs files to wasi-perl: the mount path, call by call

- **Status:** research, 2026-11-24. Study E of the speed research phase (`2026-11-24-perf-research-plan.md`). It changes no code: the prototype is a patch in the evidence folder, not a merge.
- **Researcher:** an agent (Claude Opus 5.5).
- **The question:** how should wasi-perl serve provider-backed (vfs) files so that agent one-liners and scripts over them are as fast as possible, and what does each WASI call cost when a provider answers it?
- **Evidence:** `2026-11-24-perf-vfs-io/`. The scripts, every raw result under `results/`, and the prototype as `mount-prototype.patch` against `src/` at `32dfb73`.

Labels: EXECUTED means run and seen. READ means read from a file. INFERRED means the researcher's reasoning.

Terms:

- A **provider** is the embedder's object that answers `stat`, `list` and `read` (and, for a writable mount, `write`, `mkdir`, `remove`, `rmdir` and `rename`) on normalized paths. This is wasi-python's mount protocol (READ: wasi-python `src/wasi_python/mounts.py`).
- A **mount** is a provider's files at a guest path. The host implements the preview1 file calls for it.
- A **round trip** is one database query a provider makes. vfs counts its cost in statements (READ: vfs spec 179, F5).
- A **shadow** is a Python host function defined over one preview1 import. It serves a mount's descriptors and forwards every other descriptor to wasmtime's own WASI.

## The answer in short

1. **Serve vfs files from a host-side mount, as wasi-python does, in Python.** It is not slower than a grant. For file-heavy programs it is faster:
   - a grep over 500 rows takes 97 ms through the mount and 156 ms through a grant;
   - a glob over them takes 124 ms against 239 ms.

   The guest still imports only `wasi_snapshot_preview1`.
2. **The provider's round trips dominate everything else.** Spend the design effort there, in two places:
   - **In the vfs provider (design e):** one batched read of the paths the embedder can see in argv, and a `list` that brings small bodies with it. A grep over 500 rows then goes from 1,000–1,503 provider calls to 1–2 round trips.
   - **In wasi-perl's mount layer:** four switches cut the provider calls the guest causes by half or more, with no guest change:
     - answer the guest's special-file look with the body the open will need;
     - keep a memo for each call;
     - defer a create until the file's close;
     - defer that close until the call's end.
3. **A bigger PerlIO buffer (study C) is worth about 10 ms per 5 MB read**, with a grant or a mount. A custom import that hands over a whole file in one call buys nothing more: preview1's `fd_read` already does that when asked for enough bytes. The rule against custom imports costs no speed here.
4. **Staging rows into a host folder that wasmtime opens itself (design d) is slower,** 2.2× to 2.4× on the 500-row greps, and it puts provider bytes on the host's disk. Reject it.

## 1. What the guest asks for

`count_calls.py` runs each program twice.

- Once through a grant, which is wasmtime's own WASI.
- Once through the prototype's mount. Every import but `proc_exit` is wrapped by a counting shadow.

Both runs printed the same output. They wrote the same bytes, and they made the *same sequence* of WASI calls (EXECUTED: `python count_calls.py results/counts.json`; the script asserts equal stdout and equal written files, and `results/counts.json` holds equal `wasi_total` for every design). So the mount is invisible to the guest at the call level.

The programs, over files at `/work`:

- `ne_50k`: `-ne 'print if /ERROR/'` on a 50 KB log row.
- `lane_5m`: `-F, -lane '$s += $F[2]'` on a 5 MB CSV row.
- `slurp_5m`: `-0777 -ne` on the same row.
- `pi_50k`: `-pi -e 's/ERROR/WARN/g'` on the log row.
- `grep_args_500`: 500 rows of 12 lines each, given as arguments.
- `grep_glob_500`: the same rows, found by `glob`.
- `grep_opendir_500`: the same rows, found by `opendir`.
- `write_new`: a script that writes a new 141 KB file.

| workload | WASI calls | of them: the fd-table's start / its look before an open | fd_read calls, bytes | fd_write | path_open | path_filestat_get | fd_readdir |
|---|---|---|---|---|---|---|---|
| hello (`-e 1`) | 36 | 10 / 1 | 0 | 0 | 1 | 1 | 0 |
| ne_50k | 52 | 10 / 2 | 8, 50,022 | 1 | 2 | 2 | 0 |
| lane_5m | 656 | 10 / 2 | 612, 5,000,032 | 1 | 2 | 2 | 0 |
| slurp_5m | 658 | 10 / 2 | 612, 5,000,032 | 1 | 2 | 2 | 0 |
| pi_50k | 66 | 10 / 2 | 8, 50,022 | 7 | 3 | 2 | 0 |
| grep_args_500 | 4,537 | 10 / 501 | 1,000, 120,118 | 1 | 501 | 501 | 0 |
| grep_glob_500 | 5,114 | 10 / 506 | 1,007, 174,761 | 1 | 506 | 1,036 | 4 |
| grep_opendir_500 | 4,545 | 10 / 502 | 1,000, 120,118 | 1 | 502 | 502 | 4 |
| write_new | 61 | 10 / 2 | 0 | 18 | 2 | 2 | 0 |

(EXECUTED: `results/counts.md`.)

Who asks (EXECUTED: the sequence in `results/counts.json`, attributed by `count_calls.attribute`, and READ: `recipes/wasi-fd-table.c`):

- **Our fd-table's start:** 10 calls once per program. It scans `fd_fdstat_get` and `fd_prestat_get` from 0 until the first number the host does not know.
- **Our fd-table's look:** one `path_filestat_get` before every open that is not an exclusive create. That is 1 per file read, and 501 of the 4,537 calls in `grep_args_500`.
- **Perl and wasi-libc:** the rest. Each file read costs 9 calls:
  1. the look;
  2. `fd_fdstat_get` of the folder (wasi-libc's `openat` reads its rights);
  3. `path_open`;
  4. `fd_fdstat_get` of the new descriptor;
  5. `fd_tell`;
  6. `fd_filestat_get`;
  7. `fd_read` of the data;
  8. `fd_read` that meets the end;
  9. `fd_close`.
- **`glob` adds an `lstat` of each match.** That is one more `path_filestat_get` per file: 1,036 against 502 for `opendir`. Its module load adds about 30 library probes.

**PerlIO reads in 8,192-byte calls.** Every `fd_read` in every workload asked for exactly 8,192 bytes (`fd_read_sizes` in `results/counts.json`). So a 5 MB row is 612 `fd_read` calls. Slurp mode (`-0777`) does not change that (EXECUTED). Writes go out in 8,192-byte calls too: `write_new` makes 18 `fd_write` calls for 140,893 bytes.

**`-pi` in detail.** It runs in this order (EXECUTED: `python trace.py`, `results/traces.txt`):

1. a looked-at open of the row;
2. an exclusive create of a temporary file (`XXeBdzCv`), with no look;
3. reads and writes, interleaved;
4. `fd_close` of the temporary file;
5. `path_rename` of the temporary file over the row;
6. `fd_close` of the row.

The temporary file is closed *before* the rename. That order decides what a mount can save (section 4).

Through the mount with the switches on, a workload's calls split as follows (EXECUTED: `python split_calls.py`, `results/split.md`). 16 calls never reach a shadow: args, environment, clocks and `fd_prestat_*`.

| workload | served by the mount | forwarded by a shadow | never shadowed |
|---|---|---|---|
| ne_50k | 14 | 22 | 16 |
| lane_5m | 618 | 22 | 16 |
| grep_args_500 | 4,000 | 521 | 16 |
| grep_glob_500 | 4,507 | 591 | 16 |

## 2. What one call costs

`bench_calls.py` uses a WAT guest that opens one file and calls one function in a loop. Each number is the median of 5 runs of 100 to 20,000 calls, under the machine lock (EXECUTED: `flock … python bench_calls.py results/calls.json`, `results/calls.md`). Wall time, so it is secondary evidence.

| call | wasmtime's WASI (µs) | a Python shadow forwarding to it (µs) | Python serving a mount from staged bytes (µs) |
|---|---|---|---|
| a Python host function that does nothing | 6.6 | | |
| fd_fdstat_get | 0.16 | 11.9 | 12.0 (forwarded: stdout is not the mount's) |
| fd_seek | 0.18 | 12.6 | 14.7 |
| fd_filestat_get | 52.0 | 88.4 | 15.9 |
| fd_pread 8 KiB | 26.8 | 59.0 | 18.1 |
| fd_seek + fd_read 8 KiB | 25.4 | 74.4 | 32.8 |
| fd_seek + fd_read 64 KiB | 47.6 | 93.9 | 33.7 |
| fd_seek + fd_read 1 MiB | 233.7 | 577.6 | 84.0 |
| fd_seek + fd_read 5 MB | 1,299 | 1,974 | 426.7 |

What it means:

- **A Python host call costs about 7 µs to enter, and 12 to 19 µs to serve a small read or stat from memory** (EXECUTED). That agrees with vfs's spike: 6.2 µs for a no-op and 14.7 µs for a decoding `fd_write` (READ: vfs `context/research/studies/2026-10-15-agent-shell/10-wasm-program-spike.md`).
- **wasmtime's own file calls are slow, and its memory calls are fast.**
  - `fd_fdstat_get` and `fd_seek` take 0.2 µs.
  - `fd_filestat_get` takes 52 µs, and an 8 KiB read 25 µs.

  The likely cause is that wasmtime-wasi runs each host-file operation on a blocking worker thread (INFERRED from the 300× gap between the two kinds; the wasmtime source was not read).
- **So a mount serves file calls faster than a grant does:** 16 µs against 52 µs for a stat, and 18 µs against 25 µs for an 8 KiB read (EXECUTED). It loses only on calls wasmtime answers from memory, by about 12 µs each. Of these, the mount forwards 22 per program plus 1 per opened file.
- **Copying is cheap once the call is made.** The mount copies 5 MB in 0.43 ms with one `memmove` from the staged bytes (the patch's `_fd_read`).
- **wasmtime caps `fd_pread` at 64 KiB, but not `fd_read`.** A 1 MiB `fd_pread` returns 65,536 bytes; a 1 MiB `fd_read` returns 1 MiB (EXECUTED: a one-off check of `bench_calls.py`'s guest in this session, its output not kept; and `readsize_counts.py` for `fd_read`). wasi-python's `PREAD_CHUNK` says the same for wasmtime 49 (READ: `_fs_memory.py`). perl.wasm does not import `fd_pread`, so the cap touches only the benchmark.

## 3. The prototype

`mount-prototype.patch` adds `src/wasi_perl/_mounts.py` (949 lines) and a `mounts=` argument to `run`. It also adds a reusable forwarder to `_wasmtime_ffi.py`. Apply it with `git apply`. Each part is adapted from wasi-python (same owner, Apache-2.0):

- **The provider protocol:** wasi-python's, unchanged.
- **Whole-file staging:** a file's bytes are read once at its open and written back whole.
- **Provider numbers from 2**30.**
- **A preopen of an empty, read-only placeholder folder for each mount.** wasi-libc's preopen scan and our fd-table's start find it like any grant.
- **A trampoline instantiated on the guest's memory**, and a prepared `wasmtime_func_call` for each forwarded call.

It shadows 26 descriptor calls. perl.wasm imports 22 of them; the other four are shadowed so that no later binary reaches a mount's number unseen (EXECUTED: the import list of `perl.wasm`, 33 functions). It is a measuring tool, not a shippable design:

- it makes no ABI checks of flags;
- it keeps no budgets (file calls, staging bytes);
- it has no fault boundary, so a provider exception traps;
- it has no thread hand-off.

A specification should take wasi-python's `_fs_*` whole, trimmed, rather than grow this (INFERRED).

It has four switches beyond wasi-python's design, each a `Mount` field so a measurement can turn it off:

| switch | what it does | why |
|---|---|---|
| `memo` | Keeps a `stat` answer and a staged body by path for the call; the call's own writes update them. | `glob` stats each match, then the look stats it again. |
| `stat_reads` | Answers a `stat` of a file with one `read`, and keeps the bytes for the open that follows. | The fd-table's look stats every path before its open, so each file costs a `stat` and a `read`. |
| `defer_create` | A created file exists in the call's own view and reaches the provider once, at its close, not as `write(path, b"")` at open and again at close. | wasi-python writes twice; in vfs that is two versions of a new file (READ: vfs `2026-10-23-wasi-python-0-1-0-over-vfs…md`, finding (b)). |
| `defer_close` | A created file that is closed is written at the call's end, under the name it has then. | `-pi` closes its temporary file, then renames it over the row. |

## 4. The designs, measured

### 4.1 Provider calls per program (deterministic)

The provider's own call counts (EXECUTED: `results/counts.md`).

- Column (a) is wasi-python's design as it stands: no memo, a create written at open, the provider asked every time.
- Column (e) is the vfs-side cache, modelled by `harness.RoundTrips`. It counts database round trips:
  - one batched read of argv's paths before the call;
  - a `list` that brings bodies of up to 64 KiB;
  - every other `stat` or `read` a hit when it can be.

| workload | (a) as wasi-python | + memo | + memo, defer_create | + memo, defer_create, stat_reads | all four switches | (e) cache under (a): round trips | (e) cache under all four: round trips |
|---|---|---|---|---|---|---|---|
| ne_50k | 2 (stat, read) | 2 | 2 | 1 (read) | 1 | 1 | 1 |
| lane_5m | 2 | 2 | 2 | 1 | 1 | 1 | 1 |
| pi_50k | 6 (2 stat, read, 2 write, rename) | 6 | 5 | 4 | **3 (2 read, write)** | 5 | 3 |
| grep_args_500 | 1,000 (500 stat, 500 read) | 1,000 | 1,000 | 500 | 500 | **1** | **1** |
| grep_glob_500 | 1,503 (1,002 stat, 500 read, list) | 1,002 | 1,002 | 502 | 502 | 3 | **2** |
| grep_opendir_500 | 1,003 | 1,002 | 1,002 | 502 | 502 | 3 | **2** |
| write_new | 4 (2 stat, 2 write) | 3 | 2 | 2 | **2 (read, write)** | 4 | 2 |

What each switch buys:

- **`stat_reads` halves every reading workload.** The fd-table's look is the guest's only extra provider call; this answers it with the body the open needs.
- **`memo` removes `glob`'s second stat.**
- **`defer_create` removes the empty write at open.**
- **`defer_close` turns `-pi` into one read and one write of the row.** That removes the temporary file's write and the rename. In vfs terms, the temporary row's create (6 statements), its write and its move (14) become one overwrite (8). Three versions become one (READ: vfs spec 179, F5, for the system actor).

The `pi_50k` and `write_new` rows still have 2 reads with all four switches. One read is the probe of the new name, which `stat_reads` answers with a read that finds nothing.

### 4.2 Wall time per call

These are the medians in ms, in one warm process (EXECUTED: `flock … python bench_workloads.py results/workloads.json`, `results/workloads.log`). There are 5 calls at 0 ms of latency and 3 calls when each provider call sleeps 1 ms to stand in for a database round trip. The second figure in each cell is the provider-call count.

(d) is staging done eagerly. The rows the program will touch are read from the provider and written to a fresh host folder. The program runs on a grant of that folder, and changed files are written back afterwards.

| workload | grant | (a) @0 | all four @0 | (d) @0 | (a) @1 ms | all four @1 ms | (d) @1 ms |
|---|---|---|---|---|---|---|---|
| hello | 3.9 | 4.5 | 4.6 | 4.1 | 4.5 | 5.9 | 5.5 |
| ne_50k | 5.3 | 5.9 / 2 | 6.1 / 1 | 6.0 / 1 | 8.3 / 2 | 7.1 / 1 | 7.3 / 1 |
| lane_5m | 154.3 | 136.1 / 2 | 138.2 / 1 | 160.4 / 1 | 145.9 / 2 | 144.9 / 1 | 165.6 / 1 |
| slurp_5m | 30.4 | 20.1 / 2 | 19.8 / 1 | 29.6 / 1 | 19.9 / 2 | 33.3 / 1 | 30.0 / 1 |
| pi_50k | 17.9 | 5.3 / 6 | 5.2 / 3 | 15.4 / 2 | 11.9 / 6 | 8.6 / 3 | 17.4 / 2 |
| grep_args_500 | 155.7 | 96.2 / 1,000 | 97.2 / 500 | 235.8 / 500 | 1,218.7 / 1,000 | 656.1 / 500 | 707.4 / 500 |
| grep_glob_500 | 238.5 | 114.9 / 1,503 | 124.0 / 502 | 267.0 / 501 | 1,891.5 / 1,503 | 723.4 / 502 | 910.3 / 501 |
| write_new | 18.6 | 5.2 / 4 | 5.2 / 2 | 14.8 / 1 | 9.5 / 4 | 8.8 / 2 | 19.7 / 1 |

What it shows:

- **A mount costs about 0.6 ms per call when nothing is read** (hello: 3.9 → 4.5 ms). That is the trampoline's instance, the placeholder's preopen and 20 forwarded calls (EXECUTED; the split is INFERRED from section 2's per-call costs).
- **A mount is faster than a grant once files are read:**
  - 97 against 156 ms on 500 rows;
  - 124 against 239 ms with `glob`;
  - 20 against 30 ms on a 5 MB slurp.

  This follows from section 2: wasmtime's own file calls cost 25 to 52 µs each, and the mount's 16 to 19 µs (INFERRED).
- **A writable mount avoids the writable grant's runtime-file walk.** That walk is section 6 of the baseline memo. Here `pi_50k` and `write_new` take 17.9 and 18.6 ms through a writable grant and 5.2 ms through a writable mount (EXECUTED). A mount has no host file to protect, so the check has nothing to do.
- **At 1 ms per provider call, provider time is most of the call.**
  - Through (a), `grep_args_500` is 1,219 ms; with all four switches, 656 ms.
  - About 1.1 ms per provider call accounts for the difference (INFERRED: (1,219 − 656) / 500 = 1.13 ms, against a 1 ms sleep plus wake-up).
- **(d) is slower everywhere files are many.** It writes each row to disk, then pays wasmtime's slower file calls: 236 against 97 ms. It needs the file set before the call, which a `glob` or a computed path does not give (INFERRED). And it leaves provider bytes on the host's disk.
- **Noise:** each delayed cell is a median of 3, on a shared machine. `slurp_5m` at 1 ms reads 33.3 ms for one provider call, against 19.9 ms for two. Read single cells to ±10 ms. The counts are the evidence.

### 4.3 Bigger guest reads (b) and a whole-file import (c)

wasi-perl's PerlIO buffer cannot be changed without a rebuild, which is study C's subject. So a Perl program stands in for it: it reads the 5 MB row by `sysread` of N bytes. That makes the same `fd_read` calls a PerlIO buffer of N bytes would. With N at least the file's size, it is one `fd_read` that hands over the whole file. That is what a custom import (c) would do, but through preview1 (EXECUTED: `flock … python bench_readsize.py results/readsize.json`, the median of 7; `python readsize_counts.py`, `results/readsize-counts.md`).

| request size | fd_read calls (grant and mount alike) | grant, ms | mount, ms |
|---|---|---|---|
| 8 KiB (today's PerlIO) | 612 | 19.5 | 18.6 |
| 64 KiB | 78 | 9.5 | 8.4 |
| 1 MiB | 6 | 7.0 | 6.8 |
| 8 MiB (whole file: what an import would do) | 2 | 8.6 | 7.9 |

- **A 64 KiB PerlIO buffer saves about 10 ms per 5 MB read**, through a grant or a mount (EXECUTED). On `lane_5m`, which spends 138 ms in Perl's own work, that is about 7% (INFERRED).
- **Above 1 MiB nothing more is saved.** A whole-file handover is no faster than 1 MiB reads.

So (c) buys nothing that (b) does not. It needs a custom import, which `AGENTS.md` forbids without the owner's agreement, and a PerlIO layer or patch in the guest to call it (INFERRED). **Not recommended. No owner question is needed.**

## 5. Where the provider's own costs dominate

- **One provider round trip is worth 50 to 1,500 guest calls.**
  - A served call costs 12 to 19 µs (section 2).
  - The simulated round trip here costs 1.1 ms.
  - vfs measured 5 to 25 ms per verb on in-memory SQLite, with two thread hops per verb (READ: study 10).
  - One verb is 1 statement for a `stat` or `read`, 2 for `ls`, 6 for a create, 8 for an overwrite and 14 for a `move`, for the system actor; a named principal adds 2 to each (READ: vfs spec 179, F5).
- **So every design's provider calls, times 5 ms, decide the call's time once a program opens more than one or two rows.** Here is what that gives at vfs's measured 5 ms per verb (INFERRED: counts from 4.1, times 5 ms):

| workload | (a) as wasi-python | all four switches | all four + (e) cache |
|---|---|---|---|
| ne_50k | 10 ms | 5 ms | 5 ms |
| pi_50k | 30 ms | 15 ms | 15 ms |
| grep_args_500 | 5.0 s | 2.5 s | 5 ms |
| grep_glob_500 | 7.5 s | 2.5 s | 10 ms |
| write_new | 20 ms | 10 ms | 10 ms |

  Against these, the guest's own I/O through a mount is 1 to 100 ms. So for any multi-file program the guest is not where the time goes (INFERRED).
- **Only a batched round trip makes a 500-row grep cheap.** The switches in wasi-perl's mount halve the calls, but 500 rows still cost 500 calls. The batch belongs in the vfs provider, and vfs already plans its cache (READ: vfs spec 172 §3, the staged-buffer cache; the 2026-10-23 memo's "a per-run stat and body cache"; spec 181's `(entry_id, version)` key). Two warmings need no protocol change:
  1. **Before the call,** the embedder reads every argv path under a mount in one `read(observations=…)`. vfs resolves names with one batched `stat` already (READ: spec 169 §a, "one batched `stat`, never an `ls`").
  2. **A `list`** fetches small bodies in the same query.

  Both are the provider's choice under wasi-python's protocol, which says nothing about how a provider answers.

## 6. The candidates, ranked

Gain is measured or estimated per program at vfs's 5 ms per verb. Cost is the work to build it. Risk is what could go wrong.

| rank | lever | gain | cost | risk | security effect | vfs relevance |
|---|---|---|---|---|---|---|
| 1 | **(e) the provider-side cache, warmed by argv and by `list`** (in vfs's provider) | 500-row grep: 1,000–1,503 calls → 1–2 round trips; about 5 → 0.01 s (INFERRED from EXECUTED counts) | vfs side: a batched read before the call, a `list` with bodies under a size cap, and the `(entry_id, version)` cache it already plans | Staleness within one call (none across calls); host memory, bounded by the staging budget | None: every read is still gated by the caller's authority in vfs | Highest: every multi-file program |
| 2 | **A host-side mount in wasi-perl**, wasi-python's protocol and design | Makes vfs possible at all; 0.6 ms per call when nothing is read; 1.6–1.9× faster than a grant on 500 rows (EXECUTED) | A specification adapting wasi-python's `_fs_*` (about 2,500 lines there), trimmed to the 22 descriptor calls perl.wasm imports and four more | The parity work wasi-python already did (ABI checks, budgets, faults) must come too | Neutral: preview1 only; the placeholder is empty and read-only; `..` above a mount is refused; a writable mount writes no host file | Precondition |
| 3 | **`stat_reads` + `memo`** in that mount | Halves provider calls per file read: 2 → 1; `glob` 3 → 1 (EXECUTED) | Small: about 40 lines | A `stat` of a large file never opened (`-s`, `-e`) fetches its body. The provider can instead answer `stat` with the body only when it is small (INFERRED) | None | High |
| 4 | **`defer_create` + `defer_close`** in that mount | `-pi`: 6 → 3 provider calls; three versions and a move → one overwrite. A new file: 2 writes → 1 (EXECUTED) | Small: about 40 lines | With `defer_close`, a refused write-back reaches no `close()`, only the call's record, which vfs already folds into the stage's result (READ: spec 169 §b5). Make it a per-mount choice | None | High: `-pi` is wasi-perl's main use |
| 5 | **(b) a 64 KiB PerlIO buffer** (study C's build option) | About 10 ms per 5 MB read, grant or mount; 612 → 78 calls (EXECUTED) | A build option | Memory: 64 KiB per open handle | None | Medium: large rows only |
| 6 | **A cheaper fd-table start and look** | 10 calls per program, and 1 call per open that `stat_reads` makes free anyway | Recipe change | The look is what refuses special files on grants | Must keep the special-file refusal | Low once rank 3 lands |
| — | (c) a custom whole-file import | None over (b) at 1 MiB (EXECUTED) | A guest import and a PerlIO layer | The guest's surface grows | Breaks "preview1 only"; needs the owner | Reject |
| — | (d) staging into a host folder, opened natively | Negative: 2.2–2.4× slower on 500 rows (EXECUTED) | Medium | Needs the file set up front; cleanup | Provider bytes on the host's disk; a writable folder brings back the hard-link and runtime-file checks | Reject |

## 7. Recommendation, and how it keeps the sandbox

**Specify a mount for wasi-perl that is wasi-python's mount, protocol and all, with the four switches as its defaults.** Keep `defer_close` a per-mount choice, because it moves a refused write's report from `close()` to the call's record. **Then ask vfs to put its planned cache in front of the provider**, warmed by the argv batch and by `list`. Leave the guest alone: take study C's 64 KiB buffer if that study recommends it on its own evidence.

How it keeps the sandbox (READ: the prototype's code; EXECUTED: perl.wasm's import list):

- **The guest's imports do not change.** It imports 33 functions from `wasi_snapshot_preview1` and nothing else, and the mount serves them. There is no custom import, so nothing needs the owner's agreement.
- **No host path is reachable through a mount.** Each mount is preopened on one empty, read-only folder the runner owns. Every path under a mount is normalized within it; `..` above the mount and absolute paths are refused (`ENOTCAPABLE`). The provider sees only those normalized paths.
- **The fd-table's special-file refusal still runs.** On a mount it is answered by the provider's `stat`, and a mount holds no special files.
- **A writable mount writes no host file.** So the writable grant's hard-link and runtime-file rules have nothing to protect there.
- **What the prototype lacks, a specification must add from wasi-python:**
  - the ABI checks of every flag and pointer;
  - the file-call and staging budgets;
  - the fault boundary that turns a provider's exception into `EIO`;
  - the hand-off of provider calls to the embedder's thread.

## 8. Limits

- **One shared x86-64 Linux machine with 4 vCPUs.** Every timing ran under the machine lock, but other studies' processes outside the lock may have run. The counts are the evidence; read the times as ±10 ms.
- **The provider is in memory, plus a 1 ms sleep.** No vfs database was driven. The 5 ms per verb in section 5 is vfs's own measurement (READ), not this study's.
- **(d) was measured eagerly.** A lazy form, which stages a row into the folder at its first open, would pay the same disk write and wasmtime's slower file calls on every open, and would also need shadows. Its gain over the mount is bounded by the eager form's (INFERRED).
- **wasi-python's own runner cannot run perl.wasm.** Column (a) is the prototype set to wasi-python's choices, checked against wasi-python's code: one read at open, one stat per `path_filestat_get`, a stat and a list for `O_DIRECTORY`, and `write(path, b"")` at a create (READ: `_fs_paths.py`, `_path_open`, `_open_folder`, `_open_writing`).
- **wasmtime's file calls were not traced to their cause.** That they run on a blocking worker thread is an inference from their cost.
- **The base build is `perf-base`'s, from `32dfb73`.**

## Reproduce

From the worktree:

```
git apply context/research/2026-11-24-perf-vfs-io/mount-prototype.patch
export WASI_PERL_ARTIFACTS=<a built artifact set>
cd context/research/2026-11-24-perf-vfs-io
uv run python count_calls.py results/counts.json > results/counts.md
uv run python split_calls.py > results/split.md
uv run python readsize_counts.py > results/readsize-counts.md
flock /home/user/wasi-perl-wt/machine.lock uv run python bench_calls.py results/calls.json > results/calls.md
flock /home/user/wasi-perl-wt/machine.lock uv run python bench_workloads.py results/workloads.json
flock /home/user/wasi-perl-wt/machine.lock uv run python bench_readsize.py results/readsize.json
```
