"""Mounts under a Perl guest: what each Perl operation costs a provider, study E's workloads, a
mount beside a grant, the limits, faults and endings, and writable mounts. Every check counts
provider calls, WASI calls, bytes and errnos; none reads a clock. A check that needs the
deadline moves the ticker's count, as the deadline checks of the runner do.

The ports keep the names of wasi-python's checks at f9bf678 (same owner, Apache-2.0:
`tests/checks/test_v1_4_embedding.py`, `tests/checks/test_v1_2_hostile_guest.py`), with Perl
guests in place of Python ones. They run the perl.wasm of the set `$WASI_PERL_ARTIFACTS` names,
else the build's."""

from __future__ import annotations

import errno
import gc
import os
import random
import tempfile
import threading
import warnings
from collections import Counter
from pathlib import Path

import pytest

from mounts.raw import METHODS, MemoryProvider
from wasi_perl import (
    Errno,
    Grant,
    Mount,
    RefusedWrite,
    Stat,
    _engines,
    _fs_table,
    defaults,
    run,
)

pytestmark = pytest.mark.slow

ZERO = dict.fromkeys(METHODS, 0)


def counts(**some: int) -> dict[str, int]:
    return {**ZERO, **some}


def go(perl, code, provider, *, guest="/work", writable=False, argv=None, **kw):
    """One run of `code` (or `argv`) with `provider` mounted at `guest`."""
    args = argv if argv is not None else ["-e", code]
    return run(args, mounts=[Mount(provider, guest, writable=writable)], artifacts=perl, **kw)


def write_tree(root: Path, tree: dict[str, bytes | None]) -> Path:
    for path, data in sorted(tree.items()):
        target = root / path.lstrip("/")
        if data is None:
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return root


def on_disk(root: Path) -> dict[str, bytes | None]:
    out: dict[str, bytes | None] = {}
    for p in sorted(root.rglob("*")):
        key = "/" + p.relative_to(root).as_posix()
        out[key] = None if p.is_dir() else p.read_bytes()
    return out


# D1: what each Perl operation costs a provider.

TREE = {
    "/a.txt": b"one\ntwo\n",
    "/b.txt": b"b\n",
    "/d": None,
    "/d/x.txt": b"x\n",
    "/d/e": None,
}

# Each row: the guest's arguments, its cwd, and the provider calls it makes. The rows marked in
# the specification as INFERRED or READ are confirmed here by their first run; three gave other
# counts than the specification's table, for the reasons the landing notes give: a glob or an
# opendir of the mount's own folder costs one stat fewer (a mount's own folder is listed with
# no stat), and a cwd in a mount costs the guest's start-up chdir its one stat.
PERL_ROWS: dict[str, tuple[list[str], str | None, dict[str, int]]] = {
    "-e 1": (["-e", "1"], None, counts()),
    'open "<", read to the end, close': (
        ["-e", 'open my $f, "<", "/work/a.txt" or die "$!"; my @l = <$f>; close $f or die'],
        None,
        counts(stat=1, read=1),
    ),
    **{
        f"{op} of a file": (["-e", f'{op} "/work/a.txt" or die'], None, counts(stat=1))
        for op in ("-e", "-f", "-s", "stat", "lstat")
    },
    "opendir, readdir to the end, closedir": (
        ["-e", 'opendir my $d, "/work/d" or die "$!"; my @e = readdir $d; closedir $d'],
        None,
        counts(stat=2, list=1),
    ),
    "opendir of the mount's own folder": (
        ["-e", 'opendir my $d, "/work" or die "$!"; my @e = readdir $d; closedir $d'],
        None,
        counts(stat=1, list=1),
    ),
    "glob over N files of a folder": (
        ["-e", 'my @g = glob("/work/d/*"); @g == 2 or die'],
        None,
        counts(stat=2 + 2, list=1),
    ),
    "glob over N files of the mount's own folder": (
        ["-e", 'my @g = glob("/work/*"); @g == 3 or die'],
        None,
        counts(stat=1 + 3, list=1),
    ),
    'open ">" of a new file, print, close': (
        ["-e", 'open my $f, ">", "/work/new" or die "$!"; print $f "hi"; close $f or die "$!"'],
        None,
        counts(stat=1, write=2),
    ),
    'open ">>" of a file, print, close': (
        ["-e", 'open my $f, ">>", "/work/a.txt" or die "$!"; print $f "hi"; close $f or die'],
        None,
        counts(stat=1, read=1, write=1),
    ),
    'open "+<", no write, close': (
        ["-e", 'open my $f, "+<", "/work/a.txt" or die "$!"; close $f or die "$!"'],
        None,
        counts(stat=1, read=1),
    ),
    "sysopen with O_CREAT | O_EXCL": (
        ["-MFcntl", "-e", 'sysopen my $f, "/work/new", O_WRONLY | O_CREAT | O_EXCL or die "$!"'],
        None,
        counts(stat=1, write=1),
    ),
    "mkdir": (["-e", 'mkdir "/work/m" or die "$!"'], None, counts(mkdir=1)),
    "rmdir": (["-e", 'rmdir "/work/d/e" or die "$!"'], None, counts(rmdir=1)),
    "rename": (["-e", 'rename "/work/b.txt", "/work/c.txt" or die "$!"'], None, counts(rename=1)),
    "unlink": (["-e", 'unlink "/work/b.txt" or die "$!"'], None, counts(stat=1, remove=1)),
    "-pi -e over one file": (
        ["-pi", "-e", "s/one/1/", "/work/a.txt"],
        None,
        counts(stat=2, read=1, write=2, rename=1),
    ),
    "-pi -e over one file, cwd at the mount's own path": (
        ["-pi", "-e", "s/one/1/", "a.txt"],
        "/work",
        counts(stat=2 + 1 + 1, list=1, read=1, write=2, rename=1),
    ),
    "-pi -e over one file, cwd below it": (
        ["-pi", "-e", "s/x/y/", "x.txt"],
        "/work/d",
        counts(stat=2 + 1 + 2, list=1, read=1, write=2, rename=1),
    ),
}


