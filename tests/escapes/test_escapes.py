"""Sandbox escapes, tried by real Perl through `run()`: reads outside a grant, `..` and absolute
climbs, links out of a grant, every write to a read-only grant, in-place edits, nothing writable
by default, the runtime's own files, the network, the module cache and planted code. Ported from
wasi-python's hostile-guest checks. Each guest prints one line per attempt, `NAME ok` or
`NAME refused <errno text>`, and each check also compares the host's files before and after."""

from __future__ import annotations

import dataclasses
import hashlib
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from wasi_perl import Grant, run

pytestmark = pytest.mark.slow

# Perl helpers every guest starts with: `t(NAME, CODE)` runs CODE in an eval and prints the line.
PRELUDE = r"""
use strict; use warnings; no warnings 'once';
$| = 1;
sub t {
    my ($name, $code) = @_;
    my $r = eval { $code->() };
    if ($r) { print "$name ok\n"; return }
    my $e = $@ || "$!";
    $e =~ s/\s+$//;
    print "$name refused $e\n";
}
sub slurp {
    my ($p) = @_;
    open(my $f, "<", $p) or return 0;
    local $/;
    my $d = <$f>;
    print "READ[$d]\n";
    1;
}
"""


@pytest.fixture
def perl_set(perl):
    """The real perl.wasm's set (the shared `perl` fixture, renamed for this file's helper)."""
    return perl


def perl_quote(text: str) -> str:
    """`text` as a single-quoted Perl string, so a Windows path's backslashes stay as they are."""
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def perl(code: str, **kw):
    return run(["-e", PRELUDE + textwrap.dedent(code)], **kw)


NOENT = "No such file or directory"
PERM = "Operation not permitted"
EITHER = (NOENT, PERM)  # the wall answers one or the other, by where the path stops


def lines(done) -> dict[str, str]:
    """The guest's rows; it must have run to its end, so a guest that stops early fails."""
    assert (done.exit_code, done.trapped) == (0, None), done
    out = {}
    for line in done.stdout.decode().splitlines():
        name, _, rest = line.partition(" ")
        out[name] = rest
    return out


def refused(seen: dict[str, str], *names: str) -> None:
    for name in names:
        assert seen[name].startswith("refused"), (name, seen)


def expect(done, want: dict[str, str | tuple[str, ...]]) -> dict[str, str]:
    """Exactly the rows `want` names, each refused with its text (one of them, for a tuple), or
    "ok" for a row that must succeed."""
    seen = lines(done)
    assert sorted(seen) == sorted(want), (sorted(seen), sorted(want))
    for name, text in want.items():
        if text == "ok":
            assert seen[name] == "ok", (name, seen[name])
            continue
        texts = (text,) if isinstance(text, str) else text
        assert seen[name].startswith("refused"), (name, seen[name])
        assert any(t in seen[name] for t in texts), (name, seen[name], texts)
    return seen


def tree(folder: Path) -> list[tuple[str, int, int, bytes]]:
    """Names, modes, times and bytes of `folder` itself and everything under it, links as their
    targets' text."""
    top = folder.lstat()
    rows = [(".", top.st_mode, top.st_mtime_ns, b"")]
    for p in sorted(folder.rglob("*")):
        st = p.lstat()
        if p.is_symlink():
            data = os.readlink(p).encode()
        elif p.is_file():
            data = p.read_bytes()
        else:
            data = b""
        rows.append((str(p.relative_to(folder)), st.st_mode, st.st_mtime_ns, data))
    return rows


@pytest.fixture
def host(tmp_path: Path) -> Path:
    """ro/ (f.txt, sub/), rw/ (w.txt), outside/ (secret.txt)."""
    for d in ("ro", "ro/sub", "rw", "outside"):
        (tmp_path / d).mkdir()
    (tmp_path / "ro" / "f.txt").write_bytes(b"a original\n")
    (tmp_path / "rw" / "w.txt").write_bytes(b"a writable\n")
    (tmp_path / "outside" / "secret.txt").write_bytes(b"TOP SECRET\n")
    return tmp_path


