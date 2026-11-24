"""No call from an image sees another's: each is a fresh instance of the image module, so what
one call does reaches neither the image nor any other call, on one thread or many."""

from __future__ import annotations

import hashlib
import threading

import pytest

from snapshot.images import call, image_set
from wasi_perl import Grant, make_snapshot

pytestmark = pytest.mark.slow


# 1


def test_a_call_changes_nothing_the_next_one_sees(package_image) -> None:
    leak = '$POSIX::planted = "secret"; *POSIX::floor = sub { 666 }; print POSIX::floor(1), "\\n"'
    assert call(["-e", leak], package_image).stdout == b"666\n"
    check = 'print defined $POSIX::planted ? "LEAK" : "clean", " ", floor(1.5), "\\n"'
    assert call(["-MPOSIX=floor", "-e", check], package_image).stdout == b"clean 1\n"


# 2 and 3


def test_threads_share_an_image_and_none_sees_anothers(package_image) -> None:
    results: dict[int, list[bytes]] = {}
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            got = []
            for i in range(10):
                marker = f"t{n}-{i}"
                code = f'$main::marker = "{marker}"; print $main::marker'
                got.append(call(["-e", code], package_image).stdout)
            results[n] = got
        except BaseException as e:  # reported on the main thread
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    for n in range(8):
        assert results[n] == [f"t{n}-{i}".encode() for i in range(10)]
    image = package_image._image
    assert hashlib.sha256(image).hexdigest() == package_image.origin.image_sha256


# 4


def test_the_makings_grants_are_not_the_calls(tmp_path) -> None:
    made_with, other = tmp_path / "made", tmp_path / "other"
    made_with.mkdir()
    other.mkdir()
    (made_with / "f.txt").write_text("the making's\n")
    (other / "f.txt").write_text("the call's\n")
    image = make_snapshot(
        'open my $f, "<", "/data/f.txt" or die; close $f;',
        grants=[Grant(made_with, "/data")],
        artifacts=image_set(),
        cache_dir=False,
    )
    probe = 'print -e "/data/f.txt" ? "seen\\n" : "unseen\\n"'
    assert call(["-e", probe], image).stdout == b"unseen\n"
    read = 'open my $f, "<", "/data/f.txt" or die "$!"; print <$f>'
    assert call(["-e", read], image, grants=[Grant(other, "/data")]).stdout == b"the call's\n"
