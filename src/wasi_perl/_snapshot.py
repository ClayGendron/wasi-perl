"""Preload images an embedder makes: `Snapshot`, `SnapshotOrigin`, `SnapshotFailed` and
`SnapshotMismatch`; the saved file; and a call's check of its start and of the set.

A snapshot is the guest's linear memory after the embedder's code ran once in a fresh instance
of the set's perl-reactor.wasm. A call given one is a fresh instance of a module the runner
writes from the reactor and the image (`_image_module`), so nothing a call does reaches the
image or another call. Memory is the guest's whole state: the reactor's one mutable global, the
stack pointer, is back at its first value whenever `prepare` returns.

Adapted from wasi-python's `_snapshot.py` at f9bf678 (same owner, Apache-2.0): the same classes,
the same file and the same checks, trimmed of checkpoints and of the restore by copy, which
wasi-perl does not have, and with three fields of the origin given neutral names
(`binary_sha256`, `library_sha256`, `runtime_version`). This module imports neither the runner
nor the making; they import it."""

from __future__ import annotations

import dataclasses
import datetime
import errno
import hashlib
import json
import os
import stat
import sys
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, NoReturn

from wasi_perl import _artifacts
from wasi_perl._artifacts import Artifacts

if TYPE_CHECKING:
    from wasi_perl.runner import Completed

MAGIC = b"wasi-perl snapshot\n"  # the file's first line names its kind and its runtime
FORMAT = 1
CODEC = "zlib"  # in every supported Python's standard library
LEVEL = 1  # a save is once per image: the fastest level
PAGE = 65536
SLICE = 1 << 20  # the file is compressed and read in slices, never a second whole copy
MAX_CODE_BYTES = 16 << 20  # the code is kept in the file's header; more is data for a grant
MAX_HEADER_BYTES = 17 << 20  # the code and room for the rest
MAX_IMAGE_BYTES = 4 << 30  # the guest's 32-bit address space
REASONS = ("exit", "trapped", "open file")
KINDS = ("prelude",)  # what made an image: a making's code
# Names an image cannot honour, because Perl reads each once, before or outside what a call
# from the image redoes: the hash seed's two in perl_construct, whose seed is frozen in the
# image; JSON::PP's switch, read when it is compiled; the locale's names, PERL_UNICODE and the
# locale switches, read by the locale's set-up in perl_construct; PERLIO and PERLIO_DEBUG, read
# at the first open of a handle; PERL_RE_COLORS, read once per interpreter; and
# PERL_ENCODE_DEBUG, which Encode reads with `use constant` as it loads. A making may not
# set them, so an image is always made without them, and a call whose environment has any of
# them runs perl.wasm instead. The locale's names are exactly the categories wasi-libc defines,
# which are all Perl reads, and LC_ALL, LANG and LANGUAGE.
FROZEN_ENV = (
    "PERL_HASH_SEED",
    "PERL_PERTURB_KEYS",
    "PERL_JSON_PP_USE_B",
    "LC_ALL",
    "LC_CTYPE",
    "LC_NUMERIC",
    "LC_COLLATE",
    "LC_TIME",
    "LC_MESSAGES",
    "LC_MONETARY",
    "LANG",
    "LANGUAGE",
    "PERL_BADLANG",
    "PERL_SKIP_LOCALE_INIT",
    "PERL_DEBUG_LOCALE_INIT",
    "PERL_UNICODE",
    "PERLIO",
    "PERLIO_DEBUG",
    "PERL_RE_COLORS",
    "PERL_ENCODE_DEBUG",
)
_NOT_PUBLIC = "make a Snapshot with make_snapshot, make_package_snapshot or Snapshot.load"
_OWNED_RULE = (
    "a snapshot is loaded only from a file only you may read or write (mode 0600, as save makes "
    "it), with one name, in a folder no other user can change"
)


class SnapshotMismatch(ValueError):
    """A snapshot does not fit where it is used: a saved file that is not one, is damaged or is
    of a newer format; a call whose artifact set is not the one the image was made with; or a
    call whose `memory_bytes` is below the image. The message says what to do."""