@pytest.mark.parametrize("row", list(PERL_ROWS))
def test_each_perl_operation_makes_its_provider_calls(perl, row) -> None:
    argv, cwd, want = PERL_ROWS[row]
    provider = MemoryProvider(dict(TREE))
    done = go(perl, None, provider, argv=argv, cwd=cwd, writable=True)
    assert (done.exit_code, done.stderr) == (0, b""), done
    assert done.files is not None and dict(done.files.calls) == provider.calls == want


# D2 and D3: study E's workloads, rebuilt.

SMALL_ROWS = 500


def study_e_data(seed: int = 7) -> dict[str, bytes]:
    """Study E's rows, by path within the mount: a 50 KB log, a 5 MB CSV and 500 small notes
    (`context/research/2026-11-24-perf-vfs-io/harness.py`, `make_data`)."""
    rnd = random.Random(seed)
    files: dict[str, bytes] = {}
    lines, size, i = [], 0, 0
    while size < 50_000:
        level = "ERROR" if i % 50 == 0 else "INFO"
        line = f"2026-11-24T10:{i % 60:02d}:00 {level} worker-{i % 16} req={rnd.randrange(10**6)}\n"
        lines.append(line)
        size += len(line)
        i += 1
    files["/logs/app.log"] = "".join(lines).encode()
    lines, size, i = ["id,name,amount,region\n"], 0, 0
    while size < 5_000_000:
        line = f"{i},name{i % 997},{rnd.randrange(10000)},r{i % 7}\n"
        lines.append(line)
        size += len(line)
        i += 1
    files["/data/sales.csv"] = "".join(lines).encode()
    for k in range(SMALL_ROWS):
        body = "".join(
            f"note {k} line {j} {'needle' if (k * 7 + j) % 41 == 0 else 'hay'}\n" for j in range(12)
        )
        files[f"/notes/n{k:03d}.txt"] = body.encode()
    return files


def study_e_tree() -> dict[str, bytes | None]:
    tree: dict[str, bytes | None] = dict(study_e_data())
    for path in list(tree):
        parts = path.strip("/").split("/")
        for i in range(1, len(parts)):
            tree["/" + "/".join(parts[:i])] = None
    return tree


def workloads(root: str) -> dict[str, tuple[list[str], bool]]:
    """Study E's programs over the files at `root`, and whether each writes."""
    notes = [f"{root}/notes/n{k:03d}.txt" for k in range(SMALL_ROWS)]
    return {
        "ne_50k": (["-ne", "print if /ERROR/", f"{root}/logs/app.log"], False),
        "lane_5m": (
            ["-F,", "-lane", "$s += $F[2]; END { print $s }", f"{root}/data/sales.csv"],
            False,
        ),
        "slurp_5m": (["-0777", "-ne", "print length, qq{\\n}", f"{root}/data/sales.csv"], False),
        "pi_50k": (["-pi", "-e", "s/ERROR/WARN/g", f"{root}/logs/app.log"], True),
        "grep_args_500": (["-ne", 'print "$ARGV:$_" if /needle/', *notes], False),
        "grep_glob_500": (
            [
                "-e",
                f'for my $p (glob "{root}/notes/*.txt") {{ open my $f, "<", $p or die "$p: $!";'
                ' while (<$f>) { print "$p:$_" if /needle/ } }',
            ],
            False,
        ),
        "grep_opendir_500": (
            [
                "-e",
                f'opendir my $d, "{root}/notes" or die $!; for my $n (sort grep {{ /\\.txt$/ }}'
                f' readdir $d) {{ my $p = "{root}/notes/$n"; open my $f, "<", $p or die $!;'
                ' while (<$f>) { print "$p:$_" if /needle/ } }',
            ],
            False,
        ),
        "write_new": (
            [
                "-e",
                f'open my $o, ">", "{root}/data/report.txt" or die $!;'
                ' print $o "line $_ ", "x" x 60, "\\n" for 1 .. 2000; close $o or die $!',
            ],
            True,
        ),
    }


# Study E's column (a), EXECUTED there, but for write_new: E's prototype stats before a create
# that truncates, where wasi-python's open (and so this one) makes one `write(path, b"")`; so
# write_new is the look's stat and two writes, 3, not E's 4 (the landing notes).
STUDY_E_CALLS = {
    "ne_50k": 2,
    "lane_5m": 2,
    "slurp_5m": 2,
    "pi_50k": 6,
    "grep_args_500": 1000,
    "grep_glob_500": 1503,
    "grep_opendir_500": 1003,
    "write_new": 3,
}


@pytest.fixture(scope="module")
def e_tree() -> dict[str, bytes | None]:
    return study_e_tree()


@pytest.mark.parametrize("name", list(STUDY_E_CALLS))
def test_each_workload_makes_its_provider_calls(perl, tmp_path, e_tree, name) -> None:
    argv, writes = workloads("/work")[name]
    provider = MemoryProvider(dict(e_tree))
    done = go(perl, None, provider, argv=argv, writable=writes)
    assert (done.exit_code, done.stderr) == (0, b""), done
    assert provider.total() == STUDY_E_CALLS[name], provider.calls
    assert done.files is not None and dict(done.files.calls) == provider.calls
    root = write_tree(tmp_path / "tree", e_tree)
    granted = run(argv, grants=[Grant(root, "/work", writable=writes)], artifacts=perl)
    assert (granted.exit_code, granted.stdout) == (0, done.stdout)
    assert on_disk(root) == provider.tree


def _logged(monkeypatch) -> list[str]:
    names: list[str] = []
    real = _fs_table._Files.__call__

    def logged(self, caller, name, *args):
        names.append(name)
        return real(self, caller, name, *args)

    monkeypatch.setattr(_fs_table._Files, "__call__", logged)
    return names


