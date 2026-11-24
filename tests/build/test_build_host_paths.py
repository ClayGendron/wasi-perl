"""Checks that no path of the machine that built the set ships in it.

A path from the build host (its home, the checkout, an agent harness's folder) would make the
set depend on where it was built, and tells every guest about that machine through %Config.
The library's own documentation names example paths such as `/home/foo` and CPAN's
`/root/.cpan`; those are upstream's and are in the pinned tarball. So every string in the set
that looks like a home path, or names `.claude`, must also be in the tarball, and the
checkout's own path must not appear at all.
"""

from __future__ import annotations

import os
import re
import tarfile
from pathlib import Path

import pytest

from conftest import ARTIFACTS, ROOT

LOOKS_LIKE_HOME = re.compile(rb"(?:/root/|/home/|/Users/)[^\s'\"<>()\\]*")
TOKEN = re.compile(rb"[^\s'\"<>()\\]*")
TARBALL = "perl-5.42.3.tar.xz"


def _tarball() -> Path:
    for folder in (
        os.environ.get("WASI_PERL_DOWNLOAD_CACHE"),
        ROOT / "downloads",
        ROOT.parent.parent / "wasi-perl" / "downloads",
    ):
        if folder and (Path(folder) / TARBALL).is_file():
            return Path(folder) / TARBALL
    pytest.skip(f"{TARBALL} is not in downloads/ or $WASI_PERL_DOWNLOAD_CACHE")


def _set_files() -> list[Path]:
    if not (ARTIFACTS / "perl.wasm").is_file():
        pytest.skip("build/artifacts is missing; run scripts/build.sh")
    return sorted(p for p in ARTIFACTS.rglob("*") if p.is_file())


def test_the_checkout_path_does_not_ship():
    needles = {str(ROOT).encode(), str(ROOT.resolve()).encode()}
    found = [
        str(p.relative_to(ARTIFACTS))
        for p in _set_files()
        if any(n in p.read_bytes() for n in needles)
    ]
    assert found == []


def test_every_home_path_in_the_set_is_upstreams():
    found: dict[bytes, str] = {}
    for p in _set_files():
        data = p.read_bytes()
        for m in LOOKS_LIKE_HOME.finditer(data):
            found.setdefault(m.group(0), str(p.relative_to(ARTIFACTS)))
        at = data.find(b".claude")
        while at >= 0:
            start = at
            while start > 0 and TOKEN.fullmatch(data[start - 1 : start]):
                start -= 1
            found.setdefault(TOKEN.match(data, start).group(0), str(p.relative_to(ARTIFACTS)))
            at = data.find(b".claude", at + 1)
    assert found, "the library's documentation should hold some example paths"
    left = set(found)
    with tarfile.open(_tarball()) as tf:
        for member in tf:
            if not left or not member.isfile():
                continue
            data = tf.extractfile(member).read()
            left = {s for s in left if s not in data}
    assert {s: found[s] for s in left} == {}
