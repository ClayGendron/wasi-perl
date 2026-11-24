"""The module cache on disk: where it is, whether a folder or a `.cwasm` is as the runner makes
it, and the load and the store. Adapted from wasi-python's `_engines.py` (`_cache_given`,
`_caches`, `_load`, `_store`) and the cache checks of its `_platform_posix.py` and
`_platform_windows.py` (same owner, Apache-2.0), with `open_cached` and `_fd_name` of its
`_platform_posix.py`, trimmed: a `.cwasm` is checked through its open descriptor, and wasmtime
maps it by that descriptor's own name, so a file put at the path after the check is not the one
loaded; where the system has no such name, its bytes are read through the descriptor.

A `.cwasm` is native code that wasmtime runs as it finds it, so it is used only as the runner
makes it. On POSIX: a folder at 0700 and files at 0600 with one name, owned by this user, on a
way of folders no other user can change. On Windows, which has no mode bits: a folder and a
file with one name, neither a link nor a junction. Anything else is not used, with a warning,
and the module is compiled in the process; a refused file is replaced by the runner's own. That
stops a file someone else put there; the guard against a guest is that no grant reaches any
cache, and that no cache is used at, inside or above a folder this process granted writable.

On Windows no owner is compared and no access list read: the check trusts the folder's access
list to keep other users out, as `%LOCALAPPDATA%`'s does, and the README says to keep the cache
in such a folder."""

from __future__ import annotations

import errno
import os
import stat
import sys
import tempfile
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

CACHE_ENV = "WASI_PERL_CACHE"
WINDOWS = os.name == "nt"
_DIR_OPEN = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
# Non-blocking, so a FIFO put at a .cwasm's name never holds the open.
_FILE_OPEN = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
_FILE_OPEN |= getattr(os, "O_BINARY", 0)
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
# wasmtime maps a compiled module from a file it opens by name. It is given a name of the
# descriptor the runner checked, which opens that very file whatever is at the path by then:
# Linux's /proc/self/fd/N reopens it, and macOS's /dev/fd/N duplicates the descriptor. Elsewhere,
# Windows included, there is no such name, and the bytes are read through the descriptor.
_FD_FOLDER = {"linux": "/proc/self/fd", "darwin": "/dev/fd"}.get(sys.platform)
_NAME_OPEN = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)


class Cache(NamedTuple):
    """The cache as a call names it, resolved once when the call is made: `folder`, the one this
    call uses, or None; `named`, the one `$WASI_PERL_CACHE` names, or None."""

    folder: Path | None
    named: Path | None


def _home() -> Path | None:
    try:
        return Path.home()
    except (RuntimeError, KeyError, OSError):
        return None


def default_dir(home: Path | None = None) -> Path | None:
    """The platform's default cache folder, or None where there is no home."""
    home = home or _home()
    if home is None:
        return None
    if WINDOWS:
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else home / "AppData" / "Local") / "wasi_perl" / "cache"
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / "wasi_perl"
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else home / ".cache") / "wasi_perl"


def default_dirs() -> tuple[Path, ...]:
    """Every default a process of this user could compute: with and without the variable the
    platform reads, under `$HOME` and under the home the password database gives this user."""
    homes = [h for h in (_home(),) if h is not None]
    if not WINDOWS:
        try:
            import pwd

            homes.append(Path(pwd.getpwuid(os.geteuid()).pw_dir))
        except (ImportError, KeyError, AttributeError):
            pass
    found: list[Path] = []
    for home in homes:
        found.append(default_dir(home))  # type: ignore[arg-type]
        if WINDOWS:
            found.append(home / "AppData" / "Local" / "wasi_perl" / "cache")
        elif sys.platform != "darwin":
            found.append(home / ".cache" / "wasi_perl")
    return tuple(dict.fromkeys(found))


def _absolute(path: str) -> Path:
    """Absolute as the system reads it: joined to the working folder, `..` left for the walk."""
    return Path(path if os.path.isabs(path) else os.path.join(os.getcwd(), path))


