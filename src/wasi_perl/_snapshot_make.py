"""Making a preload image: `make_snapshot`, which runs any code once and keeps the guest's memory,
and `make_package_snapshot`, which loads a list of modules, by default `PACKAGES`.

A making is one call of the set's perl-reactor.wasm, with the caller's arguments checked by
`run`'s rules, that enters the reactor's `prepare` export with the argv `perl -e <code>` instead
of running a program. Its image is read when the code ended with status 0, and the image module
is then written and compiled, so the install step pays the compile.

Adapted from wasi-python's `_snapshot_make.py` at f9bf678 (same owner, Apache-2.0): the same
functions, keywords, failures and name rules, trimmed to `run`'s keywords here, with Perl's
`require` in place of Python's `import` and the default list held here, not in `MANIFEST`."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from wasi_perl import _engines, _limits, _snapshot, runner
from wasi_perl._artifacts import Artifacts
from wasi_perl._grants import Grant
from wasi_perl._limits import UNSET
from wasi_perl._snapshot import Snapshot, SnapshotFailed

if TYPE_CHECKING:
    from wasi_perl.mounts import Mount

DEADLINE_S = 600.0  # a making is a setup step, run once
# The modules `make_package_snapshot()` loads, as (distribution, modules) pairs: each named by
# its CPAN distribution where it has one, else by its module. These eight are the ones the
# startup study measured. Each added makes every image larger, and every call from one pays more
# first-touch page faults; the list changes only with a drift run and a review.
PACKAGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("strict", ("strict",)),
    ("warnings", ("warnings",)),
    ("POSIX", ("POSIX",)),
    ("Scalar-List-Utils", ("List::Util",)),
    ("JSON-PP", ("JSON::PP",)),
    ("Data-Dumper", ("Data::Dumper",)),
    ("Storable", ("Storable",)),
    ("Encode", ("Encode",)),
)
MODULE = re.compile(r"[A-Za-z_]\w*(?:::\w+)*\Z", re.ASCII)  # a Perl package name


def make_snapshot(
    code: str,
    *,
    grants: Iterable[Grant] = (),
    mounts: Iterable[Mount] = (),
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    fuel: int | None = None,
    deadline_s: float | None = DEADLINE_S,
    memory_bytes: int | None = UNSET,
    output_bytes: int | None = UNSET,
    cache_dir: str | os.PathLike[str] | None | bool = None,
    artifacts: Artifacts | None = None,
    max_file_calls: int | None = UNSET,
) -> Snapshot:
    """Run your code once in a fresh guest and keep its memory, as a preload image.

    The code runs as a `-e` program in package `main`: it may load modules, read data from a
    grant or a mount and define subroutines and package variables. A call later given the
    snapshot runs its own program after it, as if both were one script: the code's
    subroutines, package variables, loaded modules and END blocks are in every call; its `my`
    variables are not. The keywords are `run`'s, checked by its rules before any work.

    The image is as trusted as its code: every call from it runs what the code left, a core
    function it redefined or a hook it set among them. So give it only code you would run in
    every call, never a program from the party the sandbox guards against. While the code runs
    its clocks do not move. Its entropy is real: the hash seed it draws is the image's, the same
    in every call, so keep the image and its saved file as private as a key. Pass no secret in
    `env` or in the code: both stay in the image.

    Args:
        code: The code to run, a str of at most 16 MiB as UTF-8, with no NUL.
        grants: As for `run`.
        mounts: As for `run`.
        env: As for `run`; it may not set `PERL_HASH_SEED`, `PERL_PERTURB_KEYS` or
            `PERL_JSON_PP_USE_B`. The origin keeps the names only, but the image holds the
            values.
        cwd: As for `run`; a call from the image starts in its own.
        fuel: A cap on the code's instructions, which the origin then records; `None`, none.
        deadline_s: Wall-clock seconds the making may run. Default: 600. `None`, no limit.
        memory_bytes: As for `run`; it caps the image's size.
        output_bytes: As for `run`.
        cache_dir: As for `run`; the image module is compiled into it.
        artifacts: As for `run`; it must have a perl-reactor.wasm, and a call from the image
            must run the same set.
        max_file_calls: As for `run`.

    Returns:
        The snapshot: its image and its origin.

    Raises:
        TypeError: An argument has the wrong type.
        ValueError: An argument is out of range, or `env` names a variable an image cannot
            honour; raised before any work.
        SnapshotFailed: The code exited with a status other than 0, died, trapped, ran past its
            deadline or its fuel, or left a file or folder open.
        ArtifactsMissing: There is no usable artifact set, or it has no perl-reactor.wasm.
        OSError: A host filesystem call failed while the folders were prepared.
    """
    text = _code(code)
    _refuse_frozen(env)
    made = runner._run(
        ["-e", text], grants, mounts, env, None, True, cwd, fuel, deadline_s, memory_bytes,
        output_bytes, cache_dir, artifacts, max_file_calls, making=True,
    )  # fmt: skip
    image = _made_or_failed(made, deadline_s, fuel)
    names = tuple(env.keys()) if env else ()
    origin = _snapshot.origin_of(
        text, image, made.completed.fuel_used, made.guest_paths, names, made.digests
    )
    snap = _snapshot._made(image, origin)
    _engines.count("snapshots_made")
    _compile(snap, made, fuel, cache_dir, artifacts)
    return snap


def _code(code: Any) -> str:
    text = _limits.text("code", code)
    if len(text.encode("utf-8")) > _snapshot.MAX_CODE_BYTES:
        raise ValueError(
            f"code must be at most {_snapshot.MAX_CODE_BYTES:,} bytes as UTF-8; give larger "
            "data through a grant"
        )
    return text


def _refuse_frozen(env: Any) -> None:
    """A making's environment may not set a variable the image would freeze for every call:
    the hash seed's two, which would make the seed known to whoever knows the environment, and
    JSON::PP's switch. `run`'s rules check the rest."""
    if env is None or not isinstance(env, Mapping):
        return
    for name in _snapshot.FROZEN_ENV:
        if name in env:
            raise ValueError(
                f"env: {name} cannot be set for a snapshot: the image would freeze it for every "
                "call; a call whose env sets it runs perl.wasm"
            )


def _made_or_failed(made: runner.Made, deadline_s: float | None, fuel: int | None) -> bytes:
    """The image, or the `SnapshotFailed` that says why there is none."""
    done = made.completed
    lines = [ln for ln in done.stderr.decode("utf-8", "replace").splitlines() if ln.strip()]
    last = f": {lines[-1].strip()}" if lines else ""
    if done.trapped is not None:
        if done.trapped == "deadline":
            why = (
                f"the snapshot's code ran past its deadline of {deadline_s:g} s; a snapshot's code "
                "sees a clock that does not move, so code that waits for time to pass never "
                "finishes"
            )
        elif done.trapped == "out of fuel":
            why = f"the snapshot's code ran out of fuel ({fuel:,} instructions); pass more fuel"
        else:
            why = f"the snapshot's code trapped: {done.trapped}{last}"
        raise SnapshotFailed(why, reason="trapped", completed=done)
    if done.exit_code != 0 or made.captured is None:
        why = f"the snapshot's code exited with status {done.exit_code}{last}"
        raise SnapshotFailed(why, reason="exit", completed=done)
    image, left = made.captured
    if left:
        raise SnapshotFailed(
            f"the snapshot's code left {left} file{'s' if left > 1 else ''} open; close what it "
            "opens (a module's __DATA__ handle counts), since a call started from the image "
            "cannot reopen it",
            reason="open file",
            completed=done,
        )
    return image


def _compile(
    snap: Snapshot, made: runner.Made, fuel: Any, cache_dir: Any, artifacts: Artifacts | None
) -> None:
    """The image module compiled now, for each flavour a call from it may run, so the install
    step pays the compile: the unmetered one, and the metered one when the making had fuel."""
    from wasi_perl import _artifacts, _cache

    found = artifacts if artifacts is not None else _artifacts.artifacts()
    assert found.reactor is not None
    cache = _cache.given(cache_dir)
    o = snap.origin
    for metered in (False, True) if fuel is not None else (False,):
        _engines.image_module(
            metered, found.reactor, made.digests[0], snap._image, o.image_sha256, cache.folder
        )


def make_package_snapshot(
    packages: Iterable[str] | None = None,
    *,
    grants: Iterable[Grant] = (),
    mounts: Iterable[Mount] = (),
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    fuel: int | None = None,
    deadline_s: float | None = DEADLINE_S,
    memory_bytes: int | None = UNSET,
    output_bytes: int | None = UNSET,
    cache_dir: str | os.PathLike[str] | None | bool = None,
    artifacts: Artifacts | None = None,
    max_file_calls: int | None = UNSET,
) -> Snapshot:
    """Load modules once and keep the guest's memory: by default the eight of `PACKAGES`.

    Its code is one `require Module;` line per module, so the modules are loaded and compiled
    but nothing is imported into `main`. A name given is a CPAN distribution's (`JSON-PP`,
    matched without regard to case), which loads that distribution's modules in `PACKAGES`; the
    first module of such a pair; or any other module name (`Text::Wrap`), loaded as given. The
    keywords are `make_snapshot`'s.

    Args:
        packages: Distribution or module names; `None` (the default) every pair of
            `PACKAGES`. A str alone is refused: pass `["POSIX"]`.
        grants: As for `make_snapshot`.
        mounts: As for `make_snapshot`.
        env: As for `make_snapshot`.
        cwd: As for `make_snapshot`.
        fuel: As for `make_snapshot`.
        deadline_s: As for `make_snapshot`. Default: 600.
        memory_bytes: As for `make_snapshot`.
        output_bytes: As for `make_snapshot`.
        cache_dir: As for `make_snapshot`.
        artifacts: As for `make_snapshot`.
        max_file_calls: As for `make_snapshot`.

    Returns:
        The snapshot, whose origin's `prelude` is the `require` lines.

    Raises:
        TypeError: `packages` is a str or holds a non-str, or another argument's type is wrong.
        ValueError: A name is neither a module's nor a listed distribution's, or another
            argument is out of range; raised before any work.
        SnapshotFailed: As for `make_snapshot`; a module the set lacks fails with "exit".
        ArtifactsMissing: As for `make_snapshot`.
        OSError: As for `make_snapshot`.
    """
    given = _checked_names(packages)
    code = _package_code(_package_names(given))
    return make_snapshot(
        code,
        grants=grants,
        mounts=mounts,
        env=env,
        cwd=cwd,
        fuel=fuel,
        deadline_s=deadline_s,
        memory_bytes=memory_bytes,
        output_bytes=output_bytes,
        cache_dir=cache_dir,
        artifacts=artifacts,
        max_file_calls=max_file_calls,
    )


def _checked_names(packages: Any) -> tuple[str, ...] | None:
    """`packages` as a tuple of names, each a str."""
    if packages is None:
        return None
    if isinstance(packages, (str, bytes)) or not isinstance(packages, Iterable):
        raise TypeError(
            f"packages must be an iterable of names, not a {type(packages).__name__}: "
            "pass ['POSIX']"
        )
    names = tuple(packages)
    for i, name in enumerate(names):
        if not isinstance(name, str):
            raise TypeError(f"packages[{i}] must be a str; got {type(name).__name__}")
    return names


def _package_names(given: Iterable[str] | None) -> list[str]:
    """The modules to load, in order, each once: every pair's for `None`; else a pair's for its
    distribution, case-folded, or for its first module; and any other module name as given."""
    by_distribution = {d.casefold(): names for d, names in PACKAGES}
    by_first = {names[0]: names for _, names in PACKAGES}
    out: list[str] = []
    for name in [d for d, _ in PACKAGES] if given is None else given:
        names = by_distribution.get(name.casefold()) or by_first.get(name)
        if names is None:
            if not MODULE.match(name):
                raise ValueError(f"packages: this artifact set ships no package named {name!r}")
            names = (name,)
        out += [n for n in names if n not in out]
    return out


def _package_code(names: Iterable[str]) -> str:
    return "".join(f"require {n};\n" for n in names)
