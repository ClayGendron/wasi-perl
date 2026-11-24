"""The artifact set: `Artifacts`, `ArtifactsMissing`, `artifacts()`, and the check of a found set
against its SHA256SUMS. Adapted from wasi-python's `_artifacts.py` (same owner, Apache-2.0):
the same order of places and the same per-stamp digest cache, with a check of the library tree
that wasi-python, whose library is one zip, does not need."""

from __future__ import annotations

import hashlib
import os
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

ARTIFACTS_ENV = "WASI_PERL_ARTIFACTS"
SUMS = "SHA256SUMS"
_WHEEL_DIR = Path(__file__).resolve().parent / "artifacts"


def _checkout_dir() -> Path | None:
    """A source checkout's build/artifacts/: only when this package is <repo>/src/wasi_perl/ and
    <repo>/pyproject.toml names wasi-perl, so a vendored copy never takes another project's."""
    package = Path(__file__).resolve().parent
    if package.parent.name != "src":
        return None
    repo = package.parent.parent
    try:
        import tomllib

        name = tomllib.loads((repo / "pyproject.toml").read_text()).get("project", {}).get("name")
    except (OSError, ValueError, UnicodeDecodeError, AttributeError):
        return None
    return repo / "build" / "artifacts" if name == "wasi-perl" else None


_CHECKOUT_DIR = _checkout_dir()


class ArtifactsMissing(RuntimeError):
    """Raised when there is no usable artifact set; the message says how to get one."""


@dataclass(frozen=True)
class Artifacts:
    """The artifact set's paths, made by `artifacts()`, by `Artifacts.at(folder)`, or by you for
    `run(artifacts=...)`, which uses a set you pass as you give it, unchecked.

    Attributes:
        perl: perl.wasm, the one static WebAssembly command.
        lib: The library folder, lib/perl5, which every guest sees read-only at
            /usr/local/lib/perl5 (so @INC's /usr/local/lib/perl5/5.42 is found).
        dev: The folder holding one empty file, null, which every guest sees read-only at /dev.
        manifest: MANIFEST: every pinned source and recipe hash the build used.
        sums: SHA256SUMS: the SHA-256 of every other file of the set.
    """

    perl: Path
    lib: Path
    dev: Path
    manifest: Path
    sums: Path

    @classmethod
    def at(cls, folder: str | os.PathLike[str]) -> Artifacts:
        """The set laid out in `folder` as the build writes it."""
        d = Path(folder)
        return cls(d / "perl.wasm", d / "lib" / "perl5", d / "dev", d / "MANIFEST", d / SUMS)


def artifact_dir() -> Path:
    """`$WASI_PERL_ARTIFACTS` when set and not empty; else a source checkout's build/artifacts/
    when it holds perl.wasm; else the wheel's own artifacts/ when it holds perl.wasm."""
    env = os.environ.get(ARTIFACTS_ENV)
    if env:
        return Path(env).resolve()
    for d in (_CHECKOUT_DIR, _WHEEL_DIR):
        if d is not None and (d / "perl.wasm").is_file():
            return d
    raise ArtifactsMissing(
        f"no runtime artifacts: ${ARTIFACTS_ENV} is unset, and neither {_CHECKOUT_DIR} nor "
        f"{_WHEEL_DIR} holds perl.wasm. Install the released wheel, or build the set with "
        "scripts/build.sh and point $WASI_PERL_ARTIFACTS at build/artifacts."
    )


def artifacts() -> Artifacts:
    """The found set's paths, unchecked; `run` checks them against SHA256SUMS before use.

    Raises:
        ArtifactsMissing: There is no set, or its folder has no perl.wasm.
    """
    d = artifact_dir()
    if not (d / "perl.wasm").is_file():
        raise ArtifactsMissing(f"{d} holds no perl.wasm (from ${ARTIFACTS_ENV})")
    return Artifacts.at(d)


_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
_lock = threading.Lock()
_digests: dict[tuple[str, int, int, int, int, int], str] = {}
_trees: set[tuple[str, int, int, int, int, int]] = set()  # SHA256SUMS stamps whose tree passed
# SHA256SUMS parsed, by its stamp; callers only read it
_sums: dict[tuple[str, int, int, int, int, int], dict[str, str]] = {}


def forget() -> None:
    """Drop what this process has checked, so the next call checks again."""
    with _lock:
        _digests.clear()
        _trees.clear()
        _sums.clear()