def _grants(host: Path) -> list[Grant]:
    return [Grant(host / "ro", "/ro"), Grant(host / "rw", "/rw", writable=True)]


def test_a_read_outside_every_grant_is_not_found(perl_set, host):
    secret = host / "outside" / "secret.txt"
    done = perl(
        f"""
        t("passwd", sub {{ slurp("/etc/passwd") }});
        t("outside", sub {{ slurp("/outside/secret.txt") }});
        t("host-path", sub {{ slurp({perl_quote(str(secret))}) }});
        t("tmp", sub {{ slurp("/tmp/x") }});
        t("relative", sub {{ slurp("secret.txt") }});
        t("root-dir", sub {{ opendir(my $d, "/") }});
        t("root-up", sub {{ opendir(my $d, "/..") }});
        """,
        grants=_grants(host),
        artifacts=perl_set,
    )
    names = ("passwd", "outside", "host-path", "tmp", "relative", "root-dir", "root-up")
    expect(done, dict.fromkeys(names, NOENT))
    assert b"SECRET" not in done.stdout


def test_dot_dot_cannot_climb_out_of_a_grant(perl_set, host):
    code = """
        t("up", sub { slurp("/ro/../outside/secret.txt") });
        t("up2", sub { slurp("/ro/sub/../../outside/secret.txt") });
        t("rel-up", sub { slurp("../outside/secret.txt") });
        t("rel-far", sub { slurp("../../../../etc/passwd") });
        t("rel-up-write", sub { open(my $f, ">", "../outside/planted") });
        """
    before = (tree(host / "outside"), tree(host / "ro"))
    want = dict.fromkeys(("up", "up2", "rel-up", "rel-far", "rel-up-write"), PERM)
    done = perl(code, grants=_grants(host), cwd="/ro", artifacts=perl_set)
    expect(done, want)
    assert b"SECRET" not in done.stdout
    # From a writable grant too.
    (host / "rw" / "sub").mkdir()
    done = perl(code.replace("/ro", "/rw"), grants=_grants(host), cwd="/rw", artifacts=perl_set)
    expect(done, want)
    assert b"SECRET" not in done.stdout
    assert (tree(host / "outside"), tree(host / "ro")) == before
    assert sorted(p.name for p in host.iterdir()) == ["outside", "ro", "rw"]


def test_an_absolute_climb_is_refused(perl_set, host):
    done = perl(
        """
        t("lib-climb", sub { slurp("/usr/local/lib/perl5/../../../../etc/passwd") });
        t("dev-climb", sub { slurp("/dev/../etc/passwd") });
        t("lib-to-grant", sub { slurp("/usr/local/lib/perl5/../../../../ro/f.txt") });
        """,
        grants=_grants(host),
        artifacts=perl_set,
    )
    expect(done, dict.fromkeys(("lib-climb", "dev-climb", "lib-to-grant"), PERM))


@pytest.mark.skipif(os.name == "nt", reason="POSIX links")
def test_a_host_made_link_out_of_a_grant_is_refused(perl_set, host):
    outside = host / "outside"
    for g in ("ro", "rw"):
        (host / g / "abs-dir").symlink_to(outside)
        (host / g / "rel-dir").symlink_to("../outside")
        (host / g / "abs-file").symlink_to(outside / "secret.txt")
        (host / g / "rel-file").symlink_to("../outside/secret.txt")
    before = tree(outside)
    names = []
    code = []
    for g in ("ro", "rw"):
        for link in ("abs-dir/secret.txt", "rel-dir/secret.txt", "abs-file", "rel-file"):
            name = f"{g}:{link}:read"
            names.append(name)
            code.append(f't("{name}", sub {{ slurp("/{g}/{link}") }});')
        for link in ("abs-dir/new", "rel-dir/new", "abs-file", "rel-file"):
            name = f"rw:{link}:write"
            if g == "rw":
                names.append(name)
                code.append(f't("{name}", sub {{ open(my $f, ">>", "/rw/{link}") }});')
        names.append(f"{g}:opendir")
        code.append(f't("{g}:opendir", sub {{ opendir(my $d, "/{g}/abs-dir") }});')
    done = perl("\n".join(code), grants=_grants(host), artifacts=perl_set)
    expect(done, dict.fromkeys(names, PERM))
    assert b"SECRET" not in done.stdout
    assert tree(outside) == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX links")
