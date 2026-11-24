"""Shared fixtures: the stand-in guest built from source with the pinned wasi-sdk, a scratch
artifact set around it, the real perl.wasm when one is built, and a private module cache for
every check, so no check writes the user's own cache."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _set_helpers import REPO, write_set

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BUILT = REPO / "build" / "fixtures"
SJLJ = ["-mllvm", "-wasm-enable-sjlj", "-mllvm", "-wasm-use-legacy-eh=false"]
STACK = ["-Wl,-z,stack-size=8388608"]  # perl.wasm's linear-memory stack
WRAP = ["-Wl,--wrap=_Exit"]  # every exit records its status, as perl.wasm's does


def _sdk() -> Path | None:
    """The pinned wasi-sdk: $WASI_SDK_PATH, else one unpacked under downloads/ (this checkout's,
    or the shared one beside the worktrees)."""
    env = os.environ.get("WASI_SDK_PATH")
    candidates = [Path(env)] if env else []
    for base in (REPO / "downloads", REPO.parent.parent / "wasi-perl-downloads"):
        candidates += sorted(base.glob("wasi-sdk-34.0-*"))
    for c in candidates:
        if (c / "bin" / "clang").exists():
            return c
    return None


def _compile(source: Path, out: Path, extra: tuple[str, ...] = tuple(WRAP)) -> Path | None:
    if out.exists() and out.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return out
    sdk = _sdk()
    if sdk is None:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(sdk / "bin" / "clang"),
        "--target=wasm32-wasip1",
        f"--sysroot={sdk / 'share' / 'wasi-sysroot'}",
        "-O2",
        *SJLJ,
        *STACK,
        *extra,
        str(source),
        "-lsetjmp",
        "-o",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


@pytest.fixture(scope="session")
def standin_wasm() -> Path:
    built = _compile(FIXTURES / "standin.c", BUILT / "standin.wasm")
    if built is None:
        pytest.skip("no wasi-sdk 34 found ($WASI_SDK_PATH or downloads/): the stand-in guest")
    return built


@pytest.fixture(scope="session")
def standin_without_status() -> Path:
    """The stand-in built without the exit-status export, as a perl.wasm from before it."""
    built = _compile(
        FIXTURES / "standin.c", BUILT / "standin-no-status.wasm", ("-DNO_EXIT_STATUS",)
    )
    if built is None:
        pytest.skip("no wasi-sdk 34 found ($WASI_SDK_PATH or downloads/): the stand-in guest")
    return built


@pytest.fixture
def standin_dir(tmp_path_factory: pytest.TempPathFactory, standin_wasm: Path) -> Path:
    """A fresh, sealed artifact set holding the stand-in, in a folder of its own, apart from the
    check's `tmp_path`, which checks grant."""
    return write_set(tmp_path_factory.mktemp("set") / "artifacts", standin_wasm)


@pytest.fixture
def standin(standin_dir: Path):
    """The stand-in set as an `Artifacts`, passed to `run(artifacts=...)`."""
    from wasi_perl import Artifacts

    return Artifacts.at(standin_dir)


def _perl_dir() -> Path | None:
    env = os.environ.get("WASI_PERL_ARTIFACTS")
    d = Path(env) if env else REPO / "build" / "artifacts"
    return d if (d / "perl.wasm").is_file() else None


@pytest.fixture
def perl():
    """The real perl.wasm's set, as given (unchecked), or a skip naming the build."""
    d = _perl_dir()
    if d is None:
        pytest.skip("no perl.wasm: set $WASI_PERL_ARTIFACTS or build it with scripts/build.sh")
    from wasi_perl import Artifacts

    return Artifacts.at(d)


@pytest.fixture(autouse=True)
def private_cache(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Every check's default cache is one of the session's, never the user's."""
    folder = tmp_path_factory.getbasetemp() / "cache"
    monkeypatch.setenv("WASI_PERL_CACHE", str(folder))
    return folder