class SnapshotFailed(RuntimeError):
    """The code given to `make_snapshot` or `make_package_snapshot` did not leave an image a
    call could start from. The message is one line that says what to do.

    Attributes:
        reason: One of three fixed strings: "exit" (the code exited with a status other than 0,
            or died), "trapped" (it trapped, ran out of fuel or passed its deadline, as
            `completed.trapped` says) or "open file" (it left a file or folder open).
        completed: The making's own `Completed`: its exit status, output and trap.
    """

    def __init__(self, message: str, *, reason: str, completed: Completed | None) -> None:
        super().__init__(message)
        self.reason = reason
        self.completed = completed


@dataclass(frozen=True)
class SnapshotOrigin:
    """Where a snapshot came from, kept with its image and in its saved file.

    Attributes:
        prelude: The code that made the image, as given: the image already holds what it
            compiled, so the origin reveals nothing the image does not.
        prelude_sha256: The SHA-256 of the code as UTF-8.
        prelude_fuel: The instructions the code ran, when the making was given `fuel`; else
            `None`.
        image_bytes: The image's size: a whole number of 64 KiB pages.
        image_sha256: The SHA-256 of the image, which `Completed.image_sha256` repeats. It
            names an image; it does not predict one, since each making draws its own hash seed.
        home: The guest's HOME while the code ran: always "", since wasi-perl sets none.
        guest_paths: The guest paths of the making's grants and mounts, in order.
        env_names: The sorted names of the making's `env`, never their values.
        binary_sha256: The SHA-256 of the set's perl-reactor.wasm it was made with.
        library_sha256: The SHA-256 of the lines of the set's SHA256SUMS that name its library.
        runtime_version: The version of wasi-perl that made it.
        made_at: When it was made, in UTC, ISO 8601, to the second.
        kind: Always "prelude": made by `make_snapshot` or `make_package_snapshot`.
        parent_sha256: Always `None`.
    """

    prelude: str
    prelude_sha256: str
    prelude_fuel: int | None
    image_bytes: int
    image_sha256: str
    home: str
    guest_paths: tuple[str, ...]
    env_names: tuple[str, ...]
    binary_sha256: str
    library_sha256: str
    runtime_version: str
    made_at: str
    kind: str = "prelude"
    parent_sha256: str | None = None