def test_a_guest_made_link_out_of_a_grant_is_refused(perl_set, host):
    outside = host / "outside"
    before = tree(outside)
    done = perl(
        f"""
        t("make-up", sub {{ symlink("../outside", "/rw/up") }});
        t("make-host", sub {{ symlink("{outside}", "/rw/host") }});
        t("make-etc", sub {{ symlink("/etc", "/rw/etc") }});
        t("make-file", sub {{ symlink("../outside/secret.txt", "/rw/file") }});
        t("read-up", sub {{ slurp("/rw/up/secret.txt") }});
        t("read-host", sub {{ slurp("/rw/host/secret.txt") }});
        t("read-etc", sub {{ slurp("/rw/etc/passwd") }});
        t("read-file", sub {{ slurp("/rw/file") }});
        t("write-up", sub {{ open(my $f, ">", "/rw/up/planted") }});
        t("write-file", sub {{ open(my $f, ">>", "/rw/file") }});
        t("rename-slash", sub {{ rename("/rw/up/", "/rw/stolen") }});
        t("stolen-read", sub {{ slurp("/rw/stolen/secret.txt") }});
        t("list-up", sub {{ opendir(my $d, "/rw/up") }});
        """,
        grants=_grants(host),
        artifacts=perl_set,
    )
    seen = expect(
        done,
        {
            "make-up": "ok",  # made, but never followed: wasmtime refuses the climb
            "make-host": PERM,
            "make-etc": PERM,
            "make-file": "ok",
            "read-up": PERM,
            "read-host": NOENT,
            "read-etc": NOENT,
            "read-file": PERM,
            "write-up": PERM,
            "write-file": PERM,
            "rename-slash": "Not a directory",
            "stolen-read": NOENT,
            "list-up": PERM,
        },
    )
    assert b"SECRET" not in done.stdout
    assert tree(outside) == before
    assert not (host / "rw" / "stolen").exists() and not (host / "stolen").exists()
    print(seen)  # which links were made at all, for the landing notes


READ_ONLY = r"""
# Fcntl is an XS module; a build without it the sysopen row says so instead of refusing.
my $creat = eval { require Fcntl; Fcntl::O_WRONLY() | Fcntl::O_CREAT() };
t("open>", sub { open(my $f, ">", "/ro/f.txt") });
t("open>>", sub { open(my $f, ">>", "/ro/f.txt") });
t("open+<", sub { open(my $f, "+<", "/ro/f.txt") });
t("open+>", sub { open(my $f, "+>", "/ro/f.txt") });
t("open>new", sub { open(my $f, ">", "/ro/new.txt") });
if (defined $creat) { t("sysopen-creat", sub { sysopen(my $f, "/ro/new2.txt", $creat) }) }
else { print "sysopen-creat absent Fcntl\n" }
t("unlink", sub { unlink("/ro/f.txt") });
t("rename-inside", sub { rename("/ro/f.txt", "/ro/g.txt") });
t("rename-out", sub { rename("/ro/f.txt", "/rw/stolen.txt") });
t("rename-in", sub { rename("/rw/w.txt", "/ro/w.txt") });
t("mkdir", sub { mkdir("/ro/d") });
t("rmdir", sub { rmdir("/ro/sub") });
t("symlink", sub { symlink("f.txt", "/ro/l") });
t("link-in-place", sub { link("/ro/f.txt", "/ro/h") });
t("link-out", sub { link("/ro/f.txt", "/rw/h") });
t("link-in", sub { link("/rw/w.txt", "/ro/h") });
t("chmod", sub { chmod(0777, "/ro/f.txt") });
t("utime", sub { utime(1, 1, "/ro/f.txt") });
t("utime-dir", sub { utime(1, 1, "/ro") });
t("truncate-name", sub { truncate("/ro/f.txt", 0) });
t("truncate-handle", sub { open(my $f, "<", "/ro/f.txt") or die "$!\n"; truncate($f, 0) });
t("write-read-handle", sub { open(my $f, "<", "/ro/f.txt") or die "$!\n";
                              syswrite($f, "x") });
t("control", sub { open(my $f, ">", "/rw/control.txt") or die "$!\n";
                   print $f "written"; close $f });
"""


