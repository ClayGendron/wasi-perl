# Review: 023, the outputs are freed by the runner (PR #33)

Reviewer: a fresh agent (Claude Opus 5.5, `claude-opus-5-5`) that did not write the change. 2026-11-25.

- **Reviewed:** round 1, `git diff origin/dev...HEAD` on `fix/arm-exit-abort` at head `8ce78e8` (`8ce78e8e7c2ab9c01a169877d724c58f9a4faa82`), base `388ddd3`, eight commits. Round 2, `git diff 8ce78e8 a5a3941`, the fix commit `a5a3941` (`a5a3941d8f98969b6fe845f2f0ce05610805d991`). I read it against `AGENTS.md`, `context/constitution.md`, the specification with its landing notes, the PR body, and the closing comment of diagnostic PR #31.
- **Sources:** wasmtime `v51.0.0` (`32656dc`, the clone under `context/prior_art/wasmtime`). wasmtime-py 51.0.0 in the venv. wasi-python at `f9bf678`. CPython branch 3.11 at `070a348`, the commit the spec cites, from a sparse clone in my scratch folder, since removed.
- **Where:** mutations ran in a scratch copy, `/home/user/wasi-perl-wt/review-023-scratch`, since removed. Probes are in `/tmp/claude-0/-home-user-wasi-perl/ecbe2564-8e44-50cd-9bc9-1c1b283f37e5/scratchpad/`. Python 3.11.18 ran from a venv of its own there, so the worktree's `.venv` is back on 3.13.18. The worktree is unchanged apart from this file (EXECUTED: `git status`). Every pytest run held `flock /home/user/wasi-perl-wt/machine.lock`. So did every repeated child run, except the 20-run count in M2's evidence. `build/artifacts` is the worktree's build of dev's module.

Labels: EXECUTED means I ran it and saw the result. READ means I read it in a file or a log. INFERRED means my reasoning.

## Verdict, round 1

**Does not land, round 1.** The code is right, and I would not change any of it. The causal chain is correct at every link I checked. The fix removes the only Python that wasmtime ran after `run()` returned. The table is thread-safe and leaks nothing. The checks fail on dev and pass here. CI is green on all seven jobs, and all 12 checks ran on Python 3.11 in each of the four wheel legs.

What is missing is one round of small fixes, none of them in `src/`:

- the spec never says how wasi-python, which still has the same hazard, would take the fix (M1);
- the spec does not say that the guest's writes still run Python on tokio's workers, which leaves one case of the same panic (M2);
- the Ctrl-C check does not check what the spec says it checks (L1);
- the roadmap still calls 023 "proposed" (L2).

One commit to the spec, the roadmap and one check fixes all four. After that, this lands with no new code to review.

## The causal chain, checked

Each link, from the source (READ unless marked):

1. **The C API's custom output is async-only.** `CustomOutputStream` implements `StdoutStream` only through `async_stream`, which returns `Box::new(self.clone())`. `p2_stream` is not overridden (`crates/c-api/src/wasi.rs:186-266`). It shares one `Arc<CustomOutputStreamInner>`, and the `ForeignData` inside it holds the free.
2. **The default `p2_stream` wraps it in an `AsyncWriteStream`** (`crates/wasi/src/cli.rs:188-192`). Its constructor spawns `w.work(writer)` on wasmtime-wasi's runtime, so the task's future owns the clone (`p2/write_stream.rs:143-147`). preview1 asks for fd 1 and fd 2 when it builds its descriptor table (`p1.rs:379-417`, `p2/stdio.rs:22-33`).
3. **Dropping the stream only aborts the task.** `AbortOnDropJoinHandle::drop` calls `abort()` (`runtime.rs:55-59`). The future, with its clone, is dropped later, by the runtime, on a worker. Whichever `Arc` is dropped last runs `ForeignData::drop`, which calls the free if it is `Some` (`c-api/src/lib.rs:111-125`). That is why the free sometimes runs inside `store.close()` and sometimes on a worker afterwards (the 15 of 20 in check 2).
4. **No write is left once the guest's `fd_write` returns.** preview1 writes in pieces of 4 KiB through `blocking_write_and_flush` (`p1.rs:290-316`). That writes, flushes, then awaits `write_ready` (`cli.rs:539-550`). The worker pops `Flush` only once its queue is empty, and clears `flush_pending` only after `writer.flush()` (`p2/write_stream.rs:69-110`). `poll_ready` stays pending while a flush is pending (`:155-163`). Host calls are not epoch-interruptible, so a deadline cannot cut one short (INFERRED).
5. **CPython 3.11 ends the worker.** A ctypes callback on a foreign thread takes the GIL. Once the runtime is finalizing, `take_gil` calls `PyThread_exit_thread` (`Python/ceval_gil.h:207,250,289`), which is `pthread_exit(0)` (`thread_pthread.h:361-367`). `Py_FinalizeEx` calls the atexit hooks, then `_PyRuntimeState_SetFinalizing`, then `flush_std_files` (`pylifecycle.c:1779,1796,1811`). Every cited line number is right.
6. **The unwind cannot cross `thread_start`.** The stack I saw on dev is CI's: `panic_cannot_unwind`, then `Thread::new::thread_start`, exit 134 (EXECUTED: the forced check's child on dev, below).

