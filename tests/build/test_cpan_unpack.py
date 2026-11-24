"""Checks on how the build takes in a CPAN tarball: only as pinned, and only into a new folder.

scripts/lib.sh's unpack_pinned is what scripts/10_native.sh, 20_target.sh and 30_library.sh
unpack each listed tarball with. It must refuse a tarball that downloads/SHA256SUMS does not
pin, one that does not match its pin, one that unpacks to anything but one top folder, and a
destination that already exists. These checks run it on small tarballs made here, so they need
no build. The next checks run each build step on a copy of the checkout whose tarball is the
real one with one byte changed: the step must fail on it before it touches its build tree.
The last reads the build's own log: a CPAN extension's Makefile.PL may need Perl's Unicode
tables (Text-CSV_XS's loads charnames), so it must run only once mktables has made them.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
from _build_helpers import ROOT

SCRIPTS = ROOT / "scripts"
DOWNLOADS = ROOT / "downloads"


def _tarball(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def _root(tmp_path: Path) -> Path:
    """A scratch checkout: scripts/lib.sh and an empty downloads/ with its own pins."""
    root = tmp_path / "root"
    (root / "scripts").mkdir(parents=True)
    (root / "downloads").mkdir()
    shutil.copy(SCRIPTS / "lib.sh", root / "scripts" / "lib.sh")
    (root / "downloads" / "SHA256SUMS").write_text("")
    return root


def _pin(root: Path, name: str, data: bytes | None = None) -> None:
    data = (root / "downloads" / name).read_bytes() if data is None else data
    digest = hashlib.sha256(data).hexdigest()
    with open(root / "downloads" / "SHA256SUMS", "a") as f:
        f.write(f"{digest}  {name}  https://example.invalid/{name}\n")


def _unpack(root: Path, name: str, dest: Path) -> subprocess.CompletedProcess:
    log = root / "unpack.log"
    return subprocess.run(
        ["sh", "-c", '. "$0"; unpack_pinned "$@"', str(root / "scripts" / "lib.sh"), name, str(dest), str(log)],
        capture_output=True,
        text=True,
        check=False,
    )


def _good(root: Path, name: str = "Good-1.0.tar.gz") -> str:
    _tarball(root / "downloads" / name, {"Good-1.0/lib/Good.pm": b"package Good; 1;\n"})
    return name


def test_a_pinned_tarball_unpacks_as_the_folder_named(tmp_path):
    root = _root(tmp_path)
    name = _good(root)
    _pin(root, name)
    p = _unpack(root, name, tmp_path / "out" / "Good")
    assert (p.returncode, p.stderr) == (0, ""), p
    assert (tmp_path / "out" / "Good" / "lib" / "Good.pm").read_bytes() == b"package Good; 1;\n"
    assert sorted(os.listdir(tmp_path / "out")) == ["Good"]


def test_a_tarball_one_byte_off_its_pin_is_refused(tmp_path):
    root = _root(tmp_path)
    name = _good(root)
    _pin(root, name)
    path = root / "downloads" / name
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))
    p = _unpack(root, name, tmp_path / "out" / "Good")
    assert p.returncode == 1
    assert p.stderr == f"FAILED: {name} does not match its SHA-256 in downloads/SHA256SUMS\n"
    assert not (tmp_path / "out").exists()


def test_an_unpinned_tarball_is_refused(tmp_path):
    root = _root(tmp_path)
    name = _good(root)
    _pin(root, "Other-1.0.tar.gz", b"other")
    p = _unpack(root, name, tmp_path / "out" / "Good")
    assert (p.returncode, p.stderr) == (1, f"FAILED: {name} is not pinned in downloads/SHA256SUMS\n")
    assert not (tmp_path / "out").exists()


def test_a_name_pinned_twice_is_refused(tmp_path):
    root = _root(tmp_path)
    name = _good(root)
    _pin(root, name)
    _pin(root, name)
    p = _unpack(root, name, tmp_path / "out" / "Good")
    assert (p.returncode, p.stderr) == (1, f"FAILED: {name} is not pinned in downloads/SHA256SUMS\n")


@pytest.mark.parametrize(
    "members",
    [
        {"A-1/x.pm": b"1;", "B-1/y.pm": b"1;"},
        {"A-1/x.pm": b"1;", ".hidden": b""},
        {"loose.pm": b"1;"},
    ],
    ids=["two folders", "a folder and a hidden file", "one file"],
)
def test_a_tarball_that_is_not_one_top_folder_is_refused(tmp_path, members):
    root = _root(tmp_path)
    name = "Odd-1.tar.gz"
    _tarball(root / "downloads" / name, members)
    _pin(root, name)
    out = tmp_path / "out"
    out.mkdir()
    p = _unpack(root, name, out / "Odd")
    assert (p.returncode, p.stderr) == (1, f"FAILED: {name} does not unpack to one top folder\n")
    assert os.listdir(out) == []


def test_a_folder_that_exists_is_not_replaced(tmp_path):
    root = _root(tmp_path)
    name = _good(root)
    _pin(root, name)
    dest = tmp_path / "out" / "Good"
    dest.mkdir(parents=True)
    (dest / "mine").write_text("kept")
    p = _unpack(root, name, dest)
    assert (p.returncode, p.stderr) == (1, f"FAILED: {dest} already exists; {name} would replace it\n")
    assert os.listdir(dest) == ["mine"]


def _listed(recipe: str) -> list[str]:
    lines = (ROOT / "recipes" / recipe).read_text().splitlines()
    return [ln.split()[0] for ln in lines if ln.strip() and not ln.startswith("#")]


def test_every_listed_tarball_is_pinned_once():
    pins = [ln.split()[1] for ln in (DOWNLOADS / "SHA256SUMS").read_text().splitlines() if ln]
    listed = _listed("cpan-xs") + _listed("cpan-pure")
    assert sorted(listed) == sorted(
        [
            "Cpanel-JSON-XS-4.83.tar.gz",
            "Text-CSV_XS-1.65.tgz",
            "JSON-MaybeXS-1.004008.tar.gz",
            "Text-CSV-2.06.tar.gz",
            "Algorithm-Diff-1.201.tar.gz",
        ]
    )
    assert all(pins.count(t) == 1 for t in listed), (listed, pins)


def _checkout_with_a_tampered(tmp_path: Path, tampered: str) -> Path:
    """A copy of the checkout's scripts, recipes and pins; every download is the real one but
    `tampered`, which has one byte changed. The build tools each step looks for first are
    empty stand-ins, so a step that got past its pins would fail at once."""
    for name in ("perl-5.42.3.tar.xz", tampered):
        if not (DOWNLOADS / name).is_file():
            pytest.skip(f"downloads/{name} is missing; run scripts/00_download.sh")
    root = tmp_path / "checkout"
    shutil.copytree(SCRIPTS, root / "scripts")
    shutil.copytree(ROOT / "recipes", root / "recipes")
    (root / "tests" / "build").mkdir(parents=True)
    shutil.copy(ROOT / "tests" / "build" / "memchr-test.c", root / "tests" / "build")
    (root / "downloads").mkdir()
    shutil.copy(DOWNLOADS / "SHA256SUMS", root / "downloads")
    for line in (DOWNLOADS / "SHA256SUMS").read_text().splitlines():
        name = line.split()[1]
        if (DOWNLOADS / name).is_file() and name != tampered:
            (root / "downloads" / name).symlink_to(DOWNLOADS / name)
    data = bytearray((DOWNLOADS / tampered).read_bytes())
    data[len(data) // 2] ^= 0x01
    (root / "downloads" / tampered).write_bytes(bytes(data))
    build = root / "build"
    for tool in (
        "tools/wasi-sdk-34.0-x86_64-linux/bin/clang",
        "native/miniperl",
        "native/generate_uudmap",
        "artifacts/perl.wasm",
        ".target.stamp",
    ):
        (build / tool).parent.mkdir(parents=True, exist_ok=True)
        (build / tool).write_text("")
        (build / tool).chmod(0o755)
    (build / "target").mkdir()
    (build / "target" / "kept").write_text("")
    return root


@pytest.mark.parametrize(
    ("step", "tampered"),
    [
        ("10_native.sh", "Text-CSV-2.06.tar.gz"),
        ("20_target.sh", "Cpanel-JSON-XS-4.83.tar.gz"),
        ("20_target.sh", "Text-CSV_XS-1.65.tgz"),
        ("30_library.sh", "Algorithm-Diff-1.201.tar.gz"),
    ],
)
def test_a_tampered_tarball_fails_the_build_step(tmp_path, step, tampered):
    root = _checkout_with_a_tampered(tmp_path, tampered)
    before = sorted(str(p.relative_to(root / "build")) for p in (root / "build").rglob("*"))
    p = subprocess.run(
        ["sh", str(root / "scripts" / step)], capture_output=True, text=True, check=False
    )
    assert p.returncode == 1, p
    assert p.stdout == ""
    assert p.stderr == f"FAILED: {tampered} does not match its SHA-256 in downloads/SHA256SUMS\n"
    # Nothing in the build tree was removed or added, beyond the folder the step logs to.
    after = sorted(
        str(q.relative_to(root / "build"))
        for q in (root / "build").rglob("*")
        if q != root / "build" / "logs" and q.parent != root / "build" / "logs"
    )
    assert after == before


def _xs_modules() -> list[str]:
    lines = (ROOT / "recipes" / "cpan-xs").read_text().splitlines()
    return [ln.split()[1].replace("-", "/") for ln in lines if ln.strip() and not ln.startswith("#")]


def test_the_cpan_extensions_are_configured_after_the_unicode_tables():
    log = ROOT / "build" / "logs" / "20_target.log"
    if not log.is_file():
        pytest.skip("build/logs/20_target.log is missing; run scripts/20_target.sh")
    lines = log.read_text(errors="replace").splitlines()
    # mktables writes its list of what it made last, once it has made every table.
    made = [i for i, ln in enumerate(lines) if ln == "Updating 'mktables.lst'"]
    assert made, "the log shows no run of mktables that made the tables"
    for module in _xs_modules():
        archive = f"lib/auto/{module}/{module.rsplit('/', 1)[-1]}.a"
        built = [i for i, ln in enumerate(lines) if f"make_ext.pl {archive} " in ln]
        assert built, (module, "is never built in the log")
        assert built[0] > made[0], (module, built[0], made[0])