def test_a_read_only_grant_refuses_every_write(perl_set, host):
    ro = host / "ro"
    before = tree(ro)
    done = perl(READ_ONLY, grants=_grants(host), artifacts=perl_set)
    names = [line.split('"')[1] for line in READ_ONLY.splitlines() if line.startswith('t("')]
    want: dict[str, str | tuple[str, ...]] = dict.fromkeys(names, PERM)
    want["chmod"] = "Function not implemented"  # WASI preview1 has no chmod
    want["write-read-handle"] = "Bad file descriptor"
    want["control"] = "ok"
    fcntl = b"sysopen-creat absent Fcntl\n" not in done.stdout  # a build before the XS modules
    if fcntl:
        want["sysopen-creat"] = PERM
    else:
        done = dataclasses.replace(
            done, stdout=done.stdout.replace(b"sysopen-creat absent Fcntl\n", b"")
        )
    seen = expect(done, want)
    assert len(seen) == (23 if fcntl else 22)
    assert tree(ro) == before
    assert (host / "rw" / "control.txt").read_text() == "written"
    assert (host / "rw" / "w.txt").read_text() == "a writable\n"
    assert sorted(p.name for p in (host / "rw").iterdir()) == ["control.txt", "w.txt"]
    print(seen)


def test_an_in_place_edit_of_a_read_only_grant_changes_nothing(perl_set, host):
    before = (tree(host / "ro"), _digest_tree(perl_set.lib))
    for argv, cwd in (
        (["-pi", "-e", "s/a/b/", "/ro/f.txt"], None),
        (["-pi.bak", "-e", "s/a/b/", "/ro/f.txt"], None),
        (["-pi", "-e", "s/a/b/", "/usr/local/lib/perl5/5.42/strict.pm"], None),
        (["-pi", "-e", "s/a/b/", "../ro/f.txt"], "/rw"),
    ):
        done = run(argv, grants=_grants(host), cwd=cwd, artifacts=perl_set)
        # Perl skips a file it cannot edit with a warning and goes on, natively too: exit 0.
        assert (done.exit_code, done.trapped, done.stdout) == (0, None, b""), (argv, done)
        assert b"Can't" in done.stderr and PERM.encode() in done.stderr, (argv, done.stderr)
        print("in-place:", argv, done.stderr)
    # Through a link the guest makes from the writable grant into the read-only one.
    made = perl(
        't("link", sub { symlink("../ro/f.txt", "/rw/rolink") });',
        grants=_grants(host),
        artifacts=perl_set,
    )
    if os.name == "nt":
        # A Windows host lets no unprivileged process make a link, so the guest's is refused,
        # and there is no link to edit through.
        expect(made, {"link": PERM})
    else:
        expect(made, {"link": "ok"})  # made, never followed
        done = run(["-pi", "-e", "s/a/b/", "/rw/rolink"], grants=_grants(host), artifacts=perl_set)
        assert (done.exit_code, done.stdout) == (0, b"") and PERM.encode() in done.stderr, done
    assert (tree(host / "ro"), _digest_tree(perl_set.lib)) == before
    # The control: the same edit in the writable grant changes the file.
    done = run(["-pi", "-e", "s/a/b/", "/rw/w.txt"], grants=_grants(host), artifacts=perl_set)
    assert (done.exit_code, done.stderr) == (0, b""), done
    assert (host / "rw" / "w.txt").read_bytes() == b"b writable\n"


