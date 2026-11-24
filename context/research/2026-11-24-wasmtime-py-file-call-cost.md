# Why a WASI file call costs 56 µs under wasmtime-py and 0.85 µs under the CLI

- **Status:** research, 2026-11-24. A root-cause study at the owner's request: "clone the library and determine the root cause first. Another option is we could build our own rust crate that compiles wasmtime." It changes nothing in `src/`, `recipes/` or `scripts/`. Evidence: the folder of the same name beside this memo.
- **Stands on:** `2026-11-24-perf-runner.md` §5.1 (study A: 56–58 µs per filesystem call, about 17 `futex` per call, a thread-pool hand-off INFERRED without reading the source); `2026-11-24-perf-interp.md` §3.5 (the per-call table); `2026-11-24-perf-vfs-io.md` §2 (a Python host function answers in 16–19 µs); wasi-python's `context/research/2026-10-18-a-rust-core-with-js-and-python-wrappers.md`, `2026-10-18-a-rust-runner-spike-against-the-judge.md` and `2026-10-23-a-rust-runner-against-wasmtime-py-on-speed.md`.
- **Machine:** Linux 6.18.44 in a Firecracker VM, 4 vCPUs (Intel Xeon @ 2.30 GHz), 15 GiB. CPython 3.11.18, wasmtime-py 51.0.0 from PyPI, the wasmtime 51.0.0 CLI, wasi-sdk 34, rustc 1.97.0. Every timing ran under `flock /home/user/wasi-perl-wt/machine.lock`.

Labels: EXECUTED means run here; READ means read from a file, cited by path and line; INFERRED means reasoning.

## The short version

1. **The cause is one boolean the C API never sets.** wasmtime-wasi's `WasiCtxBuilder::allow_blocking_current_thread` is false by default. While it is false, every blocking filesystem operation is sent to tokio's blocking thread pool with `spawn_blocking`, and the calling thread parks until a pool thread wakes it. The CLI sets it to true. The C API has no function for it, so wasmtime-py cannot set it (READ, §2).
2. **On this machine one such hand-off costs about 26 µs.** A C program that does the same hand-off through a mutex and a condition variable, with no wasmtime in it, also takes 26 µs per round trip. Pinned to one CPU it takes 2.5 µs (EXECUTED, §3.4). So the cost is two cross-CPU wake-ups in this VM. A `read` makes one hand-off (27 µs). A stat makes two (51–57 µs), because wasmtime asks for the stat and the inode hash separately.
3. **A 19-line patch to wasmtime's C API removes it.** The patch adds `wasi_config_allow_blocking_current_thread`. Built against v51.0.0 and loaded into wasmtime-py, it takes `fd_read` from 26.7 to 0.47 µs, `fd_filestat_get` from 50.8 to 0.72 µs, and `path_filestat_get` from 57.0 to 1.24 µs. Those are the CLI's numbers within 0.3 µs. The `futex` calls drop from 8–18 per call to none (EXECUTED, §3).
4. **The gain on Perl.** With the flag on, `-ne` over 200 small files takes 6.7–7.6 ms instead of 57–61 ms. 2,000 `-e` stats take 7.9–8.4 ms instead of 113–122 ms. Loading strict, warnings and List::Util takes 6.7–7.5 ms instead of 9.2–9.7 ms. Exit code, stdout and stderr are byte-identical in every arm. A sleep still ends at the deadline (EXECUTED, §3.5).
5. **Recommendation:** send the C API function upstream, and add a `WasiConfig` property to wasmtime-py (option i). Until a release carries them, change nothing. Our own PyO3 crate (option ii) would fix this with the same boolean, so this problem gives it no reason to exist. A Python mount (option iii) costs 16–19 µs per call. That beats today's grants, but with the flag on it is 15 to 40 times slower than a grant.

## 1. The sources

