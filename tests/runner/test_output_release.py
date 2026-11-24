"""A call leaves wasmtime nothing to run in Python once it has returned.

wasmtime's WASI hands each custom output stream to a task on a worker thread of its own tokio
runtime, and drops that task some time after the store is closed. A free that the runner gave
wasmtime with the stream then runs in Python on that worker, after `run()` has returned. When
Python is finalizing by then, CPython 3.11 ends the worker with `pthread_exit`; its unwind reaches
Rust's thread entry, which cannot unwind, and the process aborts ("panic in a function that
cannot unwind", exit 134). So the runner gives wasmtime no Python free, and drops its own record
of a call's streams itself, on the calling thread, however the call ends.

The checks run the real perl.wasm, so they run in every wheel leg, on Python 3.11.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading

import pytest

from mounts.raw import MemoryProvider
from wasi_perl import Grant, Mount, _grants, run


def _record_outputs(monkeypatch):
    """Wrap the two C setters wasmtime-py and the runner reach: every free and every key they
    are given is recorded, and a free called off the calling thread is held until the test lets
    it go."""
    from wasmtime import _bindings, _ffi

    free_type = _bindings._wasi_config_set_stdout_custom.argtypes[3]
    returned = threading.Event()
    seen: dict[str, list] = {"frees": [], "keys": [], "ran": []}  # ran: per free, run() over?
    kept: list[object] = []  # the wrappers, alive as long as wasmtime may call them

    def wrap(original):
        def setter(config, callback, data, finalizer):
            # None or a NULL C function pointer (false) both reach wasmtime as no free.
            seen["frees"].append(finalizer if finalizer else None)
            seen["keys"].append(data)
            if finalizer:
                inner = finalizer

                def gated(key):
                    if threading.current_thread() is not threading.main_thread():
                        returned.wait(5)
                    seen["ran"].append(returned.is_set())
                    inner(key)

                finalizer = free_type(gated)
                kept.append(finalizer)
            return original(config, callback, data, finalizer)

        return setter

    for name in ("wasi_config_set_stdout_custom", "wasi_config_set_stderr_custom"):
        monkeypatch.setattr(_ffi, name, wrap(getattr(_ffi, name)))
    return returned, seen


def _assert_released(seen) -> None:
    """Both outputs were registered, with no free, and the runner holds neither key now."""
    assert len(seen["frees"]) == 2, seen  # stdout and stderr, each one custom output
    assert seen["frees"] == [None, None]
    from wasi_perl import _outputs

    assert all(isinstance(k, int) and k > 0 for k in seen["keys"]), seen["keys"]
    assert not any(_outputs.holds(k) for k in seen["keys"])
    seen["frees"].clear()
    seen["keys"].clear()


def test_the_outputs_give_wasmtime_no_python_free(perl, monkeypatch):
    _, seen = _record_outputs(monkeypatch)
    done = run(["-e", 'print "hello\\n"'], artifacts=perl)
    assert (done.exit_code, done.stdout) == (0, b"hello\n")
    assert len(seen["frees"]) == 2
    assert seen["frees"] == [None, None]


def test_nothing_of_a_call_runs_in_python_after_it_returns(perl, monkeypatch):
    returned, seen = _record_outputs(monkeypatch)
    done = run(["-e", 'print "hello\\n"'], artifacts=perl)
    returned.set()
    assert (done.exit_code, done.stdout) == (0, b"hello\n")
    # A free held on a worker runs once let go: wait, up to 5 s, for each one given to run.
    given = sum(f is not None for f in seen["frees"])
    pause = threading.Event()
    for _ in range(500):
        if len(seen["ran"]) >= given:
            break
        pause.wait(0.01)
    assert True not in seen["ran"], "a free ran in Python after run() had returned"


def test_the_streams_still_reach_the_result(perl):
    done = run(["-e", 'print "out\\n"; print STDERR "err\\n"; exit 3'], artifacts=perl)
    assert (done.exit_code, done.stdout, done.stderr) == (3, b"out\n", b"err\n")
    capped = run(["-e", 'print "x" x 100'], output_bytes=10, artifacts=perl)
    assert (capped.stdout, capped.truncated) == (b"x" * 10, ("stdout",))


# Every way a call ends, each with the outputs released on the calling thread.


def test_a_normal_end_releases_the_outputs(perl, monkeypatch):
    _, seen = _record_outputs(monkeypatch)
    done = run(["-e", 'print "hello\\n"'], artifacts=perl)
    assert (done.exit_code, done.stdout) == (0, b"hello\n")
    _assert_released(seen)


def test_an_exit_and_a_die_release_the_outputs(perl, monkeypatch):
    _, seen = _record_outputs(monkeypatch)
    assert run(["-e", "exit 3"], artifacts=perl).exit_code == 3
    _assert_released(seen)
    done = run(["-e", 'die "no\\n"'], artifacts=perl)
    assert (done.exit_code, done.stderr) == (255, b"no\n")
    _assert_released(seen)


def test_running_out_of_fuel_releases_the_outputs(perl, monkeypatch):
    _, seen = _record_outputs(monkeypatch)
    done = run(["-e", "1 while 1"], fuel=1_000_000, artifacts=perl)
    assert done.trapped == "out of fuel"
    _assert_released(seen)


def test_the_deadline_releases_the_outputs(perl, monkeypatch):
    _, seen = _record_outputs(monkeypatch)
    done = run(["-e", "1 while 1"], deadline_s=0.2, artifacts=perl)
    assert done.trapped == "deadline"
    _assert_released(seen)


def test_a_trap_at_instantiation_releases_the_outputs(perl, monkeypatch):
    _, seen = _record_outputs(monkeypatch)
    done = run(["-e", "1"], memory_bytes=65536, artifacts=perl)  # less than its first memory
    assert done.exit_code is None and done.trapped
    _assert_released(seen)


def test_a_host_error_at_the_guests_start_releases_the_outputs(perl, tmp_path, monkeypatch):
    _, seen = _record_outputs(monkeypatch)
    gone = tmp_path / "gone"
    gone.mkdir()
    real_hold = _grants.hold

    def hold_then_remove(placed, slot):  # the folder vanishes between the check and the open
        real_hold(placed, slot)
        gone.rmdir()

    monkeypatch.setattr(_grants, "hold", hold_then_remove)
    done = run(["-e", "1"], grants=[Grant(gone, "/g")], artifacts=perl)
    assert done.trapped and "preopen" in done.trapped
    _assert_released(seen)


def test_a_providers_base_exception_releases_the_outputs(perl, monkeypatch):
    class Stop(BaseException):
        pass

    _, seen = _record_outputs(monkeypatch)
    provider = MemoryProvider({"/a.txt": b"a"})
    provider.answers[("read", "/a.txt")] = Stop("the provider's own")
    with pytest.raises(Stop):
        run(
            ["-e", 'open my $f, "<", "/work/a.txt"; print "after\\n"; 1 while 1'],
            mounts=[Mount(provider, "/work")],
            artifacts=perl,
        )
    _assert_released(seen)


CTRL_C = """
import signal, sys, threading
from pathlib import Path
from wasmtime import _ffi
from wasi_perl import Artifacts, _outputs, run

