"""Sandbox escapes through a mount, tried by real Perl through `run()`: `..` climbs, absolute
paths, a mount over a grant, a writable mount that must write no host file, a writable mount at
the root beside the runtime and other grants, names a host could read another way, and the
budgets that bound a hostile guest. Written as `test_escapes.py`'s are: each guest prints
`NAME ok` or `NAME refused <errno text>` for each attempt, and each check also compares the
host's files before and after, and reads the provider's log: every path it was given is
normalized and within its mount. Ports of wasi-python's hostile-guest checks at f9bf678 (same
owner, Apache-2.0) where the name says so. Nothing is timed."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from escapes.test_escapes import EITHER, PERM, PRELUDE, lines, refused
from mounts import raw as files
from mounts.raw import MemoryProvider
from wasi_perl import Errno, Grant, Mount, RefusedWrite, _fs_table, run

pytestmark = pytest.mark.slow

TREE = {"/a.txt": b"a\n", "/b.txt": b"b\n", "/d": None, "/d/x.txt": b"x\n", "/e": None}
XDEV = "Cross-device link"  # wasi-libc's text for EXDEV
BUSY = "Resource busy"  # and for EBUSY


def guest(code: str, **kw):
    return run(["-e", PRELUDE + textwrap.dedent(code)], **kw)


def snapshot(*roots: Path) -> dict[str, str]:
    """Every entry under `roots`, by path, with a file's hash: what the host holds."""
    out: dict[str, str] = {}
    for root in roots:
        for p in sorted(root.rglob("*")):
            out[str(p)] = "dir" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def normalized(provider: MemoryProvider) -> None:
    """Every path the provider was given is absolute and normalized, within its mount."""
    for _, path in provider.paths:
        assert path.startswith("/"), path
        assert path == "/" or not any(part in ("", ".", "..") for part in path[1:].split("/")), path


def runtime_hashes(perl) -> dict[str, str]:
    return snapshot(perl.lib, perl.dev)


@pytest.mark.parametrize("writable", [False, True], ids=["read-only", "writable"])
def test_dot_dot_cannot_climb_out_of_a_mount(perl, tmp_path, writable) -> None:
    before = snapshot(tmp_path)
    provider = MemoryProvider(dict(TREE))
    done = guest(
        """
        t("up-etc", sub { open(my $f, "<", "/work/../etc/passwd") });
        t("up-twice", sub { open(my $f, "<", "/work/d/../../x") });
        t("relative-up", sub { open(my $f, "<", "../x") });
        t("from-a-folder", sub { chdir "/work/d" or die "chdir: $!"; open(my $f, "<", "../../x") });
        t("make-up", sub { open(my $f, ">", "/work/../new") });
        """,
        mounts=[Mount(provider, "/work", writable=writable)],
        cwd="/work",
        artifacts=perl,
    )
    seen = lines(done)
    refused(seen, "up-etc", "up-twice", "relative-up", "from-a-folder", "make-up")
    for name in ("up-etc", "up-twice", "relative-up", "from-a-folder", "make-up"):
        assert seen[name].endswith(PERM), (name, seen)
    normalized(provider)
    assert {p for _, p in provider.paths} == {"/", "/d"}  # the two chdirs' own stats
    assert snapshot(tmp_path) == before


def test_an_absolute_path_never_reaches_a_provider(perl, tmp_path) -> None:
    provider = MemoryProvider(dict(TREE))
    done = guest(
        """
        t("doubled", sub { slurp("/work//a.txt") });
        t("dotted", sub { slurp("/work/./d/./x.txt") });
        t("climbs-back", sub { slurp("/work/d/../a.txt") });
        """,
        mounts=[Mount(provider, "/work")],
        artifacts=perl,
    )
    seen = lines(done)
    assert seen["doubled"] == seen["climbs-back"] == "ok" and seen["dotted"] == "ok", seen
    normalized(provider)
    assert {p for _, p in provider.paths} == {"/a.txt", "/d/x.txt"}
    # A raw path_open of an absolute path on a mount's own descriptor: EPERM, no provider call.
    placeholder = tmp_path / "placeholder"
    placeholder.mkdir()
    raw_provider = MemoryProvider(dict(TREE))
    m = files.Mounted(raw_provider, placeholder)
    assert m.raw.open(3, "/a.txt")[0] == 63 and raw_provider.total() == 0


