"""A call leaves wasmtime nothing to run in Python once it has returned.

wasmtime's WASI hands each custom output stream to a task on a worker thread of its own tokio
runtime, and drops that task some time after the store is closed. A free that the runner gave
wasmtime with the stream then runs in Python on that worker, after `run()` has returned. When
Python is finalizing by then, CPython 3.11 ends the worker with `pthread_exit`; its unwind reaches
Rust's thread entry, which cannot unwind, and the process aborts ("panic in a function that
cannot unwind", exit 134). So the runner gives wasmtime no Python free, and drops its own record
of a call's streams itself, on the calling thread.
"""

from __future__ import annotations

import threading

from wasi_perl import run


def _record_outputs(monkeypatch):
    """Wrap the two C setters wasmtime-py and the runner reach: every free they are given is
    recorded, and, when called off the calling thread, held until the test lets it go."""
    import wasmtime
    from wasmtime import _bindings, _ffi

    free_type = _bindings._wasi_config_set_stdout_custom.argtypes[3]
    returned = threading.Event()
    frees: list[object] = []  # the finalizer each setter was given, None for a NULL one
    ran: list[bool] = []  # per free that ran: whether run() had returned by then
    kept: list[object] = []  # the wrappers, alive as long as wasmtime may call them

    def wrap(original):
        def setter(config, callback, data, finalizer):
            # None or a NULL C function pointer (false) both reach wasmtime as no free.
            frees.append(finalizer if finalizer else None)
            if finalizer:
                inner = finalizer

                def gated(key):
                    if threading.current_thread() is not threading.main_thread():
                        returned.wait(5)
                    ran.append(returned.is_set())
                    inner(key)

                finalizer = free_type(gated)
                kept.append(finalizer)
            return original(config, callback, data, finalizer)

        return setter

    for name in ("wasi_config_set_stdout_custom", "wasi_config_set_stderr_custom"):
        monkeypatch.setattr(_ffi, name, wrap(getattr(_ffi, name)))
    assert wasmtime  # imported, so the setters above are the ones its WasiConfig reaches
    return returned, frees, ran


def test_the_outputs_give_wasmtime_no_python_free(standin, monkeypatch):
    _, frees, _ = _record_outputs(monkeypatch)
    done = run(["hello"], artifacts=standin)
    assert (done.exit_code, done.stdout) == (0, b"hello\n")
    assert len(frees) == 2  # stdout and stderr, each one custom output
    assert frees == [None, None]


def test_nothing_of_a_call_runs_in_python_after_it_returns(standin, monkeypatch):
    returned, frees, ran = _record_outputs(monkeypatch)
    done = run(["hello"], artifacts=standin)
    returned.set()
    assert (done.exit_code, done.stdout) == (0, b"hello\n")
    # A free held on a worker runs once let go: wait, up to 5 s, for each one given to run.
    given = sum(f is not None for f in frees)
    pause = threading.Event()
    for _ in range(500):
        if len(ran) >= given:
            break
        pause.wait(0.01)
    assert True not in ran, "a free ran in Python after run() had returned"