| what | where | commit | licence |
|---|---|---|---|
| wasmtime-py, tag `51.0.0` | `/home/user/wasi-perl-downloads/src/wasmtime-py-51.0.0` | `8202959f035a131d208b81ef1809e7c6347eee3c` (2026-11-20) | Apache-2.0 (`LICENSE`) |
| wasmtime, tag `v51.0.0` | `/home/user/wasi-perl-downloads/src/wasmtime-v51.0.0` | `32656dc4873fcec5c8662aa937bd01acda3b2e85` (2026-11-20) | Apache-2.0 WITH LLVM-exception (`LICENSE`, `crates/c-api/Cargo.toml:6`) |

Both are shallow clones outside the repository, read as data. The wasmtime clone carries the patch of §3.3 in its working tree.

wasmtime-py's shared library is wasmtime's released C API. Its CI downloads `wasmtime-v51.0.0-<arch>-<os>-c-api.tar.xz` (READ: `ci/download-wasmtime.py:15,28`). It loads the library from the package folder with `cdll.LoadLibrary`. No environment variable overrides the path (READ: `wasmtime/_ffi.py:36-38`). So the patched library was tested from a copy of the package (§3.3).

## 2. The code path (READ)

### 2.1 From wasmtime-py to `fd_read`

1. `WasiConfig()` calls `wasi_config_new`, which makes a bare `WasiCtxBuilder::new()` (`crates/c-api/src/wasi.rs:45-50`). No `wasi_config_*` function reaches `allow_blocking_current_thread`: the file's 17 exported functions set argv, env, stdio, network and preopens only (`wasi.rs:45-345`).
2. `Store.set_wasi` calls `wasmtime_context_set_wasi`, which builds the context with `build_p1()` (`store.rs:236-243`, `wasi.rs:39-43`).
3. `Linker.define_wasi` calls `wasmtime_linker_define_wasi`, which links `wasmtime_wasi::p1::add_to_linker_sync` (`crates/c-api/src/linker.rs:110-119`).
4. **The implementation is wasmtime-wasi's preview1 over preview2.** The C API depends on `wasmtime-wasi` with the `p1` feature (`crates/c-api/Cargo.toml:33,48`). `wasi-common` is not in the v51.0.0 tree at all: no crate under `crates/` and no reference in either `Cargo.toml`.
5. The p1 functions are `async fn`s (`crates/wasi/src/p1.rs:857-869`). The sync linker wraps each one in `block_on[in_tokio]` (`p1.rs:871-895`).
6. `in_tokio` finds no tokio runtime on a Python thread. So it enters the crate's own lazily built multi-thread runtime and calls `RUNTIME.block_on`, after a `yield_now` (`crates/wasi/src/runtime.rs:27-33`, `104-154`).
7. `fd_read` on a file reads straight into guest memory, on the calling thread, **only if** `file.as_blocking_file()` is `Some`. Otherwise it allocates a buffer and calls `run_blocking`, then copies the bytes into guest memory (`p1.rs:1665-1721`).
8. `as_blocking_file()` is `Some` only when the file's `allow_blocking_current_thread` is true (`crates/wasi/src/filesystem.rs:757-763`). `run_blocking` runs the body in place when it is true. When it is false, it calls `spawn_blocking` and awaits the handle (`filesystem.rs:734-743` for files, `846-857` for folders).
9. `spawn_blocking` is `tokio::task::spawn_blocking` on that runtime (`runtime.rs:95-102`). The calling thread parks in `block_on` until a pool thread finishes and wakes it.
10. The flag defaults to false (`filesystem.rs:71-82`). Only `WasiCtxBuilder::allow_blocking_current_thread` sets it (`crates/wasi/src/ctx.rs:162-165`). Its doc says why it exists: the "back-and-forth between a blocking a non-blocking context can have overhead", and the option lets blocking operations run "on-thread" (`ctx.rs:140-161`). It cites wasmtime issue 7973 and PR 8190 (`filesystem.rs:731-733`).