def test_a_mount_over_a_grant(perl, tmp_path) -> None:
    grant, rw = tmp_path / "g", tmp_path / "rw"
    grant.mkdir()
    rw.mkdir()
    (grant / "x").write_bytes(b"the grant's x\n")
    before = snapshot(grant, rw)
    with pytest.raises(ValueError, match="a grant or another mount"):
        run(["-e", "1"], grants=[Grant(grant, "/data")], mounts=[Mount(MemoryProvider(), "/data")])
    provider = MemoryProvider(dict(TREE))
    done = guest(
        """
        t("dot-into-grant", sub { open(my $f, "<", "/data/./g/x") });
        t("climb-into-grant", sub { open(my $f, "<", "/data/d/../g/x") });
        t("the-grant", sub { slurp("/data/g/x") });
        t("make-dot", sub { open(my $f, ">", "/data/./g/new") });
        t("make", sub { open(my $f, ">", "/data/g/new") });
        t("rename-into-grant", sub { rename("/data/a.txt", "/data/g/a.txt") });
        t("rename-into-writable", sub { rename("/data/a.txt", "/rw/a.txt") });
        """,
        mounts=[Mount(provider, "/data", writable=True)],
        grants=[Grant(grant, "/data/g"), Grant(rw, "/rw", writable=True)],
        artifacts=perl,
    )
    seen = lines(done)
    for name in ("dot-into-grant", "climb-into-grant", "make-dot", "make", "rename-into-grant"):
        assert seen[name] == f"refused {PERM}", (name, seen)
    assert seen["the-grant"] == "ok" and b"READ[the grant's x\n]" in done.stdout
    assert seen["rename-into-writable"] == f"refused {XDEV}", seen
    normalized(provider)
    assert not any(p == "/g" or p.startswith("/g/") for _, p in provider.paths)
    assert {k: provider.calls[k] for k in files.METHODS[3:]} == dict.fromkeys(files.METHODS[3:], 0)
    assert snapshot(grant, rw) == before


WRITES = """
t("create", sub { open(my $f, ">", "$R/new.txt") or die; print $f "made"; close $f });
t("append", sub { open(my $f, ">>", "$R/a.txt") or die; print $f "more"; close $f });
t("truncate", sub { truncate("$R/b.txt", 1) });
t("rename", sub { rename("$R/new.txt", "$R/moved.txt") });
t("mkdir", sub { mkdir("$R/m") }); t("rmdir", sub { rmdir("$R/m") });
t("up", sub { open(my $f, ">", "$R/../up.txt") });
t("up-folder", sub { mkdir("$R/../upd") });
t("placeholder", sub { open(my $f, ">", "$P/x") });
t("tmp-path", sub { open(my $f, ">", "$T/x") });
t("link-out", sub { link("$R/a.txt", "$T/l") });
t("symlink-out", sub { symlink("$T/x", "$R/s") });
"""


@pytest.mark.parametrize("at", ["/w", "/"])
def test_a_writable_mount_writes_no_host_file(perl, tmp_path, monkeypatch, at) -> None:
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    library = runtime_hashes(perl)
    before = snapshot(tmp_path)
    seen_placeholder: list[list[str]] = []

    class Looking(MemoryProvider):
        def write(self, path, data):
            if not seen_placeholder:
                (folder,) = list(temp.iterdir())
                seen_placeholder.append(os.listdir(folder / "mount"))
            return super().write(path, data)

    provider = Looking(dict(TREE))
    root = "" if at == "/" else at
    # The host paths of the placeholder's folder and of the test's own folder, spelled as guest
    # paths: no preopen serves them under /w, and under / they are names in the provider.
    spelled = f"my $R = '{root}'; my $P = '{temp.as_posix()}'; my $T = '{tmp_path.as_posix()}';"
    code = spelled + WRITES
    done = guest(code, mounts=[Mount(provider, at, writable=True)], artifacts=perl)
    seen = lines(done)
    for name in ("create", "append", "truncate", "rename", "mkdir", "rmdir"):
        assert seen[name] == "ok", (name, seen)
    refused(seen, "link-out", "symlink-out")
    if at == "/w":
        refused(seen, "up", "up-folder", "placeholder", "tmp-path")
    pi = run(["-pi", "-e", "s/a/A/", f"{root}/a.txt"], mounts=[Mount(provider, at, writable=True)],
             artifacts=perl)  # fmt: skip
    assert (pi.exit_code, pi.stderr) == (0, b""), pi
    assert provider.tree["/a.txt"] == b"A\nmore" and provider.tree["/moved.txt"] == b"made"
    assert provider.tree["/b.txt"] == b"b"
    normalized(provider)
    assert seen_placeholder == [[]]
    assert list(temp.iterdir()) == []  # the placeholder and its folder are gone
    assert snapshot(tmp_path) == before
    assert runtime_hashes(perl) == library


