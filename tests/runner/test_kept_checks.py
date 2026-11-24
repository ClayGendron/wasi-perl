"""The hard-link rule, kept across calls: the runtime's files are scanned once and the scan is
used again while every folder it walked is as it was, and the writable grants are walked on
every call. Each check counts listings of the library folder, or what is refused; never time.

The checks that run a guest use the perl.wasm of the set `$WASI_PERL_ARTIFACTS` names, as CI's
wheel jobs set it, else the stand-in; each copies it into a scratch set of its own, which it may
change. The others call the scan and the walk directly, and need no guest.

The checks that need a kept scan move the runner's clock forward, so the scratch sets they make
are as settled as an installed set is: a folder changed within two seconds of a scan is scanned
again, since a coarse file time may not yet show a change made in the same tick."""

from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from standin_sets import write_set
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


def _built_perl() -> Path | None:
    env = os.environ.get("WASI_PERL_ARTIFACTS")
    d = Path(env) if env else REPO / "build" / "artifacts"
    return d / "perl.wasm" if (d / "perl.wasm").is_file() else None


@pytest.fixture(scope="session")
def guest(request: pytest.FixtureRequest) -> tuple[Path, list[str]]:
    """A guest, and the arguments that make it print "hello": the built perl.wasm, else the
    stand-in, which skips without wasi-sdk."""
    built = _built_perl()
    if built is not None:
        return built, ["-e", 'print "hello\\n"']
    return request.getfixturevalue("standin_wasm"), ["hello"]


@pytest.fixture
def set_dir(tmp_path_factory: pytest.TempPathFactory, guest) -> Path:
    """A scratch artifact set around the guest, in a folder of its own, which the check may
    change: perl.wasm, a one-module library, dev/null, MANIFEST and SHA256SUMS."""
    return write_set(tmp_path_factory.mktemp("set") / "artifacts", guest[0])


@pytest.fixture
def the_set(set_dir: Path) -> Artifacts:
    return Artifacts.at(set_dir)


@pytest.fixture
def bare_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A set laid out as the build lays it out, around bytes no guest runs."""
    wasm = tmp_path_factory.mktemp("bytes") / "perl.wasm"
    wasm.write_bytes(b"\0asm\1\0\0\0")
    return write_set(tmp_path_factory.mktemp("set") / "artifacts", wasm)


@pytest.fixture
def scans(set_dir: Path):
    """How many times the scratch set's library folder has been listed since the check began:
    once per scan of the runtime's files, since nothing else lists it in a call given its set."""
    global _listings
    lib = os.path.abspath(set_dir / "lib" / "perl5")
    _listings = []
    yield lambda: sum(1 for p in _listings if p == lib)
    _listings = None


@pytest.fixture
def settled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runner's clock ten seconds on, so every folder made by the check is settled."""
    monkeypatch.setattr(_grants, "_now_ns", lambda: time.time_ns() + WRITE_MS, raising=False)


@pytest.fixture
def calls(guest):
    """`calls(set, grant, n)`: n calls that print hello, each with `grant` writable at /w."""

    def calls(found: Artifacts, grant: Path, n: int) -> None:
        for _ in range(n):
            done = run(guest[1], grants=[Grant(grant, "/w", writable=True)], artifacts=found)
            assert done.stdout == b"hello\n", done

    return calls


@pytest.fixture
def same_disk():
    """A scratch folder on wasmtime-py's disk, where a hard link to its library can be made: in
    the environment's own folder, which holds no runtime code itself, else under build/."""
    try:
        folder = Path(tempfile.mkdtemp(prefix="kept-checks-", dir=sys.prefix))
    except OSError:
        base = REPO / "build" / "hardening"
        base.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(dir=base))
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


def _wasmtime_library() -> Path:
    import wasmtime

    top = Path(wasmtime.__file__).parent
    libs = [p for p in sorted(top.rglob("*wasmtime*.*")) if p.suffix in (".so", ".dylib", ".dll")]
    assert libs, top
    return libs[0]


def _replace(path: Path) -> None:
    """Another file put at `path` by a rename, as an installer replaces one."""
    fresh = path.with_name(path.name + ".new")
    shutil.copyfile(path, fresh)
    os.replace(fresh, path)