Every filesystem operation goes through `run_blocking`: `stat`, `metadata_hash`, `get_flags`, `get_type`, `stat_at`, `open_at`, `read`, `write` and the rest (`filesystem.rs:523-678`, `869-1151`; `p1.rs:673`). So `fd_filestat_get` makes two hand-offs, `stat` and then `metadata_hash` (`p1.rs:1590-1598`). So does `path_filestat_get`, with `stat_at` and `metadata_hash_at` (`p1.rs:2055-2075`). `fd_fdstat_get` on a file makes two as well: `get_flags` and the file's type (`filesystem.rs:556`, `581`). Calls on stdio, a preopen's own `fd_fdstat_get`, seeks and clocks answer from memory, so they pay nothing.

### 2.2 The CLI

- `wasmtime run` builds its own multi-thread tokio runtime and runs everything in `runtime.block_on` (`src/commands/run.rs:236-242`).
- It links the same crate's **async** preview1, `wasmtime_wasi::p1::add_to_linker_async` (`run.rs:1147-1166`). So it uses the same implementation, not another one.
- It calls `configure_wasip2(&mut builder, self.run.common.wasm.timeout.is_none())` (`run.rs:1350-1359`), which calls `builder.allow_blocking_current_thread(...)` (`src/common.rs:318-327`). The comment says why: "It's ok to block the current thread since we're the only thread in the program as the CLI." The CLI leaves the flag off when a timeout is set, because its timeout is `tokio::time::timeout` around the async call (`run.rs:424`), and an operation that blocks the thread cannot be cancelled that way.

**So the CLI and the C API differ in exactly one setting.** Without a timeout the CLI runs file operations on the calling thread; the C API always hands them off.

### 2.3 What else the flag changes

- **The single sleep.** A `poll_oneoff` with one relative clock and nothing else calls `std::thread::sleep` when the flag is on. Otherwise it waits on a tokio timer in `block_on` (`p1.rs:2307-2348`). Either way the calling thread is blocked in the host, out of the epoch deadline's reach. wasi-perl's fd-table already cuts every guest wait into 10 ms slices for this reason (READ: `recipes/wasi-fd-table.c:390-410`). §3.5 checks that a sleep still ends at the deadline.
- **Order matters.** A preopened folder copies the flag when `preopened_dir` is called (`ctx.rs:316-336`). A file opened later takes the context's flag at `build` time (`crates/wasi/src/p2/host/filesystem.rs:270-290`). So the flag must be set before the first `preopen_dir`. If it is set after, reads and fstats on opened files are fast, but `path_open` and `path_filestat_get` on the folder still hand off (EXECUTED, §3.3).
- **Nothing else.** The body that runs is the same closure on either thread (`filesystem.rs:734-743`). So the sandbox's path checks (cap-std, `openat2` with `RESOLVE_BENEATH` in the syscall counts) are unchanged (INFERRED from the code). The flag lives in the filesystem context alone (`filesystem.rs:69-73`).

### 2.4 The same path in wasi-python's Rust spike

wasi-python's Rust spike linked `wasmtime_wasi::p1::add_to_linker_sync` and never set the flag (READ: `2026-10-23-…/spike/src/main.rs:182`, `295`). Its memo found Rust "8 percent slower" than bare wasmtime-py on file calls and left it "unexplained" (READ: `2026-10-23-…on-speed.md` §3.2). The cause is the same hand-off: a Rust embedding that does not set the flag pays it too (INFERRED).

## 3. The experiment

### 3.1 The probe

`probe.c` makes N calls of one kind on `f.txt` in the first preopen. It times its own loop with `clock_time_get`, so instantiation and setup are not in the number. Built with wasi-sdk 34: `clang --target=wasm32-wasip1 -O2` (sha256 `d7fe6469…05bb`). The data file holds 200,000 bytes. N = 20,000, and each arm runs 7 times; the table gives the median. Drivers: `cost.py` (wasmtime-py), `cli.sh` (CLI), `run_all.sh` (everything, under the lock). Raw results: `results/run_all.txt` and `results/*.jsonl`.

### 3.2 µs per call (EXECUTED)