The C entry points take the free as `Option<extern "C" fn(*mut c_void)>`, both for the outputs (`wasi.rs:271-281`) and for the epoch callback (`store.rs:151-162`). So a NULL pointer is plainly "no free". `ctypes.cast(_NO_FREE, c_void_p).value` is `None` (EXECUTED: `probe.py`).

## Findings

### Medium

**M1. The spec does not say how wasi-python would adopt the fix, though wasi-python carries the same hazard.**

- **Evidence.**
  - wasi-python's `_CallbackCapture.open` passes `_output_free`, a ctypes `CFUNCTYPE` over `_free_output`, as the free of both outputs (READ: wasi-python `_outputs.py:639-648,661-669` at `f9bf678`).
  - It is the only capture on Windows, where `output_channel()` returns `None` (READ: `_platform_windows.py:489-492`). So on Windows, wasmtime calls a Python free on a tokio worker after the store closes, exactly as this runner did. What CPython 3.11 does to that thread on Windows (`_endthreadex`, not `pthread_exit`) is not studied (INFERRED).
  - Its `close` already pops both keys on the calling thread ("the guest has ended; wasmtime frees the keys later", `:678-682`). So the free there is redundant, and the adoption is small: pass a NULL free of the record's type, and drop `_free_output`, after checking that `close` runs on every ending.
  - Spec 023 names the hazard in a clause ("wasi-python passes a Python free, `_free_output`, which carries the same hazard", Decision 1). Decision 5 says to report the hazard to wasmtime and to wasmtime-py, but not to wasi-python. Nothing in `context/` records the lesson for the sister project. `AGENTS.md` makes wasi-python the guide and wants all project knowledge in this repo. So the place this lesson should go back to is the one place it is not written.
  - The API is unchanged (READ: no change to `__init__.py`, `run`'s signature or `Completed`; `_writer` was private). The adaptation is noted in both module docstrings (READ: `_outputs.py:13-16`, `_wasmtime_ffi.py:12-19`).
- **Fix.** Add a short section to the spec, "For wasi-python". It says: the hazard is live in `_CallbackCapture`, the only capture on Windows; the change is to pass a NULL free and rely on `close`; and the checks here that carry over are 1 and 3, with check 4 run on its Windows leg. Add an open item to `context/roadmap.md` to report it to wasi-python.

**M2. The guest's writes still run Python on tokio's workers. So a `run()` still writing on a daemon thread at exit still panics on a worker. The spec does not record this case.**

- **Evidence.**
  - `_OUTPUT_WRITE` is a Python callback, and wasmtime calls it from the `AsyncWriteStream` worker, not from the guest's thread (READ: link 2 above; spec §2's `which_thread.py` saw "Dummy-N").
  - `daemon.py` starts a `run()` that loops on `$|=1; print "x\n"` on a daemon thread, sleeps 0.3 s, and lets the main thread exit. Under 3.11.18, with the fix, stderr held CI's `panic in a function that cannot unwind` from `tokio-rt-worker` with the `thread_start` frame in 20 of 20 runs (EXECUTED, unlocked). The exit code was 0 in all 30 runs of the fix, 10 of them under the lock, because the main thread's `exit` won the race with the abort. On a slower machine the abort could win and give 134 (INFERRED). dev's code exited 0 in 10 of 10, and the last run's stderr held the same panic (EXECUTED). So this is not a regression, and it is outside 023's Intent, which covers what is left after `run()` *returns*. The CHANGELOG line is accurate as worded.
  - Decision 5 turns down wasi-python's pipe capture only on speed grounds ("if output speed matters"). Yet that capture, which writes on the guest's own thread, is what would close this case. Decision 6 ("What stays") lists the mounts' host functions and the atexit hook, but not the writes.