def _digest_tree(folder: Path) -> str:
    """Names, modes, times and bytes of `folder` and everything under it, as one digest."""
    h = hashlib.sha256()
    for row in tree(folder):
        h.update(repr(row[:3]).encode() + b"\0" + row[3] + b"\0")
    return h.hexdigest()


def test_nothing_is_writable_without_a_grant(perl_set, tmp_path):
    before = (_digest_tree(perl_set.lib), _digest_tree(perl_set.dev), tree(tmp_path))
    done = perl(
        """
        t("root", sub { open(my $f, ">", "/x") });
        t("relative", sub { open(my $f, ">", "x") });
        t("tmp", sub { open(my $f, ">", "/tmp/x") });
        t("lib-new", sub { open(my $f, ">", "/usr/local/lib/perl5/x") });
        t("lib-append", sub { open(my $f, ">>", "/usr/local/lib/perl5/5.42/strict.pm") });
        t("mkdir-tmp", sub { mkdir("/tmp") });
        t("list-root", sub { opendir(my $d, "/") });
        t("compute", sub { 6 * 7 == 42 });
        """,
        artifacts=perl_set,
    )
    want: dict[str, str | tuple[str, ...]] = dict.fromkeys(
        ("root", "relative", "tmp", "mkdir-tmp", "list-root"), NOENT
    )
    want.update({"lib-new": PERM, "lib-append": PERM, "compute": "ok"})
    expect(done, want)
    assert (_digest_tree(perl_set.lib), _digest_tree(perl_set.dev), tree(tmp_path)) == before


def test_the_library_and_dev_null_are_not_writable(perl_set, host):
    before = (_digest_tree(perl_set.lib), _digest_tree(perl_set.dev))
    done = perl(
        """
        t("append-strict", sub { open(my $f, ">>", "/usr/local/lib/perl5/5.42/strict.pm") });
        t("create-in-lib", sub { open(my $f, ">", "/usr/local/lib/perl5/5.42/Evil.pm") });
        t("unlink-lib", sub { unlink("/usr/local/lib/perl5/5.42/strict.pm") });
        t("rename-lib", sub { rename("/usr/local/lib/perl5/5.42/strict.pm", "/rw/strict.pm") });
        t("null>", sub { open(my $f, ">", "/dev/null") });
        t("null>>", sub { open(my $f, ">>", "/dev/null") });
        t("dev-new", sub { open(my $f, ">", "/dev/tty") });
        t("null-read", sub { open(my $n, "<", "/dev/null") or die "$!\\n"; !defined <$n> });
        """,
        grants=_grants(host),
        artifacts=perl_set,
    )
    want: dict[str, str | tuple[str, ...]] = dict.fromkeys(
        ("append-strict", "create-in-lib", "unlink-lib", "rename-lib", "null>", "null>>"), PERM
    )
    want.update({"dev-new": PERM, "null-read": "ok"})  # reading /dev/null gives end of file
    expect(done, want)
    assert (_digest_tree(perl_set.lib), _digest_tree(perl_set.dev)) == before
    assert sorted(p.name for p in (host / "rw").iterdir()) == ["w.txt"]


