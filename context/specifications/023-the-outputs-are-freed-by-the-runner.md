# 023 — The outputs are freed by the runner

- **Status:** agreed by the owner 2026-11-24, through AskUserQuestion: "Agree, build it (Recommended)" (as the orchestrator relayed it). Written by an investigating agent, 2026-11-24, from the evidence below, for the abort at interpreter exit seen twice on `ubuntu-24.04-arm` (the roadmap's item on it, which called it one-time, is corrected in the same change).
- **Owner:** Clay Gendron
- **Kind:** `src/wasi_perl/runner.py` (`_set_up`, `_call`, `_writer`) and `src/wasi_perl/_wasmtime_ffi.py` (a new `set_custom_outputs`, `NEEDED`, `on_epoch`); the checks in `tests/runner/test_output_release.py`; `CHANGELOG.md`. No guest change, no artifact change, no public API change.
- **Depends on:** nothing unbuilt. Independent of 022.

Labels: EXECUTED means run and seen. READ means read from a file. INFERRED means reasoned.

## Intent

After `run()` returns, wasmtime must have nothing left to run in Python. Today it does: the free of each output stream, a Python callback, runs on a worker thread of wasmtime-wasi's tokio runtime some time after the store is closed. If Python is already finalizing when that worker asks for the GIL, CPython 3.11 ends the worker with `pthread_exit`. The forced unwind reaches Rust's thread entry, which cannot unwind, and the whole process aborts with exit code 134, after a call that succeeded. After this specification, the runner gives wasmtime its two outputs with no free at all, and drops its own record of a call's streams itself, on the calling thread, once the store is closed.

## Terms

- **Custom output:** wasmtime's C API hook for a guest's stdout or stderr: a write callback, a data pointer and an optional free (`wasi_config_set_stdout_custom`, `wasi_config_set_stderr_custom`). wasmtime-py's `WasiConfig.stdout_custom` and `stderr_custom` use it, with its own Python free, `custom_finalize`.
- **Stream worker:** the tokio task that wasmtime-wasi makes for each such output, which holds a clone of the stream and writes on a worker thread.

## The evidence

### 1. The rate in CI

Every run of `ci.yml` on the repository, 68 runs, of which 58 reached the ARM wheel-test job, was read for the conclusion of the step "The installed package is the one imported, and it runs perl" (EXECUTED: `gh api repos/ClayGendron/wasi-perl/actions/workflows/ci.yml/runs?per_page=100`, then `gh api repos/ClayGendron/wasi-perl/actions/runs/<id>/jobs?filter=all` per run, the step of the job named `test the installed wheel (ubuntu-24.04-arm)`, 2026-11-24).

| runs | code | the step failed |
|---|---|---|
| 45 | without 019's guest change (`c6ab588`); every run from `a9e5074` to `c79b8c9`, with 020's three runs | 0 |
| 13 | with it: 019's own runs `48ef643`, `d4db0ab`, `f47b716`, `8fb8d1b`; `50e0298`; and every later run, `5d93c62`, `3d51777`, `aaf2c50`, `5a80c37`, `eba03ba`, `49fd412`, `90f4152`, `388ddd3` | 2 (`f47b716`, job 138774884353; `50e0298`, job 138792311744) |

- A pull request's run tests its head merged with `dev`; a run is counted as carrying 019 when its head has `c6ab588` (EXECUTED: `git merge-base --is-ancestor`) or `dev` held `50e0298` when it ran.
- 0 of 45 against 2 of 13 is weak evidence that 019 matters. If the two failures were spread at random over the 58 runs, both would fall in the 13 with probability C(13,2)/C(58,2) = 78/1653 = 0.047 (INFERRED: arithmetic). The overall rate is 2 in 58, about 3%.
- Both failing logs show the same lines after `run()` had returned `Completed(exit_code=0, stdout=b'hello\n', ...)`: `thread 'tokio-rt-worker' panicked at /rustc/58cf8f9c35b355199727627d07ae34a4e2846d67/library/core/src/panicking.rs:225:5: panic in a function that cannot unwind`, `thread caused non-unwinding panic. aborting.`, exit 134. The first has two such panics, from two workers (READ: both jobs' logs, through the GitHub API, 2026-11-24). The job's Python is 3.11.14 (READ: the same log).
- The diagnostic pull request's counts on ARM are in §6: there the pre-019 wheel aborted too.

### 2. Where the stream's free runs

- wasmtime 51's C API makes a custom output a `CustomOutputStream` that implements `StdoutStream` by `async_stream` alone (READ: `crates/c-api/src/wasi.rs:187-268` at tag `v51.0.0`, a shallow clone under `context/prior_art/wasmtime`, Apache-2.0 WITH LLVM-exception).
- So wasmtime-wasi's default `p2_stream` wraps it in an `AsyncWriteStream` (READ: `crates/wasi/src/cli.rs:188-192`). That spawns a task on its tokio runtime holding the stream's clone (READ: `crates/wasi/src/p2/write_stream.rs:143-147`). WASI preview1 makes one such stream for fd 1 and one for fd 2 when the context is built (READ: `crates/wasi/src/p1.rs:379-417`, `crates/wasi/src/p2/stdio.rs:22-33`).
- Dropping the stream aborts the task (READ: `crates/wasi/src/runtime.rs:55-59`). The task's future, and with it the clone, is dropped later on a worker thread. The last clone's drop calls the free (READ: `crates/c-api/src/lib.rs:111-125`). wasmtime-py's free is `custom_finalize`, a ctypes callback, which takes the GIL (READ: `wasmtime/_wasi.py:198-214` of wasmtime-py 51.0.0 in `.venv`).
- A guest's writes are complete when `fd_write` returns: preview1 writes with `blocking_write_and_flush` (READ: `crates/wasi/src/p1.rs:290-316`). So after the guest ends, the worker's only remaining Python is the free.
- Seen: with wasmtime-py alone and a module that writes "hi" to fd 1, both the writes and the frees ran on threads Python did not start ("Dummy-N"), never the main thread, and one free of each store ran after `store.close()` had returned, 0.15 ms later (EXECUTED: `which_thread.py`, in "Evidence scripts", under `.venv`'s Python 3.13.18, three stores).
- Seen in the runner: of 200 runs of the smoke script on x86-64 under Python 3.11.18 with a warm cache, 12 still had a free pending when `run()` returned (11 one, 1 both); none had one pending after the last atexit hook (EXECUTED: `pending.py`, in "Evidence scripts", with `WASI_PERL_ARTIFACTS` set to a pre-019 set). The atexit hook's own ctypes calls release the GIL and let the worker in.

### 3. What CPython does to that worker

- CPython 3.11: a thread that takes the GIL once the runtime is finalizing calls `PyThread_exit_thread` (READ: `Python/ceval_gil.h:207,250,289` on branch 3.11 at `070a348`), which is `pthread_exit(0)` (READ: `Python/thread_pthread.h:361-367`). `Py_FinalizeEx` runs the atexit hooks, then marks the runtime finalizing, then flushes stdout and stderr (READ: `Python/pylifecycle.c:1779,1796,1811`).
- CPython 3.13 hangs such a thread instead, since gh-87135 (READ: `Python/ceval_gil.c:305-318` on branch 3.13 at `cf31c24`). 3.12 is not read; it is INFERRED to exit as 3.11 does.
- `pthread_exit` unwinds the thread. Unwinding into Rust's `thread_start`, an `extern "C"` function, aborts with "panic in a function that cannot unwind" (EXECUTED: the backtrace in §4).

### 4. The abort, forced, on x86-64

`force_abort.py` (in "Evidence scripts") does what the race does by chance: it holds the GIL from `store.close()` (a pure-Python busy wait with the switch interval at 1000 s) until Python is finalizing, then releases it once, in the `__del__` of an object torn down with `builtins`.

| Python | the free | aborted (exit 134) |
|---|---|---|
| 3.11.18 | wasmtime-py's `custom_finalize` | 8 of 20; again 21 of 40 |
| 3.11.18 | NULL, write callback alone (`force_abort_free.py late null-free`) | 0 of 40 |
| 3.13.18 | wasmtime-py's | 0 of 30, and 0 of 10; the free stays pending and the thread hangs |

(EXECUTED: each a fresh process, x86-64, 2026-11-24.) When it aborts, it prints what CI printed, with the same rustc hash and line, once per pending free; with `RUST_BACKTRACE=full` the stack is `panic_cannot_unwind`, `<std::sys::thread::unix::Thread>::new::thread_start`, `start_thread`, `clone3` (EXECUTED). An abort happens only on runs that still had a free pending at finalization (EXECUTED: the pending count printed in the late `__del__`).

### 5. Why it was not reproduced before

The earlier 500 local runs were on x86-64 under Python 3.13 (INFERRED: the `.venv`'s Python), where the worker hangs instead of exiting. 300 warm and 150 cold runs of the smoke under Python 3.11 on x86-64 did not abort either (EXECUTED): there the atexit hook's ctypes calls always let the worker take the GIL in time (§2). On the ARM runner it loses that race now and then (§6).

### 6. On ARM (the diagnostic pull request)

Draft pull request #31 changed only the ARM leg of `ci.yml`: it ran the smoke script many times, each a fresh process, with `RUST_BACKTRACE=full`, and counted the exits by SIGABRT (EXECUTED: run 45510880573, job 138860099069, `ubuntu-24.04-arm`, aarch64, 4 cores, Python 3.11.14 and 3.13.11, 2026-11-24; READ: its log).

| variant | runs | aborted |
|---|---|---|
| dev's wheel, a fresh cache per run | 100 | 0 |
| the pre-019 wheel (dev's run at `8897c22`), a fresh cache per run | 100 | 0 |
| dev's wheel, warm cache | 300 | 0 |
| the pre-019 wheel, warm cache | 300 | **1** |
| dev's wheel, the runner's atexit hook unregistered | 200 | **6** |
| dev's wheel, the hook's release called before exit | 200 | 0 |
| dev's wheel, waiting (GIL released) until wasmtime-py's table of custom outputs is empty | 200 | 0 |
| dev's wheel, `os._exit(0)` after the flush | 200 | 0 |
| dev's wheel under Python 3.13 | 200 | 0 |
| the forced case, wasmtime-py only, 3.11 | 40 | **18** |
| the forced case, 3.13 | 40 | 0 |

- Every abort printed CI's lines, with the same rustc hash and line, and this stack on aarch64: `panic_nounwind_fmt`, `panic_nounwind`, `panic_cannot_unwind`, `<std::sys::thread::unix::Thread>::new::thread_start`, then two frames of libc without symbols. The pre-019 abort printed two panics, from two workers, as CI's first failure did.
- With the free counted (the "pending" variant, 200 runs, no abort): 17 runs had one free still pending when `run()` returned and 1 had both; none had one after the last atexit hook. On x86-64 the same count was 12 of 200 (§2).
- So: the pre-019 wheel aborts too, and 019 is not the cause. The atexit hook makes the abort rarer, not more likely: without it, 6 of 200. Waiting for the frees, or skipping finalization, or Python 3.13, each gave none.
- The natural rate here, 1 in 800 plain runs, is lower than CI's 2 in 58 first steps. Those ran first on a fresh runner, once; why that is more exposed is not known (INFERRED candidates: a cold machine, the first compile of the module in the process).

### 7. What 019 has to do with it

Nothing in 019 touches the outputs or the runner's exit, and the pre-019 wheel aborted on ARM in the same way (§6). The two failures falling on 019's runs (§1, p = 0.047) is taken as chance. The race is older than 019: wasmtime-py's custom outputs have been the runner's since specification 004 (EXECUTED: `git log -S stdout_custom`, `b5dea95`).

## Decisions

### 1. The runner registers its two outputs itself, with no free

- `_wasmtime_ffi.set_custom_outputs(wasi, key_out, key_err)` calls `_ffi.wasi_config_set_stdout_custom` and `_ffi.wasi_config_set_stderr_custom` with one write callback and a NULL free. The callback and the NULL are C function pointers of the binding's own types (`_bindings._wasi_config_set_stdout_custom.argtypes[1]` and `[3]`), made once at import and kept for the process's life, as wasmtime may call the write as long as a store lives.
- Why NULL: the free is what runs on the worker after the call returns (evidence §2). With no free, the worker's drop of its clone runs no Python (evidence §4: 0 of 40).
- `_set_up` calls it in place of `wasi.stdout_custom = ...` and `wasi.stderr_custom = ...`. wasmtime-py's table of custom outputs (`wasmtime._wasi.CUSTOM_OUTPUTS`) is no longer used by the runner.
- Adapted from wasi-python's `_wasmtime_ffi.set_custom_outputs` and `_outputs._CallbackCapture` at `f9bf678` (same owner, Apache-2.0), with one change: wasi-python passes a Python free, `_free_output`, which carries the same hazard. Its main capture, a pipe that wasmtime writes on the guest's own thread, is not taken here (Decision 5).

### 2. The runner's own table of streams, freed on the calling thread

- A process-wide `dict[int, _Stream]` under a lock, keyed by `itertools.count(1)`, so a key is never reused and never 0 (a NULL data pointer reads back as `None`). Writes arrive on wasmtime's threads while other calls register and drop keys on theirs, hence the lock.
- `_call` takes two keys before `_set_up`, and pops them in its `finally`, after `store.close()`, on the calling thread, on every ending: a normal exit, a trap, a host error at the guest's start, a `KeyboardInterrupt`.
- The write looks its key up under the lock. A key not found, a write after its call ended, which preview1's blocking write rules out (evidence §2), is reported written and dropped.
- The write never raises and never prints: an exception while keeping a piece marks that stream truncated, so the result names it, and the piece is reported written. Today wasmtime-py's `custom_call` prints "failed custom output" and returns EIO for that case; a capped stream already reports a piece past its cap as written (`_Stream.write`), and this does the same.

### 3. The epoch callback's free is NULL too

`_wasmtime_ffi.on_epoch` gives wasmtime a Python free that does nothing (`lambda _data: None`). It runs when the store is deleted, which is on the calling thread, so it does not race; it is made NULL by the same rule, as wasi-python's is (`_wasmtime_ffi.py:305-313` at `f9bf678`, READ), and one callback object fewer is kept per call.

### 4. `NEEDED` names the new private names

`wasmtime._ffi.wasi_config_set_stdout_custom`, `wasmtime._ffi.wasi_config_set_stderr_custom` and the record `wasmtime._bindings._wasi_config_set_stdout_custom` (read for its argument types; `_has` already checks a record has four) join `NEEDED`, so a wasmtime-py that moved them is refused at the first `run()` with the one `ImportError`.

### 5. What is not chosen

- **Waiting for the frees after `store.close()`.** It would keep Python running on a worker and add a wait, with a timeout to choose, to every call.
- **`os._exit` or skipping finalization.** It is the embedder's process; the runner does not end it.
- **wasi-python's pipe capture.** It moves the writes off the workers too, and is faster (its docstring, READ), but it is a reader thread, a platform channel and a fallback. It is a later specification of its own if output speed matters.
- **Waiting for a wasmtime fix.** The c-api's `CustomOutputStream` already implements `OutputStream` itself; returning it from `p2_stream` would keep its writes and its drop on the guest's thread. That is worth reporting upstream (to wasmtime and to wasmtime-py), and this change does not depend on it.

### 6. What stays

The mounts' host functions are Python callbacks with wasmtime-py's free, but wasmtime calls them on the guest's thread and frees them when a linker is deleted, on the thread that deletes it (`_engines.forget` or the atexit hook). `_release_at_exit` stays: it is what frees the engines while wasmtime-py's bindings exist.

## Checks (written first, seen failing)

In `tests/runner/test_output_release.py`, on the stand-in guest:

1. `test_the_outputs_give_wasmtime_no_python_free`: both C setters, wrapped, are called once each, and each is given no free (None or a NULL pointer). On dev: FAILED, two Python frees (EXECUTED, 2026-11-24).
2. `test_nothing_of_a_call_runs_in_python_after_it_returns`: every free the setters are given is wrapped so that, called off the calling thread, it waits until `run()` has returned; the check asserts none ran after. On dev: FAILED in 15 of 20 runs, `[True, True]`; it passes when both frees happen to run on the calling thread inside `store.close()` (EXECUTED). Check 1 is the deterministic one.

## Acceptance

- Both checks pass; `uv run pytest -m "not slow"`, `ruff check` and `ruff format --check` stay green.
- On the next ARM runs the step does not abort. That alone proves little at about 3% (evidence §1); the checks are the proof.

## Evidence scripts

Run from a scratch folder with `-I`, under a virtual environment holding `wasmtime==51.0.0` (and, for `pending.py`, `wasi_perl` and `WASI_PERL_ARTIFACTS`).

### `which_thread.py`

Where and when the writes and the frees run.

```python
"""Where and when wasmtime-py's custom stdout finalizer runs, relative to store.close()."""
import threading, time, wasmtime
from wasmtime import _wasi

WAT = r'''(module
  (import "wasi_snapshot_preview1" "fd_write" (func $w (param i32 i32 i32 i32) (result i32)))
  (memory (export "memory") 1)
  (data (i32.const 16) "hi\n")
  (func (export "_start")
    (i32.store (i32.const 0) (i32.const 16)) (i32.store (i32.const 4) (i32.const 3))
    (drop (call $w (i32.const 1) (i32.const 0) (i32.const 1) (i32.const 8)))))'''

log = []
orig = _wasi.CUSTOM_OUTPUTS.deallocate
def dealloc(idx):
    log.append(("finalize", idx, threading.current_thread().name, time.perf_counter()))
    orig(idx)
_wasi.CUSTOM_OUTPUTS.deallocate = dealloc

eng = wasmtime.Engine()
mod = wasmtime.Module(eng, WAT)
linker = wasmtime.Linker(eng); linker.define_wasi()
writes = []
def w(data):
    writes.append((data, threading.current_thread().name)); return len(data)
for i in range(3):
    store = wasmtime.Store(eng)
    cfg = wasmtime.WasiConfig(); cfg.stdout_custom = w; cfg.stderr_custom = w
    store.set_wasi(cfg)
    linker.instantiate(store, mod).exports(store)["_start"](store)
    t0 = time.perf_counter(); store.close(); t1 = time.perf_counter()
    log.append(("closed", None, threading.current_thread().name, t1))
    time.sleep(0.05)
print("writes:", writes[:2])
base = log[0][3]
for kind, idx, name, t in log:
    print(f"{kind:9} idx={idx} thread={name} t={1e3*(t-base):8.3f} ms")
```

### `pending.py`

The frees still pending when `run()` returns and after the last atexit hook; it writes one line to fd 3: `for i in $(seq 1 200); do python -I pending.py 3>>pending.txt >/dev/null; done`.

```python
import atexit, os, sys
slab = []
res = {}
def last():  # registered first, so it runs after every other atexit hook, wasi_perl's included
    res["atexit_end"] = len(slab[0].handles)
    os.write(3, f"{res['returned']} {res['atexit_end']}\n".encode())
atexit.register(last)
import wasi_perl
done = wasi_perl.run(["-e", 'print "hello\\n"'])
from wasmtime import _wasi
slab.append(_wasi.CUSTOM_OUTPUTS)
res["returned"] = len(_wasi.CUSTOM_OUTPUTS.handles)
print(done)
```

### `force_abort.py`

The forced abort: `python -I force_abort.py late`. With `none` in place of `late` nothing releases the GIL during finalization and it never aborts. `force_abort_free.py` is the same with the outputs registered by `_ffi.wasi_config_set_stdout_custom` and `_stderr_custom`, either with wasmtime-py's free (`late py-free`) or with a NULL one (`late null-free`).

```python
"""Hold the GIL from store.close() until Python is finalizing, then release it once: the tokio
worker that still has to run wasmtime-py's stream finalizer takes the GIL during finalization."""
import builtins, sys, time, wasmtime
from wasmtime import _wasi

WAT = r'''(module
  (import "wasi_snapshot_preview1" "fd_write" (func $w (param i32 i32 i32 i32) (result i32)))
  (memory (export "memory") 1)
  (data (i32.const 16) "hi\n")
  (func (export "_start")
    (i32.store (i32.const 0) (i32.const 16)) (i32.store (i32.const 4) (i32.const 3))
    (drop (call $w (i32.const 1) (i32.const 0) (i32.const 1) (i32.const 8)))))'''

class ReleaseLate:
    """Torn down with builtins, after the runtime is marked finalizing: sleeps, GIL released."""
    import os, time
    def __init__(self, slab):
        self.slab = slab
    def __del__(self):
        self.os.write(2, f"late release, pending={len(self.slab.handles)}\n".encode())
        self.time.sleep(0.05)
        self.os.write(2, f"after sleep, pending={len(self.slab.handles)}\n".encode())

eng = wasmtime.Engine()
mod = wasmtime.Module(eng, WAT)
linker = wasmtime.Linker(eng); linker.define_wasi()
store = wasmtime.Store(eng)
cfg = wasmtime.WasiConfig(); cfg.stdout_custom = lambda b: len(b); cfg.stderr_custom = lambda b: len(b)
store.set_wasi(cfg)
linker.instantiate(store, mod).exports(store)["_start"](store)
sys.setswitchinterval(1000.0)   # the waiting worker never asks the main thread to yield
store.close()                   # a ctypes call: the GIL is released, then taken back
end = time.perf_counter() + 0.05
while time.perf_counter() < end:  # pure Python: the GIL is held throughout
    pass
pending = len(_wasi.CUSTOM_OUTPUTS.handles)
if sys.argv[1:] == ["late"]:
    builtins._release_late = ReleaseLate(_wasi.CUSTOM_OUTPUTS)
```
