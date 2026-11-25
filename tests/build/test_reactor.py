"""Checks on perl-reactor.wasm, the second link of the same objects that a preload image starts
from: its form, its place in the set, the version pin of its entry, and that the build makes
no image of its own.

The form is the one the runner's image-module writer takes (`wasi_perl._image_module`), and the
check runs that writer's own reading of it, so the two cannot drift apart.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from _build_helpers import ARTIFACTS, BUILD, ROOT
from wasi_perl import _image_module

REACTOR = ARTIFACTS / "perl-reactor.wasm"
LINKED_REACTOR = BUILD / "link" / "perl-reactor.wasm"
SDK = BUILD / "tools" / "wasi-sdk-34.0-x86_64-linux"
TARGET = BUILD / "target"
SNAPSHOT_MAGIC = b"wasi-perl snapshot\n"


@pytest.fixture(scope="session")
def reactor(perl_wasm: Path) -> Path:
    assert REACTOR.is_file(), f"{REACTOR} is missing: scripts/40_link.sh builds it"
    return REACTOR


# 1. The form.


def test_the_reactor_has_the_form_an_image_module_needs(reactor: Path):
    form = _image_module.check_form(reactor.read_bytes())
    assert sorted(form.exports) == sorted(_image_module.EXPORTS)
    assert len(form.exports) == 6
    assert form.memory_max is None  # wasm-ld writes no maximum: the store's limit is the bound
    assert form.stack_init > 0


def test_an_image_module_from_the_reactor_compiles(reactor: Path):
    import wasmtime as wt

    data = reactor.read_bytes()
    form = _image_module.check_form(data)
    image = bytes(form.memory_min * _image_module.PAGE)  # the smallest image the memory takes
    written, stats = _image_module.write(data, image)
    config = wt.Config()
    config.wasm_exceptions = True
    # A data-count section is dropped; a code section that indexed a segment would not compile.
    wt.Module(wt.Engine(config), written)
    assert 12 not in _image_module.section_ids(written)
    assert stats["segments"] == 0


def test_the_reactor_holds_no_path_of_the_build_host(reactor: Path):
    data = reactor.read_bytes()
    for root in {str(ROOT), str(ROOT.resolve())}:
        assert root.encode() not in data
    assert b"/build/target/" not in data


def test_the_linked_reactor_is_the_pass_s_input(reactor: Path):
    # The shipped reactor is wasm-opt's output over wasm-ld's, as perl.wasm is.
    assert LINKED_REACTOR.is_file(), f"{LINKED_REACTOR} is missing: scripts/40_link.sh builds it"
    assert LINKED_REACTOR.read_bytes() != reactor.read_bytes()
    assert b"binaryen" not in LINKED_REACTOR.read_bytes()


# 2. Its place in the set.


def _listed(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        digest, sep, name = line.partition("  ")
        if sep:
            out[name] = digest
    return out


def test_sha256sums_lists_the_reactor_and_manifest_does_not(reactor: Path):
    import hashlib

    sums = _listed(ARTIFACTS / "SHA256SUMS")
    assert sums["perl-reactor.wasm"] == hashlib.sha256(reactor.read_bytes()).hexdigest()
    assert "perl-reactor.wasm" not in _listed(ARTIFACTS / "MANIFEST")
    assert all(name.startswith(("lib/", "dev/")) for name in _listed(ARTIFACTS / "MANIFEST"))


# 3. The entry's pin to Perl 5.42.3.


def test_the_entry_does_not_compile_for_another_perl(perl_wasm: Path, tmp_path: Path):
    assert (SDK / "bin" / "clang").is_file(), f"{SDK} is missing: scripts/00_download.sh"
    assert (TARGET / "perlmain.c").is_file(), "build/target is missing: scripts/20_target.sh"
    real = (TARGET / "patchlevel.h").read_text()
    standin = re.sub(r"(#define PERL_SUBVERSION\s+)3\b", r"\g<1>4", real)
    assert standin != real
    (tmp_path / "patchlevel.h").write_text(standin)
    # The command 20_target.sh compiles the entry with: the one Perl gives perlmain.o.
    cc = subprocess.run(
        ["sh", "cflags", "optimize='-O2'", "perlmain.o"],
        cwd=TARGET,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    base = [*cc, "-fsyntax-only", f"-I{TARGET}"]
    source = str(ROOT / "recipes" / "wasi-reactor.c")
    control = subprocess.run([*base, source], capture_output=True, text=True)
    assert control.returncode == 0, control.stderr[-2000:]
    # The stand-in comes first, so the tree's own header is skipped by its include guard.
    given = subprocess.run(
        [*base, "-include", str(tmp_path / "patchlevel.h"), source],
        capture_output=True,
        text=True,
    )
    assert given.returncode != 0
    assert "read it again for this Perl" in given.stderr, given.stderr[-2000:]


# 4. No image is made by the build.


def test_the_set_holds_no_image_and_no_script_runs_wizer(perl_wasm: Path):
    for p in ARTIFACTS.rglob("*"):
        if p.is_file():
            with open(p, "rb") as f:
                assert f.read(len(SNAPSHOT_MAGIC)) != SNAPSHOT_MAGIC, p
    for folder in (ROOT / "scripts", ROOT / "recipes"):
        for p in folder.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                assert b"wizer" not in p.read_bytes(), p


# The wheel carries the reactor: the copy into the package refuses a set without one.


def _assemble():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "assemble_artifacts", ROOT / "scripts" / "assemble_artifacts.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_package_copy_needs_the_reactor(tmp_path: Path):
    from standin_sets import write_set

    assemble = _assemble()
    wasm = tmp_path / "x.wasm"
    wasm.write_bytes(b"\0asm\x01\0\0\0")
    without = write_set(tmp_path / "without", wasm)
    with pytest.raises(assemble.Refused, match="perl-reactor.wasm"):
        assemble.verify(without)
    (tmp_path / "with").mkdir()
    (tmp_path / "with" / "perl-reactor.wasm").write_bytes(b"\0asm\x01\0\0\0")
    with_reactor = write_set(tmp_path / "with", wasm)
    assert assemble.verify(with_reactor) == 5  # perl.wasm, the reactor, MANIFEST, lib, dev
    assert "perl-reactor.wasm" in assemble.SET_PARTS