@pytest.mark.parametrize("name", list(STUDY_E_CALLS))
def test_a_mount_and_a_grant_see_the_same_wasi_calls(
    perl, tmp_path, e_tree, monkeypatch, name
) -> None:
    """Both runs instantiate from the mount linker: the tree as a grant beside an unused empty
    mount, and the tree as a mount beside an unused empty grant. The guest makes the same
    shadowed calls, in the same order, served or forwarded."""
    argv, writes = workloads("/work")[name]
    names = _logged(monkeypatch)
    root = write_tree(tmp_path / "tree", e_tree)
    empty = tmp_path / "empty"
    empty.mkdir()
    granted = run(
        argv,
        grants=[Grant(root, "/work", writable=writes)],
        mounts=[Mount(MemoryProvider({}), "/unused")],
        artifacts=perl,
    )
    as_grant = list(names)
    names.clear()
    provider = MemoryProvider(dict(e_tree))
    mounted = run(
        argv,
        grants=[Grant(empty, "/unused")],
        mounts=[Mount(provider, "/work", writable=writes)],
        artifacts=perl,
    )
    assert (granted.exit_code, mounted.exit_code, granted.stdout) == (0, 0, mounted.stdout)
    assert as_grant and names == as_grant


# D4 to D6: reads, a mount answering as a grant does, a mount at the root.


def test_a_mount_serves_reads(perl, tmp_path) -> None:
    """Port of wasi-python's check of this name: the bytes are the provider's, the descriptor is
    the guest's own small number, `select` reports a mount file ready, a grant in the same call
    reads as before, and the record's counts are the provider's own."""
    (tmp_path / "b.txt").write_bytes(b"from the grant\n")
    provider = MemoryProvider({"/a.txt": b"from the mount\n" * 3})
    code = (
        'open my $f, "<", "/work/a.txt" or die "$!"; print <$f>;'
        ' print fileno($f) < 256 ? "small\\n" : "large\\n";'
        ' my $r = ""; vec($r, fileno($f), 1) = 1; print scalar(select($r, undef, undef, 0)), "\\n";'
        ' open my $g, "<", "/data/b.txt" or die "$!"; print <$g>;'
    )
    done = run(
        ["-e", code],
        mounts=[Mount(provider, "/work")],
        grants=[Grant(tmp_path, "/data")],
        artifacts=perl,
    )
    assert (done.exit_code, done.stderr) == (0, b""), done
    assert done.stdout == b"from the mount\n" * 3 + b"small\n1\nfrom the grant\n"
    assert dict(done.files.calls) == provider.calls == counts(stat=1, read=1)


# Each row prints one line: its result, or `$!`'s text. A grant and a mount of the same tree
# must print the same lines.
AS_A_GRANT = r"""
no warnings;
my $R = $ARGV[0];
sub say_ { print "$_[0]: $_[1]\n" }
sub try_ { my ($name, $ok) = @_; say_($name, $ok ? "ok" : "$!") }
try_("missing file", open(my $m, "<", "$R/missing"));
my $dir_ok = open(my $df, "<", "$R/d");
my $got = $dir_ok ? (defined(sysread($df, my $b, 4)) ? "read" : "$!") : "$!";
say_("a folder opened as a file", $got);
try_("a file opendir'd", opendir(my $od, "$R/a.txt"));
try_("a missing parent", open(my $mp, "<", "$R/nope/x"));
try_("dot dot that escapes", open(my $esc, "<", "$R/../etc/passwd"));
my $cr; my $back = open($cr, "<", "$R/d/../a.txt") ? scalar(<$cr>) : "$!\n";
print "dot dot that climbs and returns: $back";
try_("open to write", open(my $w, ">", "$R/a.txt"));
try_("open to append", open(my $ap, ">>", "$R/a.txt"));
try_("open to create", open(my $c, ">", "$R/new.txt"));
try_("open to truncate", open(my $t, "+>", "$R/a.txt"));
try_("mkdir", mkdir("$R/m"));
try_("unlink", unlink("$R/b.txt"));
try_("rmdir", rmdir("$R/e"));
try_("rename", rename("$R/a.txt", "$R/z.txt"));
try_("link", link("$R/a.txt", "$R/l.txt"));
try_("symlink", symlink("a.txt", "$R/s.txt"));
try_("utime", utime(undef, undef, "$R/a.txt"));
try_("truncate", truncate("$R/a.txt", 0));
my $rl = readlink("$R/a.txt");
say_("readlink of a file", defined $rl ? $rl : "$!");
open(my $sk, "<", "$R/a.txt") or die;
say_("seek past the end", seek($sk, 100, 0) ? tell($sk) : "$!");
say_("read past the end", defined(sysread($sk, my $nb, 4)) ? length($nb) : "$!");
try_("a trailing slash on a file", open(my $ts, "<", "$R/a.txt/"));
try_("a trailing slash and dot on a file", open(my $td, "<", "$R/a.txt/."));
try_("a component of 256 bytes", open(my $lc, "<", "$R/" . ("x" x 256)));
say_("stat kinds", join(",", map { -d "$R/$_" ? "dir" : -f "$R/$_" ? "file" : "none" }
    qw(a.txt d e missing)));
say_("a file's size", -s "$R/a.txt");
opendir(my $ls, "$R/d") or die "$!";
say_("a sorted listing", join(",", sort readdir $ls));
"""

AS_A_GRANT_TREE = {
    "/a.txt": b"hello, mount\n",
    "/b.txt": b"b\n",
    "/d": None,
    "/d/x.txt": b"x\n",
    "/e": None,
}


