"""Checks that a guest's sleeps still sleep their time, now that every clock wait is sliced.

The guest cuts each clock wait into slices of at most 10 ms so the runner's deadline and fuel
can stop it between slices (tests/escapes). These checks make sure the sleeps themselves are
unchanged: each program measures its own wait with the guest's clock and prints only whether
it was at least the time asked, so the comparison with native perl counts no wall-clock time.
"""

from __future__ import annotations

import subprocess

import pytest

from _build_helpers import NATIVE_PERL

LONG_ENOUGH = {
    "select": ("select(undef, undef, undef, 0.25)", 0.25),
    "hires-sleep": ("Time::HiRes::sleep(0.05)", 0.05),
    "sleep": ("sleep 1", 1),
    "nanosleep": ("Time::HiRes::nanosleep(3e7)", 0.03),
    "usleep": ("Time::HiRes::usleep(20_000)", 0.02),
    "absolute-clock": (
        "Time::HiRes::clock_nanosleep(Time::HiRes::CLOCK_REALTIME(), "
        "(Time::HiRes::time() + 0.1) * 1e9, Time::HiRes::TIMER_ABSTIME())",
        0.08,  # the deadline is taken a little after the clock is read
    ),
    "relative-clock": (
        "Time::HiRes::clock_nanosleep(Time::HiRes::CLOCK_MONOTONIC(), 4e7)",
        0.04,
    ),
}


@pytest.mark.parametrize("case", LONG_ENOUGH.values(), ids=LONG_ENOUGH.keys())
def test_a_sleep_lasts_its_time(guest, native, case):
    call, at_least = case
    code = (
        "use Time::HiRes; my $t = Time::HiRes::time(); "
        f"my @r = ({call}); my $d = Time::HiRes::time() - $t; "
        f'print $d >= {at_least} ? "long enough\\n" : "short: $d\\n"; '
        'print scalar(@r), "\\n"'
    )
    g, n = guest("-e", code), native("-e", code)
    assert n.code == 0, n.stderr
    assert (g.code, g.stdout, g.stderr) == (n.code, n.stdout, n.stderr)


# What the short sleeps return, as natively.
SHORT = (
    'print sleep(0), "\\n"; print scalar(select(undef, undef, undef, 0)), "\\n"; '
    'print scalar(select(undef, undef, undef, 0.001)), "\\n"; '
    'use Time::HiRes; print Time::HiRes::sleep(0) >= 0 ? "zero or more\\n" : "negative\\n"; '
    'print Time::HiRes::sleep(0.001) > 0 ? "slept\\n" : "did not\\n"; '
    'print Time::HiRes::usleep(0) >= 0 ? "usleep\\n" : "negative\\n"'
)


def test_short_sleeps_are_native(guest, native):
    g, n = guest("-e", SHORT), native("-e", SHORT)
    assert n.code == 0, n.stderr
    assert (g.code, g.stdout, g.stderr) == (n.code, n.stdout, n.stderr)


# The table holds 256 numbers: stdio, the two default grants and 251 files. Past that, EMFILE,
# as natively under the same open-file limit.
OPENS = (
    'my @h; for (1 .. 257) { open my $h, "<", "/dev/null" or last; push @h, $h } '
    'print scalar(@h), " ", ($!{EMFILE} ? "EMFILE" : "$!"), "\\n"'
)


def test_257_opens_meet_the_256_limit(guest):
    g = guest("-e", OPENS)
    assert (g.code, g.stdout, g.stderr) == (0, b"251 EMFILE\n", b"")
    n = subprocess.run(
        ["/bin/sh", "-c", f"ulimit -n 256 && exec \"$0\" -e '{OPENS}'", str(NATIVE_PERL)],
        capture_output=True,
        check=False,
    )
    assert (n.returncode, n.stdout) == (0, b"253 EMFILE\n")  # 256 less stdio
