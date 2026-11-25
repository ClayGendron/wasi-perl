"""The preload image's API: `Snapshot`, its file and its checks, `make_snapshot`'s failures and
`make_package_snapshot`'s names, as wasi-python's are. The listing against wasi-python's own is
in `tests/mounts/test_mount_api.py`, beside the mounts' listing."""

from __future__ import annotations

import os
import pickle
import shutil

import pytest

from mounts.raw import MemoryProvider
from snapshot.images import call, image_set
from wasi_perl import (
    Artifacts,
    ArtifactsMissing,
    Grant,
    Mount,
    Snapshot,
    SnapshotFailed,
    SnapshotMismatch,
    _artifacts,
    _cache,
    _engines,
    _snapshot,
    _snapshot_make,
    make_package_snapshot,
    make_snapshot,
)

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def small():
    return make_snapshot("our $kept = 7;", artifacts=image_set(), cache_dir=False)


# 1


def test_a_snapshot_is_made_only_by_the_runner_and_never_changes(small) -> None:
    with pytest.raises(TypeError):
        Snapshot()
    with pytest.raises(TypeError, match="not pickled"):
        pickle.dumps(small)
    with pytest.raises(AttributeError):
        small._image = b""
    with pytest.raises(AttributeError):
        small.origin = None
    assert "our $kept = 7;" in repr(small) and "wasi-perl" in repr(small)


# 2


def test_save_then_load_gives_the_same_snapshot(small, tmp_path) -> None:
    path = tmp_path / "small.wpsnap"
    small.save(path)
    again = Snapshot.load(path)
    assert again.origin == small.origin
    assert again._image == small._image
    o = small.origin
    assert (o.kind, o.parent_sha256, o.home, o.prelude_fuel) == ("prelude", None, "", None)
    assert o.binary_sha256 == _artifacts.digest(image_set().reactor)
    assert o.image_bytes == len(small._image)


# 3


