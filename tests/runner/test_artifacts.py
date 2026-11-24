"""The artifact set: found by the variable, a checkout or the wheel, and checked against its
SHA256SUMS before use: perl.wasm and MANIFEST on every call, the library tree once."""

from __future__ import annotations

import pytest

from wasi_perl import ArtifactsMissing, _artifacts, artifacts, run


@pytest.fixture
def found(standin_dir, monkeypatch):
    monkeypatch.setenv("WASI_PERL_ARTIFACTS", str(standin_dir))
    _artifacts.forget()
    return standin_dir


def test_the_variable_names_the_set(found):
    a = artifacts()
    assert a.perl == found / "perl.wasm"
    assert a.lib == found / "lib" / "perl5"
    assert a.dev == found / "dev"
    assert a.manifest == found / "MANIFEST"
    assert a.sums == found / "SHA256SUMS"
    assert run(["hello"]).stdout == b"hello\n"


def test_no_set_is_artifacts_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("WASI_PERL_ARTIFACTS", str(tmp_path))
    with pytest.raises(ArtifactsMissing, match="perl.wasm"):
        run(["hello"])


@pytest.mark.parametrize(
    ("damage", "words"),
    [
        ("perl.wasm", "perl.wasm does not match"),
        ("MANIFEST", "MANIFEST does not match"),
        ("lib/perl5/5.42/strict.pm", "strict.pm does not match"),
        ("lib/perl5/5.42/planted.pm", "planted.pm is not listed"),
        ("dev/tty", "is not listed"),
        ("SHA256SUMS", "SHA256SUMS"),
    ],
)
def test_a_changed_set_is_refused(found, damage, words):
    path = found / damage
    if damage == "SHA256SUMS":
        path.unlink()
    elif path.exists():
        with open(path, "ab") as f:
            f.write(b"# changed\n")
    else:
        path.write_text("1;\n")
    with pytest.raises(ArtifactsMissing, match=words):
        run(["hello"])


def test_dev_null_must_be_empty(found):
    (found / "dev" / "null").write_bytes(b"x")
    import subprocess
    import sys

    from conftest import REPO

    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "write_sums.py"), str(found)], check=True
    )
    with pytest.raises(ArtifactsMissing, match="null"):
        run(["hello"])


def test_a_set_given_is_used_as_given(standin, standin_dir):
    (standin_dir / "SHA256SUMS").unlink()
    assert run(["hello"], artifacts=standin).stdout == b"hello\n"
