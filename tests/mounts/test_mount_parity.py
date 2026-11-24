"""A mount answers raw preview1 calls as a grant does: each shadowed call on a provider file, a
provider folder and a mount's preopen, against the same call on a read-only grant of the same
tree, and every call that can write against a writable grant, with what differs by host or by
design masked, and wasi-python's deliberate differences asserted as decided. Nothing here needs
Perl, so it runs on every CI system; nothing is timed.

A port of wasi-python's `tests/unit/test_runner_grants.py` at f9bf678 (same owner,
Apache-2.0), its parity half, from `HOST_ROWS` to
`test_the_write_parity_asserts_each_systems_grant`, test for test and name for name; its grant
rules half tests wasi-python's own walk, which wasi-perl does not have."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from mounts import raw as files

POSIX = os.name == "posix"


HOST_ROWS = {
    "file fd_advise",
    "file fd_write",
    "file fd_pwrite",
    "file fd_datasync",
    "file fd_sync",
    "open 'a.txt\\x00'",
    "open 'a.txt/'",
    "open 'a.txt/.'",
    "open 'x' * 256",
    "pstat 'a.txt/x'",
    "pstat 'a.txt/' following links",
    "pstat 'a.txt/.' following links",
    "pstat 'a\\x00'",
    "pstat 'x' * 256",
    "readlink 'a.txt'",
    "readlink 'd'",
    "readlink 'a.txt/'",
}
# Rows whose grant answer differs between POSIX systems, compared only on the systems named
# (the landing notes, "Revised after landing", second part). A read whose end passes 2**63 - 1
# is EINVAL on Linux, the reference, and the mount answers so everywhere; macOS answers 0 (and
# fd_pread counts at most 64 KiB of the end). A stat of `a.txt/` or `a.txt/.` without
# SYMLINK_FOLLOW (`os.lstat`) is ENOTDIR on macOS and on the mount, as an open of either is on
# every system and as the same stat following links is on Linux too; Linux's grant answers the
# file's stat to the no-follow stat alone, a deliberate difference in Decision 6.
SYSTEM_ROWS = {
    "pread at 2**63 - 1": {"linux"},
    "read at 2**63 - 1": {"linux"},
    "pstat 'a.txt/'": {"darwin"},
    "pstat 'a.txt/.'": {"darwin"},
    "pread 100,000 at 2**63 - 65,536": {"linux"},
    "pread 100,000 at 2**63 - 65,537": {"linux"},
    "read 100,000 at 2**63 - 100,000": {"linux"},
    "read 100,000 at 2**63 - 100,001": {"linux"},
}
# A pread of nothing at an offset of 2**63 or more: a deliberate difference. The mount answers
# EINVAL, which is what the offset is, on every system. wasmtime 51's grant answers EINTR, from
# its own early return for a read of no bytes before any host call; an EINTR invites a guest to
# retry the same call forever. Both are asserted in the test of the deliberate differences.
PAST_I64_ROWS = {
    f"pread {what} at {at}" for what in ("nothing", "an empty buffer") for at in (2**63, -1)
}
GRANT_PAST_I64 = 27  # EINTR


def _parity_script(r: files.Raw) -> dict[str, object]:
    """Every row on one side. The descriptors a row opens are named, never numbered."""
    out: dict[str, object] = {}

    def row(label: str, fn, *args) -> None:
        try:
            out[label] = fn(*args)
        except files.Ends as e:
            out[label] = ("ends", e.message)

    def opened(label: str, *args, **kw) -> int | None:
        try:
            errno, fd = r.open(*args, **kw)
        except files.Ends as e:
            out[label] = ("ends", e.message)
            return None
        out[label] = (errno, fd is not None)
        if fd is not None and label.startswith("open "):
            r("fd_close", fd)
        return fd

    P, W = 3, 4
    F = opened("the file F", P, "a.txt")
    D = opened("the folder D", P, "d", files.O_DIRECTORY)
    E = opened("the folder E (no O_DIRECTORY)", P, "e")
    assert F is not None and D is not None and E is not None, out
    for kind, T in (("file", F), ("dir", D), ("plain dir", E), ("preopen", P)):
        row(f"{kind} fd_advise", r, "fd_advise", T, 0, 0, 0)
        row(f"{kind} fd_advise 6", r, "fd_advise", T, 0, 0, 6)
        row(f"{kind} fd_advise -1", r, "fd_advise", T, 0, 0, -1)
        row(f"{kind} fd_allocate", r, "fd_allocate", T, 0, 10)
        row(f"{kind} fd_datasync", r, "fd_datasync", T)
        row(f"{kind} fd_sync", r, "fd_sync", T)
        row(f"{kind} fd_fdstat_get", r.fdstat, T)
        for flags in (0, files.APPEND, files.DSYNC, files.NONBLOCK, files.RSYNC, files.SYNC):
            row(f"{kind} set_flags {flags}", r, "fd_fdstat_set_flags", T, flags)
        row(f"{kind} set_flags unknown bit", r, "fd_fdstat_set_flags", T, 32)
        row(f"{kind} set_flags out of range", r, "fd_fdstat_set_flags", T, 1 << 16)
        row(f"{kind} fdstat after flags", r.fdstat, T)
        row(f"{kind} set_flags back", r, "fd_fdstat_set_flags", T, 0)
        row(f"{kind} set_rights", r, "fd_fdstat_set_rights", T, 0, 0)
        row(f"{kind} set_rights unknown bit", r, "fd_fdstat_set_rights", T, 1 << 30, 0)
        row(f"{kind} fd_filestat_get", r.filestat, T)
        row(f"{kind} set_size", r, "fd_filestat_set_size", T, 0)
        for flags in (0, 1, 2, 3, 12):
            row(f"{kind} set_times {flags}", r, "fd_filestat_set_times", T, 0, 0, flags)
        row(f"{kind} set_times unknown bit", r, "fd_filestat_set_times", T, 0, 0, 16)
        row(f"{kind} fd_write", r.write, T, b"zz")
        row(f"{kind} fd_pwrite", r.write, T, b"zz", 0)
        row(f"{kind} fd_tell", r.tell, T)
        row(f"{kind} fd_seek", r.seek, T, 0, 0)
        row(f"{kind} fd_read", r.read, T, [3])
        row(f"{kind} fd_pread", r.pread, T, [3], 1)
        if kind != "preopen":  # the preopen's listing order is the host's: compared by name
            row(f"{kind} fd_readdir", r.readdir, T)
    row("preopen fd_readdir names", r.readdir_names, P)
    # Seeks and reads on the file, in order: whence 3, an overflow, past the end, past i64.
    for offset, whence in [
        (0, 0), (3, 0), (1, 1), (-100, 1), (0, 2), (-1, 2), (5, 3), (-1, 0), (5, 256), (5, -1),
    ]:  # fmt: skip
        row(f"seek {offset} {whence}", r.seek, F, offset, whence)
    row("seek to 0", r.seek, F, 0, 0)
    row("read 3", r.read, F, [3])
    row("read two iovecs, the first empty", r.read, F, [0, 3, 3])
    row("read no iovec", r.read, F, [])
    row("read one empty iovec", r.read, F, [0])
    row("read the rest", r.read, F, [100])
    row("read at the end", r.read, F, [10])
    row("pread past the end", r.pread, F, [4], 1000)
    row("pread at 2**63 - 1", r.pread, F, [4], 2**63 - 1)
    row("pread at 2**64 - 1", r.pread, F, [4], -1)
    row("seek past then read", lambda: (r.seek(F, 100, 0), r.read(F, [4]), r.tell(F)))
    row("seek from the end", lambda: (r.seek(F, -2, 2), r.read(F, [4])))
    row("seek to 2**63 - 1", r.seek, F, 2**63 - 1, 0)
    row("read at 2**63 - 1", r.read, F, [4])
    row("seek to 2**63", r.seek, F, 1, 1)
    row("read at 2**63", r.read, F, [4])
    row("seek to 2**64 - 1", r.seek, F, 2**63 - 2, 1)
    row("seek past 2**64 - 1", r.seek, F, 1, 1)
    row("tell after it", r.tell, F)
    row("seek back", r.seek, F, 0, 0)
    # Nothing to read at an offset of 2**63 or more: still EINVAL, on every system.
    for at in (2**63, -1):  # -1 is 2**64 - 1 as an i64
        row(f"pread nothing at {at}", r.pread, F, [], at)
        row(f"pread an empty buffer at {at}", r.pread, F, [0], at)
    row("seek to 2**63 for nothing", lambda: (r.seek(F, 2**63 - 1, 0), r.seek(F, 1, 1)))
    row("read nothing at 2**63", r.read, F, [])
    row("read an empty buffer at 2**63", r.read, F, [0])
    # fd_pread counts at most 64 KiB of a read's end (wasmtime's MAX_READ_SIZE_ALLOC); fd_read
    # counts all of it (Linux only, SYSTEM_ROWS).
    top = 2**63 - 1
    row("pread 100,000 at 2**63 - 65,536", r.pread, F, [100_000], top - 65_535)
    row("pread 100,000 at 2**63 - 65,537", r.pread, F, [100_000], top - 65_536)
    row(
        "read 100,000 at 2**63 - 100,000",
        lambda: (r.seek(F, top - 99_999, 0), r.read(F, [100_000])),
    )
    row(
        "read 100,000 at 2**63 - 100,001",
        lambda: (r.seek(F, top - 100_000, 0), r.read(F, [100_000])),
    )
    row("seek back again", r.seek, F, 0, 0)
    # fd_readdir on the one-entry folder: cookies and short buffers, byte for byte.
    for size, cookie in [(4096, 0), (4096, 1), (4096, 2), (4096, 3), (4096, 10), (10, 0),
                         (24, 0), (25, 0), (30, 0), (50, 0), (0, 0), (4096, -1)]:  # fmt: skip
        row(f"readdir {size} {cookie}", r.readdir, D, size, cookie)
    # path_open from the preopen and from the folder.
    R, ALL = files.READ_RIGHTS, files.RIGHTS_ALL
    for label, base, path, oflags, rights, fdflags, dirflags in [
        ("'a.txt'", P, "a.txt", 0, R, 0, 0),
        ("''", P, "", 0, R, 0, 0),
        ("'/a.txt'", P, "/a.txt", 0, R, 0, 0),
        ("'a.txt\\x00'", P, b"a.txt\0", 0, R, 0, 0),
        ("'\\xff'", P, b"\xff", 0, R, 0, 0),
        ("'a.txt/'", P, "a.txt/", 0, R, 0, 0),
        ("'a.txt/.'", P, "a.txt/.", 0, R, 0, 0),
        ("'d/../a.txt'", P, "d/../a.txt", 0, R, 0, 0),
        ("'..'", P, "..", 0, R, 0, 0),
        ("'d/../../m/a.txt'", P, "d/../../m/a.txt", 0, R, 0, 0),
        ("'x' * 256", P, "x" * 256, 0, R, 0, 0),
        ("'x' * 255", P, "x" * 255, 0, R, 0, 0),
        ("'.'", P, ".", 0, R, 0, 0),
        ("'./a.txt'", P, "./a.txt", 0, R, 0, 0),
        ("'d/'", P, "d/", 0, R, 0, 0),
        ("'d/.'", P, "d/.", 0, R, 0, 0),
        ("'d//x.txt'", P, "d//x.txt", 0, R, 0, 0),
        ("'missing'", P, "missing", 0, R, 0, 0),
        ("O_DIRECTORY on a file", P, "a.txt", files.O_DIRECTORY, R, 0, 0),
        ("O_DIRECTORY on nothing", P, "missing", files.O_DIRECTORY, R, 0, 0),
        ("O_DIRECTORY on a folder", P, "d", files.O_DIRECTORY, R, 0, 0),
        ("for writing", P, "a.txt", 0, ALL, 0, 0),
        ("a folder for writing", P, "d", 0, ALL, 0, 0),
        ("truncating", P, "a.txt", files.O_TRUNC, R, 0, 0),
        ("creating", P, "new", files.O_CREAT, R, 0, 0),
        ("creating exclusively", P, "a.txt", files.O_CREAT | files.O_EXCL, R, 0, 0),
        ("exclusive alone", P, "a.txt", files.O_EXCL, R, 0, 0),
        ("creating under nothing", P, "missing/new", files.O_CREAT, R, 0, 0),
        ("O_DIRECTORY and O_CREAT", P, "d", files.O_DIRECTORY | files.O_CREAT, R, 0, 0),
        ("appending (read-only)", P, "a.txt", 0, R, files.APPEND, 0),
        ("non-blocking", P, "a.txt", 0, R, files.NONBLOCK, 0),
        ("SYNC", P, "a.txt", 0, R, files.SYNC, 0),
        ("DSYNC", P, "a.txt", 0, R, files.DSYNC, 0),
        ("no rights", P, "a.txt", 0, 0, 0, 0),
        ("the top right", P, "a.txt", 0, 1 << 29, 0, 0),
        ("following links", P, "a.txt", 0, R, 0, 1),
        ("unknown oflags", P, "a.txt", 16, R, 0, 0),
        ("oflags out of range", P, "a.txt", 1 << 16, R, 0, 0),
        ("unknown rights", P, "a.txt", 0, 1 << 30, 0, 0),
        ("unknown fdflags", P, "a.txt", 0, R, 32, 0),
        ("unknown lookup flags", P, "a.txt", 0, R, 0, 2),
        ("lookup flags out of range", P, "a.txt", 0, R, 0, -1),
        ("from the folder", D, "x.txt", 0, R, 0, 0),
        ("from the folder, '..'", D, "..", 0, R, 0, 0),
        ("from the folder, '../a.txt'", D, "../a.txt", 0, R, 0, 0),
        ("from the folder, '.'", D, ".", 0, R, 0, 0),
        ("from the file", F, "a.txt", 0, R, 0, 0),
        ("from the file, creating", F, "x", files.O_CREAT, R, 0, 0),
        ("from the file, not UTF-8", F, b"\xff", 0, R, 0, 0),
        ("from nothing", 99, "a.txt", 0, R, 0, 0),
    ]:
        opened(f"open {label}", base, path, oflags, rights, fdflags, dirflags)
    for label, base, path in [
        ("'a.txt'", P, "a.txt"), ("'d'", P, "d"), ("'missing'", P, "missing"),
        ("'a.txt/'", P, "a.txt/"), ("'a.txt/.'", P, "a.txt/."), ("''", P, ""),
        ("'/a.txt'", P, "/a.txt"), ("'..'", P, ".."), ("'.'", P, "."), ("'d/..'", P, "d/.."),
        ("'d/../../m'", P, "d/../../m"), ("'\\xff'", P, b"\xff"), ("'a\\x00'", P, b"a\0"),
        ("'x' * 256", P, "x" * 256), ("'d/x.txt'", P, "d/x.txt"), ("'a.txt/x'", P, "a.txt/x"),
        ("from the folder", D, "x.txt"), ("from the file", F, "a.txt"),
    ]:  # fmt: skip
        row(f"pstat {label}", r.path_filestat, base, path)
    row("pstat following links", r.path_filestat, P, "a.txt", 1)
    row("pstat 'a.txt/' following links", r.path_filestat, P, "a.txt/", 1)
    row("pstat 'a.txt/.' following links", r.path_filestat, P, "a.txt/.", 1)
    row("pstat unknown lookup flags", r.path_filestat, P, "a.txt", 2)
    for label, base, path in [
        ("'a.txt'", P, "a.txt"), ("'d'", P, "d"), ("'missing'", P, "missing"), ("''", P, ""),
        ("'/a.txt'", P, "/a.txt"), ("'..'", P, ".."), ("'a.txt/'", P, "a.txt/"),
        ("from the file", F, "a.txt"),
    ]:  # fmt: skip
        row(f"readlink {label}", r.readlink, base, path)
    # Every write-shaped path call: EPERM on a read-only grant, before any provider call.
    for name in ("path_create_directory", "path_remove_directory", "path_unlink_file"):
        for path in ("new", "d", "a.txt", "", "/x", "..", b"\xff", b"a\0", "missing/x", "e"):
            row(f"{name} {path!r}", r.path_call, name, P, path)
        row(f"{name} from the file", r.path_call, name, F, "x")
        row(f"{name} from the folder", r.path_call, name, D, "x.txt")
    row("rename within", r.rename, P, "a.txt", P, "c.txt")
    row("rename nothing", r.rename, P, "missing", P, "c.txt")
    row("rename from the file", r.rename, F, "a.txt", P, "c.txt")
    row("rename to the file", r.rename, P, "a.txt", F, "c.txt")
    row("rename to nothing", r.rename, P, "a.txt", 99, "c.txt")
    row("rename to a writable grant", r.rename, P, "a.txt", W, "c.txt")
    row("rename from a writable grant", r.rename, W, "w.txt", P, "c.txt")
    row("rename not UTF-8", r.rename, P, "a.txt", P, "\udcff".encode("utf-8", "surrogatepass"))
    row("link within", r.link, P, "a.txt", P, "c.txt")
    row("link following links", r.link, P, "a.txt", P, "c.txt", 1)
    row("link unknown flags", r.link, P, "a.txt", P, "c.txt", 2)
    row("link to nothing", r.link, P, "a.txt", 99, "c.txt")
    row("link to a writable grant", r.link, P, "a.txt", W, "c.txt")
    row("link from a writable grant", r.link, W, "w.txt", P, "c.txt")
    row("symlink", r.symlink, "a.txt", P, "l")
    row("symlink from the file", r.symlink, "a.txt", F, "l")
    for flags in (0, 1, 3, 4, 12):
        row(f"path set_times {flags}", r.path_set_times, P, "a.txt", flags)
    row("path set_times unknown bit", r.path_set_times, P, "a.txt", 16)
    row("path set_times from the file", r.path_set_times, F, "a.txt", 0)
    row("path set_times unknown lookup flags", r.path_set_times, P, "a.txt", 0, 2)
    # The forward-safe calls on a mount's preopen: wasmtime's answers, as for a grant's.
    row("prestat of the preopen", r.prestat, P)
    row(
        "prestat name of the preopen",
        lambda: (r("fd_prestat_dir_name", P, files.BUFFER, 2), r.get(files.BUFFER, 2)),
    )
    row("sock_shutdown on the preopen", r, "sock_shutdown", P, 3)
    # Closing: the file twice, the folders, then the preopen itself.
    row("close the file", r, "fd_close", F)
    row("close the file again", r, "fd_close", F)
    row("fstat after close", r.filestat, F)
    row("close the folder", r, "fd_close", D)
    row("close the plain folder", r, "fd_close", E)
    row("close the preopen", r, "fd_close", P)
    row("open from the closed preopen", r.open, P, "a.txt")
    row("prestat of the closed preopen", r, "fd_prestat_get", P, files.RESULT)
    return out


def _two_sides(tmp_path: Path) -> tuple[files.Raw, files.Mounted]:
    tree = files.write_tree(tmp_path / "tree")
    placeholder = tmp_path / "placeholder"
    placeholder.mkdir()
    sides = []
    for name in ("w-grant", "w-mount"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "w.txt").write_bytes(b"w\n")
        sides.append(tmp_path / name)
    # A grant as wasmtime serves it: every call wasmtime's own.
    grant = files.grant_raw([(tree, "/m", False), (sides[0], "/w", True)])
    mount = files.Mounted(files.MemoryProvider(), placeholder, folders=[(sides[1], "/w", True)])
    return grant, mount


def test_raw_calls_answer_as_a_grant_does(tmp_path) -> None:
    """Each of the 29 shadowed calls on a provider file, folder and
    preopen answers as on a read-only grant's (errno and every byte written), but for Decision
    6's deliberate differences, asserted in the next test."""
    grant, mount = _two_sides(tmp_path)
    want, got = _parity_script(grant), _parity_script(mount.raw)
    assert list(want) == list(got)
    differ = {
        label: (want[label], got[label])
        for label in want
        if want[label] != got[label]
        and label not in PAST_I64_ROWS
        and (POSIX or label not in HOST_ROWS)
        and sys.platform in SYSTEM_ROWS.get(label, {sys.platform})
    }
    assert differ == {}, differ
    assert sorted(p.name for p in (tmp_path / "w-mount").iterdir()) == ["w.txt"]
    assert sorted(p.name for p in (tmp_path / "tree").iterdir()) == ["a.txt", "b.txt", "d", "e"]


