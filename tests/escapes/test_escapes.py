"""Sandbox escapes, tried by real Perl through `run()`: reads outside a grant, `..` and absolute
climbs, links out of a grant, every write to a read-only grant, in-place edits, nothing writable
by default, the runtime's own files, the network, the module cache and planted code. Ported from
wasi-python's hostile-guest checks. Each guest prints one line per attempt, `NAME ok` or
`NAME refused <errno text>`, and each check also compares the host's files before and after."""

from __future__ import annotations

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


def perl(code: str, **kw):
    return run(["-e", PRELUDE + textwrap.dedent(code)], **kw)


def lines(done) -> dict[str, str]:
    assert done.trapped is None, done
    out = {}
    for line in done.stdout.decode().splitlines():
        name, _, rest = line.partition(" ")
        out[name] = rest
    return out


def refused(seen: dict[str, str], *names: str) -> None:
    for name in names:
        assert seen[name].startswith("refused"), (name, seen)


def tree(folder: Path) -> list[tuple[str, int, int, bytes]]:
    """Names, modes, times and bytes under `folder`, links as their targets' text."""
    rows = []
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
    (tmp_path / "ro" / "f.txt").write_text("a original\n")
    (tmp_path / "rw" / "w.txt").write_text("a writable\n")
    (tmp_path / "outside" / "secret.txt").write_text("TOP SECRET\n")
    return tmp_path


def _grants(host: Path) -> list[Grant]:
    return [Grant(host / "ro", "/ro"), Grant(host / "rw", "/rw", writable=True)]


def test_a_read_outside_every_grant_is_not_found(perl_set, host):
    secret = host / "outside" / "secret.txt"
    done = perl(
        f"""
        t("passwd", sub {{ slurp("/etc/passwd") }});
        t("outside", sub {{ slurp("/outside/secret.txt") }});
        t("host-path", sub {{ slurp("{secret}") }});
        t("tmp", sub {{ slurp("/tmp/x") }});
        t("relative", sub {{ slurp("secret.txt") }});
        t("root-dir", sub {{ opendir(my $d, "/") }});
        """,
        grants=_grants(host),
        artifacts=perl_set,
    )
    seen = lines(done)
    refused(seen, "passwd", "outside", "host-path", "tmp", "relative", "root-dir")
    assert "No such file or directory" in seen["passwd"]
    assert b"SECRET" not in done.stdout


def test_dot_dot_cannot_climb_out_of_a_grant(perl_set, host):
    code = """
        t("up", sub { slurp("/ro/../outside/secret.txt") });
        t("up2", sub { slurp("/ro/sub/../../outside/secret.txt") });
        t("rel-up", sub { slurp("../outside/secret.txt") });
        t("rel-far", sub { slurp("../../../../etc/passwd") });
        t("rel-up-write", sub { open(my $f, ">", "../outside/planted") });
        """
    done = perl(code, grants=_grants(host), cwd="/ro", artifacts=perl_set)
    seen = lines(done)
    refused(seen, "up", "up2", "rel-up", "rel-far", "rel-up-write")
    assert b"SECRET" not in done.stdout
    assert sorted(p.name for p in (host / "outside").iterdir()) == ["secret.txt"]
    # From a writable grant too.
    done = perl(code.replace("/ro", "/rw"), grants=_grants(host), cwd="/rw", artifacts=perl_set)
    refused(lines(done), "up", "up2", "rel-up", "rel-far", "rel-up-write")
    assert sorted(p.name for p in (host / "outside").iterdir()) == ["secret.txt"]


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
    refused(lines(done), "lib-climb", "dev-climb", "lib-to-grant")


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
    refused(lines(done), *names)
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
    seen = lines(done)
    refused(seen, "read-up", "read-host", "read-etc", "read-file", "write-up", "write-file")
    refused(seen, "rename-slash", "stolen-read", "list-up")
    assert b"SECRET" not in done.stdout
    assert tree(outside) == before
    assert not (host / "rw" / "stolen").exists() and not (host / "stolen").exists()
    print(seen)  # which links were made at all, for the landing notes