def test_a_mount_answers_as_a_grant_does(perl, tmp_path) -> None:
    """Port of wasi-python's check of this name: the same tree as an in-memory mount and as a
    read-only grant gives the same result, or the same `$!`, row for row, and every write row
    makes no provider call. A file's times are not printed: a mount's stat of an open file has
    none, one of wasi-python's deliberate differences."""
    root = write_tree(tmp_path / "tree", AS_A_GRANT_TREE)
    granted = run(["-e", AS_A_GRANT, "/R"], grants=[Grant(root, "/R")], artifacts=perl)
    provider = MemoryProvider(dict(AS_A_GRANT_TREE))
    mounted = run(["-e", AS_A_GRANT, "/R"], mounts=[Mount(provider, "/R")], artifacts=perl)
    assert (granted.exit_code, granted.stderr) == (0, b""), granted
    assert (mounted.exit_code, mounted.stderr) == (0, b""), mounted
    assert mounted.stdout.decode().splitlines() == granted.stdout.decode().splitlines()
    assert b"Operation not permitted" in mounted.stdout  # the control: the write rows refused
    assert {k: provider.calls[k] for k in METHODS[3:]} == dict.fromkeys(METHODS[3:], 0)


def test_a_mount_at_the_root(perl) -> None:
    """Port of wasi-python's check of this name: a mount at / serves absolute and relative
    paths; the library still loads from the runtime's own folder, and the provider never sees a
    path under it or under /dev; a runtime path spelled through the mount is EPERM with no
    provider call; and `-e 1` makes a bounded number of provider calls."""
    provider = MemoryProvider(
        {"/a.txt": b"root\n", "/usr": None, "/usr/local": None, "/usr/local/lib": None}
    )
    provider.tree["/usr/local/lib/perl5"] = None
    provider.tree["/usr/local/lib/perl5/strict.pm"] = b"die 'the provider was asked';\n"
    code = (
        'open my $f, "<", "/a.txt" or die "$!"; print <$f>;'
        ' open my $g, "<", "a.txt" or die "$!"; print <$g>;'
        " use POSIX (); use List::Util (); use Data::Dumper (); use strict;"
        ' print open(my $h, "<", "/usr/./local/lib/perl5/strict.pm") ? "opened\\n" : "$!\\n";'
    )
    done = go(perl, code, provider, guest="/")
    assert (done.exit_code, done.stderr) == (0, b""), done
    assert done.stdout == b"root\nroot\nOperation not permitted\n"
    seen = [p for _, p in provider.paths]
    assert seen and not any(p.startswith(("/usr/local/lib/perl5", "/dev")) for p in seen), seen
    quiet = MemoryProvider({})
    assert go(perl, "1", quiet, guest="/").exit_code == 0
    # The bound is twice the count measured on the first run (the landing notes): 0.
    assert quiet.total() <= 2 * 0


# D7 to D10: the bounds.


def test_mount_descriptors_are_bounded(perl, tmp_path, monkeypatch) -> None:
    """Port of wasi-python's check of this name, with the guest table's cap: 300 mount files
    held open at once; the first open past the cap is EMFILE, before any provider call, and so
    is every later one; `read` is called once for each file opened. The placeholder is empty
    while the guest runs, and gone after."""
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    tree: dict[str, bytes | None] = {f"/f{i}": b"x" for i in range(300)}
    seen: list[list[str]] = []

    class Looking(MemoryProvider):
        def read(self, path):
            if not seen:
                (folder,) = list(temp.iterdir())
                seen.append(os.listdir(folder / "mount"))
            return super().read(path)

    provider = Looking(tree)
    code = (
        "my (@held, $opened, $first);"
        ' for my $i (0 .. 299) { if (open my $f, "<", "/work/f$i") { push @held, $f; $opened++ }'
        ' else { $first //= "$i " . ($! + 0) . ($!{EMFILE} ? " EMFILE" : "") } }'
        ' print "$opened\\n$first\\n";'
    )
    done = go(perl, code, provider)
    assert (done.exit_code, done.stderr) == (0, b""), done
    opened, first = done.stdout.decode().splitlines()
    assert first == f"{opened} 33 EMFILE"  # wasi-libc's text: "No file descriptors available"
    assert provider.calls == counts(stat=int(opened), read=int(opened))
    assert seen == [[]]
    assert list(temp.iterdir()) == []


def test_the_file_call_limit_stops_the_run(perl) -> None:
    """Port of wasi-python's check of this name: at `max_file_calls`, the run ends "out of file
    calls" with no exit code and no fuel count, after exactly that many provider calls; the
    default is 10,000, and a default call stops after exactly that many."""
    provider = MemoryProvider(dict(TREE))
    code = 'for (1 .. 50) { -e "/work/a.txt" } print "after the loop\\n";'
    done = go(perl, code, provider, max_file_calls=5, fuel=10**12)
    assert (done.exit_code, done.trapped, done.fuel_used) == (None, "out of file calls", None)
    assert (provider.total(), done.stdout) == (5, b"")
    assert dict(done.files.calls) == provider.calls
    assert defaults().file_calls == 10_000
    provider = MemoryProvider(dict(TREE))
    done = go(perl, code.replace("50", "10001"), provider)
    assert (done.trapped, provider.total(), done.stdout) == ("out of file calls", 10_000, b"")


def test_a_mount_keeps_its_deadline(perl) -> None:
    """The deadline half of wasi-python's check: a provider that moves the ticker past the
    deadline in its third call ends the run "deadline" after exactly 3 provider calls; a guest
    printing in a loop past its deadline ends "deadline" too, never with a raw error."""
    calls = []

    class Passing(MemoryProvider):
        def stat(self, path):
            calls.append(path)
            if len(calls) == 3:
                _engines.TICKER.count += 10**9
            return super().stat(path)

    provider = Passing(dict(TREE))
    done = go(perl, '1 while -e "/work/a.txt"', provider, deadline_s=600)
    assert (done.exit_code, done.trapped, done.fuel_used) == (None, "deadline", None)
    assert provider.total() == 3
    done = go(perl, 'print "x\\n" while 1', MemoryProvider({}), deadline_s=0.2, output_bytes=64)
    assert (done.exit_code, done.trapped) == (None, "deadline")


def test_a_mounted_run_out_of_fuel_counts_the_cap(perl) -> None:
    provider = MemoryProvider(dict(TREE))
    cap = 200_000_000
    done = go(perl, 'open my $f, "<", "/work/a.txt" or die; 1 while 1', provider, fuel=cap)
    assert (done.exit_code, done.trapped, done.fuel_used) == (None, "out of fuel", cap)
    assert provider.calls == counts(stat=1, read=1)