def test_the_deliberate_differences_are_as_decided(tmp_path) -> None:
    """Where a mount answers otherwise than a read-only grant,
    each answer is the one the table decides (and the grant's is asserted where it does not
    depend on the host, or per system where it does)."""
    grant, mount = _two_sides(tmp_path)
    g, m = grant, mount.raw

    def both(fn) -> tuple[object, object]:
        out = []
        for side in (g, m):
            try:
                out.append(fn(side))
            except files.Ends as e:
                out.append(("ends", e.message))
        return out[0], out[1]

    # `..` resolved by text: one provider call on one normalized path. A POSIX grant walks each
    # part; a Windows grant resolves `..` by text as the mount does: cap-std's own Windows open
    # pops the name before each `..` before any lookup (READ, cap-primitives 4.0.3,
    # `src/fs/manually/open.rs`, `Context::new`'s `#[cfg(windows)]` block; EXECUTED for
    # `missing/../a.txt` in release dry run 39105048310).
    assert both(lambda r: r.open(3, "missing/../a.txt")[0]) == (44 if POSIX else 0, 0)
    assert both(lambda r: r.open(3, "a.txt/../b.txt")[0]) == (54 if POSIX else 0, 0)
    # A path over 4,096 bytes: ENAMETOOLONG, unread (a grant's answer is the host's).
    assert m.open(3, "a/" * 2048 + "b")[0] == 37
    assert m.path_filestat(3, "x" * 4097)[0] == 37
    # A guest pointer past memory: EFAULT, where wasmtime ends the run.
    end = files.MEMORY_END
    assert both(lambda r: r("path_open", 3, 0, end - 2, 4, 0, files.READ_RIGHTS, 0, 0, 0)) == (
        ("ends", "Pointer out of bounds"),
        21,
    )
    gf, mf = g.open(3, "a.txt")[1], m.open(3, "a.txt")[1]
    assert gf is not None and mf is not None
    assert (gf < 2**30, mf >= 2**30) == (True, True)  # the high range (Decision 1)
    for side in (g, m):
        side.iovecs([1] * 1025)
    # More than 1,024 iovecs: EINVAL (bounded work), where wasmtime reads one byte.
    assert (g("fd_read", gf, files.IOVECS, 1025, files.RESULT), g.u32(files.RESULT)) == (0, 1)
    assert m("fd_read", mf, files.IOVECS, 1025, files.RESULT) == 28
    # fd_renumber never moves a provider number (Decision 1): ENOTSUP, where a grant succeeds.
    assert (g("fd_renumber", gf, gf), m("fd_renumber", mf, mf)) == (0, 58)
    # The forward-safe calls on a provider number: EBADF, as wasmtime answers an unknown number.
    assert (g("sock_shutdown", gf, 3), m("sock_shutdown", mf, 3)) == (57, 8)
    assert (
        g("fd_prestat_dir_name", gf, files.BUFFER, 8),
        m("fd_prestat_dir_name", mf, files.BUFFER, 8),
    ) == (54, 8)  # noqa: E501
    assert (g("fd_prestat_get", gf, files.RESULT), m("fd_prestat_get", mf, files.RESULT)) == (8, 8)
    # Letter case is the provider's: this one's names are exact.
    assert m.open(3, "A.TXT")[0] == 44
    # Reads at the end of i64, as Linux answers a grant (the reference), on every system: the
    # end of an fd_read past 2**63 - 1 is EINVAL; fd_pread counts at most 64 KiB of it.
    top = 2**63 - 1
    assert [m.pread(mf, [size], at)[0] for size, at in [
        (4, top), (4, top - 3), (4, top - 4), (1, top), (1, top - 1),
        (100_000, top - 65_535), (100_000, top - 65_536),
    ]] == [28, 28, 0, 28, 0, 28, 0]  # fmt: skip
    for at, size, errno in [
        (top, 4, 28),
        (top - 3, 4, 28),
        (top - 4, 4, 0),
        (top - 99_999, 100_000, 28),
        (top - 100_000, 100_000, 0),
    ]:  # noqa: E501
        m.seek(mf, at, 0)
        assert m.read(mf, [size])[0] == errno, (at, size)
    m.seek(mf, top, 0)
    assert (m.read(mf, [0])[0], m.read(mf, [])[0]) == (0, 0)  # nothing to read: no refusal
    # A pread of nothing at 2**63 or more: EINVAL on the mount, EINTR on the grant (the table).
    for sizes in ([], [0]):
        for at in (2**63, -1):
            assert both(lambda r, s=sizes, a=at: r.pread(gf if r is g else mf, s, a)) == (
                (GRANT_PAST_I64, 0, b""),
                (28, 0, b""),
            ), (sizes, at)
    # A trailing slash or slash-dot on a file names a folder: ENOTDIR, as macOS answers and as
    # an open of it answers everywhere (Linux's grant answers the file's stat to a stat).
    assert (m.path_filestat(3, "a.txt/")[0], m.path_filestat(3, "a.txt/.")[0]) == (54, 54)