- **Fix.** Add a line to Decision 6: the write is still Python on a worker, so a call still running when Python finalizes (a daemon thread) can still panic there, as before. Add to Decision 5 that the pipe capture would also close this case. Add an open item for it to the roadmap. No code change.

### Low

**L1. The Ctrl-C check does not check the free, though the spec says that each per-ending check does.**

- **Evidence.** Checks §3 says each ending's check asserts "that both outputs were given no free and that the runner holds neither key afterwards". `CTRL_C`'s setters record only `data`, and the child prints `len(keys)` and `holds` (READ: `test_output_release.py:177-209`). In scratch, I gave wasmtime a Python no-op free (`_NO_FREE = …argtypes[3](lambda _k: None)`). 10 checks failed, but `test_ctrl_c_releases_the_outputs` passed (EXECUTED, mutation c below). Every path sets the free in the same `_set_up` call, so there is no risk today (INFERRED). But the check claims more than it proves.
- **Fix.** In the child's setter, also record `bool(finalizer)`, and print it. Then assert `"interrupted 2 [False, False] False\n"`.

**L2. The roadmap item still says "Specification 023 (proposed)".**

- **Evidence.** `context/roadmap.md:64` ends "Specification 023 (proposed) gives wasmtime no Python free; the evidence is there". The spec's Status is agreed and its landing notes say it was built (READ).
- **Fix.** Say "built on `fix/arm-exit-abort`, PR #33". Or close the item, and leave the next ARM runs as the watch, as the spec's Acceptance does.

**L3. `NEEDED` checks a record's four argument types only by count, so a retyped record gives a bare `TypeError`.**

- **Evidence.** `_has` passes a `_bindings` record when `len(argtypes) == 4` (READ: `_wasmtime_ffi.py:105-112`). `_OUTPUT_WRITE` and `_NO_FREE` then call `argtypes[1]` and `argtypes[3]` at import. With the record's argtypes replaced by four `c_void_p`, `run()` raised `TypeError: cannot be converted to pointer` instead of the one `ImportError` (EXECUTED: `needed.py record-retyped`). A removed `_ffi` name or record gives the clear `ImportError` naming it and the pin (EXECUTED: `needed.py no-ffi-name`, `no-record`). The epoch record already had this property before the change. The pin is `wasmtime>=51,<52`, and `uv.lock` holds 51.0.0 (READ), so a retype inside that range is unlikely (INFERRED).
- **Fix.** Optional. In `_has`, for a record, also require that `argtypes[1]` and `argtypes[3]` are ctypes function-pointer types (`issubclass(t, ctypes._CFuncPtr)`). Or leave it, since the import fails either way.

## What was checked and held

