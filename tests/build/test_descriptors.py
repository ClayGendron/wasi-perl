"""Checks that the guest can duplicate descriptors as a POSIX process can.

WASI preview1 has one number per open file. The guest keeps its own table from the numbers a
program sees to WASI's, so dup, dup2 and fcntl(F_DUPFD) give a second number for the same open
file, sharing its offset, and new numbers are the lowest free. Each program runs in the guest,
with a writable folder granted as its working directory, and in native perl 5.42.3 in a
scratch folder; stdout, stderr, the exit status and the files written must agree.

The guest holds a few more descriptors at start than a native process (one per granted
folder), so programs print descriptor numbers relative to the first one they open, or ask for
an explicit number.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import NATIVE_PERL, assert_exit_refused, default_grants


def _both(guest, code: str, tmp: Path, *, ulimit: int | None = None, w2: bool = False):
    """Runs code in the guest and natively. With w2, the guest's w2/ is a second grant (at
    /work/w2), so a call between the two folders passes two descriptors; natively it is a
    plain subfolder."""
    gdir, ndir = tmp / "guest", tmp / "native"
    gdir.mkdir()
    ndir.mkdir()
    grants = [*default_grants(), (gdir, "/work", True)]
    if w2:
        (gdir / "w2").mkdir()
        (ndir / "w2").mkdir()
        grants.append((gdir / "w2", "/work/w2", True))
    g = guest("-e", code, env={"PWD": "/work"}, grants=grants)
    argv = [str(NATIVE_PERL), "-e", code]
    if ulimit is not None:
        argv = ["/bin/sh", "-c", f'ulimit -n {ulimit} && exec "$0" "$@"', *argv]
    p = subprocess.run(argv, cwd=ndir, env={}, capture_output=True, check=False)

    def files(d: Path):
        return {
            f.relative_to(d).as_posix(): f.read_bytes() for f in sorted(d.rglob("*")) if f.is_file()
        }

    return g, p, files(gdir), files(ndir)


def _assert_same(g, n, gfiles, nfiles) -> None:
    assert gfiles == nfiles
    assert (g.stdout, g.stderr) == (n.stdout, n.stderr)
    if n.returncode >= 126:
        assert_exit_refused(g, n.returncode)
    else:
        assert g.code == n.returncode, g


SAME_AS_NATIVE = {
    # Save STDOUT, send it to a file, and put it back.
    "save-restore-stdout": (
        'open my $save, ">&", STDOUT or die "save: $!\\n"; '
        'open STDOUT, ">", "f" or die "f: $!\\n"; print "to f\\n"; '
        'open STDOUT, ">&", $save or die "restore: $!\\n"; print "to stdout\\n"'
    ),
    "save-restore-stderr": (
        'open my $save, ">&STDERR" or die; open STDERR, ">", "e" or die; warn "to e\\n"; '
        'open STDERR, ">&", $save or die; close $save; warn "to stderr\\n"'
    ),
    "stderr-onto-stdout": (
        '$| = 1; open STDERR, ">&STDOUT" or die "dup: $!\\n"; print "o1\\n"; '
        'print STDERR "e\\n"; print "o2\\n"'
    ),
    "stdout-onto-stderr": 'open STDOUT, ">&", \\*STDERR or die; $| = 1; print "x\\n"; warn "w\\n"',
    "dup-stdin": (
        'open my $w, ">", "in" or die; print $w "a\\nb\\n"; close $w; '
        'open STDIN, "<", "in" or die; open my $in, "<&", STDIN or die "dup: $!\\n"; '
        "print scalar <$in>; close $in; print scalar <STDIN>"
    ),
    # "&=" names the same descriptor and makes no new one.
    "fdopen-same-fd": (
        'open my $w, ">", "in" or die; print $w "line\\n"; close $w; '
        'open my $fh, "<", "in" or die; open(my $dup, "<&=", $fh) or die "fdopen: $!\\n"; '
        'print fileno($dup) == fileno($fh) ? "same\\n" : "differ\\n"; print <$dup>'
    ),
    # The "&=" forms share one descriptor between two handles; Perl counts the handles.
    "stderr-fdopen-stdout": (
        'open STDERR, ">&=STDOUT" or die "fdopen: $!\\n"; $| = 1; print "o1\\n"; '
        'print STDERR "e\\n"; print "o2\\n"'
    ),
    "stdout-fdopen-handle": (
        'open my $h, ">", "f" or die; open STDOUT, ">&=", $h or die "fdopen: $!\\n"; '
        'print "to f\\n"; close STDOUT; print $h "more\\n"; close $h'
    ),
    "fdopen-stdin-by-number": (
        'open my $w, ">", "in" or die; print $w "a\\nb\\n"; close $w; '
        'open STDIN, "<", "in" or die; open my $fh, "<&=", fileno(STDIN) or die "fdopen: $!\\n"; '
        "print scalar <$fh>; print fileno($fh) == fileno(STDIN) ? qq{same\\n} : qq{differ\\n}"
    ),
    "fdopen-close-new": (
        'open my $h, ">&=STDOUT" or die; print $h "h\\n"; close $h or die "close h: $!\\n"; '
        'print "stdout\\n"; close STDOUT or die "close: $!\\n"'
    ),
    "fdopen-close-old": (
        'open my $h, ">&=STDOUT" or die; $h->autoflush(1); print "one\\n"; close STDOUT; '
        'print $h "h\\n" or print STDERR "h: $!\\n"; close $h'
    ),
    "stderr-fdopen-close-stderr": (
        'open STDERR, ">&=STDOUT" or die; $| = 1; print "o\\n"; close STDERR; print "after\\n"'
    ),
    # Two numbers for one open file share its offset.
    "shared-offset": (
        'open my $a, ">", "f" or die; open my $b, ">&", $a or die; '
        'syswrite $a, "1"; syswrite $b, "2"; print sysseek($a, 0, 1), "\\n"; '
        'close $a; syswrite $b, "3"; close $b; open my $r, "<", "f"; print <$r>, "\\n"'
    ),
    # Closing one number leaves the other open.
    "close-one-of-two": (
        'open my $d, ">&", STDOUT or die; close $d or die "close: $!\\n"; print "still\\n"; '
        'open my $e, ">&", STDOUT or die; close STDOUT; print $e "via e\\n"; close $e'
    ),
    # Lowest-free numbering, relative to the first descriptor the program opens.
    "lowest-free": (
        'open my $a, ">", "a" or die; open my $b, ">", "b" or die; open my $c, ">", "c" or die; '
        "my $z = fileno $a; print join(q{ }, map { fileno($_) - $z } $a, $b, $c), qq{\\n}; "
        'close $b; open my $d, ">", "d" or die; print fileno($d) - $z, "\\n"; '
        'close $a; open my $e, ">&", $c or die; print fileno($e) - $z, "\\n"; '
        'open my $f, ">&", $c or die; print fileno($f) - $z, "\\n"'
    ),
    "reopen-keeps-number": (
        'open STDOUT, ">", "f" or die; print STDERR fileno(STDOUT), "\\n"; '
        'open STDERR, ">", "e" or die; print STDOUT fileno(STDERR), "\\n"; '
        'open STDIN, "<", "f" or die; print STDOUT fileno(STDIN), "\\n"'
    ),
    "posix-dup-dup2": (
        "use POSIX (); "
        'open my $f, ">", "f" or die; my $n = POSIX::dup2(fileno($f), 20) or die "dup2: $!\\n"; '
        'print "$n\\n"; open(my $g, ">&=", 20) or die "fdopen: $!\\n"; select $g; $| = 1; '
        'print "via 20\\n"; select STDOUT; print POSIX::dup2(20, 20), "\\n"; '
        'my $d = POSIX::dup(20); print $d - fileno($f), "\\n"; '
        'POSIX::close($d) or die "close: $!\\n"; POSIX::close(20); syswrite $f, "via f\\n"'
    ),
    "posix-dup-bad": (
        "use POSIX (); "
        'print defined(POSIX::dup(99)) ? "def\\n" : $!{EBADF} ? "EBADF\\n" : "$!\\n"; '
        'print defined(POSIX::dup2(99, 20)) ? "def\\n" : $!{EBADF} ? "EBADF\\n" : "$!\\n"; '
        'print defined(POSIX::close(99)) ? "def\\n" : $!{EBADF} ? "EBADF\\n" : "$!\\n"'
    ),
    "fcntl-dupfd": (
        "use Fcntl; "
        'my $n = fcntl(STDOUT, F_DUPFD, 30) or die "F_DUPFD: $!\\n"; print "$n\\n"; '
        "my $m = fcntl(STDOUT, F_DUPFD, 30) or die; print qq{$m\\n}; "
        'open(my $o, ">&=", $n) or die; print $o "via $n\\n"; close $o; '
        'print defined(fcntl(STDOUT, F_DUPFD, -1)) ? "def\\n" : $!{EINVAL} ? "EINVAL\\n" : "$!\\n"'
    ),
    "local-stdout": (
        'sub cap { local *STDOUT; open STDOUT, ">", "f" or die; print "in f\\n"; close STDOUT } '
        'print "before\\n"; cap(); print "after\\n"; '
        'open my $old, ">&", \\*STDOUT or die; '
        '{ local *STDOUT; open STDOUT, ">&", $old or die "dup: $!\\n"; '
        'print "dup\'d\\n"; close STDOUT } '
        'print "again\\n"'
    ),
    "select-round-trip": (
        'open my $f, ">", "f" or die; my $old = select $f; print "to f\\n"; select $old; '
        'print "to out\\n"; open my $d, ">&STDOUT" or die; select $d; $| = 1; print "via dup\\n"; '
        'select STDOUT; print "direct\\n"'
    ),
    "die-after-reopen": 'open my $s, ">&STDERR" or die; open STDERR, ">", "e" or die; die "d\\n"',
}


@pytest.mark.parametrize("code", SAME_AS_NATIVE.values(), ids=SAME_AS_NATIVE.keys())
def test_same_as_native(guest, tmp_path: Path, code):
    _assert_same(*_both(guest, code, tmp_path))


# The paths that pass a descriptor somewhere other than a first argument, or a remapped one.
TRANSLATED = {
    # poll_oneoff with descriptors in its subscriptions: a readable file, then STDOUT. The file
    # is polled alone and with a bound, because wasmtime reports a file ready only once a read
    # it starts in the background is done, which a zero timeout can outrun.
    "poll-real-descriptors": (
        "use IO::Poll qw(POLLIN POLLOUT); "
        'open my $w, ">", "in" or die; print $w "x\\n"; close $w; open my $f, "<", "in" or die; '
        "my $p = IO::Poll->new; $p->mask($f => POLLIN); my $n = $p->poll(5); "
        'print "$n ", ($p->events($f) & POLLIN ? "in" : "-"), "\\n"; '
        "my $q = IO::Poll->new; $q->mask(\\*STDOUT => POLLOUT); $n = $q->poll(0); "
        'print "$n ", ($q->events(\\*STDOUT) & POLLOUT ? "out" : "-"), "\\n"'
    ),
    # A closed number gets POLLNVAL for its entry alone; the other entry still answers. The
    # constants differ between WASI and Linux, so they are printed by name.
    "poll-closed-descriptor": (
        "use IO::Poll qw(POLLOUT POLLNVAL); my @a = (50, POLLOUT, 1, POLLOUT); "
        "my $n = IO::Poll::_poll(0, @a); "
        'print "$n ", ($a[1] == POLLNVAL ? "nval" : $a[1]), " ", '
        '($a[3] == POLLOUT ? "out" : $a[3]), "\\n"; '
        "my @b = (50, POLLOUT); $n = IO::Poll::_poll(100, @b); "
        'print "$n ", ($b[1] == POLLNVAL ? "nval" : $b[1]), "\\n"'
    ),
    # rename and link between two grants: two descriptors in one call.
    "rename-link-across-grants": (
        'open my $o, ">", "a" or die; print $o "A\\n"; close $o; '
        'rename "a", "w2/b" or die "rename: $!\\n"; link "w2/b", "c" or die "link: $!\\n"; '
        'rename "c", "w2/d" or die "rename back: $!\\n"; '
        'print join(" ", map { -e $_ ? 1 : 0 } qw(a w2/b c w2/d)), "\\n"'
    ),
    # readdir through a number that is not the host's: after close STDIN, opendir takes 0.
    "readdir-at-remapped": (
        'for my $n (qw(x y)) { open my $o, ">", $n or die; } close STDIN; '
        'opendir my $d, "." or die "opendir: $!\\n"; print fileno($d), "\\n"; '
        'print join(",", sort grep !/^\\./, readdir $d), "\\n"; rewinddir $d; '
        'my @again = grep !/^\\./, readdir $d; print scalar(@again), "\\n"; closedir $d; '
        'open my $z, ">", "z" or die; print fileno($z), "\\n"'
    ),
}


@pytest.mark.parametrize("code", TRANSLATED.values(), ids=TRANSLATED.keys())
def test_translated_paths_match_native(guest, tmp_path: Path, code):
    _assert_same(*_both(guest, code, tmp_path, w2=True))


# A grant's number always names its grant. wasi-libc resolves every path under a granted folder
# through the number it found at start-up, so the number is never given to another file, a
# close of it leaves the grant open, and nothing can be put on it. Natively these numbers name
# nothing, so these checks are the guest's alone. The grants are the library (3), /dev (4),
# /w1 (5) and /w2 (6).
def _grants(guest, tmp: Path, code: str):
    w1, w2 = tmp / "w1", tmp / "w2"
    w1.mkdir()
    w2.mkdir()
    g = guest("-e", code, grants=[*default_grants(), (w1, "/w1", True), (w2, "/w2", True)])
    return g, sorted(p.name for p in w1.iterdir()), sorted(p.name for p in w2.iterdir())


def test_a_closed_grant_number_is_not_reused(guest, tmp_path: Path):
    # The review's reproduction: the handle closes 5 when its block ends, and the opendir would
    # then have taken 5, so the write under /w1 would have landed in /w2.
    code = (
        '{ open my $fh, "<&=", 5 or die "fdopen: $!\\n" } '
        'opendir my $d, "/w2" or die "opendir: $!\\n"; '
        'print fileno($d) > 6 ? "skips\\n" : "took\\n"; '
        'open my $o, ">", "/w1/result" or die "open: $!\\n"; print $o "x\\n"; close $o or die; '
        'print "ok\\n"'
    )
    g, w1, w2 = _grants(guest, tmp_path, code)
    assert (g.code, g.stdout, g.stderr) == (0, b"skips\nok\n", b"")
    assert (w1, w2) == (["result"], [])


def test_close_of_a_grant_number_keeps_the_grant(guest, tmp_path: Path):
    code = (
        "use POSIX (); "
        'print POSIX::close(5) ? "closed\\n" : "close: $!\\n"; '
        'open my $o, ">", "/w1/still" or die "open: $!\\n"; close $o; '
        'print POSIX::dup(1) > 6 ? "skips\\n" : "took\\n"'
    )
    g, w1, w2 = _grants(guest, tmp_path, code)
    assert (g.code, g.stdout, g.stderr) == (0, b"closed\nskips\n", b"")
    assert (w1, w2) == (["still"], [])


def test_nothing_is_put_on_a_grant_number(guest, tmp_path: Path):
    code = (
        "use POSIX (); use Fcntl; "
        'open my $f, ">", "/w2/f" or die; '
        'print defined POSIX::dup2(fileno($f), 5) ? "dup2\\n" : $!{EBADF} ? "EBADF\\n" : "$!\\n"; '
        'print defined POSIX::dup2(5, 9) ? "from a grant\\n" : "$!\\n"; '
        'open my $o, ">", "/w1/g" or die "open: $!\\n"; close $o; print "w1 ok\\n"'
    )
    g, w1, w2 = _grants(guest, tmp_path, code)
    assert (g.code, g.stdout, g.stderr) == (0, b"EBADF\nfrom a grant\nw1 ok\n", b"")
    assert (w1, w2) == (["g"], ["f"])


TEST_MORE = {
    "pass": 'use Test::More tests => 3; ok(1, "one"); is(2, 2, "two"); like("abc", qr/b/, "three")',
    "fail": (
        'use Test::More tests => 3; ok(1, "one"); is(1, 2, "two"); like("abc", qr/b/, "three")'
    ),
    "done-testing": 'use Test::More; ok 1; note "n"; diag "d"; is_deeply [1], [1]; done_testing',
}


@pytest.mark.parametrize("code", TEST_MORE.values(), ids=TEST_MORE.keys())
def test_test_more_tap_matches_native(guest, tmp_path: Path, code):
    g, n, gfiles, nfiles = _both(guest, code, tmp_path)
    assert n.stdout.startswith(b"1..3\nok 1") or n.stdout.startswith(b"ok 1")
    _assert_same(g, n, gfiles, nfiles)


@pytest.mark.parametrize("module", ["Test::More", "Test::Simple", "Test2::V0", "Term::Table"])
def test_modules_that_copy_stdout_load(guest, native, module):
    code = f"use {module}; print qq{{ok\\n}}"
    n = native("-e", code)
    if n.code != 0:
        pytest.skip(f"{module} does not load natively: {n.stderr[:80]!r}")
    g = guest("-e", code)
    assert (g.code, g.stdout, g.stderr) == (0, b"ok\n", b"")


# Past the table's 256 numbers, a copy fails with EMFILE, as it does natively under the same
# open-file limit; closing one lets the next copy succeed.
EMFILE = (
    'my @h; while (1) { open my $h, ">&", STDOUT or last; push @h, $h } '
    'print @h > 240 ? "many\\n" : "few: " . scalar(@h) . "\\n"; '
    'print $!{EMFILE} ? "EMFILE\\n" : "other: $!\\n"; '
    'print defined(open my $x, "<", "f") ? "opened\\n" '
    ': $!{EMFILE} ? "EMFILE\\n" : "other: $!\\n"; '
    'pop @h; open my $y, ">&", STDOUT or die "after close: $!\\n"; print "again\\n"'
)


def test_dup_past_the_limit_is_emfile(guest, tmp_path: Path):
    g, n, gfiles, nfiles = _both(guest, EMFILE, tmp_path, ulimit=256)
    assert n.stdout == b"many\nEMFILE\nEMFILE\nagain\n", n
    _assert_same(g, n, gfiles, nfiles)