# The raw write rows. Each row runs on a fresh tree, as a writable grant (plain wasmtime) and as
# a writable mount (the runner's mount
# linker over the in-memory provider); preopen 3 is the tree on each side, 4 a writable folder and 5
# a read-only one. A row's whole result (the errno and every byte written; dev, ino, times and
# numbers masked) and the tree it leaves, after the call's ending, are compared. Decision 12's
# deliberate differences are asserted in the test after this one.

C, X, T, D = files.O_CREAT, files.O_EXCL, files.O_TRUNC, files.O_DIRECTORY
R, RW = files.READ_RIGHTS, files.RIGHTS_ALL
W_ONLY = files.FD_WRITE | files.FD_SEEK


def _opened(path, oflags=0, rights=RW, fdflags=0, then=None):
    """A row that opens `path` from the tree's preopen: the errno, and for a file opened, its
    fdstat, its filestat, what `then` gives, and its close."""

    def row(r: files.Raw):
        errno, fd = r.open(3, path, oflags, rights, fdflags)
        if fd is None:
            return errno
        out = [errno, r.fdstat(fd), r.filestat(fd)]
        if then is not None:
            out.append(then(r, fd))
        out.append(r("fd_close", fd))
        return out

    return row


def _write_rows() -> dict[str, object]:
    rows: dict[str, object] = {}
    shapes = {
        "r+": (0, RW),
        "w": (C | T, RW),
        "x": (C | X, RW),
        "a": (C, W_ONLY),
        "truncate": (T, R),
        "create read-only": (C, R),
        "exclusive alone": (X, RW),
        "x and truncate": (C | X | T, RW),
        "write-only": (0, W_ONLY),
    }
    for path in ("a.txt", "new", "d", "missing/x", "a.txt/x", "a.txt/", "new/", "d/", "."):
        for shape, (oflags, rights) in shapes.items():
            rows[f"open {shape} {path!r}"] = _opened(path, oflags, rights)
    for name, flag in (("SYNC", files.SYNC), ("DSYNC", files.DSYNC), ("RSYNC", files.RSYNC)):
        rows[f"open {name} to write"] = _opened("a.txt", 0, RW, flag)
    for path in ("d", "a.txt", "missing"):
        rows[f"open O_DIRECTORY with a write right {path!r}"] = _opened(path, D, RW)
    rows["open O_DIRECTORY and O_CREAT"] = _opened("d", D | C, R)
    rows["open O_DIRECTORY and O_TRUNC"] = _opened("new", D | T, R)
    then = {
        "write": lambda r, fd: (r.write(fd, b"zz"), r.tell(fd), r.pread(fd, [20], 0)),
        "two ciovecs": lambda r, fd: (
            r.put(files.BUFFER, b"abcdef"),
            r("fd_write", fd, r.iovecs([0, 2, 3]), 3, files.RESULT),
            r.u32(files.RESULT),
            r.pread(fd, [20], 0),
        ),
        "pwrite past the end": lambda r, fd: (
            r.write(fd, b"q", 20),
            r.filestat(fd),
            r.tell(fd),
            r.pread(fd, [30], 0),
        ),
        "pwrite at 2**63": lambda r, fd: r.write(fd, b"q", -(2**63)),
        "pwrite at 2**64 - 1": lambda r, fd: r.write(fd, b"q", -1),
        "an empty pwrite past the end": lambda r, fd: (r.write(fd, b"", 100), r.filestat(fd)),
        "a seek past the end, then a write": lambda r, fd: (
            r.seek(fd, 20, 0),
            r.write(fd, b"s"),
            r.tell(fd),
            r.pread(fd, [30], 0),
        ),
        "a seek to 2**63, then a write": lambda r, fd: (
            r.seek(fd, 2**62, 0),
            r.seek(fd, 2**62, 1),
            r.write(fd, b"s"),
        ),
        "set size 0": lambda r, fd: (r("fd_filestat_set_size", fd, 0), r.pread(fd, [20], 0)),
        "set size 20": lambda r, fd: (r("fd_filestat_set_size", fd, 20), r.pread(fd, [30], 0)),
        "set size 2**63": lambda r, fd: r("fd_filestat_set_size", fd, -(2**63)),
        "sync": lambda r, fd: (r.write(fd, b"y"), r("fd_sync", fd), r("fd_datasync", fd)),
        "set APPEND, then write": lambda r, fd: (
            r("fd_fdstat_set_flags", fd, files.APPEND),
            r.fdstat(fd),
            r.write(fd, b"q"),
            r.tell(fd),
            r("fd_fdstat_set_flags", fd, 0),
            r.write(fd, b"Q"),
            r.tell(fd),
            r.pread(fd, [30], 0),
        ),
        "allocate": lambda r, fd: r("fd_allocate", fd, 0, 10),
        "set times": lambda r, fd: [r("fd_filestat_set_times", fd, 0, 0, f) for f in (0, 3, 12)],
        "a write, a seek and a read": lambda r, fd: (
            r.write(fd, b"XY"),
            r.seek(fd, 0, 0),
            r.read(fd, [20]),
        ),
    }
    for name, fn in then.items():
        rows[f"r+ then {name}"] = _opened("a.txt", 0, RW, then=fn)
    rows["w then a write"] = _opened("new", C | T, RW, then=then["write"])
    rows["append then pwrite and write"] = _opened(
        "a.txt",
        0,
        RW,
        files.APPEND,
        then=lambda r, fd: (
            r.write(fd, b"zz", 0),
            r.tell(fd),
            r.write(fd, b"yy"),
            r.tell(fd),
            r.pread(fd, [30], 0),
        ),
    )
    rows["read-only, then the write calls"] = _opened(
        "a.txt",
        0,
        R,
        then=lambda r, fd: (
            r.write(fd, b"zz"),
            r.write(fd, b"zz", 0),
            r("fd_filestat_set_size", fd, 0),
            r("fd_sync", fd),
            r("fd_datasync", fd),
            r("fd_filestat_set_times", fd, 0, 0, 0),
        ),
    )
    rows["read-only, pwrite at 2**63"] = _opened("a.txt", 0, R, then=then["pwrite at 2**63"])
    rows["write-only, then reads"] = _opened(
        "a.txt", 0, W_ONLY, then=lambda r, fd: (r.read(fd, [3]), r.pread(fd, [3], 0))
    )
    rows["x twice"] = lambda r: (r.open(3, "n", C | X, RW)[0], r.open(3, "n", C | X, RW)[0])

    def folder(r: files.Raw):
        fd = r.open(3, "d", D)[1]
        return [
            r.write(fd, b"zz"),
            r.write(fd, b"zz", 0),
            r("fd_filestat_set_size", fd, 0),
            r("fd_sync", fd),
            r("fd_datasync", fd),
            r("fd_fdstat_set_flags", fd, files.APPEND),
            r("fd_allocate", fd, 0, 1),
            r("fd_filestat_set_times", fd, 0, 0, 0),
        ]

    rows["a folder, the write calls"] = folder
    rows["the preopen, the write calls"] = lambda r: [
        r.write(3, b"zz"),
        r("fd_filestat_set_size", 3, 0),
        r("fd_sync", 3),
        r("fd_datasync", 3),
        r("fd_fdstat_set_flags", 3, files.APPEND),
        r("fd_allocate", 3, 0, 1),
        [r("fd_filestat_set_times", 3, 0, 0, f) for f in (0, 3, 12)],
    ]
    for path in ("a.txt", ".", "d", "d/", "missing", "", "/x", ".."):
        rows[f"path set times {path!r}"] = lambda r, p=path: r.path_set_times(3, p, 0)
    rows["path set times, bad flags"] = lambda r: r.path_set_times(3, "a.txt", 3)
    calls = {
        "mkdir": "path_create_directory",
        "unlink": "path_unlink_file",
        "rmdir": "path_remove_directory",
    }
    for name, call in calls.items():
        for path in (
            "new", "new/", "d", "e", "a.txt", "missing", "missing/x", "a.txt/x", "a.txt/", "d/",
            "e/", ".", "..", "d/.", "e/.", "d/..", "new/.", "missing/.", "a.txt/.", "", "/x",
            "e/./", "d/./", "e/.//",
        ):  # fmt: skip
            rows[f"{name} {path!r}"] = lambda r, c=call, p=path: r.path_call(c, 3, p)
    for src, dst in [
        ("a.txt", "new"), ("a.txt", "b.txt"), ("d", "e"), ("e", "d"), ("a.txt", "d"),
        ("d", "a.txt"), ("d", "d/sub"), ("e", "e/sub"), ("a.txt", "a.txt"), ("d", "d"),
        ("d", "./d/"), ("missing", "missing"), ("missing", "new"), ("a.txt", "missing/c"),
        ("a.txt", "b.txt/c"), ("a.txt", "b.txt/"), ("a.txt/", "c"), ("d/", "f"), ("d", "f/"),
        ("a.txt", "newname/"), ("missing/", "x"), ("a.txt/", "a.txt"), ("a.txt", "a.txt/"),
        (".", "q"), ("a.txt", "."), ("a.txt", ".."), ("e", "e/."), ("e/.", "f"),
        ("d/..", "x"), ("d/../", "x"), ("e", "d/.."), ("a.txt", ""), ("", "x"),
        ("d/x.txt", "e/x.txt"), ("e", "d/e2"),
    ]:  # fmt: skip
        rows[f"rename {src!r} {dst!r}"] = lambda r, s=src, d=dst: r.rename(3, s, 3, d)
    rows["rename to a read-only folder"] = lambda r: r.rename(3, "a.txt", 5, "c")
    rows["rename from a read-only folder"] = lambda r: r.rename(5, "r.txt", 3, "c")
    rows["link to a read-only folder"] = lambda r: r.link(3, "a.txt", 5, "c")
    rows["link following links within"] = lambda r: r.link(3, "a.txt", 3, "c", 1)
    rows["link following links to a read-only folder"] = lambda r: r.link(3, "a.txt", 5, "c", 1)
    rows["link following links to a writable folder"] = lambda r: r.link(3, "a.txt", 4, "c", 1)
    rows["a file renamed while open"] = lambda r: (
        lambda fd: (r.rename(3, "a.txt", 3, "moved"), r.write(fd, b"M"), r("fd_close", fd))
    )(r.open(3, "a.txt", 0, RW)[1])
    rows["a file removed while open"] = lambda r: (
        lambda fd: (
            r.path_call("path_unlink_file", 3, "a.txt"),
            r.write(fd, b"gone"),
            r.seek(fd, 0, 0),
            r.read(fd, [20]),
            r.filestat(fd),
            r("fd_close", fd),
        )
    )(r.open(3, "a.txt", 0, RW)[1])
    return rows