# D11 to D14: errnos and faults.


def test_the_errno_values_are_the_guests(perl) -> None:
    """Port of wasi-python's check of this name: each member a provider answers to a read is
    the guest's own errno, by value, and Perl's `%!` knows it by its name. EISDIR answered to a
    read is a folder, which Perl's open takes, so it is answered to a `stat` instead."""
    for member in Errno:
        provider = MemoryProvider({"/a.txt": b"a"})
        method = "stat" if member is Errno.EISDIR else "read"
        provider.answers[(method, "/a.txt")] = member
        call = 'stat "/work/a.txt"' if method == "stat" else 'open my $f, "<", "/work/a.txt"'
        code = (
            f'{call} and die "opened";'
            f' print $! + 0, " ", ($!{{{member.name}}} ? "named" : "unnamed"), "\\n";'
        )
        done = go(perl, code, provider)
        assert (done.exit_code, done.stdout) == (0, f"{int(member)} named\n".encode()), member


def test_a_raised_oserror_is_the_answer_of_its_errno(perl) -> None:
    provider = MemoryProvider({"/a.txt": b"a"})
    provider.answers[("read", "/a.txt")] = FileNotFoundError(errno.ENOENT, "gone", "/a.txt")
    provider.answers[("stat", "/b.txt")] = OSError(errno.EACCES, "no")
    code = (
        'print open(my $f, "<", "/work/a.txt") ? "opened" : "$!", "\\n";'
        ' print -e "/work/b.txt" ? "there" : "$!", "\\n";'
    )
    done = go(perl, code, provider)
    assert done.stdout == b"No such file or directory\nPermission denied\n"
    assert done.files.faults == ()


def test_an_oserrors_class_answers_when_its_errno_cannot(perl) -> None:
    provider = MemoryProvider({"/a.txt": b"a"})
    for path, error in (
        ("/1", FileNotFoundError()),
        ("/2", FileExistsError()),
        ("/3", NotADirectoryError()),
        ("/4", PermissionError()),
        ("/5", TimeoutError()),
    ):
        provider.answers[("stat", path)] = error
    code = 'for (1 .. 5) { stat "/work/$_" and die; print $! + 0, "\\n" }'
    done = go(perl, code, provider)
    want = [Errno.ENOENT, Errno.EEXIST, Errno.ENOTDIR, Errno.EACCES, Errno.ETIMEDOUT]
    assert done.stdout.decode().split() == [str(int(e)) for e in want]
    assert done.files.faults == ()


def test_a_write_halfs_raised_oserror_is_a_refusal(perl) -> None:
    provider = MemoryProvider({})
    provider.answers[("mkdir", "/m")] = PermissionError(errno.EACCES, "no")
    done = go(perl, 'mkdir "/work/m" and die; print "$!\\n"', provider, writable=True)
    assert done.stdout == b"Permission denied\n"
    assert done.files.refused == (
        RefusedWrite("/work", "mkdir", ("/m",), Errno.EACCES, "call", "refused"),
    )


async def _coroutine() -> None:
    return None


def _faults() -> dict[str, tuple[str, object, bytes]]:
    """Each fault: the method overridden, its answer, and the `$!` the guest prints."""

    class Raises:
        def __iter__(self):
            yield ("ok", "file")
            raise ValueError("listing broke")

    class BadBuffer:
        def __buffer__(self, flags):
            raise ValueError("no buffer")

    eio = b"I/O error"
    return {
        "ValueError": ("read", ValueError("broke"), eio),
        "None": ("read", lambda path: None, eio),
        "text": ("read", lambda path: "text", eio),
        "an object": ("read", lambda path: object(), eio),
        "a coroutine": ("read", lambda path: _coroutine(), eio),
        "a listing that raises": ("list", lambda path: Raises(), eio),
        "a buffer that raises": ("read", lambda path: BadBuffer(), eio),
        **{
            f"a listed {what}": ("list", lambda path, n=name: [n], eio)
            for what, name in (
                ("/", ("a/b", "file")),
                ("NUL", ("a\0", "file")),
                ("..", ("..", "file")),
                ("lone surrogate", ("\ud800", "file")),
                ("256 bytes", ("x" * 256, "file")),
                ("kind link", ("a", "link")),
            )
        },
        "a listed duplicate": ("list", lambda path: [("a", "file"), ("a", "file")], eio),
        "size=True": ("stat", lambda path: Stat("file", size=True), eio),
        "stat('/') a file": ("stat", lambda path: Stat("file"), eio),
        "past the staging bound": ("read", lambda path: b"x" * (64 << 20), b"File too large"),
    }


@pytest.mark.parametrize("fault", list(_faults()))
def test_a_provider_fault_never_escapes(perl, fault) -> None:
    """Port of wasi-python's check of this name: a provider that raises, or answers outside the
    protocol, gives the guest EIO (EFBIG past the staging bound), which it prints, and exits 0;
    `run` raises nothing; the record names the fault, and no "never awaited" warning is
    emitted."""
    method, answer, text = _faults()[fault]
    provider = MemoryProvider({"/a.txt": b"a", "/d": None})
    path = {"list": "/d", "stat": "/", "read": "/a.txt"}[method]
    if fault == "size=True":
        path = "/a.txt"
    provider.answers[(method, path)] = answer
    code = {
        "read": 'print open(my $f, "<", "/work/a.txt") ? "opened" : "$!";',
        "list": 'print opendir(my $d, "/work/d") ? "opened" : "$!";',
        "stat": f'print stat("/work{path.rstrip("/")}") ? "there" : "$!";',
    }[method]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        done = go(perl, code, provider, memory_bytes=32 << 20)
        gc.collect()
    assert (done.exit_code, done.stdout) == (0, text), (fault, done)
    expected = 0 if text == b"File too large" else 1
    assert len(done.files.faults) == expected, done.files.faults
    if expected:
        assert done.files.faults[0].method == method
    assert not [w for w in caught if "never awaited" in str(w.message)]