# What the guest does with the call's folder, `/t/$name`, through its writable grant over the
# temporary folder: rename the folder away to `/t/moved` and put something of its name in its
# place, or write into the folder or into the placeholder itself.
MOVE = 'rename("/t/$name", "/t/moved") or die "rename: $!";'
PUT = 'sub put { open(my $f, ">", $_[0]) or die "make: $!"; print $f "guest"; close $f }'
CHANGES = {
    "a file in its place": MOVE + ' put("/t/$name");',
    "a folder holding files in its place": MOVE
    + ' mkdir("/t/$name") or die; mkdir("/t/$name/mount") or die;'
    ' put("/t/$name/kept"); put("/t/$name/mount/kept");',
    "a link out of the grant in its place": MOVE
    + ' symlink("../victim", "/t/$name") or die "link: $!";',
    "a file in the moved placeholder, an empty folder in its place": MOVE
    + ' put("/t/moved/mount/kept"); mkdir("/t/$name") or die;',
    "a file beside the placeholder": 'put("/t/$name/kept");',
}
# Windows will not rename a folder below which a handle is open, and the call's store holds the
# placeholder open as each mount's preopen (wasmtime opens a Windows grant's folders and files
# without FILE_SHARE_DELETE, as it refuses a rename over a held file: wasi-perl's `CHANGELOG.md`
# 0.1.0, and wasi-python's `tests/harness/files.py` at f9bf678). So there a move is refused.
# Seen on windows-2025 in CI run 45462992471, job 138692425791.
MOVE_BY_SYSTEM = {
    "linux": "ok",
    "darwin": "ok",
    "win32": "refused rename: Permission denied",
}


@pytest.mark.parametrize("change", list(CHANGES))
def test_the_clean_up_removes_only_an_empty_folder(perl, tmp_path, monkeypatch, change) -> None:
    """A guest with a writable grant over the placeholder's parent changes the call's folder.
    The call returns normally. The runner removes its own placeholder, wherever the guest moved
    it, once it is empty, and its own folder while its name still gives that folder, once it is
    empty; nothing else: not the guest's file or folder or what they hold, and not a folder a
    link points to outside every grant. The clean-up holds the folder by its descriptor, never by
    its name, and every removal is an `rmdir`. On Windows a move is refused (`MOVE_BY_SYSTEM`)."""
    temp, victim = tmp_path / "temp", tmp_path / "victim"
    temp.mkdir()
    (victim / "mount").mkdir(parents=True)
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    code = (
        PUT + ' t("change", sub {'
        ' opendir(my $d, "/t") or die; my ($name) = grep { /^wasi-perl-/ } readdir $d;'
        ' $name or die "no folder"; ' + CHANGES[change] + " 1 });"
    )
    done = guest(
        code,
        grants=[Grant(temp, "/t", writable=True)],
        mounts=[Mount(MemoryProvider({}), "/m")],
        artifacts=perl,
    )
    answer = lines(done)["change"].split(" at -e line ")[0]
    assert (victim / "mount").is_dir()  # outside every grant: never touched
    moves = CHANGES[change].startswith(MOVE)
    if moves and sys.platform == "win32":
        assert answer == MOVE_BY_SYSTEM["win32"]
        assert list(temp.iterdir()) == []  # no move: the placeholder and its folder are gone
        return
    assert answer == (MOVE_BY_SYSTEM[sys.platform] if moves else "ok")
    names = sorted(p.name for p in temp.iterdir())
    (made,) = [n for n in names if n != "moved"]
    assert made.startswith("wasi-perl-") and len(names) == (2 if moves else 1)
    if change == "a file beside the placeholder":
        assert os.listdir(temp / made) == ["kept"]  # the empty placeholder alone is removed
        assert (temp / made / "kept").read_bytes() == b"guest"
        return
    if change == "a file in the moved placeholder, an empty folder in its place":
        assert os.listdir(temp / "moved" / "mount") == ["kept"]
        assert os.listdir(temp / made) == []  # the guest's folder: not the runner's own
        return
    assert os.listdir(temp / "moved") == []  # the runner's own placeholder, removed by descriptor
    if change == "a file in its place":
        assert (temp / made).read_bytes() == b"guest"
    elif change == "a folder holding files in its place":
        assert (temp / made / "kept").read_bytes() == b"guest"
        assert (temp / made / "mount" / "kept").read_bytes() == b"guest"
    else:
        assert os.readlink(temp / made) == "../victim"