class Snapshot:
    """A preload image: the guest's memory after your code ran once, and its origin.

    Make one with `make_snapshot` or `make_package_snapshot`, or read a saved one with
    `Snapshot.load`, and pass it as `snapshot=` to `run`. Every call from it is a fresh instance
    that starts from the image, so nothing one call does reaches the image or another call. A
    snapshot is immutable and may be used by any number of calls on any threads at once. It
    binds to the artifact set it was made with. It is not pickled: `save()` it and
    `Snapshot.load()` it, which checks the file.
    """

    __slots__ = ("_image", "_origin")
    _image: bytes
    _origin: SnapshotOrigin

    def __new__(cls, *args: object, **kwargs: object) -> Snapshot:
        raise TypeError(_NOT_PUBLIC)

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError("a Snapshot cannot be changed")

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError("a Snapshot cannot be changed")

    def __copy__(self) -> Snapshot:
        return self

    def __deepcopy__(self, memo: object) -> Snapshot:
        return self

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        raise TypeError(
            "a Snapshot is not pickled: save() it and Snapshot.load() it, which checks it"
        )

    def __repr__(self) -> str:
        origin = self._origin
        lines = origin.prelude.splitlines() or [""]
        first = lines[0] if len(lines[0]) <= 40 else lines[0][:40] + "…"
        more = len(lines) - 1
        others = "" if not more else f" +{more} line{'s' if more > 1 else ''}"
        size = origin.image_bytes / 2**20
        return (
            f"<Snapshot {first!r}{others}, {size:.1f} MiB, made {origin.made_at} "
            f"by wasi-perl {origin.runtime_version}>"
        )

    @property
    def origin(self) -> SnapshotOrigin:
        """Where the image came from: its code, its size and hash, and the set it binds to."""
        return self._origin

    def save(self, path: str | os.PathLike[str]) -> None:
        """Write the snapshot to a file only you can read or write, which `Snapshot.load` reads
        back.

        The file holds the image's hash seed (docs/snapshots.md), so it is made at mode 0600.
        The folder is checked first by the rule `load` holds it to, so a file is never saved
        where it could not be loaded. The file is written beside its name and then moved onto
        it, replacing a file there. Keep it where no guest is granted writing.

        Args:
            path: The file to write; by convention its name ends in `.wpsnap`.

        Raises:
            TypeError: `path` is not a str or a path.
            PermissionError: Others may change the folder, or a folder on the way to it, or a
                call running now may write it.
            OSError: The file could not be written.
        """
        target = Path(_path("path", path)).absolute()
        _publish_and_check(target.parent)  # no running guest may rewrite it
        fd, temp = tempfile.mkstemp(prefix=".wasi-perl-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                os.close(_open_owned(Path(temp)))  # load's rule, first
                _write(f, self)
            os.replace(temp, target)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> Snapshot:
        """Read a snapshot that `save` wrote, and check it before it is returned.

        The file must be one only you can read or write, as `save` makes it, and is read
        through the descriptor that was checked. Its header, its size, its stream and the
        image's hash are each checked, and the stream is never read past the size its header
        names. The set is not checked here: `Snapshot.check` does, and so does every call.
        Nothing is compiled.

        Args:
            path: The file `save` wrote.

        Returns:
            The snapshot the file holds.

        Raises:
            TypeError: `path` is not a str or a path.
            SnapshotMismatch: The file is not a snapshot, is damaged, or is of a newer format.
            PermissionError: Others may read or write the file, or change its folder, or it is
                a link, or a call running now may write it.
            OSError: The file could not be read.
        """
        where = Path(_path("path", path))
        _publish_and_check(where.absolute().parent)
        fd = _open_owned(where)
        with os.fdopen(fd, "rb") as f:
            image, origin = _read(f, where)
        return _made(image, origin)

    def check(self, artifacts: Artifacts | None = None) -> None:
        """Raise the `SnapshotMismatch` a call would, without running a call.

        The set is found as a call finds it, and checked against its SHA256SUMS, unless one is
        given; its reactor's and its library's digests must be the origin's.

        Args:
            artifacts: The artifact set to check against; `None` finds it with `artifacts()`.

        Raises:
            TypeError: `artifacts` is not an `Artifacts`.
            SnapshotMismatch: The set is not the one the image was made with.
            ArtifactsMissing: There is no usable artifact set, or it has no reactor.
        """
        if artifacts is not None and not isinstance(artifacts, Artifacts):
            raise TypeError(f"artifacts must be an Artifacts, or None; got {type(artifacts)}")
        found = artifacts if artifacts is not None else _artifacts.checked()[0]
        same_set(self, found, artifacts is None)


def _made(image: bytes, origin: SnapshotOrigin) -> Snapshot:
    """A snapshot, made by the runner's own functions only."""
    snap = object.__new__(Snapshot)
    object.__setattr__(snap, "_image", image)
    object.__setattr__(snap, "_origin", origin)
    return snap


def _path(name: str, value: Any) -> str:
    if not isinstance(value, (str, os.PathLike)):
        raise TypeError(f"{name} must be a str or a path; got {type(value).__name__}")
    text = os.fspath(value)
    if not isinstance(text, str) or not text:
        raise TypeError(f"{name} must be a non-empty str path")
    return text


def version() -> str:
    """The version of the package that runs: its own `__version__`, already imported."""
    return str(getattr(sys.modules.get(__package__ or "wasi_perl"), "__version__", "unknown"))


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def origin_of(
    code: str,
    image: bytes,
    fuel: int | None,
    paths: tuple[str, ...],
    env_names: tuple[str, ...],
    digests: tuple[str, str],
) -> SnapshotOrigin:
    """The origin a making records."""
    return SnapshotOrigin(
        prelude=code,
        prelude_sha256=hashlib.sha256(code.encode("utf-8")).hexdigest(),
        prelude_fuel=fuel,
        image_bytes=len(image),
        image_sha256=hashlib.sha256(image).hexdigest(),
        home="",
        guest_paths=tuple(paths),
        env_names=tuple(sorted(env_names)),
        binary_sha256=digests[0],
        library_sha256=digests[1],
        runtime_version=version(),
        made_at=_now(),
    )


def _publish_and_check(folder: Path) -> None:
    """Keep a saved or loaded file's folder from every later call's writable grants, then refuse
    it if a call running now may write it (`_grants.publish_snapshot_folder`)."""
    from wasi_perl import _grants

    _grants.publish_snapshot_folder(folder)


# The saved file is held to the module cache's rule (`_cache`): a regular file with one name,
# this user's, at a mode no one else may read or write, in a folder on a way no other user can
# change, opened inside its folder without following a link and checked through the descriptor
# it is read from. On Windows, which has no mode bits, the file must be no link or junction and
# have one name, and the folder's access list is trusted, as the cache's is.
_FILE_OPEN = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
_FILE_OPEN |= getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
_DIR_OPEN = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _open_owned(path: Path) -> int:
    from wasi_perl import _cache

    folder = Path(os.path.realpath(path.absolute().parent))
    try:
        _cache._way(folder)
    except PermissionError as e:
        raise PermissionError(
            errno.EACCES, f"{path}: {e.strerror} ({e.filename}); {_OWNED_RULE}"
        ) from None
    at = os.open in os.supports_dir_fd and os.name != "nt"
    try:
        if at:
            dir_fd = os.open(folder, _DIR_OPEN)
            try:
                fd = os.open(path.name, _FILE_OPEN, dir_fd=dir_fd)
            finally:
                os.close(dir_fd)
        else:
            if stat.S_ISLNK(os.lstat(path).st_mode):
                raise PermissionError(errno.EACCES, f"{path} is a link; {_OWNED_RULE}")
            fd = os.open(folder / path.name, _FILE_OPEN)
    except OSError as e:
        if e.errno != errno.ELOOP:
            raise
        raise PermissionError(errno.EACCES, f"{path} is a link; {_OWNED_RULE}") from None
    try:
        st = os.fstat(fd)
        why = None
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            why = "is not a regular file with one name"
        elif os.name == "nt":
            if getattr(os.lstat(path), "st_file_attributes", 0) & _REPARSE:
                why = "is a link or a junction"
        elif st.st_uid != os.geteuid():
            why = f"is another user's (uid {st.st_uid})"
        elif st.st_mode & 0o077:
            who = "written" if st.st_mode & 0o022 else "read"
            why = f"may be {who} by others (mode {stat.S_IMODE(st.st_mode):o})"
        if why is not None:
            raise PermissionError(errno.EACCES, f"{path} {why}; {_OWNED_RULE}")
    except BaseException:
        os.close(fd)
        raise
    return fd


# ---------------------------------------------------------------------------------------------
# The file: the first line, one line of JSON (the format, the codec and the origin, keys
# sorted), then the image as one zlib stream.


def _write(f: Any, snap: Snapshot) -> None:
    header = {"format": FORMAT, "codec": CODEC, **dataclasses.asdict(snap._origin)}
    f.write(MAGIC)
    f.write(json.dumps(header, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n")
    squeeze = zlib.compressobj(LEVEL)
    view = memoryview(snap._image)
    for at in range(0, len(view), SLICE):
        f.write(squeeze.compress(view[at : at + SLICE]))
    f.write(squeeze.flush())


def _read(f: Any, where: Path) -> tuple[bytes, SnapshotOrigin]:
    if f.readline(len(MAGIC)) != MAGIC:
        raise SnapshotMismatch(f"{where} is not a wasi-perl snapshot file")
    line = f.readline(MAX_HEADER_BYTES + 1)
    if len(line) > MAX_HEADER_BYTES or not line.endswith(b"\n"):
        raise SnapshotMismatch(f"{where} has a header past {MAX_HEADER_BYTES:,} bytes, or none")
    try:
        header = json.loads(line)
    except ValueError:
        raise SnapshotMismatch(f"{where} has a damaged header") from None
    origin = _header_origin(header, where)
    image = _image_of(f, origin.image_bytes, where)
    if hashlib.sha256(image).hexdigest() != origin.image_sha256:
        raise SnapshotMismatch(f"{where} is damaged: its image does not match its hash")
    return image, origin


def _header_origin(header: Any, where: Path) -> SnapshotOrigin:
    """The origin a header holds, each field of its kind; the format and codec this reads."""
    if not isinstance(header, dict):
        raise SnapshotMismatch(f"{where} has a damaged header")
    form = header.get("format")
    if type(form) is not int or form < 1:
        raise SnapshotMismatch(f"{where} has a damaged header: format {form!r}")
    if form > FORMAT:
        raise SnapshotMismatch(
            f"{where} was saved by a newer wasi-perl, format {form}; this one reads format {FORMAT}"
        )
    if header.get("codec") != CODEC:
        raise SnapshotMismatch(f"{where} is compressed with {header.get('codec')!r}, not zlib")
    values: dict[str, Any] = {}
    for field in dataclasses.fields(SnapshotOrigin):
        value = header.get(field.name, field.default)  # a field added later has its default
        if field.name in ("guest_paths", "env_names") and isinstance(value, list):
            value = tuple(value)
        kinds = {
            "prelude_fuel": (int, type(None)),
            "image_bytes": (int,),
            "parent_sha256": (type(None),),
        }.get(field.name)
        if field.name in ("guest_paths", "env_names"):
            ok = isinstance(value, tuple) and all(isinstance(v, str) for v in value)
        else:
            ok = type(value) in (kinds or (str,))
        if not ok:
            raise SnapshotMismatch(f"{where} has a damaged header: {field.name} {value!r}")
        values[field.name] = value
    if values["kind"] not in KINDS:
        raise SnapshotMismatch(f"{where} has a damaged header: kind {values['kind']!r}")
    if values["home"] != "":
        raise SnapshotMismatch(f"{where} has a damaged header: home {values['home']!r}")
    size = values["image_bytes"]
    if size <= 0 or size % PAGE or size > MAX_IMAGE_BYTES:
        raise SnapshotMismatch(
            f"{where} names an image of {size:,} bytes: not a whole number of 64 KiB pages up "
            "to 4 GiB"
        )
    return SnapshotOrigin(**values)


def _image_of(f: Any, size: int, where: Path) -> bytes:
    """The stream decompressed to exactly `size` bytes, never one byte further."""
    stream = zlib.decompressobj()
    out = bytearray()
    try:
        while not stream.eof:
            chunk = stream.unconsumed_tail or f.read(SLICE)
            if not chunk:
                break
            out += stream.decompress(chunk, size - len(out) + 1)
            if len(out) > size:
                raise SnapshotMismatch(f"{where} holds more than its image of {size:,} bytes")
    except zlib.error:
        raise SnapshotMismatch(f"{where} is damaged: its stream does not decompress") from None
    if not stream.eof or len(out) != size:
        raise SnapshotMismatch(f"{where} is damaged: its stream ends before {size:,} bytes")
    if stream.unused_data or f.read(1):
        raise SnapshotMismatch(f"{where} is damaged: it has bytes past its stream")
    return bytes(out)


# ---------------------------------------------------------------------------------------------
# A call's start, and the set it binds to.


class Start(NamedTuple):
    """What a checked call starts from: the image, its SHA-256, and the digests of the reactor
    and the library it binds to."""

    image: bytes
    sha256: str
    digests: tuple[str, str]


def start(snapshot: Any, memory_bytes: int | None) -> Start | None:
    """`snapshot=` checked: `None` for perl.wasm (`True` or `False`), else what the call starts
    from. A snapshot needs `memory_bytes` at least its image."""
    if isinstance(snapshot, bool):
        return None
    if not isinstance(snapshot, Snapshot):
        name = type(snapshot).__name__
        raise TypeError(f"snapshot must be True, False or a Snapshot; got {name}")
    size = snapshot.origin.image_bytes
    if memory_bytes is not None and memory_bytes < size:
        raise SnapshotMismatch(
            f"memory_bytes is {memory_bytes:,}, below the snapshot's image of {size:,} bytes; "
            "pass at least that, or None"
        )
    o = snapshot._origin
    return Start(snapshot._image, o.image_sha256, (o.binary_sha256, o.library_sha256))


def falls_back(env: dict[str, str]) -> bool:
    """Whether a call with this environment runs perl.wasm though it was given an image."""
    return any(name in env for name in FROZEN_ENV)


def same_set(snapshot: Snapshot | Start, found: Artifacts, checked_set: bool) -> tuple[str, str]:
    """The reactor's and the library's digests of `found`, which must be the image's.

    Raises:
        ArtifactsMissing: The set has no reactor, or it does not match its SHA256SUMS.
        SnapshotMismatch: The set is not the one the image was made with.
    """
    want = (
        snapshot.digests
        if isinstance(snapshot, Start)
        else (snapshot._origin.binary_sha256, snapshot._origin.library_sha256)
    )
    got = _artifacts.image_digests(found, checked_set)
    if got != want:
        which, made, runs = (
            ("perl-reactor.wasm", want[0], got[0])
            if want[0] != got[0]
            else ("library", want[1], got[1])
        )
        raise SnapshotMismatch(
            f"this snapshot was made for the artifact set whose {which} is {made[:12]}…, and "
            f"this call runs {runs[:12]}… (wasi-perl {version()}); make it again with this set"
        )
    return got
