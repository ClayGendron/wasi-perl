"""Shared fixtures: the stand-in guest built from source with the pinned wasi-sdk, a scratch
artifact set around it, the real perl.wasm when one is built, and a private module cache for
every check, so no check writes the user's own cache."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from standin_sets import write_set

REPO = Path(__file__).resolve().parent.parent
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


# WASI_PERL_TESTS_FROM_IMAGE=1 runs the suite with every call that can start from the package
# preload image started there: a call that leaves `snapshot` at its default, on the set the
# image was made from, with room for the image in its memory limit. So the walls the escape and
# mount checks hold are held on the image's path too. The one accepted difference an image makes
# that these checks can see is the hash seed every call from one image shares.
FROM_IMAGE = os.environ.get("WASI_PERL_TESTS_FROM_IMAGE") == "1"
SHARED_SEED = {"tests/escapes/test_escapes.py::test_every_call_has_its_own_seeds"}
_from_image: dict = {"image": None, "started": 0}


def _route_to_the_image() -> None:
    from wasi_perl import Artifacts, make_package_snapshot, runner

    folder = _perl_dir()
    if folder is None:
        return
    found = Artifacts.at(folder)
    real = runner._run

    def routed(argv, grants, mounts, env, stdin, snapshot, cwd, fuel, deadline_s, memory_bytes,
               output_bytes, cache_dir, artifacts, max_file_calls, *, making=False):  # fmt: skip
        if not making and snapshot is True and found.reactor is not None:
            ours = artifacts is None or (
                artifacts.reactor is not None
                and Path(artifacts.reactor).resolve() == Path(found.reactor).resolve()
            )
            if _from_image["image"] is None and ours:
                _from_image["image"] = make_package_snapshot(artifacts=found, cache_dir=False)
            image = _from_image["image"]
            room = not isinstance(memory_bytes, int) or memory_bytes >= image.origin.image_bytes
            if ours and room:
                snapshot = image
        done = real(argv, grants, mounts, env, stdin, snapshot, cwd, fuel, deadline_s,
                    memory_bytes, output_bytes, cache_dir, artifacts, max_file_calls,
                    making=making)  # fmt: skip
        if not making and getattr(done, "image_sha256", None) is not None:
            _from_image["started"] += 1
        return done

    runner._run = routed


def pytest_configure(config: pytest.Config) -> None:
    if FROM_IMAGE:
        _route_to_the_image()


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    if not FROM_IMAGE:
        return
    for item in items:
        if item.nodeid.split("[")[0] in SHARED_SEED:
            item.add_marker(
                pytest.mark.xfail(
                    reason="from an image every call shares its hash seed", strict=True
                )
            )


def pytest_terminal_summary(terminalreporter) -> None:
    if FROM_IMAGE:
        terminalreporter.write_line(
            f"calls started from the package image: {_from_image['started']}"
        )