def test_there_is_no_network(perl_set):
    done = perl(
        """
        t("socket", sub { socket(my $s, 2, 1, 0) });
        my $inet = eval { require IO::Socket::INET; 1 };
        if ($inet) {
            t("inet", sub { IO::Socket::INET->new(PeerAddr => "127.0.0.1", PeerPort => 9,
                                                  Proto => "tcp", Timeout => 1) });
        } else {
            print "inet absent\\n";
        }
        """,
        artifacts=perl_set,
    )
    assert b"connected" not in done.stdout, done.stdout
    want: dict[str, str | tuple[str, ...]] = {"socket": "socket"}  # refused, by whichever text
    if b"inet absent\n" in done.stdout:  # a build before the socket modules
        done = dataclasses.replace(done, stdout=done.stdout.replace(b"inet absent\n", b""))
    else:
        want["inet"] = ""
    print(expect(done, want))


def test_the_module_cache_is_never_reachable(perl_set, host):
    from wasi_perl import _engines

    cache = host / "cache"
    _engines.forget()  # so this call writes its .cwasm to this cache
    run(["-e", "1"], cache_dir=cache, artifacts=perl_set)
    (cwasm,) = list(cache.iterdir())
    before = tree(cache)
    (host / "rw" / "to-cache").symlink_to("../cache")
    done = perl(
        f"""
        t("climb-list", sub {{ opendir(my $d, "/rw/../cache") }});
        t("climb-read", sub {{ slurp("/rw/../cache/{cwasm.name}") }});
        t("climb-write", sub {{ open(my $f, ">", "/rw/../cache/{cwasm.name}") }});
        t("link-list", sub {{ opendir(my $d, "/rw/to-cache") }});
        t("link-write", sub {{ open(my $f, ">", "/rw/to-cache/{cwasm.name}") }});
        t("host-path", sub {{ open(my $f, ">", "{cwasm}") }});
        """,
        grants=_grants(host),
        cache_dir=cache,
        artifacts=perl_set,
    )
    names = ("climb-list", "climb-read", "climb-write", "link-list", "link-write")
    want: dict[str, str | tuple[str, ...]] = dict.fromkeys(names, PERM)
    want["host-path"] = NOENT
    expect(done, want)
    assert tree(cache) == before
    for grant in (Grant(cache, "/c"), Grant(cache, "/c", writable=True), Grant(host, "/h", True)):
        with pytest.raises(ValueError, match="module cache"):
            run(["-e", "1"], grants=[grant], cache_dir=cache, artifacts=perl_set)


PLANT = r"""
my @at = ("/usr/local/lib/perl5/5.42", "/usr/local/lib/perl5/site_perl",
          "/usr/local/lib/perl5/site_perl/5.42", "/usr/./local/lib/perl5/5.42",
          "//usr/local/lib/perl5/5.42", "/usr/local/lib/perl5/5.42/../5.42");
for my $i (0 .. $#at) {
    t("plant$i", sub { open(my $f, ">", "$at[$i]/Evil.pm") or die "$!\n";
                       print $f "print qq{EVIL\\n}; 1;\n"; close $f });
}
t("plant-grant", sub { open(my $f, ">", "/rw/Evil.pm") or die "$!\n";
                       print $f "print qq{EVIL\\n}; 1;\n"; close $f });
"""


