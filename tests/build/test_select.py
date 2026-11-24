"""Checks that four-argument select reads and writes Perl's bit vectors as natively.

Perl hands select the strings `vec` builds, a bit per descriptor, as the fd_set buffers;
wasi-libc's fd_set is a count and a list of descriptors. The guest converts at its select, so
the vectors that come back mark the same descriptors natively would. Each program runs in the
guest, with a writable folder as its working directory, and in native perl in a scratch
folder; stdout, stderr and the exit status must agree. Descriptor numbers are those of the
handles themselves, so the programs print bits by fileno, never whole vectors that hold a
file's number, which differs between the two (the guest's granted folders hold 3 and up).
"""

from __future__ import annotations

import pytest
from test_descriptors import _assert_same, _both

SETUP = (
    'open my $w, ">", "in" or die; print $w "a\\nb\\n"; close $w; '
    'open STDIN, "<", "in" or die; open my $f, "<", "in" or die; '
    'open my $o, ">", "out" or die; '
)

SAME_AS_NATIVE = {
    "stdin-readable": (
        SETUP + 'my $r = ""; vec($r, fileno(STDIN), 1) = 1; '
        "my $n = select(my $rout = $r, undef, undef, 0); "
        'print "$n ", unpack("b*", $rout), "\\n"'
    ),
    "file-readable": (
        SETUP + 'my $r = ""; vec($r, fileno($f), 1) = 1; '
        "my $n = select(my $rout = $r, undef, undef, 0); "
        'print "$n ", vec($rout, fileno($f), 1), " ", length($rout), "\\n"'
    ),
    "file-writable": (
        SETUP + 'my $w = ""; vec($w, fileno($o), 1) = 1; '
        "my $n = select(undef, my $wout = $w, undef, 0); "
        'print "$n ", vec($wout, fileno($o), 1), "\\n"'
    ),
    "read-and-write": (
        SETUP + 'my $v = ""; vec($v, fileno($_), 1) = 1 for \\*STDIN, $f, $o; '
        "my $n = select(my $r = $v, my $w = $v, my $e = $v, 0); "
        'print "$n\\n"; for my $h (\\*STDIN, $f, $o) { '
        'print vec($r, fileno($h), 1), vec($w, fileno($h), 1), vec($e, fileno($h), 1), "\\n" }'
    ),
    "unset-bits-stay-clear": (
        SETUP + 'my $r = ""; vec($r, fileno($f), 1) = 1; '
        "select(my $rout = $r, undef, undef, 0); "
        "my @set = grep { vec($rout, $_, 1) } 0 .. 8 * length($rout) - 1; "
        'print scalar(@set), " ", $set[0] == fileno($f) ? "f" : "other", "\\n"'
    ),
    "utf8-flagged-mask": (
        SETUP + 'my $r = ""; vec($r, fileno($f), 1) = 1; utf8::upgrade($r); '
        "my $n = select(my $rout = $r, undef, undef, 0); "
        'my $m = select(undef, my $wout = $r, undef, 0); print "$n $m\\n"'
    ),
    "closed-descriptor": (
        SETUP + 'my $r = ""; vec($r, 40, 1) = 1; '
        "my $n = select(my $rout = $r, undef, undef, 0); "
        'print "$n ", ($!{EBADF} ? "EBADF" : "$!"), '
        '" ", $rout eq $r ? "kept" : "changed", "\\n"'
    ),
    # Linux reads no further than its descriptor table and leaves later bits as they were.
    "past-the-table": (
        'my $r = ""; vec($r, 2000, 1) = 1; my $n = select(my $rout = $r, undef, undef, 0); '
        'print "$n [$!] ", vec($rout, 2000, 1), " ", length($rout), "\\n"'
    ),
    "empty-and-undef-masks": (
        'print select("", "", "", 0), " ", select(undef, undef, undef, 0), "\\n"'
    ),
    "timeout-only": 'print scalar(select(undef, undef, undef, 0.01)), "\\n"',
    "io-select": (
        SETUP + "use IO::Select; my $s = IO::Select->new($f, \\*STDIN); "
        "my @r = $s->can_read(0); print scalar(@r), "
        '" ", IO::Select->new($o)->can_write(0) ? "w" : "-", "\\n"; '
        "my @ready = IO::Select->select(IO::Select->new($f), IO::Select->new($o), undef, 0); "
        'print scalar(@{$ready[0]}), scalar(@{$ready[1]}), "\\n"'
    ),
    "four-arg-select-keeps-default-handle": (
        SETUP + 'my $r = ""; vec($r, fileno($f), 1) = 1; select(my $x = $r, undef, undef, 0); '
        'print STDOUT "out\\n"; my $old = select $o; print "to o\\n"; select $old; close $o; '
        'open my $c, "<", "out"; print <$c>'
    ),
}


@pytest.mark.parametrize("code", SAME_AS_NATIVE.values(), ids=SAME_AS_NATIVE.keys())
def test_select_matches_native(guest, tmp_path, code):
    g, n, gfiles, nfiles = _both(guest, code, tmp_path)
    assert n.returncode == 0, n.stderr
    _assert_same(g, n, gfiles, nfiles)
