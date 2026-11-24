"""The module cache: a .cwasm is loaded only as the runner makes it, and anything else is
refused with a warning and replaced. Each check forgets the process's own modules first, so
the disk is what is judged, and counts what the runner compiled, loaded and wrote."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from wasi_perl import _engines, run

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the POSIX checks of the cache")


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    _engines.forget()
    return tmp_path / "cache"


def _one(cache: Path) -> Path:
    (found,) = [p for p in cache.iterdir() if p.suffix == ".cwasm"]
    return found


def _fresh_call(standin, cache, **kw):
    _engines.forget()
    before = dict(_engines.counts())
    done = run(["hello"], cache_dir=cache, artifacts=standin, **kw)
    assert done.stdout == b"hello\n"
    return {k: _engines.counts()[k] - before[k] for k in before}


def test_a_second_process_loads_what_the_first_wrote(standin, cache):
    assert _fresh_call(standin, cache) == {"compiled": 1, "loaded": 0, "written": 1}
    assert stat.S_IMODE(cache.stat().st_mode) == 0o700
    cwasm = _one(cache)
    assert stat.S_IMODE(cwasm.stat().st_mode) == 0o600
    assert _fresh_call(standin, cache) == {"compiled": 0, "loaded": 1, "written": 0}


def test_one_process_compiles_once(standin, cache):
    _engines.forget()
    before = _engines.counts()["compiled"]
    run(["hello"], cache_dir=cache, artifacts=standin)
    run(["hello"], cache_dir=cache, artifacts=standin)
    assert _engines.counts()["compiled"] - before == 1


def test_the_key_names_the_binary_wasmtime_and_the_flavor(standin, cache, standin_wasm):
    import hashlib
    from importlib.metadata import version

    _fresh_call(standin, cache)
    _fresh_call(standin, cache, fuel=10**9)
    digest = hashlib.sha256(standin_wasm.read_bytes()).hexdigest()[:32]
    names = sorted(p.name for p in cache.iterdir())
    w = version("wasmtime")
    assert names == [f"{digest}-wasmtime{w}-fuel.cwasm", f"{digest}-wasmtime{w}-plain.cwasm"]


def test_cache_dir_false_writes_nothing(standin, cache):
    assert _fresh_call(standin, False) == {"compiled": 1, "loaded": 0, "written": 0}
    assert not cache.exists()


def test_a_group_writable_folder_is_not_used(standin, cache):
    cache.mkdir()
    cache.chmod(0o770)
    with pytest.warns(RuntimeWarning, match="not using the module cache"):
        assert _fresh_call(standin, cache) == {"compiled": 1, "loaded": 0, "written": 0}
    assert list(cache.iterdir()) == []


def test_a_folder_on_the_way_others_can_write_is_not_used(standin, tmp_path):
    _engines.forget()
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)  # not sticky
    with pytest.warns(RuntimeWarning, match="not using the module cache"):
        assert _fresh_call(standin, shared / "cache")["written"] == 0


@pytest.mark.parametrize("damage", ["symlink", "mode", "hardlink", "garbage", "folder"])
def test_a_cwasm_not_as_the_runner_made_it_is_not_loaded(standin, cache, tmp_path, damage):
    _fresh_call(standin, cache)
    cwasm = _one(cache)
    good = cwasm.read_bytes()
    if damage == "symlink":
        elsewhere = tmp_path / "elsewhere.cwasm"
        elsewhere.write_bytes(good)
        elsewhere.chmod(0o600)
        cwasm.unlink()
        cwasm.symlink_to(elsewhere)
    elif damage == "mode":
        cwasm.chmod(0o644)
    elif damage == "hardlink":
        os.link(cwasm, tmp_path / "second-name")
    elif damage == "garbage":
        cwasm.write_bytes(b"not a module")
    elif damage == "folder":
        cwasm.unlink()
        cwasm.mkdir()
    counts = _fresh_call(standin, cache)
    assert counts["loaded"] == 0 and counts["compiled"] == 1
    if damage == "folder":
        assert cwasm.is_dir()  # never replaced
        return
    assert counts["written"] == 1  # replaced by the runner's own
    assert not cwasm.is_symlink()
    st = cwasm.lstat()
    assert (stat.S_IMODE(st.st_mode), st.st_nlink) == (0o600, 1)
    if damage == "symlink":
        assert (tmp_path / "elsewhere.cwasm").read_bytes() == good  # never what a link names
    assert _fresh_call(standin, cache)["loaded"] == 1


def test_a_cwasm_of_another_owner_is_not_loaded(standin, cache):
    if os.geteuid() != 0:
        pytest.skip("only root can give a file to another user")
    _fresh_call(standin, cache)
    cwasm = _one(cache)
    os.chown(cwasm, 12345, 12345)
    with pytest.warns(RuntimeWarning, match="not using the module cache"):
        counts = _fresh_call(standin, cache)
    assert (counts["loaded"], counts["written"]) == (0, 1)
    assert cwasm.stat().st_uid == os.geteuid()


def test_a_folder_of_another_owner_is_not_used(standin, cache):
    if os.geteuid() != 0:
        pytest.skip("only root can give a folder to another user")
    cache.mkdir(mode=0o700)
    os.chown(cache, 12345, 12345)
    with pytest.warns(RuntimeWarning, match="not using the module cache"):
        assert _fresh_call(standin, cache)["written"] == 0


def test_the_cache_variable_and_the_default(standin, private_cache, monkeypatch, tmp_path):
    _engines.forget()
    run(["hello"], artifacts=standin)
    assert any(p.suffix == ".cwasm" for p in private_cache.iterdir())
    monkeypatch.delenv("WASI_PERL_CACHE")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    from wasi_perl import _cache

    assert _cache.given(None).folder == _cache.default_dir()
    if os.uname().sysname == "Linux":
        assert _cache.given(None).folder == tmp_path / "xdg" / "wasi_perl"


def test_a_relative_cache_dir_is_made_absolute(monkeypatch, tmp_path):
    from wasi_perl import _cache

    monkeypatch.chdir(tmp_path)
    assert _cache.given("c").folder == tmp_path / "c"
