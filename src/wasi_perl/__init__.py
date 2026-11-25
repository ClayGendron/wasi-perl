"""wasi_perl: Perl 5.42 compiled to WebAssembly as one static binary, a sandbox for the Perl,
above all the one-liners, that agents write.

One call runs one program in a fresh, sealed guest::

    from wasi_perl import Grant, run

    done = run(["-ne", "print if /error/", "/logs/app.log"], grants=[Grant("logs", "/logs")])
    done.exit_code, done.stdout

A `Mount` serves the guest's files from the embedder's own code instead of a host folder. A
`Snapshot`, made once with `make_snapshot` or `make_package_snapshot`, starts later calls with
their modules already loaded.

The public API is this module's `__all__`, the `wasi-perl` command, and the environment
variables `WASI_PERL_ARTIFACTS` and `WASI_PERL_CACHE`. Everything else is internal and may
change in any release. Importing this module never loads wasmtime-py; `run` does.
"""

from __future__ import annotations

from wasi_perl._artifacts import Artifacts, ArtifactsMissing, artifacts
from wasi_perl._limits import TooManyCalls
from wasi_perl._snapshot import Snapshot, SnapshotFailed, SnapshotMismatch, SnapshotOrigin
from wasi_perl._snapshot_make import make_package_snapshot, make_snapshot
from wasi_perl.mounts import (
    Errno,
    FileFault,
    FileProvider,
    FileRecord,
    Mount,
    RefusedWrite,
    Stat,
    WritableFileProvider,
)
from wasi_perl.runner import Completed, Defaults, Grant, defaults, run, set_defaults

__version__ = "0.1.0"

__all__ = [
    "Artifacts",
    "ArtifactsMissing",
    "Completed",
    "Defaults",
    "Errno",
    "FileFault",
    "FileProvider",
    "FileRecord",
    "Grant",
    "Mount",
    "RefusedWrite",
    "Snapshot",
    "SnapshotFailed",
    "SnapshotMismatch",
    "SnapshotOrigin",
    "Stat",
    "TooManyCalls",
    "WritableFileProvider",
    "__version__",
    "artifacts",
    "defaults",
    "make_package_snapshot",
    "make_snapshot",
    "run",
    "set_defaults",
]
