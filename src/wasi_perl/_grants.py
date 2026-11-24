"""Grants: `Grant`, the checks of each guest path, the walk of each host path, and the rules that
keep the runtime's own files out of reach. Adapted from wasi-python's typed grants
(`_places.py`, `_platform_posix.py`'s walk; same owner, Apache-2.0), trimmed: the walk resolves
a path link by link and passes wasmtime the resolved path; it holds no descriptor and runs no
post-preopen identity check (the README's "Not protected" says so).

Comparisons are by identity, (st_dev, st_ino) of a folder and of each of its ancestors, never
by spelling, so `~/LIBRARY/CACHES` on a case-insensitive disk is `~/Library/Caches`."""

from __future__ import annotations

import itertools
import os
import stat
import threading
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
_live: dict[int, tuple[tuple[str, Placed], ...]] = {}
_tokens = itertools.count(1)


def live_count() -> int:
    with _live_lock:
        return len(_live)


def hold(placed: Sequence[Placed]) -> int:
    """Register a starting call's grants, once each is checked against every live call's: a grant
    at, inside or above a live writable grant is refused, and a writable grant at, inside or above
    any live grant. The check and the registration are one step under the lock, so of two calls
    that start at once, the second sees the first. Returns the token `let_go` takes."""
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
        token = next(_tokens)
        _live[token] = tuple((f"grant at {p.guest}", p) for p in placed)
        return token


def let_go(token: int) -> None:
    with _live_lock:
        _live.pop(token, None)


def judge(
    grants: Sequence[Grant],
    cwd: str | None,
    *,
    caches: Iterable[Path],
    runtime: Iterable[Path],
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