# Rows whose answer on a writable grant is Linux's on Linux and the host's own elsewhere: the
# mount gives Linux's everywhere (wasmtime's reference; EXECUTED on wasmtime 49 in a Linux
# container in the build, and on macOS, which answers otherwise here). Each holds Linux's answer.
LINUX_ROWS: dict[str, object] = {
    "unlink 'd'": 31,
    "unlink 'e'": 31,
    "unlink 'd/'": 31,
    "unlink 'e/'": 31,
    "unlink '.'": 31,
    "unlink 'd/.'": 31,
    "unlink 'e/.'": 31,
    "unlink 'd/..'": 31,
    "unlink 'e/./'": 31,
    "unlink 'd/./'": 31,
    "unlink 'e/.//'": 31,
    "rename '.' 'q'": 10,
    "rename 'a.txt' '.'": 10,
    "rename 'e' 'e/.'": 10,
    "rename 'e/.' 'f'": 10,
    "rename 'd/..' 'x'": 10,
    "rename 'd/../' 'x'": 10,
    "rename 'e' 'd/..'": 10,
    "open w 'new/'": 31,
    "open x 'new/'": 31,
    "open a 'new/'": 31,
    "open create read-only 'new/'": 31,
    "open x and truncate 'new/'": 31,
    "open w 'a.txt/'": 31,
    "open x 'a.txt/'": 31,
    "open a 'a.txt/'": 31,
    "open create read-only 'a.txt/'": 31,
    "open x and truncate 'a.txt/'": 31,
    "open w 'd/'": 31,
    "open x 'd/'": 31,
    "open a 'd/'": 31,
    "open create read-only 'd/'": 31,
    "open x and truncate 'd/'": 31,
    "open x '.'": 20,
    "open x and truncate '.'": 20,
    "read-only, pwrite at 2**63": [0, (0, 4, 0, files.READ_RIGHTS, files.READ_RIGHTS),
                                  (0, 4, 1, 13), (28, 0), 0],
    "path set times 'missing'": 0,
}  # fmt: skip
# Rows wasmtime answers by its own logic, the same on every host; every other row's grant answer
# is the host's own, so it is compared on POSIX hosts only (the release dry run's Windows `verify`
# job asserts the mount's side, as the mounts' rows do).
WASMTIME_ROWS = {
    "open SYNC to write",
    "open DSYNC to write",
    "open RSYNC to write",
    "open O_DIRECTORY and O_CREAT",
    "open O_DIRECTORY and O_TRUNC",
    "r+ then allocate",
    "path set times, bad flags",
    *(
        "rename 'a.txt' 'newname/'",
        "rename 'a.txt' 'b.txt/'",
        "rename 'a.txt' 'a.txt/'",
    ),
}
# A file renamed to a name ending in `/`: ENOTDIR, the source's kind deciding it, on the grant
# (wasmtime's own rule) and on the mount (`RENAME_SLASH_NEEDS_FOLDER`), the same on every host.
SLASH_ROWS = {
    "rename 'a.txt' 'newname/'": 54,
    "rename 'a.txt' 'b.txt/'": 54,
    "rename 'a.txt' 'a.txt/'": 54,
    "rename 'd' 'f/'": 0,  # a folder: renamed by its plain names
}


