"""wasi_perl: Perl 5.42 compiled to WebAssembly as one static binary, a sandbox for the Perl,
above all the one-liners, that agents write.

One call runs one program in a fresh, sealed guest::

    from wasi_perl import Grant, run

    done = run(["-ne", "print if /error/", "/logs/app.log"], grants=[Grant("logs", "/logs")])
    done.exit_code, done.stdout

The public API is this module's `__all__`, the `wasi-perl` command, and the environment
variables `WASI_PERL_ARTIFACTS` and `WASI_PERL_CACHE`. Everything else is internal and may
change in any release. Importing this module never loads wasmtime-py; `run` does.
"""

from __future__ import annotations

from wasi_perl._artifacts import Artifacts, ArtifactsMissing, artifacts
from wasi_perl.runner import Completed, Defaults, Grant, defaults, run, set_defaults

__version__ = "0.1.0"

__all__ = [
    "Artifacts",
    "ArtifactsMissing",
    "Completed",
    "Defaults",
    "Grant",
    "__version__",
    "artifacts",
    "defaults",
    "run",
    "set_defaults",
]