def test_a_providers_base_exception_is_raised_from_run(perl) -> None:
    class Stop(BaseException):
        pass

    provider = MemoryProvider({"/a.txt": b"a"})
    provider.answers[("read", "/a.txt")] = Stop("the provider's own")
    with pytest.raises(Stop):
        go(perl, 'open my $f, "<", "/work/a.txt"; print "after\\n"; 1 while 1', provider)


def test_an_oserror_that_names_no_member_stays_a_fault(perl) -> None:
    provider = MemoryProvider({"/a.txt": b"a"})
    provider.answers[("read", "/a.txt")] = OSError(errno.EINTR, "interrupted")
    done = go(perl, 'print open(my $f, "<", "/work/a.txt") ? "opened" : "$!"', provider)
    assert (done.exit_code, done.stdout) == (0, b"I/O error")
    assert [f.method for f in done.files.faults] == ["read"]


# D15: calls share nothing.


def test_calls_with_mounts_share_nothing(perl) -> None:
    """Two calls on two threads, each with its own provider, see only their own paths and get
    their own records; a provider that itself calls `run` with a mount of its own gets its
    result, and the outer record holds only the outer provider's calls."""
    results: dict[str, list[bytes]] = {}
    providers = {k: MemoryProvider({"/x": f"from {k}\n".encode()}) for k in "ab"}

    def one(key: str) -> None:
        for _ in range(3):
            done = go(
                perl, 'open my $f, "<", "/m/x" or die; print <$f>', providers[key], guest="/m"
            )
            results.setdefault(key, []).append(done.stdout)

    threads = [threading.Thread(target=one, args=(k,)) for k in "ab"]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == {"a": [b"from a\n"] * 3, "b": [b"from b\n"] * 3}
    for p in providers.values():
        assert p.calls == counts(stat=3, read=3) and {q for _, q in p.paths} == {"/x"}
    inner = MemoryProvider({"/y": b"inner\n"})

    class Nesting(MemoryProvider):
        def read(self, path):
            done = go(perl, 'open my $f, "<", "/i/y" or die; print <$f>', inner, guest="/i")
            return done.stdout

    outer = Nesting({"/x": b""})
    done = go(perl, 'open my $f, "<", "/o/x" or die; print <$f>', outer, guest="/o")
    assert (done.exit_code, done.stdout) == (0, b"inner\n")
    assert dict(done.files.calls) == counts(stat=1, read=1)
    assert inner.calls == counts(stat=1, read=1)


# D16: writable mounts.


def test_a_writable_mount_serves_writes(perl) -> None:
    provider = MemoryProvider({"/a.txt": b"one\n", "/b.txt": b"bee\n"})
    code = (
        'open my $f, ">", "/w/new.txt" or die "$!"; print $f "made\\n"; close $f or die "$!";'
        ' open my $g, ">>", "/w/a.txt" or die "$!"; print $g "two\\n"; close $g or die "$!";'
        ' mkdir "/w/m" or die "$!"; rename "/w/new.txt", "/w/m/new.txt" or die "$!";'
        ' unlink "/w/b.txt" or die "$!";'
    )
    done = go(perl, code, provider, guest="/w", writable=True)
    assert (done.exit_code, done.stderr) == (0, b""), done
    assert provider.tree == {"/a.txt": b"one\ntwo\n", "/m": None, "/m/new.txt": b"made\n"}
    for argv, want in (
        (["-pi", "-e", "s/one/1/", "/w/a.txt"], {"/a.txt": b"1\ntwo\n"}),
        (
            ["-pi.bak", "-e", "s/one/1/", "/w/a.txt"],
            {"/a.txt": b"1\ntwo\n", "/a.txt.bak": b"one\ntwo\n"},
        ),
    ):
        provider = MemoryProvider({"/a.txt": b"one\ntwo\n"})
        done = go(perl, None, provider, guest="/w", writable=True, argv=argv)
        assert (done.exit_code, done.stderr) == (0, b""), (argv, done)
        assert provider.tree == want, argv
        assert done.files.refused == ()


def test_refused_writes_are_recorded_on_exit_0(perl) -> None:
    provider = MemoryProvider({"/a.txt": b"a"})
    provider.answers[("write", "/denied.txt")] = lambda path, data: Errno.EACCES if data else None
    provider.answers[("write", "/create.txt")] = Errno.EACCES
    provider.answers[("mkdir", "/nomk")] = Errno.EACCES
    provider.answers[("rename", "/a.txt")] = Errno.EACCES
    code = (
        'open my $f, ">", "/work/denied.txt" or die "$!"; print $f "x";'
        ' print close($f) ? "closed" : ($!{EACCES} ? "EACCES" : "$!"), "\\n";'
        ' print open(my $c, ">", "/work/create.txt") ? "opened" : "$!", "\\n";'
        ' print mkdir("/work/nomk") ? "made" : "$!", "\\n";'
        ' print rename("/work/a.txt", "/work/z.txt") ? "renamed" : "$!", "\\n";'
    )
    done = go(perl, code, provider, writable=True)
    assert (done.exit_code, done.stderr) == (0, b""), done
    assert done.stdout.decode().splitlines() == ["EACCES"] + ["Permission denied"] * 3
    w = "/work"
    assert done.files.refused == (
        RefusedWrite(w, "write", ("/denied.txt",), Errno.EACCES, "close", "refused"),
        RefusedWrite(w, "write", ("/create.txt",), Errno.EACCES, "open", "refused"),
        RefusedWrite(w, "mkdir", ("/nomk",), Errno.EACCES, "call", "refused"),
        RefusedWrite(w, "rename", ("/a.txt", "/z.txt"), Errno.EACCES, "call", "refused"),
    )
    assert done.files.refused_total == 4
    # A refused rename over a file the guest holds open is tried again, once, after the guest
    # closes and reopens it: a second rename, and a second refusal.
    provider = MemoryProvider({"/a.txt": b"a", "/z.txt": b"z"})
    provider.answers[("rename", "/a.txt")] = Errno.EACCES
    code = 'open my $h, "<", "/work/z.txt" or die; rename("/work/a.txt", "/work/z.txt") and die'
    done = go(perl, code, provider, writable=True)
    assert done.exit_code == 0 and provider.calls["rename"] == 2
    assert [r.method for r in done.files.refused] == ["rename", "rename"]


