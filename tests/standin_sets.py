"""Scratch artifact sets for the runner's checks, importable by name from any test folder; the
name `conftest` is not, since every test folder has one and the first imported wins."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def write_set(folder: Path, wasm: Path) -> Path:
    """An artifact set around `wasm`: perl.wasm, a one-module library, dev/null, MANIFEST and
    SHA256SUMS, sealed by the same tool the build uses."""
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(wasm, folder / "perl.wasm")
    lib = folder / "lib" / "perl5" / "5.42"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "strict.pm").write_text("package strict; 1;\n")
    (folder / "dev").mkdir(exist_ok=True)
    (folder / "dev" / "null").write_bytes(b"")
    (folder / "MANIFEST").write_text("perl 5.42.3 (stand-in)\n")
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "write_sums.py"), str(folder)], check=True
    )
    return folder