keys = []
for name in ("wasi_config_set_stdout_custom", "wasi_config_set_stderr_custom"):
    def setter(config, callback, data, finalizer, _original=getattr(_ffi, name)):
        keys.append(data)
        return _original(config, callback, data, finalizer)
    setattr(_ffi, name, setter)
threading.Timer(0.5, signal.raise_signal, (signal.SIGINT,)).start()
try:
    run(["-e", "1 while 1"], deadline_s=None, artifacts=Artifacts.at(Path(sys.argv[1])))
except KeyboardInterrupt:
    print("interrupted", len(keys), any(_outputs.holds(k) for k in keys))
    sys.exit(0)
print("returned")
"""


def test_ctrl_c_releases_the_outputs(perl):
    try:
        out = subprocess.run(
            [sys.executable, "-c", CTRL_C, str(perl.perl.parent)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("Ctrl-C never reached the guest")
    assert (out.returncode, out.stdout) == (0, "interrupted 2 False\n"), out.stderr


# The abort itself, forced: the first free a worker has to run is held there until Python is
# finalizing, then let go while the main thread sleeps. CPython 3.11 ends that worker with
# pthread_exit, which aborts the process; 3.13 hangs it instead, so there the check passes
# whatever the runner does.

FORCED = """
import builtins, sys, threading, time
from pathlib import Path
from wasmtime import _bindings, _ffi
from wasi_perl import Artifacts, run

FREE = _bindings._wasi_config_set_stdout_custom.argtypes[3]
gate = threading.Event()
held = []  # the one free held on a worker
kept = []


def wrap(original):
    def setter(config, callback, data, finalizer):
        if finalizer:
            inner = finalizer

            def gated(key):
                if threading.current_thread() is not threading.main_thread() and not held:
                    held.append(key)
                    while not gate.is_set():
                        gate.wait(0.001)
                inner(key)

            finalizer = FREE(gated)
            kept.append(finalizer)
        return original(config, callback, data, finalizer)

    return setter


for name in ("wasi_config_set_stdout_custom", "wasi_config_set_stderr_custom"):
    setattr(_ffi, name, wrap(getattr(_ffi, name)))


class LetGoLate:
    # Torn down with builtins, once the runtime is marked finalizing: lets the held free go and
    # sleeps, so the worker takes the GIL while Python finalizes.
    def __init__(self):
        self.gate, self.sleep = gate, time.sleep

    def __del__(self):
        self.gate.set()
        self.sleep(0.05)


artifacts = Artifacts.at(Path(sys.argv[1]))
for _ in range(5):
    assert run(["-e", 'print "hello\\\\n"'], artifacts=artifacts).stdout == b"hello\\n"
builtins._let_go_late = LetGoLate()
"""


def test_a_free_held_until_finalization_does_not_abort(perl):
    out = subprocess.run(
        [sys.executable, "-c", FORCED, str(perl.perl.parent)],
        capture_output=True,
        text=True,
        timeout=60,
        env=dict(os.environ, RUST_BACKTRACE="0"),
    )
    assert out.returncode == 0, (out.returncode, out.stderr[-2000:])