def _stamp(path: Path) -> tuple[str, int, int, int, int, int]:
    st = os.stat(path)
    return (str(path), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _hash_stream(f: BinaryIO) -> str:
    h = hashlib.sha256()
    for block in iter(lambda: f.read(1 << 20), b""):
        h.update(block)
    return h.hexdigest()


def digest(path: Path) -> str:
    """The SHA-256 of `path`, kept per path and stamp, so an unchanged file is hashed once and a
    replacement of the same size and time (another inode or ctime) is hashed again. It is kept
    only when the descriptor hashed is the file the stamp names."""
    key = _stamp(path)
    with _lock:
        found = _digests.get(key)
    if found is not None:
        return found
    fd = os.open(path, _OPEN_FLAGS)
    with os.fdopen(fd, "rb") as f:
        opened = os.fstat(fd)
        got = _hash_stream(f)
    if (opened.st_dev, opened.st_ino) == key[1:3]:
        with _lock:
            _digests[key] = got
    return got


def _read_sums(found: Artifacts) -> dict[str, str]:
    """SHA256SUMS parsed, kept per stamp of the file as `digest` keeps a hash: it is parsed again
    when its inode, size, mtime or ctime changes. A parse is kept only when the file's stamp is
    the same after the read as before it, so a file replaced or written meanwhile is not kept
    under a stamp it does not have."""
    try:
        before = _stamp(found.sums)
    except OSError:
        before = None  # the read below says why
    if before is not None:
        with _lock:
            kept = _sums.get(before)
        if kept is not None:
            return kept
    sums = _parse_sums(found)
    try:
        after = _stamp(found.sums)
    except OSError:
        after = None
    if before is not None and after == before:
        with _lock:
            _sums.clear()  # one list kept at a time
            _sums[before] = sums
    return sums


def _parse_sums(found: Artifacts) -> dict[str, str]:
    try:
        text = found.sums.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as e:
        raise ArtifactsMissing(
            f"{found.sums} cannot be read ({e}): a set the runner finds is checked against "
            "its SHA256SUMS before use"
        ) from None
    sums: dict[str, str] = {}
    for line in text.splitlines():
        want, sep, name = line.partition("  ")
        if not sep:
            want, sep, name = line.partition(" *")
        if sep and len(want) == 64:
            sums[name] = want.lower()
    return sums


def _refuse(path: Path, sums: Path, got: str, want: str) -> ArtifactsMissing:
    return ArtifactsMissing(
        f"{path} does not match {sums} (SHA-256 {got[:16]}…, listed {want[:16]}…): the set "
        "changed after it was built; rebuild or reinstall it"
    )


def checked() -> tuple[Artifacts, str]:
    """The found set, and perl.wasm's digest as checked: perl.wasm and MANIFEST against
    SHA256SUMS on every call (each hashed again only when its stamp changes), and the library and
    dev/ trees once per stamp of SHA256SUMS: every file listed, every file listed as hashed, and
    dev/ exactly one empty `null`. The digest is handed on, so the module is compiled only from
    bytes that hash to it."""
    found = artifacts()
    root = found.perl.parent
    if found.lib != root / "lib" / "perl5" or found.manifest.parent != root:
        raise ArtifactsMissing(f"{root} is not laid out as an artifact set")
    sums = _read_sums(found)
    got_perl = ""
    for path in (found.manifest, found.perl):
        want = sums.get(path.name)
        if want is None:
            raise ArtifactsMissing(f"{path} is not listed in {found.sums}")
        try:
            got = digest(path)
        except OSError as e:
            raise ArtifactsMissing(f"{path} cannot be read ({e})") from None
        if got != want:
            raise _refuse(path, found.sums, got, want)
        got_perl = got
    key = _stamp(found.sums)
    with _lock:
        done = key in _trees
    if not done:
        _check_tree(root, sums, found.sums)
        with _lock:
            _trees.add(key)
    return found, got_perl


def _check_tree(root: Path, sums: dict[str, str], sums_path: Path) -> None:
    listed = {name for name in sums if name.startswith(("lib/", "dev/"))}
    present = set()
    for top in ("lib", "lib/perl5", "dev"):
        if stat.S_ISLNK(os.lstat(root / top).st_mode):
            raise ArtifactsMissing(f"{root / top} is a link; a set holds no links")
    for top in ("lib", "dev"):
        for dirpath, dirnames, filenames in os.walk(root / top, followlinks=False):
            for name in filenames + [d for d in dirnames if (Path(dirpath) / d).is_symlink()]:
                path = Path(dirpath) / name
                rel = path.relative_to(root).as_posix()
                st = os.lstat(path)
                if not stat.S_ISREG(st.st_mode):
                    raise ArtifactsMissing(f"{path} is not a regular file; a set holds no links")
                if rel not in listed:
                    raise ArtifactsMissing(
                        f"{path} is not listed in {sums_path}: a file the set does not list may "
                        "be code Perl would load, so the set is not used"
                    )
                present.add(rel)
                got = digest(path)
                if got != sums[rel]:
                    raise _refuse(path, sums_path, got, sums[rel])
    missing = sorted(listed - present)
    if missing:
        raise ArtifactsMissing(f"{root / missing[0]} is listed in {sums_path} but missing")
    if {n for n in present if n.startswith("dev/")} != {"dev/null"}:
        raise ArtifactsMissing(f"{root / 'dev'} must hold exactly one empty file, null")
    if os.stat(root / "dev" / "null").st_size != 0:
        raise ArtifactsMissing(f"{root / 'dev' / 'null'} must be empty")
