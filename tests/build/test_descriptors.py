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


def _both(guest, code: str, tmp: Path, *, ulimit: int | None = None):
    gdir, ndir = tmp / "guest", tmp / "native"
    gdir.mkdir()
    ndir.mkdir()
    g = guest(
        "-e",
        code,
        env={"PWD": "/work"},
        grants=[*default_grants(), (gdir, "/work", True)],
    )
    argv = [str(NATIVE_PERL), "-e", code]
    if ulimit is not None:
        argv = ["/bin/sh", "-c", f'ulimit -n {ulimit} && exec "$0" "$@"', *argv]
    p = subprocess.run(argv, cwd=ndir, env={}, capture_output=True, check=False)

    def files(d: Path):
        return {f.name: f.read_bytes() for f in sorted(d.iterdir())}

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
        "my $d = POSIX::dup(20); print $d > 20 ? qq{low\\n} : qq{lowest\\n}; "
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
        '{ local *STDOUT; open STDOUT, ">&", $old or die "dup: $!\\n"; print "dup\'d\\n"; close STDOUT } '
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


TEST_MORE = {
    "pass": 'use Test::More tests => 3; ok(1, "one"); is(2, 2, "two"); like("abc", qr/b/, "three")',
    "fail": (
        'use Test::More tests => 3; ok(1, "one"); is(1, 2, "two"); '
        'like("abc", qr/b/, "three")'
    ),
    "done-testing": 'use Test::More; ok 1; note "n"; diag "d"; is_deeply [1], [1]; done_testing',
}


@pytest.mark.parametrize("code", TEST_MORE.values(), ids=TEST_MORE.keys())
def test_test_more_tap_matches_native(guest, tmp_path: Path, code):
    g, n, gfiles, nfiles = _both(guest, code, tmp_path)
    assert n.stdout.startswith(b"1..3\nok 1") or n.stdout.startswith(b"ok 1")
    _assert_same(g, n, gfiles, nfiles)


@pytest.mark.parametrize(
    "module", ["Test::More", "Test::Simple", "Test2::V0", "Term::Table"]
)
def test_modules_that_copy_stdout_load(guest, native, module):
    code = f"use {module}; print qq{{ok\\n}}"
    n = native("-e", code)
    if n.code != 0:
        pytest.skip(f"{module} does not load natively: {n.stderr[:80]!r}")
    g = guest("-e", code)
    assert (g.code, g.stdout, g.stderr) == (0, b"ok\n", b"")


# Past the table's 1,024 numbers, a copy fails with EMFILE, as it does natively under the same
# open-file limit; closing one lets the next copy succeed.
EMFILE = (
    'my @h; while (1) { open my $h, ">&", STDOUT or last; push @h, $h } '
    'print @h > 1000 ? "many\\n" : "few: " . scalar(@h) . "\\n"; '
    'print $!{EMFILE} ? "EMFILE\\n" : "other: $!\\n"; '
    'print defined(open my $x, "<", "f") ? "opened\\n" : $!{EMFILE} ? "EMFILE\\n" : "other: $!\\n"; '
    'pop @h; open my $y, ">&", STDOUT or die "after close: $!\\n"; print "again\\n"'
)


def test_dup_past_the_limit_is_emfile(guest, tmp_path: Path):
    g, n, gfiles, nfiles = _both(guest, EMFILE, tmp_path, ulimit=1024)
    assert n.stdout == b"many\nEMFILE\nEMFILE\nagain\n", n
    _assert_same(g, n, gfiles, nfiles)