- **The checks fail on dev.** With `src/` reverted to `origin/dev` in scratch, under 3.11.18: 11 failed and 1 passed (`test_the_streams_still_reach_the_result`, which is meant to pass on both). The free checks failed at `frees == [None, None]`. `test_nothing_of_a_call…` failed with "a free ran in Python after run() had returned". Ctrl-C failed at the import of `_outputs`, as the spec says. The forced check's child exited -6 (EXECUTED).
- **The forced check is not vacuous on 3.11.** I ran its own `FORCED` script, taken from the module, 20 times each, under 3.11.18. On dev, 20 of 20 exited 134 with CI's panic and stack. With the fix, 0 of 20 (EXECUTED). With a Python no-op free in place of NULL, the check fails (mutation c). So it proves what the fix removes. On 3.13 it passes whatever the runner does, as the spec says. Its proof comes from the wheel legs' 3.11.
- **An unwrapped reproduction is too weak to count.** `natural_forced.py` wraps nothing. It holds the GIL in pure Python after five calls, with the runner's atexit hook unregistered, and lets go once Python is finalizing. dev aborted 2 of 40, then 0 of 200; the fix 0 of 40, then 0 of 200 (EXECUTED). So it does not distinguish the two, and the forced check's gate is what makes the race certain.
- **Mutations** in scratch, under 3.11 (EXECUTED):
  - (a) no `_outputs.drop`: the 8 per-ending checks fail.
  - (b) the drop only after a normal return: the host error at the start, the provider's `BaseException` and Ctrl-C fail. Fuel, deadline and trap still pass, since they return normally.
  - (c) a Python no-op free: 10 fail, including the forced check. Ctrl-C passes (L1).
- **A late or unknown key.** Through the real C thunk `_OUTPUT_WRITE` (EXECUTED: `probe.py`), live writes of 5, 5, 5 and 0 bytes return their sizes. With a cap of 7, the stream keeps `b"hellohe"` and is marked dropped. After `drop`, writes to the dropped key, to `None`, to 0 and to an unknown key each return 5, keep nothing, and raise nothing. A stream whose `write` raises `MemoryError` returns 5 and is marked truncated. A late write is reported written and its bytes are dropped, as Decision 2 says. By link 4, none can come in a call that returned.
- **Nothing is lost, and the cap is exact.** One call wrote 2,622,225 bytes of stdout and 70,000 of stderr with no cap, and both came back byte for byte, untruncated (EXECUTED). The 16 MiB default cap let 5,000,000 bytes through whole (EXECUTED). Under 3.11 and 3.13, `test_the_streams_still_reach_the_result` passes (EXECUTED).
- **Concurrent calls.** `concurrent.py` ran 8 threads × 50 calls. Each call wrote 300 lines of its own to stdout and 50 to stderr, gave its own exit code, and had an odd cap on one call in three. All 400 matched the expected stdout, stderr, exit code and `truncated` byte for byte, under 3.11.18 and under 3.13.18. The table was empty afterwards, and the next key was 801, so two keys were used per call and none was reused (EXECUTED).
- **No leak.** `leak.py` ran 200 warm-up calls, then 2,000 calls under 3.11 (EXECUTED):

  | code | RSS | live objects | runner's table | wasmtime-py's slab |
  |---|---|---|---|---|
  | the fix | 35,580 → 35,936 KiB (+356) | +1 | 0 | 0 live |
  | dev | 35,676 → 36,036 KiB (+360) | +0 | (none) | 0 live |

  The two drifts are the same, so the change adds nothing. The thunk is made once, and its only referrers are module dictionaries (EXECUTED: `gc.get_referrers`). So it lives as long as `wasi_perl._wasmtime_ffi`, as wasmtime-py's own `custom_call` does.
