"""What the checks on the built perl.wasm import by name: where the build is, how a run ended,
and the default grants. It is not in conftest.py because every conftest.py is a module named
`conftest`, and `from conftest import ...` gets whichever folder's was loaded first."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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
    module: Path | None = None  # the module that ran, when a Guest ran it


def _leb(b: bytes, i: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        byte = b[i]
        i += 1
        n |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return n, i


def function_import(wasm: bytes, module: str, name: str) -> int:
    """The function index of the import module.name: its place among the function imports."""
    i, index = 8, 0
    while i < len(wasm):
        section, i = wasm[i], i + 1
        size, i = _leb(wasm, i)
        if section == 2:
            j = i
            count, j = _leb(wasm, j)
            for _ in range(count):
                found = []
                for _ in range(2):
                    n, j = _leb(wasm, j)
                    found.append(wasm[j : j + n].decode())
                    j += n
                kind, j = wasm[j], j + 1
                assert kind == 0, f"{found}: only function imports are read here"
                _, j = _leb(wasm, j)
                if found == [module, name]:
                    return index
                index += 1
        i += size
    raise AssertionError(f"no import {module}.{name}")


def assert_exit_refused(r: Result, status: int) -> None:
    """The program exited with a status of 126 or more, which wasmtime refuses to pass on.

    The error must be that refusal and nothing else: the innermost frame stands at a call of the
    WASI exit import, so no trap happened on the way, and the export kept the status. The frame
    is read by its offset in the module, whose byte there must be a call of proc_exit's function
    index, not by its name: the shipped module has no name section.
    """
    assert r.code is None, r
    assert r.status == status, r
    frames = [ln.split() for ln in r.error.splitlines() if ln.strip()[:1].isdigit()]
    assert frames and frames[0][1].startswith("0x"), r.error
    wasm = (r.module or PERL_WASM).read_bytes()
    at = int(frames[0][1], 16)
    exit_call = function_import(wasm, "wasi_snapshot_preview1", "proc_exit")
    assert wasm[at] == 0x10 and _leb(wasm, at + 1)[0] == exit_call, r.error  # 0x10 is call
    cause = r.error.rsplit("Caused by:", 1)[-1].strip()
    assert cause == "exit with invalid exit status outside of [0..126)", r.error


def default_grants() -> list[tuple[Path, str, bool]]:
    return [(LIBRARY, LIBRARY_GUEST, False), (DEV, DEV_GUEST, False)]


class Guest:
    """Runs perl.wasm once per call, in a fresh store, with only the grants given."""

    def __init__(self, module_path: Path) -> None:
        import wasmtime

        self.wasmtime = wasmtime
        self.path = module_path
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
        return Result(code, out.read_bytes(), err.read_bytes(), error, status, self.path)