| call | wasmtime-py 51 stock | patched C API, flag off | **patched, flag on** | CLI | CLI with `-W timeout=3600s` | stock, pinned to 1 CPU |
|---|---:|---:|---:|---:|---:|---:|
| `fd_read`, 1 byte | 26.7 | 33.9 | **0.47** | 0.33 | 29.6 | 4.06 |
| `fd_read` at end of file | 26.7 | 27.6 | **0.42** | 0.28 | 29.7 | |
| `fd_filestat_get` | 50.8 | 52.8 | **0.72** | 0.59 | 63.2 | 7.57 |
| `fd_fdstat_get`, a file | 54.6 | 53.6 | **0.52** | 0.42 | 58.7 | |
| `path_filestat_get` | 57.0 | 61.6 | **1.24** | 0.94 | 58.1 | |
| `path_open` + `fd_close` | 34.1 | 31.1 | **1.49** | 1.23 | 32.5 | |
| `clock_time_get` (floor) | 0.11 | 0.11 | 0.11 | 0.11 | 0.17 | |

The guest's own share is 146 to 385 instructions per call, including the probe's loop and its `strcmp` chain (EXECUTED: `cost.py … --fuel`, `results/fuel.jsonl`). That is well under 0.1 µs at study A's 8.5 B instructions per second (INFERRED).

What the table shows:

- **The control holds.** The patched library with the flag off costs what the stock library costs. So the build is not faster by itself.
- **The CLI with a timeout costs what wasmtime-py costs.** Its only change is the flag (§2.2).
- **With the flag on, wasmtime-py is within 0.1 to 0.3 µs of the CLI.** The remaining gap is `in_tokio`'s `block_on` and `yield_now` on each call, which the sync linker still pays (INFERRED from `runtime.rs:147-152`).
- Study A's 56–58 µs was `path_filestat_get`, which makes two hand-offs. Study interp's table agrees with this one row by row.

### 3.3 The patch, and how it was loaded

`c-api-allow-blocking-current-thread.patch`: 8 lines in `crates/c-api/src/wasi.rs` (a function that calls `config.builder.allow_blocking_current_thread(enable)`) and 11 lines of declaration and doc comment in `crates/c-api/include/wasi.h`.