def _scan_of(set_dir: Path) -> _grants.RuntimeFiles:
    """The set's files alone, scanned as the runner scans the runtime."""
    a = Artifacts.at(set_dir)
    return _grants.RuntimeFiles((a.lib,), (a.perl, a.manifest, a.sums, a.dev / "null"))


def _placed(folder: Path) -> tuple[_grants.Placed, ...]:
    host = folder.resolve()
    return (_grants.Placed(host, "/w", True, _grants.chain(host)),)


# Scans.


@KEPT
def test_ten_writable_calls_scan_the_runtime_once(the_set, tmp_path, scans, settled, calls):
    calls(the_set, tmp_path, 10)
    assert scans() == 1


@KEPT
@pytest.mark.parametrize("how", ["a new file", "a removal", "a rename over it"])
def test_a_change_in_the_library_makes_the_next_call_scan_again(
    the_set, set_dir, tmp_path, scans, settled, calls, how
):
    calls(the_set, tmp_path, 3)
    assert scans() == 1
    folder = set_dir / "lib" / "perl5" / "5.42"
    if how == "a new file":
        (folder / "new.pm").write_text("1;\n")
    elif how == "a removal":
        (folder / "strict.pm").unlink()
    else:
        _replace(folder / "strict.pm")
    calls(the_set, tmp_path, 3)
    assert scans() == 2


@KEPT
def test_a_folder_changed_within_two_seconds_of_its_stamp_is_scanned_again(
    the_set, set_dir, tmp_path, scans, monkeypatch, calls
):
    folder = set_dir / "lib" / "perl5" / "5.42"
    (folder / "x").write_text("")
    (folder / "x").unlink()
    changed = max(os.stat(d).st_ctime_ns for d in (set_dir, folder, set_dir / "dev"))
    # One second after the change: a file time as coarse as a second may not show a later one.
    monkeypatch.setattr(_grants, "_now_ns", lambda: changed + 10**9, raising=False)
    calls(the_set, tmp_path, 3)
    assert scans() == 3
    # Three seconds after it: the stamps are settled, and the next scan is kept.
    monkeypatch.setattr(_grants, "_now_ns", lambda: changed + 3 * 10**9, raising=False)
    calls(the_set, tmp_path, 3)
    assert scans() == 4


