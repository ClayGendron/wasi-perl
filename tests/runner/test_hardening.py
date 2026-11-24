"""The runner's release hardening: hard links to the runtime's files, folders once granted
writable never used as a cache, a per-process budget of host descriptors, wasmtime errors at a
guest's start reported as traps, a floor on wasmtime-py, the base interpreter's folders, and
Ctrl-C reaching a computing guest. Each counts outcomes, never time."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import wasi_perl
from wasi_perl import Grant, _engines, _grants, _limits, run
from wasi_perl._grants import Grant as _Grant  # noqa: F401  (the one Grant)

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def same_disk(tmp_path_factory):
    """A scratch folder on the venv's disk, where a hard link to wasmtime's library can be made."""
    base = REPO / "build" / "hardening"
    base.mkdir(parents=True, exist_ok=True)
    folder = Path(__import__("tempfile").mkdtemp(dir=base))
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


def _wasmtime_library() -> Path:
    import wasmtime

    found = sorted(Path(wasmtime.__file__).parent.rglob("*wasmtime*.*"))
    libs = [p for p in found if p.suffix in (".so", ".dylib", ".dll")]
    assert libs, found
    return libs[0]


def test_a_hard_link_to_wasmtimes_library_in_a_writable_grant_is_refused(same_disk, standin):
    lib = _wasmtime_library()
    deep = same_disk / "a" / "b"
    deep.mkdir(parents=True)
    try:
        os.link(lib, deep / "copy.so")
    except OSError as e:
        pytest.skip(f"no hard link to wasmtime's library here: {e}")
    with pytest.raises(ValueError, match="hard link"):
        run(["hello"], grants=[Grant(same_disk, "/w", writable=True)], artifacts=standin)
    # Read-only, the second name changes nothing the guest could write.
    assert run(["hello"], grants=[Grant(same_disk, "/r")], artifacts=standin).exit_code == 0


def test_a_hard_link_to_the_artifact_set_in_a_writable_grant_is_refused(
    tmp_path, standin, standin_dir
):
    (tmp_path / "w").mkdir()
    os.link(standin_dir / "lib" / "perl5" / "5.42" / "strict.pm", tmp_path / "w" / "strict.pm")
    with pytest.raises(ValueError, match="hard link"):
        run(["hello"], grants=[Grant(tmp_path / "w", "/w", writable=True)], artifacts=standin)


def test_a_grant_too_large_to_walk_is_refused_while_a_runtime_file_has_two_names(
    tmp_path, standin, standin_dir, monkeypatch
):
    os.link(standin_dir / "perl.wasm", tmp_path / "elsewhere.wasm")  # a second name, not granted
    big = tmp_path / "big"
    big.mkdir()
    for i in range(20):
        (big / f"f{i}").write_text("x")
    monkeypatch.setattr(_grants, "MAX_WALK", 10)
    with pytest.raises(ValueError, match="too large"):
        run(["hello"], grants=[Grant(big, "/w", writable=True)], artifacts=standin)
    monkeypatch.setattr(_grants, "MAX_WALK", 100)
    assert run(["hello"], grants=[Grant(big, "/w", True)], artifacts=standin).exit_code == 0


def test_a_folder_once_granted_writable_is_never_a_cache(tmp_path, standin):
    d = tmp_path / "d"
    (d / "sub").mkdir(parents=True)
    run(["hello"], grants=[Grant(d, "/w", writable=True)], cache_dir=False, artifacts=standin)
    for cache in (d / "cache", d, d / "sub" / "deeper", tmp_path):
        _engines.forget()
        before = _engines.counts()
        with pytest.warns(RuntimeWarning, match="granted writable"):
            assert run(["hello"], cache_dir=cache, artifacts=standin).exit_code == 0
        after = _engines.counts()
        assert (after["loaded"], after["written"]) == (before["loaded"], before["written"])
    assert not (d / "cache").exists() or not any((d / "cache").iterdir())


def test_a_planted_cwasm_is_never_loaded(tmp_path, standin):
    # The review's probe: a guest plants real .cwasm bytes; a later call names that folder.
    mine = tmp_path / "mine"
    _engines.forget()
    run(["hello"], cache_dir=mine, artifacts=standin)
    (blob,) = mine.glob("*.cwasm")
    d = tmp_path / "d"
    d.mkdir()
    run(["hello"], grants=[Grant(d, "/w", writable=True)], cache_dir=False, artifacts=standin)
    (d / "cache").mkdir(mode=0o700)
    shutil.copyfile(blob, d / "cache" / blob.name)  # as the guest's write would leave it
    (d / "cache" / blob.name).chmod(0o600)
    _engines.forget()
    before = _engines.counts()["loaded"]
    with pytest.warns(RuntimeWarning, match="granted writable"):
        run(["hello"], cache_dir=d / "cache", artifacts=standin)
    assert _engines.counts()["loaded"] == before


def _while_running(standin, body):
    done = {}
    a = threading.Thread(
        target=lambda: done.update(a=run(["spin"], deadline_s=2, artifacts=standin))
    )
    before = _limits.descriptors_reserved()
    a.start()
    try:
        while _limits.descriptors_reserved() == before and a.is_alive():
            threading.Event().wait(0.001)
        assert a.is_alive()
        body()
    finally:
        a.join()
    assert done["a"].trapped == "deadline"
    assert _limits.descriptors_reserved() == before


def test_host_descriptors_are_budgeted_per_process(standin, monkeypatch):
    monkeypatch.setattr(_limits, "descriptor_budget", lambda: 300)

    def body():
        with pytest.raises(wasi_perl.TooManyCalls, match="descriptor"):
            run(["hello"], artifacts=standin)

    _while_running(standin, body)
    assert run(["hello"], artifacts=standin).exit_code == 0  # A has ended: room again
    assert issubclass(wasi_perl.TooManyCalls, RuntimeError)


def test_the_budget_is_half_the_soft_limit():
    resource = pytest.importorskip("resource")
    soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    if soft == resource.RLIM_INFINITY:
        pytest.skip("no soft limit")
    assert _limits.descriptor_budget() == soft // 2


def test_a_wasmtime_error_at_the_guests_start_is_a_trap(tmp_path, standin, monkeypatch):
    gone = tmp_path / "gone"
    gone.mkdir()
    real_hold = _grants.hold

    def hold_then_remove(placed, slot):  # the folder vanishes between the check and the open
        real_hold(placed, slot)
        gone.rmdir()

    monkeypatch.setattr(_grants, "hold", hold_then_remove)
    done = run(["hello"], grants=[Grant(gone, "/g")], artifacts=standin)
    assert (done.exit_code, done.stdout) == (None, b"")
    assert done.trapped and "preopen" in done.trapped


def test_wasmtime_older_than_51_is_refused(standin, monkeypatch):
    monkeypatch.setattr(_engines, "wasmtime_version", lambda: "50.0.0")
    with pytest.raises(ImportError, match="50.0.0"):
        run(["hello"], artifacts=standin)


def test_the_base_interpreters_folders_are_protected(standin):
    folders = {Path(os.path.realpath(sys.executable)).parent}
    folders.add(Path(sys.base_prefix) / ("Scripts" if os.name == "nt" else "bin"))
    for folder in folders:
        if folder.is_dir():
            with pytest.raises(ValueError, match="runtime"):
                run(["hello"], grants=[Grant(folder, "/w", writable=True)], artifacts=standin)


CTRL_C = r"""
import signal, sys, threading
from wasi_perl import Artifacts, run
a = Artifacts.at(sys.argv[1])
run(["hello"], artifacts=a)  # warm: compiled and cached before the signal is armed
threading.Timer(0.5, signal.raise_signal, (signal.SIGINT,)).start()
try:
    done = run(["spin"], deadline_s=None, artifacts=a)
