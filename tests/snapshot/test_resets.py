"""What a call from an image finds afresh: its argv, environment, working folder, descriptors,
standard input, clock, random state and END blocks, whatever the prelude did with them."""

from __future__ import annotations

import time

import pytest

from snapshot.images import call, image_set, shown
from wasi_perl import Grant, make_snapshot

pytestmark = pytest.mark.slow

PINNED = 1767225600  # the making's clock


def made(code: str, **kw):
    return make_snapshot(code, artifacts=image_set(), cache_dir=False, **kw)


# 1


def test_argv_and_switches_are_the_calls(empty_image) -> None:
    for argv in (
        ["-e", 'print "$0|@ARGV|", scalar(@ARGV), "\\n"', "x", "y z"],
        ["-l", "-e", 'print "line"'],
        ["-w", "-e", 'my $u; print "a" . $u'],
        ["-s", "-e", 'print "$flag\\n"', "--", "-flag=on"],
    ):
        assert shown(call(argv, empty_image)) == shown(call(argv)), argv


# 2


def test_the_environment_is_the_calls() -> None:
    image = made('our $seen = join ",", sort keys %ENV;', env={"MAKING": "1", "OTHER": "x"})
    done = call(["-e", 'print join(",", sort keys %ENV), "|$seen\\n"'], image, env={"CALL": "2"})
    assert done.stdout == b"CALL|MAKING,OTHER\n"


# 3


def test_the_working_folder_is_the_calls(tmp_path) -> None:
    (tmp_path / "here.txt").write_text("found\n")
    image = made("1;")
    code = 'open my $f, "<", "here.txt" or die "no: $!\\n"; print <$f>, "$ENV{PWD}\\n"'
    done = call(["-e", code], image, grants=[Grant(tmp_path, "/data")], cwd="/data")
    assert (done.exit_code, done.stdout) == (0, b"found\n/data\n")


# 4: isolation's check 4 holds the descriptor table to the call's grants.


# 5


def test_stdin_is_the_calls() -> None:
    image = made("my @x = <STDIN>; our $read = scalar @x;")
    done = call(["-e", 'print scalar <STDIN>; print "$read\\n"'], image, stdin=b"a\n")
    assert (done.exit_code, done.stdout) == (0, b"a\n0\n")


# 6


def test_the_clock_is_the_calls(empty_image) -> None:
    done = call(["-e", 'print "$^T ", time, "\\n"'], empty_image)
    start, now = (int(x) for x in done.stdout.split())
    assert start != PINNED and start <= now
    assert abs(start - time.time()) < 3600
    pinned = made("our $then = time;")
    assert call(["-e", 'print "$then\\n"'], pinned).stdout == f"{PINNED}\n".encode()


# 7


def test_rand_is_seeded_afresh() -> None:
    image = made("srand(42);")
    draws = {call(["-e", "print rand()"], image).stdout for _ in range(20)}
    assert len(draws) == 20
    seeded = ["-e", 'print join(",", map { rand } 1 .. 3)']
    env = {"PERL_RAND_SEED": "7"}
    assert call(seeded, image, env=env).stdout == call(seeded, env=env).stdout


# 8


def test_the_preludes_end_blocks_run_after_the_calls() -> None:
    image = made('END { print "pre\\n" }')
    done = call(["-e", 'END { print "call\\n" } print "main\\n"'], image)
    assert (done.exit_code, done.stdout) == (0, b"main\ncall\npre\n")


# 9


def test_the_makings_working_folder_is_not_kept(tmp_path) -> None:
    image = made("1;", grants=[Grant(tmp_path, "/data")], cwd="/data")
    done = call(["-MCwd", "-e", 'print Cwd::getcwd(), "\\n"'], image)
    assert done.stdout == call(["-MCwd", "-e", 'print Cwd::getcwd(), "\\n"']).stdout == b"/\n"