def given(value: Any) -> Cache:
    """`cache_dir` checked and resolved when the call is made. `None` and `True` defer to
    `$WASI_PERL_CACHE`, else the platform default; `False` keeps none on disk; an empty string
    names no folder and is refused."""
    env = os.environ.get(CACHE_ENV)
    named = _absolute(env) if env else None
    if value is False:
        return Cache(None, named)
    if value is None or value is True:
        return Cache(named if named is not None else default_dir(), named)
    if isinstance(value, bytes) or not isinstance(value, (str, os.PathLike)):
        raise TypeError(f"cache_dir must be a folder, True, False or None; got {value!r}")
    path = os.fspath(value)
    if not isinstance(path, str):
        raise TypeError(f"cache_dir must be a str path; got {type(path).__name__}")
    if path == "":
        raise ValueError("cache_dir must be a folder, True, False or None; '' names none")
    return Cache(_absolute(path), named)


def every(chosen: Cache) -> tuple[Path, ...]:
    """Every cache a call of this user could load native code from: this call's, the one the
    variable names, and every platform default. No grant may reach any of them."""
    found = [p for p in (chosen.folder, chosen.named, *default_dirs()) if p is not None]
    return tuple(dict.fromkeys(found))


def _warn(path: Path, must: str) -> None:
    warnings.warn(
        f"wasi_perl: not using the module cache at {path}: {must}", RuntimeWarning, stacklevel=5
    )


def _uid() -> int | None:
    return None if WINDOWS else os.geteuid()


def _as_made(st: os.stat_result, *, is_dir: bool) -> str | None:
    """Why `st` is not as the runner makes it, or None when it is."""
    if WINDOWS:
        if getattr(st, "st_file_attributes", 0) & _REPARSE:
            return "it must be no link or junction"
        if is_dir:
            return None if stat.S_ISDIR(st.st_mode) else "it must be a folder"
        return None if stat.S_ISREG(st.st_mode) and st.st_nlink == 1 else "a file, one name"
    how = "owned by this user that no one else may read, write or enter"
    if is_dir:
        ok = stat.S_ISDIR(st.st_mode)
        must = f"it must be a directory {how} (mode 0700)"
    else:
        ok = stat.S_ISREG(st.st_mode) and st.st_nlink == 1
        must = f"it must be a file {how} (mode 0600), with one name"
    if ok and st.st_uid == _uid() and not st.st_mode & 0o077:
        return None
    return must


def _way(folder: Path) -> None:
    """Every folder from / to `folder`, links resolved, is this user's or root's and closed to
    other writers unless sticky (as /tmp is); else whoever could write one could put another
    folder at the cache's name between the check and the load. OSError names the one that fails.
    Windows has no mode bits, and there the grant rule is the guard."""
    if WINDOWS:
        return
    real = Path(os.path.realpath(folder))
    for step in (real, *real.parents):
        st = os.lstat(step)
        sticky = st.st_mode & stat.S_ISVTX
        if st.st_uid not in (0, _uid()) or (st.st_mode & 0o022 and not sticky):
            raise PermissionError(
                errno.EPERM, "a folder on the way to it is one another user could change",
                str(step),
            )  # fmt: skip


def folder(cache: Path) -> Path | None:
    """The cache folder, made at 0700 if missing, or None (with a warning) when it or the way to
    it is not as the runner makes it."""
    from wasi_perl import _grants

    if _grants.granted_writable(cache):
        _warn(
            cache,
            "it is at, inside or above a folder this process granted writable to a guest, which "
            "may have left a .cwasm there",
        )
        return None
    try:
        existing = cache.parent
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        _way(existing)  # before anything is made there
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        _way(cache.parent)  # again, with what was made
        before = os.lstat(cache)
        why = _as_made(before, is_dir=True)
        if why is None and not WINDOWS:
            fd = os.open(cache, _DIR_OPEN)
            try:
                st = os.fstat(fd)
                if (st.st_dev, st.st_ino) != (before.st_dev, before.st_ino):
                    why = "it changed while it was checked"
                else:
                    why = _as_made(st, is_dir=True)
            finally:
                os.close(fd)
    except OSError as e:
        _warn(cache, e.strerror or str(e))
        return None
    if why is not None:
        _warn(cache, why)
        return None
    return cache