def _write_sides(root: Path) -> tuple[files.Raw, Path, files.Mounted, files.MemoryProvider]:
    root.mkdir()
    out = []
    for side in ("grant", "mount"):
        w, ro = root / f"{side}-w", root / f"{side}-ro"
        w.mkdir()
        ro.mkdir()
        (ro / "r.txt").write_bytes(b"r")
        out.append([(w, "/w", True), (ro, "/ro", False)])
    tree = files.write_tree(root / "tree")
    grant = files.grant_raw([(tree, "/m", True), *out[0]])  # plain wasmtime
    placeholder = root / "placeholder"
    placeholder.mkdir()
    provider = files.MemoryProvider()
    mount = files.Mounted(provider, placeholder, writable=True, folders=out[1])
    return grant, tree, mount, provider


def _attempt(fn, r: files.Raw) -> object:
    try:
        return fn(r)
    except files.Ends as e:
        return ("ends", e.message)


def _on_disk(tree: Path) -> list[str]:
    return sorted(
        p.relative_to(tree).as_posix() + ("/" if p.is_dir() else "=" + p.read_bytes().hex())
        for p in tree.rglob("*")
    )


def _in_memory(provider: files.MemoryProvider) -> list[str]:
    return sorted(
        p.lstrip("/") + ("/" if data is None else "=" + data.hex())
        for p, data in provider.tree.items()
    )


