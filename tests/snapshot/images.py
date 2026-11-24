"""What the preload image's checks share: the set they run, made once per session, its images,
and a call from an image or from perl.wasm under the same arguments.

The set is the one `$WASI_PERL_ARTIFACTS` names, else the build's, as the shared fixtures find
it; it must hold perl-reactor.wasm. Images are made with no disk cache, so the user's cache is
never written; the compiled image module stays in the process, which compiles it once."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from wasi_perl import Artifacts, Completed, run

REPO = Path(__file__).resolve().parents[2]
SAME_RUN = ("exit_code", "stdout", "stderr", "trapped")


def set_dir() -> Path | None:
    env = os.environ.get("WASI_PERL_ARTIFACTS")
    d = Path(env) if env else REPO / "build" / "artifacts"
    return d if (d / "perl.wasm").is_file() else None


def image_set() -> Artifacts:
    """The set, with its reactor, or a skip naming the build when there is no set at all."""
    d = set_dir()
    if d is None:
        pytest.skip("no perl.wasm: set $WASI_PERL_ARTIFACTS or build it with scripts/build.sh")
    found = Artifacts.at(d)
    assert found.reactor is not None, f"{d} has no perl-reactor.wasm: scripts/40_link.sh builds it"
    return found


def call(argv: list[str], image: Any = False, **kw: Any) -> Completed:
    """One call of the set: from `image` when it is a Snapshot, else from perl.wasm."""
    kw.setdefault("artifacts", image_set())
    kw.setdefault("cache_dir", False)
    return run(argv, snapshot=image, **kw)


def shown(done: Completed) -> tuple[Any, ...]:
    return tuple(getattr(done, f) for f in SAME_RUN)
