"""The hard-link rule, kept across calls: the runtime's files are scanned once and the scan is
used again while every folder it walked is as it was, and the writable grants are walked on
every call. Each check counts listings of the library folder, or what is refused; never time.

The tests that need a kept scan move the runner's clock forward, so the scratch sets they make
are as settled as an installed set is: a folder changed within two seconds of a scan is scanned
again, since a coarse file time may not yet show a change made in the same tick."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from wasi_perl import Artifacts, Grant, _grants, run, runner

REPO = Path(__file__).resolve().parents[2]
WRITE_MS = 10**10  # how far forward the clock is moved: ten seconds, in nanoseconds
KEPT = pytest.mark.skipif(
    not getattr(_grants, "SCANS_KEPT", True), reason="a scan is not kept on this system"
)

# Listings of folders, as the audit events of os.scandir and os.listdir name them. A hook cannot
# be removed, so it records only while a check has set `_listings`.
_listings: list[str] | None = None


def _audit(event: str, args: tuple) -> None:
    if _listings is not None and event in ("os.scandir", "os.listdir") and args:
        where = args[0]
        if isinstance(where, (str, os.PathLike)):
            _listings.append(os.path.abspath(os.fspath(where)))


sys.addaudithook(_audit)


@pytest.fixture
def scans(standin_dir: Path):
    """How many times the stand-in's library folder has been listed since the check began: once
    per scan of the runtime's files, since nothing else lists it in a call given its set."""
    global _listings
    lib = os.path.abspath(standin_dir / "lib" / "perl5")
    _listings = []
    yield lambda: sum(1 for p in _listings if p == lib)
    _listings = None


@pytest.fixture
def settled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runner's clock ten seconds on, so every folder made by the check is settled."""
    monkeypatch.setattr(_grants, "_now_ns", lambda: time.time_ns() + WRITE_MS, raising=False)


@pytest.fixture
def same_disk():
    """A scratch folder on the venv's disk, where a hard link to wasmtime's library can be made."""
    base = REPO / "build" / "hardening"
    base.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(dir=base))
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


def _wasmtime_library(top: Path | None = None) -> Path:
    if top is None:
        import wasmtime

        top = Path(wasmtime.__file__).parent
    libs = [p for p in sorted(top.rglob("*wasmtime*.*")) if p.suffix in (".so", ".dylib", ".dll")]
    assert libs, top
    return libs[0]


def _calls(standin: Artifacts, grant: Path, n: int) -> None:
    for _ in range(n):
        done = run(["hello"], grants=[Grant(grant, "/w", writable=True)], artifacts=standin)
        assert done.stdout == b"hello\n"


# Scans.


@KEPT
def test_ten_writable_calls_scan_the_runtime_once(standin, tmp_path, scans, settled):
    _calls(standin, tmp_path, 10)
    assert scans() == 1


@KEPT
@pytest.mark.parametrize("how", ["a new file", "a removal", "a rename over it"])
def test_a_change_in_the_library_makes_the_next_call_scan_again(
    standin, standin_dir, tmp_path, scans, settled, how
):
    _calls(standin, tmp_path, 3)
    assert scans() == 1
    folder = standin_dir / "lib" / "perl5" / "5.42"
    if how == "a new file":
        (folder / "new.pm").write_text("1;\n")
    elif how == "a removal":
        (folder / "strict.pm").unlink()
    else:
        (folder / "strict.pm.new").write_text("package strict; 1;\n")
        os.replace(folder / "strict.pm.new", folder / "strict.pm")
    _calls(standin, tmp_path, 3)
    assert scans() == 2


@KEPT
def test_a_folder_changed_within_two_seconds_of_its_stamp_is_scanned_again(
    standin, standin_dir, tmp_path, scans, monkeypatch
):
    folder = standin_dir / "lib" / "perl5" / "5.42"
    (folder / "x").write_text("")
    (folder / "x").unlink()
    changed = max(os.stat(d).st_ctime_ns for d in (standin_dir, folder, standin_dir / "dev"))
    # One second after the change: a file time as coarse as a second may not show a later one.
    monkeypatch.setattr(_grants, "_now_ns", lambda: changed + 10**9, raising=False)
    _calls(standin, tmp_path, 3)
    assert scans() == 3
    # Three seconds after it: the stamps are settled, and the next scan is kept.
    monkeypatch.setattr(_grants, "_now_ns", lambda: changed + 3 * 10**9, raising=False)
    _calls(standin, tmp_path, 3)
    assert scans() == 4


# The rule, after a scan.


