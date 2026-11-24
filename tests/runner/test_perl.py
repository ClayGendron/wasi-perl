"""The runner with the real perl.wasm (spec 001's build), skipped while it is absent."""

from __future__ import annotations

import pytest

from wasi_perl import Grant, run

pytestmark = pytest.mark.slow


def test_hello(perl):
    done = run(["-e", 'print "hello, WebAssembly\\n"'], artifacts=perl)
    assert (done.exit_code, done.stdout, done.stderr, done.trapped) == (
        0,
        b"hello, WebAssembly\n",
        b"",
        None,
    )


def test_exit_and_die(perl):
    assert run(["-e", "exit 7"], artifacts=perl).exit_code == 7
    done = run(["-e", 'die "bye\\n"'], artifacts=perl)
    assert (done.exit_code, done.stderr, done.trapped) == (255, b"bye\n", None)


def test_the_library_is_read(perl):
    done = run(["-e", 'use strict; use warnings; print "ok\\n"'], artifacts=perl)
    assert (done.exit_code, done.stdout) == (0, b"ok\n")


def test_a_one_liner_over_a_grant(perl, tmp_path):
    (tmp_path / "f.txt").write_text("apple\nbanana\ncherry\n")
    done = run(
        ["-ne", "print if /an/", "/data/f.txt"], grants=[Grant(tmp_path, "/data")], artifacts=perl
    )
    assert (done.exit_code, done.stdout) == (0, b"banana\n")


def test_a_write_to_a_read_only_grant_is_refused(perl, tmp_path):
    code = 'open(my $f, ">", "/data/x") or die "no: $!\\n"; print "wrote\\n"'
    done = run(["-e", code], grants=[Grant(tmp_path, "/data")], artifacts=perl)
    assert done.exit_code != 0 and done.stdout == b""
    assert list(tmp_path.iterdir()) == []


def test_cwd(perl, tmp_path):
    (tmp_path / "f.txt").write_text("x\n")
    g = [Grant(tmp_path, "/data", writable=True)]
    done = run(
        ["-e", 'open(my $f, "<", "f.txt") or die "$!\\n"; print <$f>'],
        grants=g,
        cwd="/data",
        artifacts=perl,
    )
    if done.exit_code != 0 and b"No such file" in done.stderr:
        pytest.skip("this perl.wasm does not chdir to $PWD at start-up yet (spec 001)")
    assert (done.exit_code, done.stdout) == (0, b"x\n")


def test_fuel_and_deadline(perl):
    assert run(["-e", "1 while 1"], deadline_s=0.5, artifacts=perl).trapped == "deadline"
    done = run(["-e", "1 while 1"], fuel=10_000_000, artifacts=perl)
    assert (done.trapped, done.fuel_used) == ("out of fuel", 10_000_000)
