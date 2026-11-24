"""Where the checks on the built perl.wasm find the build, by a name of its own: a check that
wrote `from conftest import ...` would get whichever folder's conftest.py was loaded first."""

from __future__ import annotations

import os
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