@KEPT
def test_a_link_to_wasmtimes_library_made_after_several_calls_is_refused(
    standin, same_disk, scans, settled
):
    _calls(standin, same_disk, 3)
    assert scans() == 1
    deep = same_disk / "a" / "b"
    deep.mkdir(parents=True)
    try:
        os.link(_wasmtime_library(), deep / "copy.so")
    except OSError as e:
        pytest.skip(f"no hard link to wasmtime's library here: {e}")
    with pytest.raises(ValueError, match="hard link"):
        _calls(standin, same_disk, 1)


@KEPT
def test_a_link_to_a_library_file_made_after_several_calls_is_refused(
    standin, standin_dir, tmp_path, scans, settled
):
    _calls(standin, tmp_path, 3)
    assert scans() == 1
    os.link(standin_dir / "lib" / "perl5" / "5.42" / "strict.pm", tmp_path / "strict.pm")
    with pytest.raises(ValueError, match="hard link"):
        _calls(standin, tmp_path, 1)
    assert scans() == 1  # the scan held, and the walk of the grant found the link


@KEPT
def test_a_runtime_file_replaced_by_rename_then_linked_is_refused(
    standin, standin_dir, tmp_path, scans, settled
):
    _calls(standin, tmp_path, 3)
    folder = standin_dir / "lib" / "perl5" / "5.42"
    (folder / "strict.pm.new").write_text("package strict; 1;\n")
    os.replace(folder / "strict.pm.new", folder / "strict.pm")  # another inode at the name
    os.link(folder / "strict.pm", tmp_path / "strict.pm")
    with pytest.raises(ValueError, match="hard link"):
        _calls(standin, tmp_path, 1)
    assert scans() == 2


@KEPT
def test_a_grant_past_max_walk_after_a_kept_scan_is_refused_while_a_file_has_two_names(
    standin, standin_dir, tmp_path, scans, settled, monkeypatch
):
    big = tmp_path / "big"
    big.mkdir()
    for i in range(20):
        (big / f"f{i}").write_text("x")
    _calls(standin, big, 3)
    assert scans() == 1
    os.link(standin_dir / "perl.wasm", tmp_path / "elsewhere.wasm")  # a second name, not granted
    monkeypatch.setattr(_grants, "MAX_WALK", 10)
    with pytest.raises(ValueError, match="too large"):
        _calls(standin, big, 1)


def _scan_of(standin_dir: Path):
    """The stand-in set's files alone, scanned as the runner scans the runtime."""
    a = Artifacts.at(standin_dir)
    return _grants.RuntimeFiles((a.lib,), (a.perl, a.manifest, a.sums, a.dev / "null"))


def _placed(folder: Path) -> tuple[_grants.Placed, ...]:
    host = folder.resolve()
    return (_grants.Placed(host, "/w", True, _grants.chain(host)),)


def test_past_max_walk_a_grant_is_refused_exactly_when_a_runtime_file_has_two_names(
    standin_dir, tmp_path, monkeypatch
):
    big = tmp_path / "big"
    big.mkdir()
    for i in range(20):
        (big / f"f{i}").write_text("x")
    monkeypatch.setattr(_grants, "MAX_WALK", 10)
    scan = _scan_of(standin_dir)
    assert scan.linked_now() == {}
    _grants._refuse_second_names(_placed(big), scan)  # no second name anywhere: nothing to find
    os.link(standin_dir / "perl.wasm", tmp_path / "elsewhere.wasm")
    with pytest.raises(ValueError, match="too large"):
        _grants._refuse_second_names(_placed(big), scan)


def test_an_unreadable_folder_is_refused_exactly_when_a_runtime_file_has_two_names(
    standin_dir, tmp_path, monkeypatch
):
    grant = tmp_path / "w"
    (grant / "shut").mkdir(parents=True)
    real = os.scandir

    def scandir(path="."):
        if os.fspath(path).endswith("shut"):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    scan = _scan_of(standin_dir)
    _grants._refuse_second_names(_placed(grant), scan)
    os.link(standin_dir / "perl.wasm", tmp_path / "elsewhere.wasm")
    with pytest.raises(ValueError, match="cannot search"):
        _grants._refuse_second_names(_placed(grant), scan)


def test_a_match_is_read_afresh_and_a_stale_one_scans_again(standin_dir, tmp_path):
    # A scan that names, under a runtime file's path, the identity of a file that is not one:
    # as if the runtime file had been replaced in a tick its folder's time did not show.
    grant = tmp_path / "w"
    grant.mkdir()
    (grant / "mine").write_text("x")
    os.link(grant / "mine", tmp_path / "also-mine")  # two names, neither the runtime's
    st = os.lstat(grant / "mine")
    scan = _scan_of(standin_dir)
    strict = standin_dir / "lib" / "perl5" / "5.42" / "strict.pm"
    scan.files[(st.st_dev, st.st_ino)] = str(strict)
    _grants._refuse_second_names(_placed(grant), scan)  # read afresh, it is not strict.pm
    os.link(strict, grant / "strict.pm")  # a real second name is still refused
    with pytest.raises(ValueError, match="hard link"):
        _grants._refuse_second_names(_placed(grant), scan)