except KeyboardInterrupt:
    print("interrupted")
    sys.exit(0)
print("returned", done)
"""


def test_ctrl_c_reaches_a_computing_guest(standin_dir):
    # A fail-safe timeout only: without the fix the guest spins forever and the child is killed.
    try:
        out = subprocess.run(
            [sys.executable, "-c", CTRL_C, str(standin_dir)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("Ctrl-C never reached the guest")
    assert (out.returncode, out.stdout) == (0, "interrupted\n"), out.stderr


def test_the_command_exits_130_on_ctrl_c(standin_dir):
    code = (
        "import signal, sys, threading\n"
        "from wasi_perl import cli\n"
        "threading.Timer(0.5, signal.raise_signal, (signal.SIGINT,)).start()\n"
        "sys.exit(cli.main(['run', '--deadline-s', 'none', '--', 'spin']))\n"
    )
    env = {**os.environ, "WASI_PERL_ARTIFACTS": str(standin_dir)}
    try:
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        pytest.fail("Ctrl-C never reached the guest")
    assert (out.returncode, out.stdout) == (130, b"")


LOW_LIMIT = r"""
import resource, sys, threading
from wasi_perl import Artifacts, TooManyCalls, _limits, run
soft, hard = int(sys.argv[2]), sys.argv[3]
resource.setrlimit(resource.RLIMIT_NOFILE, (soft, soft if hard == "same" else
                   resource.getrlimit(resource.RLIMIT_NOFILE)[1]))
