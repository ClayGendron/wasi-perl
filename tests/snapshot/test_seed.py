"""The image's hash seed: drawn at each making from the host's randomness, then the same in every
call from that image; the per-call random state drawn again from each call's own randomness; no
environment that would make the seed known; and the saved file kept as private as a key.

The seed is traced to random_get by a linker of this file's own, whose random_get answers a
fixed byte pattern, as the build checks trace perl.wasm's seeds."""

from __future__ import annotations

import os
import sys

import pytest

from snapshot.images import call, image_set
from wasi_perl import Snapshot, _artifacts, _cache, _engines, make_snapshot

pytestmark = pytest.mark.slow

WASI = "wasi_snapshot_preview1"
SEED = ["-MHash::Util=hash_seed", "-e", 'print unpack("H*", hash_seed()), "\\n"']
ORDER = ["-e", 'my %h = map { $_ => 1 } "a" .. "p"; print join("", keys %h), " ", rand(), "\\n"']


def seed_of(image) -> bytes:
    done = call(SEED, image)
    assert done.exit_code == 0, done
    return done.stdout


# 1


def test_each_making_draws_its_own_seed_and_every_call_shares_it() -> None:
    images = [make_snapshot("", artifacts=image_set(), cache_dir=False) for _ in range(5)]
    seeds = {seed_of(image) for image in images}
    assert len(seeds) == 5
    assert len({image.origin.image_sha256 for image in images}) == 5
    assert {seed_of(images[0]) for _ in range(20)} == {seed_of(images[0])}


# 2 and 3


@pytest.fixture
def probe(monkeypatch):
    """Every call links against a linker whose random_get answers bytes start, start+1, ...;
    `probe.start` sets the pattern."""
    import wasmtime as wt

    class Probe:
        start = 1

    def random_get(caller, ptr: int, length: int) -> int:
        memory = caller.get("memory")
        data = bytes((Probe.start + i) % 256 for i in range(length))
        memory.write(caller, data, ptr)
        return 0

    linkers: dict = {}

    def linked(metered, mod, mounts=False):
        eng = _engines.engine(metered)
        if eng not in linkers:
            linker = wt.Linker(eng)
            linker.define_wasi()
            linker.allow_shadowing = True
            i32 = wt.ValType.i32()
            ty = wt.FuncType([i32, i32], [i32])
            linker.define_func(WASI, "random_get", ty, random_get, True)
            linkers[eng] = linker
        return linkers[eng].instantiate_pre(mod)

    monkeypatch.setattr(_engines, "linked", linked)
    return Probe


def test_the_seed_comes_from_the_makings_random_get(probe) -> None:
    a = make_snapshot("", artifacts=image_set(), cache_dir=False)
    b = make_snapshot("", artifacts=image_set(), cache_dir=False)
    probe.start = 101
    c = make_snapshot("", artifacts=image_set(), cache_dir=False)
    probe.start = 1
    assert seed_of(a) == seed_of(b)
    assert seed_of(a) != seed_of(c)


def test_the_calls_random_state_comes_from_its_own_random_get(probe) -> None:
    image = make_snapshot("", artifacts=image_set(), cache_dir=False)
    first, second = call(ORDER, image), call(ORDER, image)
    assert first.stdout == second.stdout  # the same bytes from random_get, the same draws
    probe.start = 77
    assert call(ORDER, image).stdout != first.stdout


def test_under_the_real_runner_the_key_order_varies(empty_image) -> None:
    orders = {call(ORDER, empty_image).stdout.split()[0] for _ in range(20)}
    assert len(orders) > 1


# 4


@pytest.mark.parametrize("name", ["PERL_HASH_SEED", "PERL_PERTURB_KEYS", "PERL_JSON_PP_USE_B"])
def test_a_making_may_not_set_what_the_image_would_freeze(monkeypatch, name) -> None:
    calls: list = []
    for owner, attr in ((_artifacts, "checked"), (_cache, "every"), (_engines, "module")):
        monkeypatch.setattr(owner, attr, lambda *a, n=attr, **k: calls.append(n) or 1 / 0)
    with pytest.raises(ValueError, match=name):
        make_snapshot("", env={name: "0"})
    assert calls == []


# 5


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_saved_file_is_private(empty_image, tmp_path) -> None:
    path = tmp_path / "seed.wpsnap"
    empty_image.save(path)
    assert os.stat(path).st_mode & 0o777 == 0o600
    Snapshot.load(path)
    for mode in (0o640, 0o606):
        os.chmod(path, mode)
        with pytest.raises(PermissionError, match="by others"):
            Snapshot.load(path)


# 6


def test_the_makings_environment_is_held_once_in_its_image() -> None:
    marker = "marker-4f1c9e27d3b8a6"
    image = make_snapshot("", env={"MARKER": marker}, artifacts=image_set(), cache_dir=False)
    assert image._image.count(marker.encode()) == 1  # %ENV's copy; the dropped block is zeroed
    assert image.origin.env_names == ("MARKER",)
    assert marker not in repr(image.origin)
