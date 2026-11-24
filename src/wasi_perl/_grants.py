"""Grants: `Grant`, the checks of each guest path, the walk of each host path, and the rules that
keep the runtime's own files out of reach. Adapted from wasi-python's typed grants
(`_places.py`, `_platform_posix.py`'s walk; same owner, Apache-2.0), trimmed: the walk resolves
a path link by link and passes wasmtime the resolved path; it holds no descriptor and runs no
post-preopen identity check (the README's "Not protected" says so).

Comparisons are by identity, (st_dev, st_ino) of a folder and of each of its ancestors, never
by spelling, so `~/LIBRARY/CACHES` on a case-insensitive disk is `~/Library/Caches`."""

from __future__ import annotations

import os
import stat
import sys
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

LIBRARY_GUEST = "/usr/local/lib/perl5"
DEV_GUEST = "/dev"
RESERVED = (LIBRARY_GUEST, DEV_GUEST)
MAX_LINKS = 40
WINDOWS = os.name == "nt"
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

Identity = tuple[int, int]


@dataclass(frozen=True)
class Grant:
    """A host folder the guest sees at `guest`: read-only unless `writable=True`.

    Attributes:
        host: The host folder, a str or a path. It must exist; a relative one is taken from the
            working folder when the call is made.
        guest: Where the guest sees it: an absolute, normalized path, not `/`, and neither at,
            under nor above `/usr/local/lib/perl5` or `/dev`, which the runtime's own read-only
            folders take.
        writable: Whether the guest may create, change and remove files in it.
    """

    host: str | os.PathLike[str]
    guest: str
    writable: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.host, bytes) or not isinstance(self.host, (str, os.PathLike)):
            raise TypeError(f"Grant host must be a str or a path, not {type(self.host).__name__}")
        if not isinstance(os.fspath(self.host), str):
            raise TypeError("Grant host must be a str path, not bytes")
        if not isinstance(self.guest, str):
            raise TypeError(f"Grant guest must be a str, not {type(self.guest).__name__}")
        if not isinstance(self.writable, bool):
            raise TypeError(f"Grant writable must be True or False, not {self.writable!r}")