def test_a_writable_root_mount_keeps_the_runtime_and_other_grants(perl, tmp_path) -> None:
    """Port of wasi-python's check of this name."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "x").write_bytes(b"the grant's\n")
    before = snapshot(data)
    library = runtime_hashes(perl)
    root = MemoryProvider({"/a.txt": b"a\n"})
    out = MemoryProvider({})
    done = guest(
        """
        t("lib", sub { open(my $f, ">", "/usr/local/lib/perl5/x") });
        t("lib-dot", sub { open(my $f, ">", "/usr/./local/lib/perl5/x") });
        t("dev", sub { open(my $f, ">", "/dev/x") });
        t("data", sub { open(my $f, ">", "/data/x") });
        t("mkdir-lib", sub { mkdir("/usr/./local/lib/perl5/m") });
        t("mkdir-data", sub { mkdir("/data/m") });
        t("unlink-data", sub { unlink("/data/x") });
        t("rename-into-lib", sub { rename("/a.txt", "/usr/./local/lib/perl5/a.txt") });
        t("rename-into-data", sub { rename("/a.txt", "/data/a.txt") });
        t("rmdir-usr", sub { rmdir("/usr") }); t("rmdir-usr-local", sub { rmdir("/usr/local") });
        t("rename-usr", sub { rename("/usr", "/u2") });
        t("rename-work", sub { rename("/work", "/w2") });
        t("rename-out", sub { rename("/a.txt", "/work/out/a.txt") });
        t("posix", sub { require POSIX });
        """,
        mounts=[Mount(root, "/", writable=True), Mount(out, "/work/out", writable=True)],
        grants=[Grant(data, "/data")],
        artifacts=perl,
    )
    seen = lines(done)
    for name in ("lib", "lib-dot", "dev", "data", "mkdir-lib", "mkdir-data", "unlink-data",
                 "rename-into-lib", "rename-into-data"):  # fmt: skip
        assert seen[name] == f"refused {PERM}", (name, seen)
    for name in ("rmdir-usr", "rmdir-usr-local", "rename-usr", "rename-work"):
        assert seen[name] == f"refused {BUSY}", (name, seen)
    assert seen["rename-out"] == f"refused {XDEV}" and seen["posix"] == "ok", seen
    assert root.total() == out.total() == 0
    assert snapshot(data) == before and runtime_hashes(perl) == library


NAMES = """
t("backslash-climb", sub { open(my $f, "<", "$R/a\\\\..\\\\..\\\\x") });
t("drive", sub { open(my $f, "<", "$R/C:x") });
t("drive-folder", sub { open(my $f, "<", "$R/d/C:") });
t("backslash", sub { open(my $f, "<", "$R/d\\\\x.txt") });
"""


@pytest.mark.parametrize("writable", [False, True], ids=["read-only", "writable"])
def test_a_mount_refuses_a_name_a_host_could_reinterpret(perl, writable) -> None:
    """Port of wasi-python's check of this name, with its drive-prefixed case
    (`test_a_drive_prefixed_name_stays_in_its_folder`, mounted): a backslash or a colon in a
    component is EINVAL, and the provider sees no call for it."""
    provider = MemoryProvider(dict(TREE))
    done = guest(
        "my $R = '/work';" + NAMES,
        mounts=[Mount(provider, "/work", writable=writable)],
        artifacts=perl,
    )
    seen = lines(done)
    for name in ("backslash-climb", "drive", "drive-folder", "backslash"):
        assert seen[name] == "refused Invalid argument", (name, seen)
    assert provider.total() == 0


SLASHES = """
t("file-slash-source", sub { rename("$R/a.txt/", "$R/b2") });
t("file-slash-target", sub { rename("$R/a.txt", "$R/b2/") });
t("folder-slash", sub { rename("$R/e/", "$R/e2/") });
t("folder-dot", sub { rename("$R/e/.", "$R/e3") });
t("symlink-slash", sub { symlink("$R/a.txt", "$R/s") and rename("$R/s/", "$R/s2") });
"""


def test_a_trailing_slash_rename_rule(perl, tmp_path) -> None:
    """Ports of wasi-python's `test_a_trailing_slash_rename_rule` and
    `test_a_trailing_slash_symlink_rename_cannot_escape`, mounted: a trailing slash on a file
    is ENOTDIR on either side of a rename, a folder renames, a name ending in `.` is busy, and a
    link cannot be made to rename through; each as a writable grant answers it."""
    root = tmp_path / "tree"
    for path, data in TREE.items():
        target = root / path.lstrip("/")
        if data is None:
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    granted = guest("my $R = '/w';" + SLASHES, grants=[Grant(root, "/w", writable=True)],
                    artifacts=perl)  # fmt: skip
    provider = MemoryProvider(dict(TREE))
    mounted = guest("my $R = '/w';" + SLASHES, mounts=[Mount(provider, "/w", writable=True)],
                    artifacts=perl)  # fmt: skip
    g, m = lines(granted), lines(mounted)
    assert m["file-slash-source"] == m["file-slash-target"] == "refused Not a directory"
    assert m["folder-slash"] == "ok" and m["folder-dot"].startswith("refused"), m
    assert m["symlink-slash"] == "refused Not supported"  # a mount has no links
    for name in ("file-slash-source", "file-slash-target", "folder-slash"):
        assert m[name] == g[name], (name, g, m)
    assert "/e2" in provider.tree and "/e" not in provider.tree
    normalized(provider)


def test_budgets_bound_a_hostile_guest(perl, monkeypatch) -> None:
    # file calls
    provider = MemoryProvider(dict(TREE))
    done = run(["-e", '1 while -e "/w/a.txt"'], mounts=[Mount(provider, "/w")],
               max_file_calls=7, artifacts=perl)  # fmt: skip
    assert (done.trapped, provider.total()) == ("out of file calls", 7)
    # staging: a file past the bound, an append past it, a listing past it
    bound = 32 << 20
    provider = MemoryProvider({"/big": b"x" * (bound + 1), "/d": None})
    provider.answers[("list", "/d")] = lambda path: ((f"n{i:06}", "file") for i in range(10**6))
    peak = [0]
    real = _fs_table._Files._changed

    def changed(self, entry):
        real(self, entry)
        peak[0] = max(peak[0], self.staged)

    monkeypatch.setattr(_fs_table._Files, "_changed", changed)
    done = guest(
        """
        t("big", sub { open(my $f, "<", "/w/big") });
        t("grow", sub {
            open(my $f, ">", "/w/grow") or die "$!"; my $chunk = "y" x (1 << 20);
            for (1 .. 64) { defined syswrite($f, $chunk) or die "$!\\n" } 1 });
        t("listing", sub { opendir(my $d, "/w/d") });
        """,
        mounts=[Mount(provider, "/w", writable=True)],
        memory_bytes=bound,
        artifacts=perl,
    )
    seen = lines(done)
    assert seen["big"] == seen["listing"] == "refused File too large", seen
    assert seen["grow"] == "refused File too large", seen
    assert 0 < peak[0] <= bound
    # the walk: the ending's write-backs pass the limit
    provider = MemoryProvider({})
    code = (
        "use Fcntl; use POSIX (); my @fh; for my $n (1 .. 3) {"
        ' sysopen my $f, "/w/$n", O_WRONLY | O_CREAT | O_TRUNC or die "$!"; push @fh, $f;'
        ' syswrite $f, "dirty" } POSIX::_exit(0)'
    )
    done = run(["-e", code], mounts=[Mount(provider, "/w", writable=True)], max_file_calls=7,
               artifacts=perl)  # fmt: skip
    assert done.exit_code == 0 and provider.total() == 7
    assert done.files.refused == tuple(
        RefusedWrite("/w", "write", (f"/{n}",), Errno.EIO, "exit", "limit") for n in (2, 3)
    )


def test_open_files_bound_a_hostile_guest(perl) -> None:
    """The open-file bound, as `test_mount_descriptors_are_bounded` holds it: past the guest
    table's cap an open is EMFILE before any provider call."""
    provider = MemoryProvider({f"/f{i}": b"x" for i in range(300)})
    code = (
        "my (@held, $n); for my $i (0 .. 299) { if (open my $f, '<', \"/w/f$i\")"
        " { push @held, $f } else { $n++ } } print $n;"
    )
    done = run(["-e", code], mounts=[Mount(provider, "/w")], artifacts=perl)
    assert done.exit_code == 0 and 0 < int(done.stdout) < 300
    assert provider.calls["read"] == 300 - int(done.stdout)


assert EITHER  # the shared constants are the escape tests' own