- **The epoch callback's NULL free is safe.** Before, the free was `finalizer_type(lambda _data: None)`, a no-op that ran in Python when the store's callback closure was dropped. That happened in `store.close()`, on the calling thread (READ: `store.rs:151-183`; dev's `_wasmtime_ffi.py`). It did nothing, so NULL changes no behaviour and keeps one callback object fewer per call. wasi-python does the same (READ: `_wasmtime_ffi.py:305-313`).
- **The order in `_call`.** The keys are taken after `wt.Store(engine)` and before the `try`. They are dropped in a `finally` nested in the outer `finally`, so a raise from `store.close()` still drops them (READ). Two dict entries could be left only by a `KeyboardInterrupt` delivered between `take` and `try`, which are adjacent statements (INFERRED; harmless).
- **`NEEDED` and the source scan.** `test_missing_names…` scans the source for every `_ffi.` and `_bindings._` name, so it now covers `_wasi_config_set_stdout_custom` too (READ). A removed name gives the one `ImportError` (L3).
- **The `test_mount_api.py` change is not a weakening.** The partial-holder row tests that a record with three argtypes, and a class without the annotation, each read as missing, in order. It must still list exactly those two. Since `NEEDED` now holds a second record, the stand-in needs it, or the expected list would change. The row's assertion is unchanged, and the source scan grew (READ). It passes (EXECUTED).
- **CI on the head.** All seven jobs pass (READ: `get_check_runs`). The ARM leg ran `test_output_release.py ............` under "Python 3.11.14". All four wheel legs printed "505 guest checks (at least 380: … 12 output-release checks), 0 skipped" (READ: the four job logs). The setup step's `uv venv --python 3.11` is the same template on all four (READ: the ARM log).
- **The rules.** No added line of `src/`, `tests/`, `scripts/`, `recipes/` or `.github/` names a specification or review (EXECUTED: `grep` over the diff; the one hit was `gate.wait(0.001)`). The CHANGELOG line is under "Unreleased", "Fixed". No test was weakened, and `tests/perl_t/` is untouched (READ). Both code commits carry the model trailer (EXECUTED: `git show`). The wasmtime clone's licence is recorded in `context/prior_art/README.md` (READ).

## Runs

| command | result |
|---|---|
| `uv run pytest -m "not slow"`, 3.13.18, with `WASI_PERL_ORACLE` (EXECUTED) | 645 passed, 3 skipped |
| `uv run pytest tests/runner tests/escapes tests/mounts`, 3.11.18 (EXECUTED) | 476 passed, 2 skipped |
| `tests/runner/test_output_release.py`, 3.11.18 and 3.13.18 (EXECUTED) | 12 passed each |
| the same, `src/` from `origin/dev`, 3.11.18 (EXECUTED) | 11 failed, 1 passed |
| mutations a, b, c, 3.11.18 (EXECUTED) | 8, 3 and 10 failed |
| `ruff check src tests`, `ruff format --check src tests` (EXECUTED) | clean; 48 files formatted |
| `forced.py` (the check's child) ×20, fix and dev, 3.11.18 (EXECUTED) | 0 of 20; 20 of 20 exit 134 |
| `natural_forced.py nohook` ×40 then ×200, fix and dev (EXECUTED) | fix 0, 0; dev 2, 0 |
| `concurrent.py`, 8 × 50, 3.11.18 and 3.13.18 (EXECUTED) | 400 of 400 identical; table empty |
| `leak.py`, 2,000 calls, fix and dev (EXECUTED) | +356 and +360 KiB; table empty |
| `probe.py`, the C thunk with live, dropped and raising streams (EXECUTED) | as above |
| `needed.py`, three moved names (EXECUTED) | ImportError, ImportError, TypeError |
| `daemon.py`, a call in flight at exit, fix ×30 (10 under the lock), dev ×10 (EXECUTED) | exit 0 in all 40; fix's panic printed in 20 of 20 counted; dev's in the run read |
| CI run 45515504338: check runs and the four wheel logs (READ) | 7 of 7 green; 12 output-release checks, 0 skipped, on 3.11 |

## Round 2

The builder answered round 1 in one commit, `a5a3941`. It changes the spec, the roadmap, `_wasmtime_ffi._has`, and two test files (READ: `git diff 8ce78e8 a5a3941`, 55 lines added, 8 removed).

### Each round-1 finding

- **M1, fixed.**
  - The spec has a new section, "For wasi-python". It says:
    - where the hazard is: `_CallbackCapture`, the only capture on Windows;
    - the change: a NULL free of the record's own type, with `_free_output` dropped;
    - the precondition: check first that `close` runs on every ending, sessions included;
    - which checks carry over;
    - that the residual case is wasi-python's too;
    - that neither API changes.
  - Its citations are the ones I gave, and they are right (READ: wasi-python at `f9bf678`, round 1). The roadmap has an item to take the fix to wasi-python (READ: `context/roadmap.md:66`).
- **M2, fixed.**
  - Decision 6 has a new bullet: the writes still run Python on a worker; a daemon thread's `run()`, still running at exit, can still meet the panic; it is not a regression, and it is outside the Intent. Its figures are the ones I measured.
  - Decision 5 now says the pipe capture is the cure for that case, and that the roadmap holds it. The roadmap has that item (READ: `context/roadmap.md:65`).
- **L1, fixed.**
  - The Ctrl-C child records `bool(finalizer)` for each setter, and the check expects `interrupted 2 [False, False] False`. With a Python no-op free put in place of the NULL one, the check fails with `[True, True]` (EXECUTED, scratch copy of `a5a3941`, 3.11.18).
  - The child now makes one call before it starts the timer, so that Ctrl-C reaches the guest, not the compile. That is not a weakening. The check still requires a `KeyboardInterrupt` from the guest's loop with both keys dropped. The keys and frees from the warm-up call are cleared before the timer starts (READ). Ten fresh pytest sessions, each with a cold cache, passed 10 of 10 (EXECUTED).
- **L2, fixed.** The roadmap item now says "Specification 023, agreed and built (PR #33)" and "Watch the next ARM runs" (READ: `context/roadmap.md:64`).
- **L3, fixed.**
  - `_has` now requires a record's second and fourth argument types to be ctypes function-pointer types, through `_is_function_type`.
  - A new check, `test_a_record_whose_callbacks_are_not_c_function_types_is_missing`, covers a record retyped in full and a record with only its free retyped. With round 1's `_has` put back, it fails, and the old row still passes (EXECUTED, scratch).
  - In a child, a record retyped to four `c_void_p` now gives the one `ImportError` naming `wasmtime._bindings._wasi_config_set_stdout_custom` and the pin (EXECUTED: `needed.py record-retyped`; `no-record` gives the same).
  - The only other stand-in record in the tests, the partial row's `argtypes=[1, 2, 3]`, is meant to read as missing, and still does (READ: `grep` over `tests/`).

### What the fix commit could have broken, checked

- `ruff check src tests` and `ruff format --check src tests`: clean (EXECUTED).
- `uv run pytest -m "not slow"`, 3.13.18, with `WASI_PERL_ORACLE`: 646 passed, 3 skipped. That is round 1's 645 plus the new check (EXECUTED).
- `tests/runner tests/escapes tests/mounts` under 3.11.18: 477 passed, 2 skipped. That is round 1's 476 plus the new check (EXECUTED).
- CI run 45519358374 on `a5a3941`: all seven jobs pass (READ: `get_check_runs`). The ARM leg's gate printed "506 guest checks (at least 381: … 89 mount checks, 12 output-release checks), 0 skipped" (READ: job 138889547204). The new check is counted with the mounts.
- The new comment in `_has` gives its reason in words and names no specification (READ). `ctypes._CFuncPtr` is a private name of ctypes. It is the base class of every `CFUNCTYPE` type in 3.11 to 3.13, and it ran on 3.11 and 3.13 here (EXECUTED) and in CI's 3.11 legs (READ). No finding.
- One nit, with no action needed. The two new roadmap items sit under "Open items from the CI of 019 (PR #29)", though they come from this review. They read correctly where they are.

### Runs, round 2

| command | result |
|---|---|
| `uv run pytest -m "not slow"`, 3.13.18 (EXECUTED) | 646 passed, 3 skipped |
| `uv run pytest tests/runner tests/escapes tests/mounts`, 3.11.18 (EXECUTED) | 477 passed, 2 skipped |
| `ruff check`, `ruff format --check` (EXECUTED) | clean |
| Ctrl-C check with a Python no-op free, scratch (EXECUTED) | fails: `[True, True]` |
| `test_mount_api.py` with round 1's `_has`, scratch (EXECUTED) | the new check fails; the old row passes |
| `needed.py record-retyped`, `no-record`, at `a5a3941` (EXECUTED) | the one ImportError, both |
| Ctrl-C check ×10, fresh sessions (EXECUTED) | 10 passed |
| CI run 45519358374 (READ) | 7 of 7 green; 12 output-release checks, 0 skipped |

## Verdict, round 2

**Lands.** Every round-1 finding is fixed, and each fix that adds a check was seen failing with the fault put back. The code that ships is round 1's, plus a stricter `NEEDED` check. Nothing else changed in `src/`. The spec now says what the change leaves and how wasi-python takes it. The suites are green on 3.11 and 3.13, and CI is green on all seven jobs, with all 12 output-release checks run on Python 3.11 in each wheel leg.