READ_ONLY = r"""
# Fcntl is an XS module (spec 002); without it the sysopen row says so instead of refusing.
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
    seen = lines(done)
    assert seen.pop("control") == "ok"
    if seen["sysopen-creat"] == "absent Fcntl":  # a build before the XS modules
        seen.pop("sysopen-creat")
    refused(seen, *seen)
    assert len(seen) in (21, 22), seen
    for name in ("open>", "open>>", "open+<", "open>new", "unlink", "mkdir", "rmdir", "symlink"):
        assert "Operation not permitted" in seen[name], (name, seen[name])
    assert tree(ro) == before
    assert (host / "rw" / "control.txt").read_text() == "written"
    assert (host / "rw" / "w.txt").read_text() == "a writable\n"
    assert sorted(p.name for p in (host / "rw").iterdir()) == ["control.txt", "w.txt"]
    print(seen)


def test_an_in_place_edit_of_a_read_only_grant_changes_nothing(perl_set, host):
    before = tree(host / "ro")
    done = run(["-pi", "-e", "s/a/b/", "/ro/f.txt"], grants=_grants(host), artifacts=perl_set)
    assert done.trapped is None
    assert tree(host / "ro") == before
    assert b"Can't" in done.stderr, done
    assert done.stdout == b""
    print("in-place on read-only:", done.exit_code, done.stderr)
    # The control: the same edit in the writable grant changes the file.
    done = run(["-pi", "-e", "s/a/b/", "/rw/w.txt"], grants=_grants(host), artifacts=perl_set)
    assert (done.exit_code, done.stderr) == (0, b""), done
    assert (host / "rw" / "w.txt").read_text() == "b writable\n"


def test_nothing_is_writable_without_a_grant(perl_set):
    done = perl(
        """
        t("root", sub { open(my $f, ">", "/x") });
        t("relative", sub { open(my $f, ">", "x") });
        t("tmp", sub { open(my $f, ">", "/tmp/x") });
        t("lib-new", sub { open(my $f, ">", "/usr/local/lib/perl5/x") });
        t("lib-append", sub { open(my $f, ">>", "/usr/local/lib/perl5/5.42/strict.pm") });
        t("mkdir-tmp", sub { mkdir("/tmp") });
        t("list-root", sub { opendir(my $d, "/") });
        print "compute ", 6 * 7, "\n";
        """,
        artifacts=perl_set,
    )
    seen = lines(done)
    assert seen.pop("compute") == "42"
    refused(seen, *seen)
    assert len(seen) == 7


def _digest_tree(folder: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(folder.rglob("*")):
        h.update(str(p.relative_to(folder)).encode() + b"\0")
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


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
        open(my $n, "<", "/dev/null") or die "read null: $!\\n";
        my $got = <$n>;
        print "null-read ", defined $got ? "data" : "eof", "\n";
        """,
        grants=_grants(host),
        artifacts=perl_set,
    )
    seen = lines(done)
    assert seen.pop("null-read") == "eof"
    refused(seen, *seen)
    assert (_digest_tree(perl_set.lib), _digest_tree(perl_set.dev)) == before
    assert sorted(p.name for p in (host / "rw").iterdir()) == ["w.txt"]


def test_there_is_no_network(perl_set):
    done = perl(
        """
        t("socket", sub { socket(my $s, 2, 1, 0) });
        my $inet = eval { require IO::Socket::INET; 1 };
        if ($inet) {
            my $s = IO::Socket::INET->new(PeerAddr => "127.0.0.1", PeerPort => 9, Proto => "tcp",
                                          Timeout => 1);
            print $s ? "inet connected\n" : "inet refused $@\n";
        } else {
            print "inet absent\n";
        }
        """,
        artifacts=perl_set,
    )
    assert done.trapped is None, done
    assert b"connected" not in done.stdout, done.stdout
    seen = lines(done)
    refused(seen, "socket")
    print(seen)


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
    refused(lines(done), "climb-list", "climb-read", "climb-write", "link-list", "link-write")
    refused(lines(done), "host-path")
    assert tree(cache) == before
    for grant in (Grant(cache, "/c"), Grant(cache, "/c", writable=True), Grant(host, "/h", True)):
        with pytest.raises(ValueError, match="module cache"):
            run(["-e", "1"], grants=[grant], cache_dir=cache, artifacts=perl_set)


def test_a_writable_grant_cannot_plant_code_in_the_library_path(perl_set, host):
    for guest in ("/usr/local/lib/perl5/site_perl", "/usr/local/lib/perl5/5.42", "/usr/local"):
        with pytest.raises(ValueError, match="runtime"):
            run(["-e", "1"], grants=[Grant(host / "rw", guest, True)], artifacts=perl_set)
    with pytest.raises(ValueError, match="runtime"):
        run(["-e", "1"], grants=[Grant(perl_set.lib, "/lib", True)], artifacts=perl_set)
    plant = perl(
        """
        for my $d ("/usr/local/lib/perl5/5.42", "/usr/local/lib/perl5/site_perl",
                   "/usr/local/lib/perl5/site_perl/5.42") {
            (my $n = $d) =~ s{\\W}{_}g;
            t("plant$n", sub { open(my $f, ">", "$d/Evil.pm") or die "$!\\n";
                               print $f "print qq{EVIL\\n}; 1;\\n"; close $f });
        }
        t("plant-grant", sub { open(my $f, ">", "/rw/Evil.pm") or die "$!\\n";
                               print $f "print qq{EVIL\\n}; 1;\\n"; close $f });
        """,
        grants=_grants(host),
        artifacts=perl_set,
    )
    seen = lines(plant)
    assert seen.pop("plant-grant") == "ok"
    refused(seen, *seen)
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
    print(inc)


FIFO_CHILD = r"""
import sys
from pathlib import Path
from wasi_perl import Artifacts, Grant, run
folder, artifacts = sys.argv[1:3]
done = run(["-e", 'open(my $f, "<", "/g/pipe") ? print "opened\n" : print "refused $!\n"'],
           grants=[Grant(folder, "/g")], deadline_s=1, artifacts=Artifacts.at(artifacts))
print(done.exit_code, done.trapped, done.stdout)
"""


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs")
@pytest.mark.xfail(
    strict=True,
    reason="wasmtime opens a guest's path with a blocking open(2) on the host thread, which "
    "epoch interruption cannot stop: a FIFO in a grant holds the call past its deadline "
    "(spec 005, Decision 4)",
)
def test_a_fifo_in_a_grant_is_refused(perl_set, tmp_path):
    folder = tmp_path / "g"
    folder.mkdir()
    os.mkfifo(folder / "pipe")
    try:
        out = subprocess.run(
            [sys.executable, "-c", FIFO_CHILD, str(folder), str(perl_set.perl.parent)],
            capture_output=True,
            text=True,
            timeout=20,  # a fail-safe for the suite, not a measurement
        )
    except subprocess.TimeoutExpired:
        pytest.fail("the call never ended: the guest is blocked opening the FIFO in the host")
    assert out.returncode == 0, out.stderr
    assert "refused" in out.stdout or "deadline" in out.stdout