# The shortcut in a copy-mode install.

_CHILD = r"""
import json, os, sys, time
site, standin_dir, grant = sys.argv[1:4]
import wasmtime
assert wasmtime.__file__.startswith(site), wasmtime.__file__
from wasi_perl import Artifacts, Grant, _grants, run, runner
lib = os.path.abspath(os.path.join(standin_dir, "lib", "perl5"))
listed = []
sys.addaudithook(
    lambda e, a: listed.append(os.path.abspath(os.fspath(a[0])))
    if e == "os.scandir" and a and isinstance(a[0], str) else None
)
_grants._now_ns = lambda: time.time_ns() + 10**10
standin = Artifacts.at(standin_dir)
def call():
    try:
        done = run(["hello"], grants=[Grant(grant, "/w", writable=True)], artifacts=standin)
        return done.stdout.decode()
    except ValueError as e:
        return "refused: " + str(e)
before = [call() for _ in range(3)]
linked = sorted(runner._runtime_files(standin).linked_now().values())
target = None
for dirpath, _, names in os.walk(os.path.dirname(wasmtime.__file__)):
    for n in names:
        if "wasmtime" in n and os.path.splitext(n)[1] in (".so", ".dylib", ".dll"):
            target = os.path.join(dirpath, n)
os.makedirs(os.path.join(grant, "a"))
try:
    os.link(target, os.path.join(grant, "a", "copy" + os.path.splitext(target)[1]))
    after = call()
except OSError as e:
    after = "no link: " + str(e)
print(json.dumps({"before": before, "linked": [str(p) for p in linked], "after": after,
                  "scans": sum(1 for p in listed if p == lib)}))
"""


def test_in_a_copy_mode_install_a_link_made_after_the_scan_is_still_refused(standin_dir, tmp_path):
    import wasmtime

    import wasi_perl

    # A site folder whose wasmtime-py and wasi_perl are copies, so no runtime file has a second
    # name, as an install with UV_LINK_MODE=copy lays them out.
    site = tmp_path / "site"
    top = Path(wasmtime.__file__).parent
    shutil.copytree(top, site / "wasmtime", ignore=shutil.ignore_patterns("__pycache__"))
    for info in top.parent.glob("wasmtime-*.dist-info"):
        shutil.copytree(info, site / info.name)
    shutil.copytree(
        Path(wasi_perl.__file__).parent,
        site / "wasi_perl",
        ignore=shutil.ignore_patterns("__pycache__", "artifacts"),
    )
    grant = tmp_path / "grant"
    grant.mkdir()
    env = dict(os.environ, PYTHONPATH=str(site))
    env.pop("WASI_PERL_ARTIFACTS", None)
    out = subprocess.run(
        [sys.executable, "-c", _CHILD, str(site), str(standin_dir), str(grant)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    if got["after"].startswith("no link: "):
        pytest.skip(f"no hard link to wasmtime's library here: {got['after']}")
    assert got["before"] == ["hello\n"] * 3
    assert got["linked"] == []  # the scan was made with no second name anywhere
    assert got["after"].startswith("refused: ") and "hard link" in got["after"], got["after"]
    if getattr(_grants, "SCANS_KEPT", True):
        assert got["scans"] == 1, got


# Windows.


def test_scans_are_kept_on_linux_and_macos_only():
    assert _grants.SCANS_KEPT == (sys.platform in ("linux", "darwin"))


@pytest.mark.parametrize(
    ("kept", "want"), [(True, 1), (False, 10)], ids=["linux-and-macos", "windows"]
)
def test_on_windows_every_writable_call_scans(
    standin, tmp_path, scans, settled, monkeypatch, kept, want
):
    monkeypatch.setattr(_grants, "SCANS_KEPT", kept, raising=False)
    _calls(standin, tmp_path, 10)
    assert scans() == want


def test_the_scan_holds_every_file_of_the_three_trees_and_the_set(standin, standin_dir):
    import wasmtime

    import wasi_perl

    found = runner._runtime_files(standin)
    tops = (Path(wasmtime.__file__).parent, Path(wasi_perl.__file__).parent, standin.lib)
    files = [p for top in tops for p in top.rglob("*") if p.is_file() and not p.is_symlink()]
    files += [standin.perl, standin.manifest, standin.sums, standin.dev / "null"]
    for p in files:
        st = os.lstat(p)
        assert (st.st_dev, st.st_ino) in found.files, p
