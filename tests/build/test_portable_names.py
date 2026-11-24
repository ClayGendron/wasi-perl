"""Every path of the artifact set must install on every system the wheel supports. Windows
reserves some names and characters, and Windows and macOS both fold case, so two paths that
differ only by case would land on one file there (Pod/ and pod/ did, once)."""

from __future__ import annotations

import importlib.util

import pytest

from _build_helpers import ARTIFACTS, ROOT

spec = importlib.util.spec_from_file_location(
    "assemble_artifacts", ROOT / "scripts" / "assemble_artifacts.py"
)
assemble = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assemble)


@pytest.mark.parametrize(
    "paths",
    [
        ["lib/perl5/5.42/Pod/Usage.pm", "lib/perl5/5.42/pod/perl.pod"],
        ["lib/a.pm", "lib/A.pm"],
        ["lib/nul.pm"],
        ["lib/CON"],
        ["lib/com1.txt"],
        ["lib/trailing."],
        ["lib/trailing "],
        ["lib/what?.pm"],
        ["lib/a:b.pm"],
    ],
)
def test_unportable_paths_are_named(paths):
    assert assemble.unportable(paths)


def test_ordinary_paths_pass():
    paths = ["perl.wasm", "lib/perl5/5.42/Pod/Usage.pm", "lib/perl5/5.42/strict.pm", "dev/null"]
    assert assemble.unportable(paths) == []


def test_the_built_set_installs_everywhere():
    if not (ARTIFACTS / "perl.wasm").is_file():
        pytest.skip(f"{ARTIFACTS / 'perl.wasm'} is missing; run scripts/build.sh")
    paths = [p.relative_to(ARTIFACTS).as_posix() for p in ARTIFACTS.rglob("*") if p.is_file()]
    assert assemble.unportable(paths) == []