def test_raw_write_calls_answer_as_a_writable_grant_does(tmp_path) -> None:
    """Each call that can write, on a provider file, folder and preopen
    of a writable mount, answers as on a writable grant's (the whole result, and the tree left
    after the call's ending), but for Decision 12's differences, asserted next. A row whose
    grant answer is the host's own is asserted per system: Linux's answer on the mount
    everywhere, and the grant's beside it on Linux."""
    import sys

    rows = _write_rows()
    differ, linux = {}, {}
    for i, (label, fn) in enumerate(rows.items()):
        grant, tree, mount, provider = _write_sides(tmp_path / str(i))
        want = _attempt(fn, grant)
        got = _attempt(fn, mount.raw)
        mount.table.walk()  # the call's ending
        want_tree, got_tree = _on_disk(tree), _in_memory(provider)
        if label in SLASH_ROWS and (want, got) != (SLASH_ROWS[label],) * 2:
            linux[label] = (SLASH_ROWS[label], want, got)
        if label in LINUX_ROWS:
            if got != LINUX_ROWS[label]:
                linux[label] = (LINUX_ROWS[label], got)
            if sys.platform != "linux":
                want = got
        if (want, want_tree) != (got, got_tree) and (POSIX or label in WASMTIME_ROWS):
            differ[label] = ((want, want_tree), (got, got_tree))
    assert linux == {}, linux
    assert differ == {}, differ
    assert len(rows) > 200