def test_open_files_follow_the_guests_renames_and_removals(perl) -> None:
    provider = MemoryProvider({"/d": None})
    code = (
        'open my $f, ">", "/w/a.txt" or die "$!"; print $f "A"; rename "/w/a.txt", "/w/b.txt"'
        ' or die "$!"; close $f or die "$!";'
        ' open my $g, ">", "/w/c.txt" or die "$!"; print $g "C";'
        ' open my $h, ">", "/w/e.txt" or die; close $h; rename "/w/e.txt", "/w/c.txt" or die "$!";'
        " close $g;"
        ' use File::Temp qw(tempfile); my ($t, $name) = tempfile(DIR => "/w"); print $t "T";'
        ' close $t or die "$!"; print -s $name, "\\n";'
        ' opendir my $dh, "/w/d" or die "$!"; rmdir "/w/d" or die "$!"; mkdir "/w/d" or die "$!";'
        ' open my $x, ">", "/w/d/x" or die "$!"; close $x;'
        ' my @left = readdir $dh; print scalar(@left), "\\n";'
    )
    done = go(perl, code, provider, guest="/w", writable=True)
    assert (done.exit_code, done.stderr) == (0, b""), done
    assert provider.tree["/b.txt"] == b"A" and "/a.txt" not in provider.tree
    assert provider.tree["/c.txt"] == b""  # the held file renamed over is never written back
    temps = [p for p in provider.tree if p not in ("/b.txt", "/c.txt", "/d", "/d/x")]
    assert len(temps) == 1 and provider.tree[temps[0]] == b"T"
    size, left = done.stdout.decode().splitlines()
    assert (size, left) == ("1", "2")  # the old handle holds its dots alone


# Each ending leaves a file written with syswrite and not closed by the guest; the `tick` stat
# moves the ticker past the deadline.
ENDINGS = {
    "exit 0": ("exit 0", (0, None)),
    "POSIX::_exit(3)": ("use POSIX (); POSIX::_exit(3)", (3, None)),
    "an uncaught die": ('die "late\\n"', (255, None)),
    "its fuel cap": ("1 while 1", (None, "out of fuel")),
    "its deadline": ('-e "/work/tick"; 1 while 1', (None, "deadline")),
}
ENDING = 'open my $f, ">", "/work/f.txt" or die "$!"; syswrite $f, "data" or die "$!"; '


@pytest.mark.parametrize("ending", list(ENDINGS))
def test_writes_land_at_every_ending(perl, ending) -> None:
    code, (exit_code, trapped) = ENDINGS[ending]

    class Ticking(MemoryProvider):
        def stat(self, path):
            if path == "/tick":
                _engines.TICKER.count += 10**9
            return super().stat(path)

    provider = Ticking({})
    fuel = 300_000_000 if trapped == "out of fuel" else None
    done = go(perl, ENDING + code, provider, writable=True, fuel=fuel, deadline_s=600)
    assert (done.exit_code, done.trapped) == (exit_code, trapped), done
    assert provider.log == [("write", "/f.txt", b""), ("write", "/f.txt", b"data")]
    assert (done.files.refused, done.files.refused_total) == ((), 0)
    if fuel is not None:
        assert done.fuel_used == fuel
    provider = MemoryProvider({})
    code = ENDING + 'for (1 .. 50) { -e "/work/f.txt" }'
    done = go(perl, code, provider, writable=True, max_file_calls=5)
    assert (done.exit_code, done.trapped, done.fuel_used) == (None, "out of file calls", None)
    assert provider.writes() == [b""] and provider.total() == 5
    assert done.files.refused == (
        RefusedWrite("/work", "write", ("/f.txt",), Errno.EIO, "exit", "limit"),
    )


def test_a_failed_write_back_at_the_ending_is_recorded(perl) -> None:
    provider = MemoryProvider({})
    provider.answers[("write", "/f.txt")] = lambda path, data: Errno.ENOSPC if data else None
    done = go(perl, ENDING + "use POSIX (); POSIX::_exit(0)", provider, writable=True)
    assert done.exit_code == 0
    assert done.files.refused == (
        RefusedWrite("/work", "write", ("/f.txt",), Errno.ENOSPC, "exit", "refused"),
    )


def test_unlinked_files_are_not_written_back_at_the_ending(perl) -> None:
    provider = MemoryProvider({})
    code = ENDING + 'unlink "/work/f.txt" or die "$!"; use POSIX (); POSIX::_exit(0)'
    done = go(perl, code, provider, writable=True)
    assert done.exit_code == 0
    assert provider.writes() == [b""] and "/f.txt" not in provider.tree