- **Build:** `cargo build -p wasmtime-c-api --release -j4` against v51.0.0, with the crate's default features. 218 crates compiled, and the build finished in 3 min 04 s (EXECUTED: `results/c-api-build-tail.txt`).
- **The library:** 44.1 MB unstripped. After `strip` it is 30.1 MB, against the shipped 29.7 MB, and it exports 784 functions against the shipped 783, the one more being the new function (EXECUTED: `stat`, `strip` and `nm -D --defined-only`, with the commands and output in `results/c-api-library.txt`; the build's hashes are in `results/c-api-build-tail.txt`, and the library measured has the same hash).
- **Loading it:** a copy of the venv's `wasmtime` package went to `/home/user/wasi-perl-downloads/pylib-patched/`, with `_libwasmtime.so` replaced. The drivers put that folder first on `sys.path` (`--lib`) and call the new function through `wasmtime._ffi.dll` before the preopen (`--blocking`). The bench venv itself was not touched.
- **Order** (EXECUTED: `cost.py … --blocking-late`, `results/run_late.txt`): with the flag set after the preopen, `fd_read` costs 0.51 µs and `fd_filestat_get` 0.76 µs, but `path_filestat_get` costs 54.8 µs and `path_open` + `fd_close` 34.5 µs. That is the split §2.3 predicts.

### 3.4 The hand-off, seen and timed

`strace -f -c`: system calls per WASI call, from the difference between 2,010 calls and 10 (EXECUTED: `syscalls.sh`; `results/syscalls-stock.txt`, `results/syscalls-patched.txt`):

| call | wasmtime-py stock | patched, flag off | patched, flag on | CLI | CLI with a timeout |
|---|---|---|---|---|---|
| `fd_read` | futex 8.0, pread64 1 | futex 7.9, pread64 1 | pread64 1 | pread64 1 | futex 8.5, pread64 1 |
| `fd_filestat_get` | futex 18.4, statx 2 | futex 17.9, statx 2 | statx 2, futex 0.2 | statx 2 | futex 16.0, statx 2 |
| `fd_fdstat_get` | futex 15.1, statx 1, fcntl 1 | futex 17.4, … | statx 1, fcntl 1 | statx 1, fcntl 1 | futex 15.9, … |
| `path_filestat_get` | futex 17.2, statx 2 | futex 16.1, statx 2 | statx 2 | statx 2 | futex 18.6, statx 2 |
| `path_open` + `fd_close` | futex 4.9, statx 1, openat2 1, close 1 | futex 4.2, … | statx 1, openat2 1, close 1 | the same | futex 4.4, … |

The real work, `pread64` or `statx`, is the same in every arm. The `futex` calls come only with the flag off, and they scale with the number of `run_blocking` calls in §2.1: about 8 for one, 15–18 for two. The process has 15 to 19 threads after a stock run, and 9 after every run with the flag on, because the blocking pool is never started (EXECUTED: `cost.py … 20000 7 … --threads`, three runs per arm, `results/threads.txt`). The stock count varies because the pool grows on demand.

**What a hand-off costs here, without wasmtime** (EXECUTED: `handoff.c`, in `results/run_all.txt`): 25.9 to 26.4 µs per round trip across the VM's CPUs, three runs, and 2.53 to 2.57 µs pinned to one CPU with `taskset -c 0`. wasmtime-py pinned to one CPU drops the same way, to 4.1 µs per `fd_read` and 7.6 µs per `fd_filestat_get`. So on this VM the cost is the wake-up latency of a sleeping vCPU, about 13 µs each way (INFERRED). On other machines it will be smaller. wasi-python's macOS M1 numbers give about 17 µs per file call on bare wasmtime-py (INFERRED: 387 ms over 22,000 calls, READ `2026-10-23-…on-speed.md` §2). But it is never zero, and the flag removes it everywhere.

### 3.5 Perl, end to end (EXECUTED)

`perl_calls.py` calls `wasi_perl.run` from this worktree's `src/` on the base artifact set (`/home/user/wasi-perl-wt/perf-base/build/artifacts`). The flag is set by wrapping `WasiConfig.__init__`, so it comes before the runner's preopens. Each arm made 15 warm calls, in two interleaved rounds. The table gives the median ms of round 1 / round 2 (`results/perl_calls.jsonl`):

| program | stock | patched, flag off | **patched, flag on** |
|---|---:|---:|---:|
| `-e 1` | 3.53 / 3.39 | 3.44 / 3.74 | 3.42 / 3.99 |
| `-MList::Util=sum -Mstrict -Mwarnings` | 9.69 / 9.49 | 9.20 / 9.43 | **6.67 / 7.52** |
| `-ne` over one 0.94 MB file | 42.45 / 41.69 | 43.70 / 42.73 | **38.66 / 38.57** |
| `-ne` over 200 files of 50 lines | 56.62 / 61.13 | 60.88 / 58.33 | **6.69 / 7.59** |
| 2,000 `-e` stats | 112.86 / 121.54 | 121.03 / 172.61 | **7.91 / 8.35** |
| `sleep 2` under a 0.5 s deadline | 0.51 s, "deadline" | 0.52 s, "deadline" | 0.51 s, "deadline" |

- **Outputs are equal.** Exit code, stdout hash and stderr hash are identical across all 90 calls of each program, in all three arms (EXECUTED).
- **A bare call does not move beyond the noise.** In three rounds of 200 calls, the medians were stock 3.49, 3.58 and 4.10 ms, and flag on 3.29, 3.59 and 3.55 ms (EXECUTED: `bare_calls.py`, `results/bare_calls.txt`). One `-e 1` makes 6 `statx` and 3 `openat2` per run in the guest's name (EXECUTED: `strace -f -c` of `e1_calls.py` over 1 run and over 21, the difference divided by 20, `results/e1-syscalls.txt`). That is a handful of hand-offs, worth roughly 0.2–0.4 ms (INFERRED). Study A's "floor about 0.5–0.8 ms" with this change is therefore not borne out for a bare call. The flag pays where files are touched, not at startup.
- **The deadline still holds.** The guest slices its waits (§2.3), so the flag's `thread::sleep` path cannot hold a call past its deadline.

## 4. The options

| rank | option | per file call | work | what it costs the project |
|---|---|---|---|---|
| **1** | **(i) upstream: `wasi_config_allow_blocking_current_thread` in the C API, and a `WasiConfig` property in wasmtime-py** | 0.4–1.5 µs (EXECUTED, §3.2) | 19 lines in wasmtime (the patch); about 10 lines in wasmtime-py (the binding in `_bindings.py`, regenerated by its `ci/cbindgen.py`, and a setter in `_wasi.py`); in our runner, one call before the first `preopen_dir` in `_set_up` and a check | nothing new: the wheel stays `py3-none-any` on wasmtime-py's wheels |
| 2 | guest-side call cuts already ranked by study interp (64 KiB buffer, `isatty` answered for files the fd-table opened, `PERL_DISABLE_PMC`) | removes calls rather than cheapening them | recipe changes, already specified in outline | none new; complementary to rank 1 |
| 3 | (iii) serve file calls in Python, as a mount does (study E) | 16–19 µs (READ: `perf-vfs-io.md` §2) | already planned for provider-backed files | right for providers; wrong as a speed fix for grants once rank 1 lands |
| 4 | (ii) our own PyO3 crate embedding wasmtime | the same 0.4–1.5 µs, from the same flag (INFERRED: the same `run_blocking`) | a new runner core; see §4.2 | per-platform wheels, a Rust toolchain pin, about 190 licence notices, a release per wasmtime patch |
| — | (iv) other levers in the source, below | | | |

### 4.1 (i) Upstream

- **What:** the patch in this folder. It is opt-in, so no embedder's behaviour changes. Its doc comment should say to call it before `wasi_config_preopen_dir`, because a preopen keeps the value it was made with (§2.3, EXECUTED §3.3). A cleaner upstream form would apply the flag regardless of order. That needs the C API to keep the preopens until `wasmtime_context_set_wasi`, which moves the moment a bad folder is reported. Maintainers should choose. Also worth asking upstream: should the C API's sync path default to on? Nothing can cancel a sync host call there anyway (INFERRED: the CLI's reason for turning it off, `tokio::time::timeout`, has no counterpart in the C API's sync path).
- **Upstream state:** wasmtime `main`'s `crates/c-api/src/wasi.rs` has no such function today (READ: fetched from raw.githubusercontent.com on 2026-11-24). wasmtime-py's `main` `_wasi.py` has no "blocking" either (READ, same day).
- **Timing:** wasmtime releases a major a month, and wasmtime-py ships the same day (READ: wasi-python's `2026-10-18-a-rust-core…` §1.6). If accepted, the change reaches us in one or two months (INFERRED).
- **Our side, when it lands:** move the pin to the first wasmtime-py that has it. Set the property before the preopens in `runner._set_up` (`src/wasi_perl/runner.py:333-343`). Tests count operations, not time. So the check can run under `strace -c` in a slow test and assert no `futex` per file call: with the flag, `futex` drops from 4.9–18.4 per call (stock) to at most 0.2 (EXECUTED, §3.4). That is the better check, since it is a count per call. Threads are a weaker sign: the process has 9 after every run with the flag, and 15 to 19 without it, varying because the pool grows on demand (EXECUTED, §3.4, `results/threads.txt`). Fuel and outputs do not change (EXECUTED, §3.5), so no golden output moves.
- **Not before:** calling the symbol through `_wasmtime_ffi` only when it exists would work today on nothing, because no released library has it.

### 4.2 (ii) Our own Rust crate

What wasi-python found and decided (READ):

- `2026-10-18-a-rust-core-with-js-and-python-wrappers.md`: "adopt in part, after 0.1.0". A Node package belongs on a Rust core. **PyO3 under the Python wheel is "Not adopted"**, to be revisited only if wasmtime-py stops shipping with wasmtime or a measured fault cannot be worked around (§2.5 item 4, §2.6). The stated costs: a toolchain pin per build host, wasmtime's 581-package lock to pin and licence, about eight abi3 wheels per release, a release for every wasmtime patch, and "one core, two thick wrappers, two test suites".
- `2026-10-18-a-rust-runner-spike-against-the-judge.md`: the same wasmtime and Config give the same fuel to the instruction and equal outputs. The easy fifth of a core is 376 lines. The binary is 13.9 MB (8.7 MB lean with LTO). It has 189 crates, all permissively licensed, and a cold build takes 88 s.
- `2026-10-23-a-rust-runner-against-wasmtime-py-on-speed.md`: a Rust core is no faster at compute. It is 35 to 47 times faster per host callback, and 2 to 8 times faster at capturing output. On file calls on a grant it was not faster, which §2.4 now explains.

For wasi-perl:

- **It would not fix this problem better.** The fix is the same boolean, in either language. Rust would add a few hundred nanoseconds of headroom at most (INFERRED from §3.2: flag on against the CLI).
- **Wheels.** Today `wasi_perl` builds one pure `py3-none-any` wheel with hatchling (READ: `pyproject.toml:36-37`), and it depends on wasmtime-py's platform wheels (`pyproject.toml:20`). A PyO3 core makes our wheel per platform: manylinux x86_64 and aarch64, macOS x86_64 and arm64, Windows x86_64, about five to eight with abi3. Each holds a binary of 9 to 30 MB (READ: the spike's sizes; the 30 MB library above). It needs a maturin build matrix in CI, which this project does not have.
- **Our own native code.** We would ship native code we compiled. Its crate licences would ship in the wheel under constitution §4.11, and its supply chain would become ours under the pin rule (`AGENTS.md`).
- **How the checks would move.** `_engines.wasmtime()`'s version gate and `_wasmtime_ffi.py`'s reach-ins (epoch callback, stdin bytes) would become Rust API calls. The `.cwasm` cache key would have to name our crate's wasmtime and Config, and caches would never be shared with wasmtime-py's (wasi-python spike §3.2). `tests/runner/test_hardening.py` finds wasmtime-py's library to prove that a writable grant refuses a hard link to it (READ: lines 34-44); that check would move to our own extension module. The `tests/oneliners` oracle and `tests/perl_t` would not change in kind, but they would have to run on every platform wheel.
- **When it would pay:** host callbacks, such as stdout and stderr capture, which go through `stdout_custom` ctypes callbacks today (`runner.py:336-337`), and provider calls. Those are a separate study's question.

### 4.3 (iii) Serving file calls in Python (study E)

- **Speed:** 16–19 µs per call, against 26–57 µs today (READ: `perf-vfs-io.md` §2). With rank 1, a grant answers in 0.4–1.5 µs. A Python mount would then be 10 to 40 times slower than a grant on every call (INFERRED from the two tables).
- **What it means for study E:** keep mounts for provider-backed files, where they are needed anyway. Do not move grants or the library preopen into Python for speed. `perf-vfs-io.md` §2's line "a mount serves file calls faster than a grant does" holds only while the flag is off.

### 4.4 (iv) Other levers the source and the experiment show

- **No cheaper call exists.** `fd_pread`, `fd_readdir`, `fd_write` to a file and every stat go through the same `run_blocking` (`filesystem.rs:523-1151`, `p1.rs:673`). `fd_read`'s direct read into guest memory needs the flag too (`p1.rs:1684-1690`). So no other call avoids the hand-off for `fd_read` (READ).
- **Pinning the process to one CPU** cuts a hand-off from about 26 to about 4 µs (EXECUTED, §3.2). But it confines the embedder's whole process, and every guest in it, to one CPU. A library must not do that. It is rejected, and kept only as the diagnosis.
- **Writing the flag's byte in `wasi_config_t` by offset through ctypes.** The struct holds a `WasiCtxBuilder`, whose layout Rust does not fix (`wasi.rs:32-35`). It would be memory-unsafe guesswork in a sandbox's host, and it is rejected (INFERRED).
- **Shipping our own patched `libwasmtime` inside the wheel** carries the costs of (ii) without its gains. Rejected.
- **Fewer calls from the guest** stays useful with the flag on: each call still costs 0.4–1.5 µs and a guest-to-host crossing (rank 2).

## 5. Limits

- One machine, a 4-vCPU VM, where a cross-CPU wake-up is slow. The hand-off's size is this VM's. The fact that the flag removes the hand-off holds everywhere (READ, §2).
- The patched library was built with the crate's default features via `cargo`, not with wasmtime's release CMake flow. The flag-off control matches stock (§3.2), so this does not change the finding.
- Writes to a writable grant (`-pi -e`) were not timed. They go through the same `run_blocking` (`p1.rs:669-673`), so they should gain alike (INFERRED).
- Only Linux x86_64. macOS and Windows were not run.
- The `tests/oneliners` and `tests/perl_t` suites were not run with the flag on. §3.5's byte equality on five programs is the evidence on behaviour. A specification that turns the flag on should run both.

## 6. Reproducing

```
# sources (outside the repository)
git clone --depth 1 --branch 51.0.0  https://github.com/bytecodealliance/wasmtime-py  wasmtime-py-51.0.0
git clone --depth 1 --branch v51.0.0 https://github.com/bytecodealliance/wasmtime     wasmtime-v51.0.0
# the patched C API
(cd wasmtime-v51.0.0 && git apply <this folder>/c-api-allow-blocking-current-thread.patch && \
  flock /home/user/wasi-perl-wt/machine.lock cargo build -p wasmtime-c-api --release -j4)
cp -r <venv>/lib/python3.11/site-packages/wasmtime pylib-patched/ && \
  cp <target>/release/libwasmtime.so pylib-patched/wasmtime/linux-x86_64/_libwasmtime.so
# the probe and its data
$SDK/bin/clang --target=wasm32-wasip1 --sysroot=$SDK/share/wasi-sysroot -O2 -o probe.wasm probe.c
cc -O2 -pthread handoff.c -o handoff
head -c 200000 /dev/zero | tr '\0' x > data/f.txt
# every timing, then the syscall counts, then Perl
flock /home/user/wasi-perl-wt/machine.lock ./run_all.sh <python> <wasmtime> probe.wasm data pylib-patched handoff results
WASMTIME=<wasmtime> ./syscalls.sh <py|py-patched|py-blocking|cli|cli-timeout> <kind> <python> cost.py probe.wasm data pylib-patched
WASI_PERL_ARTIFACTS=<set> python perl_calls.py <worktree>/src data 15 results/perl_calls.jsonl [--lib pylib-patched [--blocking]]
WASI_PERL_ARTIFACTS=<set> python bare_calls.py <worktree>/src 200 [--lib pylib-patched --blocking]
# the thread counts, the library's sizes and exports, and the system calls of -e 1
python cost.py probe.wasm data 20000 7 /dev/null --threads [--lib pylib-patched --blocking]   # results/threads.txt
strip -o stripped.so <target>/release/libwasmtime.so; nm -D --defined-only stripped.so | awk '$2 == "T"' | wc -l   # results/c-api-library.txt
strace -f -c -o s1 python e1_calls.py <set> 1; strace -f -c -o s21 python e1_calls.py <set> 21   # results/e1-syscalls.txt
```

`perl_calls.py` also needs `data/big.txt` (`seq 1 150000`) and `data/many/f000.log` to `f199.log` (`seq 1 50` each).

## One line

wasmtime-py's file calls cost 27–57 µs because wasmtime's C API never sets `WasiCtxBuilder::allow_blocking_current_thread`. So every filesystem operation makes a round trip through tokio's blocking pool, which costs 26 µs on this VM and needs no wasmtime to reproduce. The CLI sets the flag; a 19-line C API patch exposing it brings wasmtime-py to the CLI's 0.4–1.5 µs per call and makes file-heavy Perl 8 to 14 times faster, with identical output. Send it upstream rather than build a Rust core.