def test_the_write_differences_are_as_decided(tmp_path) -> None:
    """Where a writable mount answers otherwise than a writable
    grant, each answer is the one the table decides; the grant's is asserted beside it on POSIX
    hosts (on Windows its answers are the host's, which the release dry run first sees)."""
    grant, tree, mount, provider = _write_sides(tmp_path / "one")
    g, m = grant, mount.raw

    def both(fn) -> tuple[object, object]:
        mounted = _attempt(fn, m)
        return (_attempt(fn, g) if POSIX else None), mounted

    def grant_says(value: object) -> object:
        return value if POSIX else None

    # Unflushed writes are seen only through their own descriptor until its close.
    fds = [r.open(3, "a.txt", 0, RW)[1] for r in (g, m)]
    assert [r.write(fd, b"++", 13) for r, fd in zip((g, m), fds, strict=True)] == [(0, 2)] * 2
    assert both(lambda r: r.path_filestat(3, "a.txt")[3]) == (grant_says(15), 13)
    assert [r("fd_close", fd) for r, fd in zip((g, m), fds, strict=True)] == [0, 0]
    assert both(lambda r: r.path_filestat(3, "a.txt")[3]) == (grant_says(15), 15)
    # Two descriptors on one path: two buffers, and the later write-back replaces the earlier.
    pairs = [(r.open(3, "b.txt", 0, RW)[1], r.open(3, "b.txt", 0, RW)[1]) for r in (g, m)]
    for r, (one, two) in zip((g, m), pairs, strict=True):
        assert (r.write(one, b"1111"), r.write(two, b"22")) == ((0, 4), (0, 2))
        assert (r("fd_close", two), r("fd_close", one)) == (0, 0)
    assert provider.tree["/b.txt"] == b"1111"
    assert not POSIX or (tree / "b.txt").read_bytes() == b"2211"
    # A write past the staging bound: EFBIG (the grant's is the host's own: EINVAL on Linux).
    fds = [r.open(3, "a.txt", 0, RW)[1] for r in (g, m)]
    assert m.write(fds[1], b"zz", 2**63 - 1) == (22, 0)
    # A rename or link to another writable preopen: EXDEV, where two grants succeed.
    assert both(lambda r: r.rename(3, "b.txt", 4, "c")) == (grant_says(0), 75)
    assert both(lambda r: r.link(3, "a.txt", 4, "c2")) == (grant_says(0), 75)
    # A link or symlink within: ENOTSUP, where a grant on a POSIX host makes it.
    assert both(lambda r: r.link(3, "a.txt", 3, "c3")) == (grant_says(0), 58)
    assert both(lambda r: r.symlink("a.txt", 3, "l")) == (grant_says(0), 58)
    # The set-times calls succeed and change nothing, with no provider call, even where a grant
    # would answer the host's error for the path.
    before = provider.total()
    assert both(lambda r: r.path_set_times(3, "a.txt/", 0)) == (grant_says(54), 0)
    assert both(lambda r: r("fd_filestat_set_times", 3, 0, 0, 0)) == (grant_says(0), 0)
    assert provider.total() == before
    # A change by the embedder while a descriptor is open is not seen: the write-back goes to
    # the path the descriptor holds.
    fd = m.open(3, "e2", files.O_CREAT, RW)[1]
    provider.tree["/moved"] = provider.tree.pop("/e2")  # the embedder renames it
    assert (m.write(fd, b"E"), m("fd_close", fd)) == ((0, 1), 0)
    assert (provider.tree["/e2"], provider.tree["/moved"]) == (b"E", b"")
    # rmdir through a folder above a nested writable grant: the grant removes the nested
    # preopen's host folder; the mount answers EPERM at it and EBUSY above it.
    if POSIX:
        inner = tree / "d" / "inner"
        inner.mkdir()
        nested = files.grant_raw([(tree, "/m", True), (inner, "/m/d/inner", True)])
        assert nested.path_call("path_remove_directory", 3, "d/./inner") == 0
    placeholder = tmp_path / "nested-placeholder"
    placeholder.mkdir()
    (tmp_path / "inner").mkdir()
    nested_mount = files.Mounted(
        files.MemoryProvider(),
        placeholder,
        writable=True,
        folders=[(tmp_path / "inner", "/m/d/inner", True)],
    ).raw
    assert nested_mount.path_call("path_remove_directory", 3, "d/./inner") == 63
    assert nested_mount.path_call("path_remove_directory", 3, "d") == 10