WRITABLE_ROWS = r"""
no warnings;
use Fcntl; use File::Copy qw(copy move); use File::Path qw(remove_tree);
my $R = $ARGV[0];
sub r { my ($name, $ok) = @_; print "$name: ", ($ok ? "ok" : "$!"), "\n" }
sub put { open my $f, ">", $_[0] or return 0; print $f $_[1]; close $f }
r("create", put("$R/cr.txt", "c"));
r("an exclusive create of an existing name", sysopen(my $x, "$R/a.txt", O_WRONLY|O_CREAT|O_EXCL));
r("create under a missing parent", put("$R/nope/x", "x"));
r("create under a file", put("$R/a.txt/x", "x"));
r("open a folder to write", open(my $df, ">", "$R/d"));
r("append", do { open my $f, ">>", "$R/b.txt" or 0; print $f "more"; close $f });
r("truncate", truncate("$R/b.txt", 1));
r("a write past the end", do { open my $f, "+<", "$R/a.txt" or 0; seek $f, 20, 0;
    print $f "z"; close $f });
r("a write on a read-only handle", do { open my $f, "<", "$R/a.txt" or 0;
    defined syswrite($f, "q") });
r("mkdir", mkdir("$R/g")); r("mkdir again", mkdir("$R/g")); r("mkdir of a file", mkdir("$R/a.txt"));
r("rmdir of a file", rmdir("$R/a.txt")); r("rmdir of a full folder", rmdir("$R/d"));
r("rmdir", rmdir("$R/e")); r("rmdir of a missing folder", rmdir("$R/e"));
r("unlink of a folder", unlink("$R/d")); r("unlink of a missing file", unlink("$R/zz"));
r("rename", rename("$R/cr.txt", "$R/h")); r("rename a missing name", rename("$R/zz", "$R/y"));
r("rename a folder over a file", rename("$R/g", "$R/b.txt"));
r("rename to a read-only grant", rename("$R/h", "/ro/h"));
r("link to a read-only grant", link("$R/h", "/ro/h"));
r("copy", copy("$R/a.txt", "$R/copy.txt")); r("move", move("$R/copy.txt", "$R/save.txt"));
r("remove_tree", remove_tree("$R/d") > 0);
print "size: ", -s "$R/save.txt", "\n";
opendir my $l, $R or die; print "listing: ", join(",", sort grep { !/^\./ } readdir $l), "\n";
"""
WRITABLE_TREE = {"/a.txt": b"hello\n", "/b.txt": b"b\n", "/d": None, "/d/x.txt": b"x\n", "/e": None}


def test_a_writable_mount_answers_as_a_writable_grant_does(perl, tmp_path) -> None:
    root = write_tree(tmp_path / "tree", WRITABLE_TREE)
    ro = tmp_path / "ro"
    ro.mkdir()
    granted = run(
        ["-e", WRITABLE_ROWS, "/R"],
        grants=[Grant(root, "/R", writable=True), Grant(ro, "/ro")],
        cwd="/R",  # File::Path's remove_tree stats its working folder
        artifacts=perl,
    )
    provider = MemoryProvider(dict(WRITABLE_TREE))
    mounted = run(
        ["-e", WRITABLE_ROWS, "/R"],
        grants=[Grant(ro, "/ro")],
        mounts=[Mount(provider, "/R", writable=True)],
        cwd="/R",
        artifacts=perl,
    )
    # remove_tree's chmod is not in WASI: both sides warn alike, and go on.
    assert granted.exit_code == mounted.exit_code == 0, (granted, mounted)
    assert mounted.stderr == granted.stderr
    assert mounted.stdout.decode().splitlines() == granted.stdout.decode().splitlines()
    assert on_disk(root) == provider.tree


def test_links_and_times_in_a_writable_mount(perl) -> None:
    provider = MemoryProvider({"/a.txt": b"a"})
    code = (
        'print link("/w/a.txt", "/w/l") ? "linked" : "$!", "\\n";'
        ' print symlink("a.txt", "/w/s") ? "linked" : "$!", "\\n";'
        ' print utime(1, 2, "/w/a.txt") ? "set" : "$!", "\\n";'
        ' print utime(1, 2, "/w/missing") ? "set" : "$!", "\\n";'
    )
    done = go(perl, code, provider, guest="/w", writable=True)
    assert done.stdout.decode().splitlines() == [
        "Not supported",
        "Not supported",
        "set",
        "No such file or directory",
    ]
    assert {k: provider.calls[k] for k in METHODS[3:]} == dict.fromkeys(METHODS[3:], 0)


def test_a_call_that_raises_writes_nothing_back(perl) -> None:
    """A provider whose call raises KeyboardInterrupt, and a Ctrl-C the test sends itself, each
    with a dirty file open: `run` raises, and no content write is made."""
    import signal

    if threading.current_thread() is not threading.main_thread():
        pytest.skip("Ctrl-C reaches only the main thread")
    provider = MemoryProvider({})
    provider.answers[("stat", "/stop")] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        go(perl, ENDING + '-e "/work/stop"; 1 while 1', provider, writable=True)
    assert provider.writes() == [b""]

    class Interrupting(MemoryProvider):
        def stat(self, path):
            if path == "/stop":
                signal.raise_signal(signal.SIGINT)
            return super().stat(path)

    provider = Interrupting({})
    with pytest.raises(KeyboardInterrupt):
        go(perl, ENDING + '-e "/work/stop"; 1 while 1', provider, writable=True)
    assert provider.writes() == [b""]


# D17: cwd in a mount.


def test_cwd_in_a_mount(perl, tmp_path) -> None:
    provider = MemoryProvider({"/d": None, "/d/a.txt": b"in d\n"})
    done = go(perl, 'open my $f, "<", "a.txt" or die "$!"; print <$f>', provider, cwd="/work/d")
    assert (done.exit_code, done.stdout) == (0, b"in d\n"), done
    assert provider.paths[0] == ("stat", "/d")
    assert Counter(p for m, p in provider.paths if m == "stat")["/d"] == 1
    provider = MemoryProvider({})
    code = 'print open(my $f, "<", "a.txt") ? "opened" : "$!"'
    done = go(perl, code, provider, cwd="/work/gone")
    assert (done.exit_code, done.stdout) == (0, b"No such file or directory")
    assert provider.paths[0] == ("stat", "/gone")
    with pytest.raises(ValueError, match="not inside a grant or a mount"):
        run(["-e", "1"], mounts=[Mount(provider, "/work")], cwd="/elsewhere", artifacts=perl)
    (tmp_path / "g").mkdir()
    done = run(
        ["-e", "print -d '.' ? 'grant' : 'none'"],
        grants=[Grant(tmp_path, "/work/g")],
        mounts=[Mount(MemoryProvider({}), "/work")],
        cwd="/work/g",
        artifacts=perl,
    )
    assert done.stdout == b"grant"  # the longest guest path serves it: the grant