def test_a_writable_grant_cannot_plant_code_in_the_library_path(perl_set, host):
    for guest in ("/usr/local/lib/perl5/site_perl", "/usr/local/lib/perl5/5.42", "/usr/local"):
        with pytest.raises(ValueError, match="runtime"):
            run(["-e", "1"], grants=[Grant(host / "rw", guest, True)], artifacts=perl_set)
    with pytest.raises(ValueError, match="runtime"):
        run(["-e", "1"], grants=[Grant(perl_set.lib, "/lib", True)], artifacts=perl_set)
    before = (_digest_tree(perl_set.lib), tree(host / "ro"))
    plant = perl(PLANT, grants=_grants(host), artifacts=perl_set)
    want: dict[str, str | tuple[str, ...]] = {f"plant{i}": EITHER for i in range(6)}
    want["plant-grant"] = "ok"
    print(expect(plant, want))
    probe = run(
        [
            "-e",
            'print "INC $_\\n" for @INC; print eval { require Evil; 1 } ? "LOADED\\n" : "no\\n"',
        ],
        grants=_grants(host),
        cwd="/rw",
        artifacts=perl_set,
    )
    assert probe.exit_code == 0, probe
    out = probe.stdout.decode().splitlines()
    assert out[-1] == "no" and "EVIL" not in probe.stdout.decode()
    inc = [line[4:] for line in out if line.startswith("INC ")]
    assert inc and all(p.startswith("/usr/local/lib/perl5") for p in inc), inc
    assert (_digest_tree(perl_set.lib), tree(host / "ro")) == before
    assert sorted(p.name for p in (host / "rw").iterdir()) == ["Evil.pm", "w.txt"]
    print(inc)


# A host's open of a FIFO waits in the kernel for the other end, where the deadline and fuel
# cannot reach. The guest looks at what a name is before it asks the host to open it, and
# refuses anything that is not a folder, a regular file or a link it will not follow. Each run
# is in a child, so a regression ends the child at its fail-safe instead of holding the suite.
FIFO_PROGRAM = r"""
for my $try (["read", "<"], ["write", ">"], ["append", ">>"]) {
    my ($what, $mode) = @$try;
    open(my $f, $mode, "pipe") ? print "$what opened\n" : print "$what refused $!\n";
}
symlink("pipe", "link") or die "symlink: $!";
open(my $l, "<", "link") ? print "link opened\n" : print "link refused $!\n";
print utime(undef, undef, "pipe") ? "utime now\n" : "utime now: $!\n";
print utime(1, 1, "pipe") ? "utime set\n" : "utime set: $!\n";
print utime(undef, undef, "link") ? "utime link\n" : "utime link: $!\n";
print -e "pipe" ? "still there\n" : "gone\n";
"""

FIFO_CHILD = r"""
import sys
from wasi_perl import Artifacts, Grant, run
folder, artifacts, code = sys.argv[1:4]
done = run(["-e", code], grants=[Grant(folder, "/g", writable=True)], cwd="/g",
           fuel=2_000_000_000, deadline_s=5, artifacts=Artifacts.at(artifacts))
print(done.exit_code, done.trapped, done.fuel_used is not None)
sys.stdout.write(done.stdout.decode())
sys.stdout.write(done.stderr.decode())
"""


def _child(folder: Path, perl_set) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", FIFO_CHILD, str(folder), str(perl_set.perl.parent), FIFO_PROGRAM],
        capture_output=True,
        text=True,
        timeout=20,  # a fail-safe for the suite, not a measurement
    )


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs")
def test_a_fifo_in_a_grant_is_refused(perl_set, tmp_path):
    # The control: the same child, a regular file at that name, opens it every way and ends.
    control = tmp_path / "control"
    control.mkdir()
    (control / "pipe").write_bytes(b"x")
    out = _child(control, perl_set)
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == [
        "0 None True",
        "read opened",
        "write opened",
        "append opened",
        "link opened",
        "utime now",
        "utime set",
        "utime link",
        "still there",
    ], out.stdout
    folder = tmp_path / "g"
    folder.mkdir()
    os.mkfifo(folder / "pipe")
    # A hang raises TimeoutExpired here: the open reached the host.
    out = _child(folder, perl_set)
    assert out.returncode == 0, out.stderr
    nxio = "No such device or address"
    assert out.stdout.splitlines() == [
        "0 None True",
        f"read refused {nxio}",
        f"write refused {nxio}",
        f"append refused {nxio}",
        f"link refused {nxio}",
        # Setting a FIFO's times opens nothing, as natively; through a link it is refused.
        "utime now",
        "utime set",
        f"utime link: {nxio}",
        "still there",
    ], out.stdout