a = Artifacts.at(sys.argv[1])
print("alone", run(["hello"], artifacts=a).exit_code)
done = {}
t = threading.Thread(target=lambda: done.update(a=run(["spin"], deadline_s=2, artifacts=a)))
t.start()
while _limits.descriptors_reserved() == 0 and t.is_alive():
    threading.Event().wait(0.001)
results = []
for _ in range(2):
    try:
        results.append(run(["hello"], artifacts=a).exit_code)
    except TooManyCalls:
        results.append("TooManyCalls")
t.join()
print("beside", results, done["a"].trapped)
"""


def _low_limit(standin_dir, soft, hard):
    resource = pytest.importorskip("resource")
    del resource
    out = subprocess.run(
        [sys.executable, "-c", LOW_LIMIT, str(standin_dir), str(soft), hard],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.splitlines()


def test_one_call_always_runs_under_a_low_limit(standin_dir):
    # macOS's usual soft limit is 256, and here the hard limit is lowered too, so it cannot be
    # raised: one call runs alone; one beside it is refused, as the budget is spent.
    lines = _low_limit(standin_dir, 256, "same")
    assert lines == ["alone 0", "beside ['TooManyCalls', 'TooManyCalls'] deadline"]


def test_the_soft_limit_is_raised_toward_the_hard_limit(standin_dir):
    # A soft limit of 256 under a higher hard limit is raised once, so calls run side by side.
    lines = _low_limit(standin_dir, 256, "keep")
    assert lines == ["alone 0", "beside [0, 0] deadline"]


def test_a_reservation_is_released_on_every_path(standin, monkeypatch):
    before = _limits.descriptors_reserved()

    real = _limits.reserve_descriptors

    def interrupted(*a, **k):  # Ctrl-C the instant the reservation is made
        got = real(*a, **k)
        del got
        raise KeyboardInterrupt

    monkeypatch.setattr(_limits, "reserve_descriptors", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run(["hello"], artifacts=standin)
    assert _limits.descriptors_reserved() == before


def test_wasmtime_52_is_refused(standin, monkeypatch):
    monkeypatch.setattr(_engines, "wasmtime_version", lambda: "52.0.0")
    with pytest.raises(ImportError, match="52.0.0"):
        run(["hello"], artifacts=standin)


def test_the_writable_registry_is_bounded(tmp_path, standin, monkeypatch):
    monkeypatch.setattr(_grants, "EVER_WRITABLE_MAX", 5)
    monkeypatch.setattr(_grants, "_ever_writable", {})
    monkeypatch.setattr(_grants, "_ever_overflowed", False)
    for i in range(12):  # folders granted, then gone: pruned, nothing to fear in them
        d = tmp_path / f"d{i}"
        d.mkdir()
        run(["hello"], grants=[Grant(d, "/w", writable=True)], cache_dir=False, artifacts=standin)
        shutil.rmtree(d)
    assert len(_grants._ever_writable) <= 5
    assert not _grants._ever_overflowed
    kept = []
    for i in range(7):  # folders that stay: past the bound the disk cache is turned off
        d = tmp_path / f"k{i}"
        d.mkdir()
        kept.append(d)
        run(["hello"], grants=[Grant(d, "/w", writable=True)], cache_dir=False, artifacts=standin)
    assert _grants._ever_overflowed
    assert _grants.granted_writable(tmp_path / "anything")  # every cache refused from now on


def test_a_folder_the_walk_cannot_list_is_refused(tmp_path, standin, standin_dir, monkeypatch):
    os.link(standin_dir / "perl.wasm", tmp_path / "elsewhere.wasm")  # a runtime file, two names
    w = tmp_path / "w"
    (w / "sub").mkdir(parents=True)
    real_walk = os.walk

    def failing_walk(top, *args, onerror=None, **kw):
        if onerror is not None:
            onerror(PermissionError(13, "Permission denied", str(w / "sub")))
        yield from real_walk(top, *args, onerror=onerror, **kw)

    monkeypatch.setattr(_grants.os, "walk", failing_walk)
    with pytest.raises(ValueError, match="cannot search"):
        run(["hello"], grants=[Grant(w, "/w", writable=True)], artifacts=standin)