def _broken(small, tmp_path, how: str):
    path = tmp_path / f"{how}.wpsnap"
    small.save(path)
    data = bytearray(path.read_bytes())
    if how == "truncated":
        data = data[: len(data) - 100]
    elif how == "flipped":
        data[-200] ^= 0xFF
    elif how == "newer":
        magic, header, rest = bytes(data).split(b"\n", 2)
        header = header.replace(b'"format": 1', b'"format": 2')
        data = bytearray(magic + b"\n" + header + b"\n" + rest)
    elif how == "another image":
        # A sound stream, of the right size, of an image that is not the header's.
        import zlib

        magic, header, _ = bytes(data).split(b"\n", 2)
        image = bytearray(small._image)
        image[len(image) // 2] ^= 0xFF
        data = bytearray(magic + b"\n" + header + b"\n" + zlib.compress(bytes(image), 1))
    elif how == "another runtime":
        data = bytearray(b"wasi-python snapshot\n" + bytes(data[len(_snapshot.MAGIC) :]))
    path.write_bytes(bytes(data))
    os.chmod(path, 0o600)
    return path


@pytest.mark.parametrize(
    "how", ["truncated", "flipped", "newer", "another image", "another runtime"]
)
def test_a_damaged_or_foreign_file_is_a_mismatch(small, tmp_path, how) -> None:
    match = "does not match its hash" if how == "another image" else None
    with pytest.raises(SnapshotMismatch, match=match):
        Snapshot.load(_broken(small, tmp_path, how))


# 4


def test_another_set_is_a_mismatch(small, tmp_path) -> None:
    found = image_set()
    other = tmp_path / "perl-reactor.wasm"
    shutil.copyfile(found.reactor, other)
    with open(other, "ab") as f:
        f.write(b"\0\x05\x04note")  # a custom section: another reactor's bytes
    with_reactor = Artifacts(found.perl, found.lib, found.dev, found.manifest, found.sums, other)
    sums = tmp_path / "SHA256SUMS"
    lines = found.sums.read_text().splitlines()
    first_lib = next(i for i, ln in enumerate(lines) if "  lib/" in ln)
    lines[first_lib] = "0" * 64 + lines[first_lib][64:]
    sums.write_bytes(("\n".join(lines) + "\n").encode())
    with_library = Artifacts(found.perl, found.lib, found.dev, found.manifest, sums, found.reactor)
    for which, other_set in (("perl-reactor.wasm", with_reactor), ("library", with_library)):
        with pytest.raises(SnapshotMismatch, match=which):
            small.check(other_set)
        with pytest.raises(SnapshotMismatch, match=which):
            call(["-e", "1"], small, artifacts=other_set)
    small.check(found)  # the control


# 5


def test_memory_below_the_image_is_a_mismatch(small) -> None:
    with pytest.raises(SnapshotMismatch, match="memory_bytes"):
        call(["-e", "1"], small, memory_bytes=small.origin.image_bytes - 65536)
    assert call(["-e", "1"], small, memory_bytes=small.origin.image_bytes).exit_code == 0


# 6 and 7


@pytest.mark.parametrize(
    ("code", "reason", "status"),
    [
        ('die "no good\\n"', "exit", 255),
        ("exit 3", "exit", 3),
        ("sub {", "exit", 255),
        ('our $F; open $F, "<", "/dev/null" or die', "open file", 0),
    ],
)
def test_a_making_that_leaves_no_image_says_why(code, reason, status) -> None:
    with pytest.raises(SnapshotFailed) as info:
        make_snapshot(code, artifacts=image_set(), cache_dir=False)
    assert info.value.reason == reason
    assert info.value.completed is not None and info.value.completed.exit_code == status
    if code.startswith("die"):
        assert str(info.value).endswith(": no good")


def test_a_making_out_of_fuel_has_trapped() -> None:
    with pytest.raises(SnapshotFailed) as info:
        make_snapshot("1 while 1;", artifacts=image_set(), cache_dir=False, fuel=50_000_000)
    assert info.value.reason == "trapped"
    assert info.value.completed.trapped == "out of fuel"


def test_exit_0_makes_an_image() -> None:
    snap = make_snapshot('our $x = "before"; exit 0;', artifacts=image_set(), cache_dir=False)
    assert call(["-e", "print $x"], snap).stdout == b"before"


def test_fuel_is_recorded_as_the_preludes() -> None:
    snap = make_snapshot("require POSIX;", artifacts=image_set(), cache_dir=False, fuel=10**10)
    assert snap.origin.prelude_fuel is not None and snap.origin.prelude_fuel > 1_000_000
    done = call(["-e", "print POSIX::floor(2.5)"], snap, fuel=10**9)
    assert (done.exit_code, done.stdout) == (0, b"2")


# 8


@pytest.fixture
def no_work(monkeypatch: pytest.MonkeyPatch):
    """Fails the check if the refused making does any work: finds the set, lists the caches, or
    compiles a module."""
    calls: list = []
    for owner, name in (
        (_artifacts, "checked"),
        (_artifacts, "artifacts"),
        (_cache, "every"),
        (_engines, "module"),
    ):
        monkeypatch.setattr(owner, name, lambda *a, n=name, **k: calls.append(n) or 1 / 0)
    yield
    assert calls == []


def test_package_names_are_resolved_as_wasi_pythons(no_work) -> None:
    with pytest.raises(TypeError, match="pass"):
        make_package_snapshot("POSIX")
    with pytest.raises(TypeError, match="packages"):
        make_package_snapshot([b"POSIX"])
    with pytest.raises(ValueError, match="ships no package named 'No-Such-Dist'"):
        make_package_snapshot(["No-Such-Dist"])


@pytest.mark.parametrize("name", ["JSON-PP", "json-pp", "JSON::PP"])
def test_a_distribution_or_its_module_loads_it(name) -> None:
    snap = make_package_snapshot([name], artifacts=image_set(), cache_dir=False)
    assert snap.origin.prelude == "require JSON::PP;\n"
    done = call(["-e", 'print exists $INC{"JSON/PP.pm"} ? "yes" : "no"'], snap)
    assert done.stdout == b"yes"


def test_the_default_list_is_the_eight_in_order(package_image) -> None:
    modules = [m for _, ms in _snapshot_make.PACKAGES for m in ms]
    assert modules == [
        "strict", "warnings", "POSIX", "List::Util", "JSON::PP", "Data::Dumper", "Storable",
        "Encode",
    ]  # fmt: skip
    assert package_image.origin.prelude == "".join(f"require {m};\n" for m in modules)


def test_a_module_the_set_lacks_fails_the_making() -> None:
    with pytest.raises(SnapshotFailed) as info:
        make_package_snapshot(["No::Such::Module"], artifacts=image_set(), cache_dir=False)
    assert info.value.reason == "exit" and "No/Such/Module.pm" in str(info.value)


# 10


def test_a_set_without_a_reactor_runs_every_call_but_an_images(small, tmp_path) -> None:
    found = image_set()
    five = Artifacts(found.perl, found.lib, found.dev, found.manifest, found.sums)
    assert five.reactor is None
    done = call(["-e", 'print "plain\\n"'], False, artifacts=five)
    assert (done.exit_code, done.stdout, done.image_sha256) == (0, b"plain\n", None)
    assert call(["-e", "1"], True, artifacts=five).exit_code == 0
    with pytest.raises(ArtifactsMissing, match="perl-reactor.wasm"):
        make_snapshot("", artifacts=five, cache_dir=False)
    with pytest.raises(ArtifactsMissing, match="perl-reactor.wasm"):
        call(["-e", "1"], small, artifacts=five)
    with pytest.raises(ArtifactsMissing, match="perl-reactor.wasm"):
        small.check(five)
    folder = tmp_path / "set"
    folder.mkdir()
    (folder / "perl.wasm").write_bytes(b"")
    assert Artifacts.at(folder).reactor is None
    (folder / "perl-reactor.wasm").write_bytes(b"")
    assert Artifacts.at(folder).reactor == folder / "perl-reactor.wasm"


def test_true_and_false_both_run_perl_wasm(monkeypatch) -> None:
    used = []
    real = _engines.module
    monkeypatch.setattr(_engines, "module", lambda m, b, *a: used.append(b.name) or real(m, b, *a))
    for flag in (True, False):
        done = call(["-e", "1"], flag)
        assert (done.exit_code, done.image_sha256) == (0, None)
    assert used == ["perl.wasm", "perl.wasm"]
    with pytest.raises(TypeError, match="snapshot must be"):
        call(["-e", "1"], "yes")


# 11


def test_a_mount_serves_the_making_and_the_image_keeps_what_it_read() -> None:
    provider = MemoryProvider({"/Mod.pm": b"package Mod; our $v = 'from the mount'; 1;\n"})
    snap = make_snapshot(
        'use lib "/work"; require Mod;',
        mounts=[Mount(provider, "/work")],
        artifacts=image_set(),
        cache_dir=False,
    )
    assert snap.origin.guest_paths == ("/work",)
    served = provider.total()
    assert served > 0
    done = call(["-e", 'print $INC{"Mod.pm"} ? "$Mod::v\\n" : "missing\\n"'], snap)
    assert (done.exit_code, done.stdout) == (0, b"from the mount\n")
    assert provider.total() == served  # the call made no provider call
    with pytest.raises(SnapshotFailed) as info:
        make_snapshot(
            'use lib "/work"; require Mod;',
            mounts=[Mount(MemoryProvider(dict(provider.tree)), "/work")],
            max_file_calls=1,
            artifacts=image_set(),
            cache_dir=False,
        )
    assert info.value.reason == "trapped"
    assert info.value.completed.trapped == "out of file calls"


def test_a_grant_serves_the_making(tmp_path) -> None:
    (tmp_path / "words.txt").write_bytes(b"alpha\nbeta\n")
    snap = make_snapshot(
        'open my $f, "<", "/data/words.txt" or die; our @w = <$f>; close $f;',
        grants=[Grant(tmp_path, "/data")],
        artifacts=image_set(),
        cache_dir=False,
    )
    assert snap.origin.guest_paths == ("/data",)
    assert call(["-e", "print scalar(@w), $w[1]"], snap).stdout == b"2beta\n"


def test_a_save_where_a_running_call_may_write_is_refused(small, tmp_path) -> None:
    from wasi_perl import _grants

    folder = tmp_path / "shared"
    folder.mkdir()
    slot = object()
    placed = _grants.judge([Grant(folder, "/w", writable=True)], None, caches=(), runtime=())[0]
    _grants.hold(placed, slot)
    try:
        with pytest.raises(PermissionError, match="call running now"):
            small.save(folder / "s.wpsnap")
    finally:
        _grants.let_go(slot)
    small.save(folder / "s.wpsnap")
    with pytest.raises(ValueError, match="saved or loaded a snapshot"):
        call(["-e", "1"], grants=[Grant(folder, "/w", writable=True)])