def _stamp(st: os.stat_result) -> tuple[int, int, int, int]:
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


def _read_all(fd: int, size: int) -> bytes:
    parts = []
    while True:
        block = os.read(fd, max(size, 1 << 20))
        if not block:
            return b"".join(parts)
        parts.append(block)


def _open_checked(cwasm: Path) -> tuple[int | None, bool]:
    """A descriptor of a `.cwasm` that is as the runner makes it, checked through itself, so a
    file put at the name after the check is not the file used; and whether the runner may put
    its own file at that name (when there is none, or the one there is refused, but never over a
    folder). The caller closes the descriptor."""
    try:
        before = os.lstat(cwasm)
    except FileNotFoundError:
        return None, True
    except OSError:
        return None, False
    why = _as_made(before, is_dir=False)
    if why is not None:
        _warn(cwasm, why)
        return None, not stat.S_ISDIR(before.st_mode)
    try:
        if WINDOWS:
            fd = os.open(cwasm, _FILE_OPEN)
        else:
            dir_fd = os.open(cwasm.parent, _DIR_OPEN)
            try:
                # The folder the file is opened in is judged as `folder` judged it, by its open
                # descriptor, so a folder swapped in since is not read from.
                if _as_made(os.fstat(dir_fd), is_dir=True) is not None:
                    return None, False
                fd = os.open(cwasm.name, _FILE_OPEN, dir_fd=dir_fd)
            finally:
                os.close(dir_fd)
    except OSError:
        return None, True
    try:
        st = os.fstat(fd)
        ok = _stamp(st) == _stamp(before) and _as_made(st, is_dir=False) is None
    except OSError:
        ok = False
    if not ok:
        os.close(fd)
        return None, True  # another file since its name was read
    return fd, False


def _fd_name(fd: int) -> str | None:
    """A name that opens the very file open at `fd`, whatever is at its path by then, checked by
    opening it; None where the system has no such name, or it opens another file."""
    if _FD_FOLDER is None:
        return None
    name = f"{_FD_FOLDER}/{fd}"
    try:
        held = os.fstat(fd)
        other = os.open(name, _NAME_OPEN)
        try:
            named = os.fstat(other)
        finally:
            os.close(other)
    except OSError:
        return None
    return name if (named.st_dev, named.st_ino) == (held.st_dev, held.st_ino) else None


def load(
    cwasm: Path, by_name: Callable[[str], Any], by_bytes: Callable[[bytes], Any]
) -> tuple[Any, bool]:
    """The module a `.cwasm` that is as the runner makes it holds, made by `by_name` from a name
    of the descriptor that was checked, so wasmtime maps the checked file and copies none of it;
    where there is no such name, by `by_bytes` from the bytes read through that descriptor. And
    whether the runner may put its own file at that name. A mapped file stays the checked one for
    the module's life: the runner never writes a `.cwasm` in place, since `write` renames a new
    file over the name, which leaves a mapped one as it was. An error of wasmtime's is raised."""
    fd, may_write = _open_checked(cwasm)
    if fd is None:
        return None, may_write
    try:
        name = _fd_name(fd)
        if name is not None:
            return by_name(name), False
        return by_bytes(_read_all(fd, os.fstat(fd).st_size)), False
    except OSError:
        return None, True
    finally:
        os.close(fd)


def write(data: bytes | bytearray, cwasm: Path) -> bool:
    """The runner's own `.cwasm`: made 0600 under another name and renamed into place, which
    replaces a refused file or link itself, never what a link names. A cache that cannot take it
    warns and the call goes on."""
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".cwasm-", dir=cwasm.parent)  # created 0600
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, cwasm)
    except OSError as e:
        if tmp is not None:
            Path(tmp).unlink(missing_ok=True)
        _warn(cwasm, f"it cannot be written ({e.strerror or e})")
        return False
    except BaseException:
        if tmp is not None:
            Path(tmp).unlink(missing_ok=True)
        raise
    return True
