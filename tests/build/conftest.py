"""Fixtures for the checks on the built perl.wasm.

The guest runs under wasmtime-py 51, the engine the wheel uses, because its WASI grants can be
read-only and the wasmtime 51 command line's cannot. The wasmtime command line is used where
its own behaviour is the fact being checked. A check whose artifact is missing is skipped with
the name of the script that builds it.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build"
ARTIFACTS = Path(os.environ.get("WASI_PERL_ARTIFACTS", BUILD / "artifacts"))
PERL_WASM = ARTIFACTS / "perl.wasm"
LIBRARY = ARTIFACTS / "lib" / "perl5"
DEV = ARTIFACTS / "dev"
WASMTIME_CLI = BUILD / "tools" / "wasmtime-v51.0.0-x86_64-linux" / "wasmtime"
NATIVE_PERL = BUILD / "native-install" / "bin" / "perl"

# The guest's view: the library and the stand-in /dev, both read-only.
LIBRARY_GUEST = "/usr/local/lib/perl5"
DEV_GUEST = "/dev"


@dataclass
class Result:
    """How one run ended.

    code is the exit status the host reported, None when it reported an error instead.
    status is what the module's wasi_perl_exit_status export returned after the run: the status
    the program exited with, -1 if it never exited, None if the export is missing.
    """

    code: int | None
    stdout: bytes
    stderr: bytes
    error: str = ""
    status: int | None = None


def _need(path: Path, script: str) -> None:
    if not path.exists():
        pytest.skip(
            f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path} is missing; run {script}"
        )


@pytest.fixture(scope="session")
def perl_wasm() -> Path:
    _need(PERL_WASM, "scripts/build.sh")
    _need(LIBRARY / "5.42", "scripts/build.sh")
    _need(DEV / "null", "scripts/build.sh")
    return PERL_WASM


@pytest.fixture(scope="session")
def wasmtime_cli() -> Path:
    _need(WASMTIME_CLI, "scripts/00_download.sh")
    return WASMTIME_CLI


@pytest.fixture(scope="session")
def native_perl() -> Path:
    _need(NATIVE_PERL, "scripts/10_native.sh")
    return NATIVE_PERL


class Guest:
    """Runs perl.wasm once per call, in a fresh store, with only the grants given."""

    def __init__(self, module_path: Path) -> None:
        import wasmtime

        self.wasmtime = wasmtime
        config = wasmtime.Config()
        config.wasm_exceptions = True
        self.engine = wasmtime.Engine(config)
        self.module = wasmtime.Module.from_file(self.engine, str(module_path))

    def run(
        self,
        *args: str,
        env: dict[str, str] | None = None,
        grants: list[tuple[Path, str, bool]] | None = None,
        tmp: Path,
    ) -> Result:
        wt = self.wasmtime
        if grants is None:
            grants = default_grants()
        out, err = tmp / "guest.stdout", tmp / "guest.stderr"
        wasi = wt.WasiConfig()
        wasi.argv = ["perl", *args]
        wasi.env = sorted((env or {}).items())
        wasi.stdout_file = str(out)
        wasi.stderr_file = str(err)
        for host, guest, writable in grants:
            wasi.preopen_dir(str(host), guest, writable)
        store = wt.Store(self.engine)
        store.set_wasi(wasi)
        linker = wt.Linker(self.engine)
        linker.define_wasi()
        code: int | None = 0
        error = ""
        instance = linker.instantiate(store, self.module)
        exports = instance.exports(store)
        try:
            exports["_start"](store)
        except wt.ExitTrap as e:
            code = e.code
        except (wt.Trap, wt.WasmtimeError) as e:
            code, error = None, str(e)
        status_fn = (
            exports.get("wasi_perl_exit_status") if hasattr(exports, "get") else None
        )
        status = status_fn(store) if status_fn is not None else None
        return Result(code, out.read_bytes(), err.read_bytes(), error, status)


def default_grants() -> list[tuple[Path, str, bool]]:
    return [(LIBRARY, LIBRARY_GUEST, False), (DEV, DEV_GUEST, False)]


@pytest.fixture(scope="session")
def guest_engine(perl_wasm: Path) -> Guest:
    pytest.importorskip(
        "wasmtime", reason="wasmtime-py 51 is needed: uv run --with wasmtime==51.0.0"
    )
    return Guest(perl_wasm)


@pytest.fixture
def guest(guest_engine: Guest, tmp_path: Path):
    """guest(*args, env=..., grants=...) runs perl.wasm and returns a Result."""

    def run(*args: str, env: dict[str, str] | None = None, grants=None) -> Result:
        d = tmp_path / f"run{len(list(tmp_path.iterdir()))}"
        d.mkdir()
        return guest_engine.run(*args, env=env, grants=grants, tmp=d)

    return run


@pytest.fixture
def native(native_perl: Path):
    """native(*args, env=...) runs the oracle perl 5.42.3 built from the same tarball."""

    def run(*args: str, env: dict[str, str] | None = None) -> Result:
        p = subprocess.run(
            [str(native_perl), *args],
            env=env or {},
            capture_output=True,
            cwd="/",
            check=False,
        )
        return Result(p.returncode, p.stdout, p.stderr)

    return run