# The Windows grant's listing at the v1.4 parity's "sorted listing" row, as release run
# 39135024458 printed it (EXECUTED, both binaries): the two `tmp…` names are a
# `TemporaryFile` and a `NamedTemporaryFile` whose open file Windows would not unlink or rename.
WINDOWS_LISTING = ["a.txt", "b.txt", "copy2.txt", "cr.txt", "g", "h", "save.txt", "tmp7zr2b8wp",
                   "tmpwlt5wwnt", "x.txt"]  # fmt: skip


@pytest.mark.parametrize("system", ["linux", "darwin", "win32"])
def test_the_write_parity_asserts_each_systems_grant(system) -> None:
    """The v1.4 write parity's per-system table gives each of the three systems its own
    grant answer, exactly, and one mount answer for all. A Windows grant renames a folder over a
    file and lists what follows from that; Linux's and macOS's grants answer `ENOTDIR` (54) and
    list what the mount lists. A `tmp…` slot takes only a name `tempfile` makes; a system the
    table does not name is an error, never a pass."""
    rename = files.write_by_system("rename a folder over a file", system)
    listing = files.write_by_system("sorted listing", system)
    mount = files.WRITE_MOUNT["sorted listing"]
    assert files.WRITE_MOUNT["rename a folder over a file"] == ["errno", 54]
    assert files.answer_matches(mount, mount)
    if system == "win32":
        assert rename is None
        assert files.answer_matches(listing, WINDOWS_LISTING)
        assert files.answer_matches(listing, [*WINDOWS_LISTING[:7], "tmp5nmlsrcc", "tmp6scne7rq",
                                              "x.txt"])  # fmt: skip
        assert not files.answer_matches(listing, mount)
        for slot in ("tmp7zr2b8w", "tmp7zr2b8wpq", "tmp7ZR2B8WP", "final.bin"):
            assert not files.answer_matches(listing, [*WINDOWS_LISTING[:7], slot,
                                                      *WINDOWS_LISTING[8:]])  # fmt: skip
        assert not files.answer_matches(listing, WINDOWS_LISTING[:-1])
    else:
        assert rename == ["errno", 54]
        assert listing == mount
        assert files.answer_matches(listing, mount)
        assert not files.answer_matches(listing, WINDOWS_LISTING)
        assert not files.answer_matches(listing, [n for n in mount if n != "e"])
    with pytest.raises(KeyError):
        files.write_by_system("sorted listing", "freebsd")


# A special file in a grant: an open of a FIFO waits in the host for its other end, beyond the
# deadline and cancel, so the runner looks at the target first and refuses anything that is not
# a regular file, a folder or a link the open will not follow.

ENXIO, ELOOP, ENOTDIR, EEXIST = 60, 32, 54, 20