@KEPT
def test_a_scan_that_could_not_list_a_runtime_folder_serves_one_call(
    the_set, set_dir, tmp_path, scans, settled, calls, monkeypatch
):
    # As when the process's rights to a folder grow after the scan: nothing in the folder moves.
    folder = os.path.abspath(set_dir / "lib" / "perl5" / "5.42")
    real = os.scandir

    def shut(path="."):
        if isinstance(path, (str, os.PathLike)) and os.path.abspath(os.fspath(path)) == folder:
            raise PermissionError(errno.EACCES, "Permission denied", os.fspath(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", shut)
    calls(the_set, tmp_path, 1)
    monkeypatch.setattr(os, "scandir", real)
    os.link(set_dir / "lib" / "perl5" / "5.42" / "strict.pm", tmp_path / "strict.pm")
    with pytest.raises(ValueError, match="hard link"):
        calls(the_set, tmp_path, 1)
    assert scans() == 2


# The rule, after a scan.


@KEPT
def test_a_link_to_wasmtimes_library_made_after_several_calls_is_refused(
    the_set, same_disk, scans, settled, calls
):
    calls(the_set, same_disk, 3)
    assert scans() == 1
    deep = same_disk / "a" / "b"
    deep.mkdir(parents=True)
    try:
        os.link(_wasmtime_library(), deep / "copy.so")
    except OSError as e:
        pytest.skip(f"no hard link to wasmtime's library here: {e}")
    with pytest.raises(ValueError, match="hard link"):
        calls(the_set, same_disk, 1)


@KEPT
def test_a_link_to_a_library_file_made_after_several_calls_is_refused(
    the_set, set_dir, tmp_path, scans, settled, calls
):
    calls(the_set, tmp_path, 3)
    assert scans() == 1
    os.link(set_dir / "lib" / "perl5" / "5.42" / "strict.pm", tmp_path / "strict.pm")
    with pytest.raises(ValueError, match="hard link"):
        calls(the_set, tmp_path, 1)
    assert scans() == 1  # the scan held, and the walk of the grant found the link


@KEPT
def test_a_runtime_file_replaced_by_rename_then_linked_is_refused(
    the_set, set_dir, tmp_path, scans, settled, calls
):
    calls(the_set, tmp_path, 3)
    strict = set_dir / "lib" / "perl5" / "5.42" / "strict.pm"
    _replace(strict)  # another inode at the name
    os.link(strict, tmp_path / "strict.pm")
    with pytest.raises(ValueError, match="hard link"):
        calls(the_set, tmp_path, 1)
    assert scans() == 2


@KEPT
def test_a_rename_with_the_folders_mtime_set_back_is_seen_by_its_ctime(
    the_set, set_dir, tmp_path, scans, settled, calls
):
    calls(the_set, tmp_path, 3)
    folder = set_dir / "lib" / "perl5" / "5.42"
    before = os.stat(folder)
    _replace(folder / "strict.pm")
    os.utime(folder, ns=(before.st_atime_ns, before.st_mtime_ns))  # the owner may set an mtime
    after = os.stat(folder)
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns  # no one but the clock sets a ctime
    os.link(folder / "strict.pm", tmp_path / "strict.pm")
    with pytest.raises(ValueError, match="hard link"):
        calls(the_set, tmp_path, 1)
    assert scans() == 2


@KEPT
def test_perl_wasm_replaced_by_rename_after_a_kept_scan_then_linked_is_refused(
    the_set, set_dir, tmp_path, scans, settled, calls
):
    # perl.wasm lies in no folder the scan walks; its own folder's stamp is what shows the rename.
    calls(the_set, tmp_path, 3)
    _replace(set_dir / "perl.wasm")
    os.link(set_dir / "perl.wasm", tmp_path / "perl.wasm")
    with pytest.raises(ValueError, match="hard link"):
        calls(the_set, tmp_path, 1)


@KEPT
def test_a_grant_past_max_walk_after_a_kept_scan_is_refused_while_a_file_has_two_names(
    the_set, set_dir, tmp_path, scans, settled, calls, monkeypatch
):
    big = tmp_path / "big"
    big.mkdir()
    for i in range(20):
        (big / f"f{i}").write_text("x")
    calls(the_set, big, 3)
    assert scans() == 1
    os.link(set_dir / "perl.wasm", tmp_path / "elsewhere.wasm")  # a second name, not granted
    monkeypatch.setattr(_grants, "MAX_WALK", 10)
    with pytest.raises(ValueError, match="too large"):
        calls(the_set, big, 1)


def test_past_max_walk_a_grant_is_refused_exactly_when_a_runtime_file_has_two_names(
    bare_dir, tmp_path, monkeypatch
):
    big = tmp_path / "big"
    big.mkdir()
    for i in range(20):
        (big / f"f{i}").write_text("x")
    monkeypatch.setattr(_grants, "MAX_WALK", 10)
    scan = _scan_of(bare_dir)
    assert scan.linked_now() == {}
    _grants._refuse_second_names(_placed(big), scan)  # no second name anywhere: nothing to find
    os.link(bare_dir / "perl.wasm", tmp_path / "elsewhere.wasm")
    with pytest.raises(ValueError, match="too large"):
        _grants._refuse_second_names(_placed(big), scan)


def test_an_unreadable_folder_is_refused_exactly_when_a_runtime_file_has_two_names(
    bare_dir, tmp_path, monkeypatch
):
    grant = tmp_path / "w"
    (grant / "shut").mkdir(parents=True)
    real = os.scandir

    def scandir(path="."):
        if os.fspath(path).endswith("shut"):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    scan = _scan_of(bare_dir)
    _grants._refuse_second_names(_placed(grant), scan)
    os.link(bare_dir / "perl.wasm", tmp_path / "elsewhere.wasm")
    with pytest.raises(ValueError, match="cannot search"):
        _grants._refuse_second_names(_placed(grant), scan)


def test_a_match_is_read_afresh(bare_dir, tmp_path):
    # A scan that names, under a runtime file's path, the identity of a file that is not one:
    # as if the runtime file had been replaced in a tick its folder's time did not show.
    grant = tmp_path / "w"
    grant.mkdir()
    (grant / "mine").write_text("x")
    os.link(grant / "mine", tmp_path / "also-mine")  # two names, neither the runtime's
    st = os.lstat(grant / "mine")
    scan = _scan_of(bare_dir)
    strict = bare_dir / "lib" / "perl5" / "5.42" / "strict.pm"
    scan.files[(st.st_dev, st.st_ino)] = str(strict)
    _grants._refuse_second_names(_placed(grant), scan)  # read afresh, it is not strict.pm
    os.link(strict, grant / "strict.pm")  # a real second name is still refused
    with pytest.raises(ValueError, match="hard link"):
        _grants._refuse_second_names(_placed(grant), scan)


def test_a_stale_match_scans_again_and_is_refused(bare_dir, tmp_path):
    # The scan holds strict.pm's identity under a path that no longer holds it, as if strict.pm
    # had been renamed within its tree in a tick its folder's time did not show. Read afresh, the
    # path says nothing; a new scan finds the file where it is.
    grant = tmp_path / "w"
    grant.mkdir()
    strict = bare_dir / "lib" / "perl5" / "5.42" / "strict.pm"
    scan = _scan_of(bare_dir)
    st = os.lstat(strict)
    scan.files[(st.st_dev, st.st_ino)] = str(bare_dir / "MANIFEST")
    os.link(strict, grant / "strict.pm")
    with pytest.raises(ValueError, match="hard link"):
        _grants._refuse_second_names(_placed(grant), scan)


@KEPT
def test_a_top_that_is_a_link_is_stamped_by_the_folder_it_names(tmp_path, settled):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "f.pm").write_text("1;\n")
    top = tmp_path / "top"
    try:
        os.symlink(tree, top, target_is_directory=True)
    except OSError:
        pytest.skip("POSIX links")
    grant = tmp_path / "w"
    grant.mkdir()
    first = _grants.runtime_scan((top,), ())
    assert _grants.runtime_scan((top,), ()) is first  # kept
    # A file of the top's own folder replaced: that folder's stamp moves, the link's does not.
    _replace(tree / "f.pm")
    os.link(tree / "f.pm", grant / "f.pm")
    with pytest.raises(ValueError, match="hard link"):
        _grants._refuse_second_names(_placed(grant), _grants.runtime_scan((top,), ()))


# The shortcut in a copy-mode install.

_CHILD = r"""
import json, os, sys, time
site, set_dir, grant, argv = sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4])
import wasmtime
assert wasmtime.__file__.startswith(site), wasmtime.__file__
from wasi_perl import Artifacts, Grant, _grants, run, runner
lib = os.path.abspath(os.path.join(set_dir, "lib", "perl5"))
listed = []
sys.addaudithook(
    lambda e, a: listed.append(os.path.abspath(os.fspath(a[0])))
    if e == "os.scandir" and a and isinstance(a[0], str) else None
)
_grants._now_ns = lambda: time.time_ns() + 10**10
found = Artifacts.at(set_dir)
# More entries in the grant than the runtime has files, walked before the link, so a call
# reaches the shortcut that reads the runtime afresh.
size = len(runner._runtime_files(found).files)
for i in range(size + 100):
    open(os.path.join(grant, f"f{i}"), "w").close()
def call():
    try:
        done = run(argv, grants=[Grant(grant, "/w", writable=True)], artifacts=found)
        return done.stdout.decode()
    except ValueError as e:
        return "refused: " + str(e)
before = [call() for _ in range(3)]
linked = sorted(runner._runtime_files(found).linked_now().values())
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
                  "size": size, "scans": sum(1 for p in listed if p == lib)}))
"""


def test_in_a_copy_mode_install_a_link_made_after_the_scan_is_still_refused(
    set_dir, guest, tmp_path
):
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
    argv = json.dumps(guest[1])
    out = subprocess.run(
        [sys.executable, "-c", _CHILD, str(site), str(set_dir), str(grant), argv],
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
    the_set, tmp_path, scans, settled, monkeypatch, calls, kept, want
):
    monkeypatch.setattr(_grants, "SCANS_KEPT", kept, raising=False)
    calls(the_set, tmp_path, 10)
    assert scans() == want


def test_the_scan_holds_every_file_of_the_three_trees_and_the_set(bare_dir):
    import wasmtime

    import wasi_perl

    found = Artifacts.at(bare_dir)
    scan = runner._runtime_files(found)
    tops = (Path(wasmtime.__file__).parent, Path(wasi_perl.__file__).parent, found.lib)
    files = [p for top in tops for p in top.rglob("*") if p.is_file() and not p.is_symlink()]
    files += [found.perl, found.manifest, found.sums, found.dev / "null"]
    for p in files:
        st = os.lstat(p)
        assert (st.st_dev, st.st_ino) in scan.files, p