def guest_path(where: str, value: Any) -> str:
    """An absolute, normalized guest path that is not the runtime's: ValueError otherwise."""
    if not isinstance(value, str):
        raise TypeError(f"{where} must be a str, not {type(value).__name__}")
    value = str.__str__(value)
    if "\0" in value:
        raise ValueError(f"{where}: a guest path holds no NUL")
    if not value.startswith("/"):
        raise ValueError(f"{where}: a guest path must be absolute; got {value!r}")
    if value == "/":
        raise ValueError(f"{where}: a grant's guest path is not /: name a folder, such as /data")
    if "\\" in value or any(part in ("", ".", "..") for part in value[1:].split("/")):
        raise ValueError(
            f"{where}: a guest path must be normalized (no empty, . or .. part, no trailing / "
            f"and no \\); got {value!r}"
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{where}: not valid UTF-8 (a lone surrogate)") from None
    return value


def _reserved(where: str, value: str) -> None:
    for r in RESERVED:
        if value == r or value.startswith(r + "/") or r.startswith(value + "/"):
            raise ValueError(
                f"{where}: {value} is at, under or above {r}, which the runtime's own "
                "read-only folders take"
            )


def check(grants: Any) -> tuple[Grant, ...]:
    """The grants' types and guest paths, and no two sharing one."""
    if isinstance(grants, (str, bytes)) or not isinstance(grants, Iterable):
        raise TypeError(f"grants must be an iterable of Grant, not {type(grants).__name__}")
    out: list[Grant] = []
    seen: dict[str, int] = {}
    for i, g in enumerate(grants):
        if not isinstance(g, Grant):
            raise TypeError(f"grants[{i}] must be a Grant, not {type(g).__name__}")
        guest = guest_path(f"grants[{i}]", g.guest)
        _reserved(f"grants[{i}]", guest)
        if guest in seen:
            raise ValueError(f"grants[{i}] at {guest}: grants[{seen[guest]}] has that guest path")
        seen[guest] = i
        out.append(g)
    return tuple(out)


def check_cwd(cwd: Any) -> str | None:
    if cwd is None:
        return None
    return guest_path("cwd", cwd)


def _can_write(folder: str) -> bool:
    if os.access in os.supports_effective_ids:
        return os.access(folder, os.W_OK, effective_ids=True)
    return os.access(folder, os.W_OK)


def _parts(text: str) -> list[str]:
    """The components of a path as this system reads it: split at `os.sep`, and at `os.altsep`
    only where the system has one (Windows' `/`). On POSIX a backslash is an ordinary character
    of a name, and a guest can make a folder named `..\\x`; read as a separator it would turn
    that name into a climb."""
    if os.altsep:
        text = text.replace(os.altsep, os.sep)
    return [p for p in text.split(os.sep) if p]


def resolve(where: str, host: str | os.PathLike[str]) -> Path:
    """The host folder, resolved link by link from the absolute path. A link is followed only if
    the folder holding it is one this user cannot write, so a link a guest could have left in a
    writable grant is never followed; on Windows every link and junction is refused."""
    text = os.fspath(host)
    if text == "":
        raise ValueError(f"{where}: the host path is empty")
    if not os.path.isabs(text):
        text = os.path.join(os.getcwd(), text)
    drive, rest = os.path.splitdrive(text)
    root = drive + os.sep
    pending = _parts(rest)
    current = root
    links = 0
    while pending:
        name = pending.pop(0)
        if name == ".":
            continue
        if name == "..":
            current = os.path.dirname(current) or root
            continue
        step = os.path.join(current, name)
        try:
            st = os.lstat(step)
        except FileNotFoundError:
            raise ValueError(f"{where}: the host folder {text} does not exist") from None
        except OSError as e:
            raise ValueError(f"{where}: {step} cannot be read ({e.strerror})") from None
        reparse = WINDOWS and getattr(st, "st_file_attributes", 0) & _REPARSE
        if stat.S_ISLNK(st.st_mode) or reparse:
            if WINDOWS or _can_write(current):
                raise ValueError(
                    f"{where}: {step} is a link in a folder this user can write, so a guest "
                    "could have made it; grant the folder it leads to"
                )
            links += 1
            if links > MAX_LINKS:
                raise ValueError(f"{where}: more than {MAX_LINKS} links on the way to {text}")
            target = os.readlink(step)
            if os.path.isabs(target):
                drive, target_rest = os.path.splitdrive(target)
                current = (drive or os.path.splitdrive(current)[0]) + os.sep
                target = target_rest
            pending = _parts(target) + pending
            continue
        current = step
    if not stat.S_ISDIR(os.stat(current).st_mode):
        raise ValueError(f"{where}: the host path {text} is not a folder")
    return Path(current)


def _identity(path: Path) -> Identity:
    st = os.stat(path)
    return st.st_dev, st.st_ino


def chain(path: Path) -> tuple[Identity, ...]:
    """The identities of `path`'s deepest existing folder and every ancestor, links followed."""
    p = Path(os.path.abspath(path))
    while not p.exists() and p != p.parent:
        p = p.parent
    real = Path(os.path.realpath(p))
    return tuple(_identity(q) for q in (real, *real.parents))


def _exists(path: Path) -> Identity | None:
    try:
        return _identity(path) if path.is_dir() else None
    except OSError:
        return None


class Placed(NamedTuple):
    """A grant as wasmtime opens it: the resolved host folder, the guest path, the mode, and the
    identities of the folder and its ancestors, which the live calls' registry compares."""

    host: Path
    guest: str
    writable: bool
    chain: tuple[Identity, ...] = ()


# The grants of the calls running now in this process, by call. A guest of one call writes its
# writable grants while another call is between its walk and wasmtime's own open, so one call's
# writable grant must never be on another live call's way, nor hold another's read-only grant.
_live_lock = threading.Lock()
_live: dict[object, tuple[tuple[str, Placed], ...]] = {}


def live_count() -> int:
    with _live_lock:
        return len(_live)


def hold(placed: Sequence[Placed], slot: object) -> None:
    """Register a starting call's grants, once each is checked against every live call's: a grant
    at, inside or above a live writable grant is refused, and a writable grant at, inside or above
    any live grant. The check and the registration are one step under the lock, so of two calls
    that start at once, the second sees the first. `slot` is the caller's, made before its `try`,
    so `let_go(slot)` in its `finally` lets go whatever interrupts: the registration is one
    dictionary store."""
    with _live_lock:
        for i, p in enumerate(placed):
            for others in _live.values():
                for where, q in others:
                    if not (q.writable or p.writable):
                        continue  # two calls may read one folder at once
                    if q.chain[0] in p.chain or p.chain[0] in q.chain:
                        mode = "writable" if q.writable else "read-only"
                        raise ValueError(
                            f"grants[{i}] at {p.guest}: {p.host} is at, inside or above the "
                            f"{mode} {where} of a call running now ({q.host}), whose guest could "
                            "change it, or it theirs; grant a folder apart from it, or wait for "
                            "that call to end"
                        )
        for p in placed:
            if p.writable:
                _ever_writable[p.chain[0]] = (str(p.host), p.chain)
        _bound_ever_writable()
        _live[slot] = tuple((f"grant at {p.guest}", p) for p in placed)


def let_go(slot: object) -> None:
    with _live_lock:
        _live.pop(slot, None)


def judge(
    grants: Sequence[Grant],
    cwd: str | None,
    *,
    caches: Iterable[Path],
    runtime: Iterable[Path],
    runtime_files: RuntimeFiles | Iterable[Path] = (),
) -> tuple[tuple[Placed, ...], str | None]:
    """Each grant resolved and held to the rules: no grant at or inside a module cache, no
    writable grant above one; no writable grant at, inside or above a runtime folder; no
    read-only grant at or inside a writable one. `cwd` inside a grant, its folder existing."""
    caches = tuple(caches)
    runtime = tuple(runtime)
    placed: list[Placed] = []
    chains: list[tuple[Identity, ...]] = []
    cache_chains = [(c, chain(c), _exists(c)) for c in caches]
    runtime_chains = [(r, chain(r), _exists(r)) for r in runtime]
    for i, g in enumerate(grants):
        where = f"grants[{i}] at {g.guest}"
        host = resolve(where, g.host)
        mine = chain(host)
        me = mine[0]
        for c, c_chain, c_id in cache_chains:
            inside = c_id is not None and c_id in mine
            if inside or (g.writable and me in c_chain):
                raise ValueError(
                    f"{where}: {host} reaches the module cache at {c}, which holds native code "
                    "the runner loads; no grant may be at or inside it, nor a writable one above it"
                )
        if g.writable:
            for r, r_chain, r_id in runtime_chains:
                if (r_id is not None and r_id in mine) or me in r_chain:
                    raise ValueError(
                        f"{where}: a writable grant of {host} reaches the runtime's own files at "
                        f"{r}, which the next call trusts; grant it read-only, or another folder"
                    )
        placed.append(Placed(host, g.guest, g.writable, mine))
        chains.append(mine)
    _refuse_second_names(placed, runtime_files)
    for i, p in enumerate(placed):
        if p.writable:
            continue
        for j, w in enumerate(placed):
            if w.writable and chains[j][0] in chains[i]:
                raise ValueError(
                    f"grants[{i}] at {p.guest}: its folder {p.host} is inside the writable "
                    f"grants[{j}] at {w.guest}, so the guest could change it there"
                )
    return tuple(placed), _judge_cwd(cwd, placed)


MAX_WALK = 100_000  # the most entries the writable grants of one call are walked through

# A scan of the runtime's files is kept and used again while every folder it walked is as it
# was, on Linux and macOS only. There a folder's ctime moves with every change of its entries and
# no user can set it; on Windows st_ctime is the creation time, which the owner can set, so
# every writable call scans.
SCANS_KEPT = sys.platform in ("linux", "darwin")
# A folder whose mtime or ctime lies this close to the start of the scan that read it may be
# changed again within the same tick of a coarse file clock, unseen, so its scan is not kept.
SETTLE_NS = 2_000_000_000

FolderStamp = tuple[int, int, int, int]  # st_dev, st_ino, st_mtime_ns, st_ctime_ns


def _now_ns() -> int:
    return time.time_ns()


def _folder_stamp(path: str, follow: bool) -> FolderStamp | None:
    try:
        st = os.stat(path) if follow else os.lstat(path)
    except OSError:
        return None
    return st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns


class RuntimeFiles:
    """The runtime's files as one scan found them: the identity of every regular file under
    `tops`, and of each of `singles`, with the stamp of every folder walked, each stamped before
    it is listed. A file is added, removed or replaced only through an entry of its folder, which
    moves that folder's mtime and ctime; a write into a file keeps its identity. So while every
    stamp holds, the identities are still the files' own. A second name made inside a grant
    changes no folder here, which is why the writable grants are walked on every call.

    A top, and a single's folder, is stamped by what its path names now, links followed, so a top
    that comes to name another folder is scanned again."""

    def __init__(self, tops: Sequence[Path], singles: Sequence[Path]) -> None:
        self.tops, self.singles = tuple(tops), tuple(singles)
        start = _now_ns()
        stamps: dict[tuple[str, bool], FolderStamp | None] = {}
        files: dict[Identity, str] = {}

        def note(path: str) -> None:
            try:
                st = os.lstat(path)
            except OSError:
                return
            if stat.S_ISREG(st.st_mode):
                files.setdefault((st.st_dev, st.st_ino), path)

        pending = [(str(top), True) for top in self.tops]
        while pending:
            folder, follow = pending.pop()
            if (folder, follow) in stamps:
                continue
            stamps[(folder, follow)] = _folder_stamp(folder, follow)  # None: not there
            try:
                with os.scandir(folder) as entries:
                    found = list(entries)
            except OSError:
                continue  # an unreadable folder: its files were never seen, as before
            for entry in found:
                if entry.is_dir(follow_symlinks=False):
                    pending.append((entry.path, False))
                else:
                    note(entry.path)
        for single in self.singles:
            parent = (str(single.parent), True)
            if parent not in stamps:
                stamps[parent] = _folder_stamp(*parent)
            note(str(single))
        self.stamps = tuple(stamps.items())
        self.files = files
        self.settled = all(
            s is None or (s[2] < start - SETTLE_NS and s[3] < start - SETTLE_NS)
            for s in stamps.values()
        )

    def fresh(self) -> bool:
        """Whether this scan may stand for a new one: every stamp settled when it was taken, and
        every folder still as the scan found it."""
        return self.settled and all(
            _folder_stamp(path, follow) == st for (path, follow), st in self.stamps
        )

    def linked_now(self) -> dict[Identity, str]:
        """The runtime files that have more than one name now, read afresh."""
        linked: dict[Identity, str] = {}
        for f in self.files.values():
            try:
                st = os.lstat(f)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
                linked[(st.st_dev, st.st_ino)] = f
        return linked

    def holds(self, identity: Identity) -> str | None:
        """The runtime file the scan names under `identity`, if its path still names it."""
        path = self.files.get(identity)
        if path is None:
            return None
        try:
            st = os.lstat(path)
        except OSError:
            return None
        same = stat.S_ISREG(st.st_mode) and (st.st_dev, st.st_ino) == identity
        return path if same else None


_scan_lock = threading.Lock()
_scan: list[RuntimeFiles] = []  # the one scan kept, for the trees it walked


def _keep(made: RuntimeFiles) -> None:
    if SCANS_KEPT:
        with _scan_lock:
            _scan[:] = [made]


def runtime_scan(tops: Sequence[Path], singles: Sequence[Path]) -> RuntimeFiles:
    """The runtime's files: the kept scan of these trees while it is fresh, else a new scan,
    kept where scans are kept."""
    tops, singles = tuple(tops), tuple(singles)
    with _scan_lock:
        kept = _scan[0] if _scan and (_scan[0].tops, _scan[0].singles) == (tops, singles) else None
    if kept is not None and SCANS_KEPT and kept.fresh():
        return kept
    made = RuntimeFiles(tops, singles)
    _keep(made)
    return made


def _refuse_second_names(placed: Sequence[Placed], files: RuntimeFiles | Iterable[Path]) -> None:
    """No writable grant holds a second name (a hard link) of a runtime file. A folder rule
    cannot see one: uv installs packages by hard link from its cache, so wasmtime-py's native
    library has other names there, and a writable grant of that cache would let a guest rewrite
    the library the next host process loads. The writable grants are walked, at most MAX_WALK
    entries in all, and every file there with more than one name is looked up among the runtime
    files' identities, and a match read afresh.

    Three shortcuts need to know whether any runtime file has a second name now, which a kept
    scan cannot say, since a link made inside a grant changes no runtime folder; so each reads
    every runtime file afresh when it is reached. A grant larger than the runtime is walked on
    only when one has: a file with one name has none to find. A folder the walk cannot read,
    and a grant too large to walk, are refused only when one has, since then its second names
    cannot be ruled out."""
    writable = [p for p in placed if p.writable]
    if not writable:
        return
    runtime = (
        files if isinstance(files, RuntimeFiles) else RuntimeFiles((), [Path(f) for f in files])
    )
    if not runtime.files:
        return
    linked: dict[Identity, str] | None = None  # read only when a shortcut needs it

    def linked_now() -> dict[Identity, str]:
        nonlocal linked
        if linked is None:
            linked = runtime.linked_now()
        return linked

    rescanned = False
    seen = 0
    for i, p in enumerate(placed):
        if not p.writable:
            continue

        def unsearchable(error: OSError, i: int = i, p: Placed = p) -> None:
            if not linked_now():
                return  # no runtime file has a second name, here or anywhere
            raise ValueError(
                f"grants[{i}] at {p.guest}: the runner cannot search {error.filename} for "
                f"second names of the runtime's files ({error.strerror}), and one of them has "
                "more than one name; grant a folder the runner can read, or reinstall with "
                "UV_LINK_MODE=copy"
            )

        for dirpath, dirnames, filenames in os.walk(p.host, onerror=unsearchable):
            # Never into a link, a junction or another reparse point: they lead out of the grant,
            # where the guest cannot follow either.
            dirnames[:] = [d for d in dirnames if not _redirects(os.path.join(dirpath, d))]
            seen += len(dirnames) + len(filenames)
            if seen > len(runtime.files) and not linked_now():
                return  # no runtime file has a second name: none is in any grant
            if seen > MAX_WALK:
                if not linked_now():
                    return  # past the walk's bound, but there is nothing to find
                some = next(iter(linked_now().values()))
                raise ValueError(
                    f"grants[{i}] at {p.guest}: {p.host} is too large to walk ({MAX_WALK} "
                    f"entries) for second names of the runtime's files, and {some} has more "
                    "than one name (a hard link, as uv installs by default); grant a smaller "
                    "folder, or reinstall with UV_LINK_MODE=copy"
                )
            for name in filenames:
                try:
                    st = os.lstat(os.path.join(dirpath, name))
                except OSError:
                    continue
                if st.st_nlink < 2:
                    continue  # one name: not a second name of anything
                identity = (st.st_dev, st.st_ino)
                if identity not in runtime.files:
                    continue
                target = runtime.holds(identity)
                if target is None and not rescanned:
                    # The scan names this identity under a path that no longer holds it: the
                    # runtime changed since. Scan again, once, and look again.
                    runtime = RuntimeFiles(runtime.tops, runtime.singles)
                    if isinstance(files, RuntimeFiles):
                        _keep(runtime)
                    rescanned, linked = True, None
                    target = runtime.holds(identity)
                if target is not None:
                    raise ValueError(
                        f"grants[{i}] at {p.guest}: {os.path.join(dirpath, name)} is a hard link "
                        f"to the runtime's {target}, which a guest could rewrite through it; "
                        "grant it read-only, or reinstall with UV_LINK_MODE=copy"
                    )


def _redirects(path: str) -> bool:
    """Whether a folder entry leads elsewhere: a symbolic link, a Windows junction, or any other
    reparse point."""
    try:
        st = os.lstat(path)
    except OSError:
        return True  # gone or unreadable: not entered
    if stat.S_ISLNK(st.st_mode):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None and isjunction(path):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


# Every folder this process has granted writable, by identity, with its chain, for the process's
# life: a guest may have left anything there, so no module cache is ever loaded at, inside or
# above one, even after the call ended.
_ever_writable: dict[Identity, tuple[str, tuple[Identity, ...]]] = {}  # with its path
EVER_WRITABLE_MAX = 10_000  # entries; about 1.2 KB each, so about 12 MB at most
_ever_overflowed = False  # past the bound: no module cache is used for the process's life


def _bound_ever_writable() -> None:
    """Under `_live_lock`: past EVER_WRITABLE_MAX entries, those whose path no longer names that
    folder are dropped (a folder that is gone holds no .cwasm); if that is not enough, the disk
    cache is turned off for the process, which is safe, and the set freed."""
    global _ever_overflowed
    if len(_ever_writable) <= EVER_WRITABLE_MAX:
        return
    for identity, (path, _) in list(_ever_writable.items()):
        try:
            st = os.stat(path)
            if (st.st_dev, st.st_ino) == identity:
                continue
        except OSError:
            pass
        del _ever_writable[identity]
    if len(_ever_writable) > EVER_WRITABLE_MAX:
        _ever_overflowed = True
        _ever_writable.clear()


def granted_writable(path: Path) -> bool:
    """Whether `path` (or its deepest existing ancestor) is at, inside or above a folder this
    process has granted writable; always, once the registry has passed its bound."""
    mine = chain(path)
    me = _exists(path)
    with _live_lock:
        if _ever_overflowed:
            return True
        ever = [(w, w_chain) for w, (_, w_chain) in _ever_writable.items()]
    return any(w in mine or (me is not None and me in w_chain) for w, w_chain in ever)


def _judge_cwd(cwd: str | None, placed: Sequence[Placed]) -> str | None:
    if cwd is None:
        return None
    serving = [p for p in placed if cwd == p.guest or cwd.startswith(p.guest + "/")]
    if not serving:
        raise ValueError(f"cwd {cwd} is not inside a grant; name a grant's guest path or below")
    p = max(serving, key=lambda s: len(s.guest))  # wasi-libc picks the longest prefix too
    rest = cwd[len(p.guest) :].lstrip("/")
    try:
        host = resolve("cwd", p.host / rest) if rest else p.host
    except ValueError as e:
        raise ValueError(str(e).replace("the host folder", f"the folder of cwd {cwd},")) from None
    if _identity(p.host) not in chain(host):
        raise ValueError(f"cwd {cwd}: its folder {host} is not inside its grant's {p.host}")
    return cwd
