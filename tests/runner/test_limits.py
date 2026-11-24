"""The limits and how a call ends, with the stand-in guest: deadline, fuel, memory, output, exit
codes, traps, exceptions, stdin and the environment. Each counts an outcome, never a time."""

from __future__ import annotations

import dataclasses

import wasi_perl
from wasi_perl import Completed, run


def test_hello(standin):
    done = run(["hello"], artifacts=standin)
    assert done == Completed(0, b"hello\n", b"", None, None, ())


def test_the_exit_code_is_the_guests(standin):
    assert run(["exit", "7"], artifacts=standin).exit_code == 7
    assert run(["exit", "255"], artifacts=standin).exit_code == 255


def test_a_status_wasmtime_refuses_is_read_from_the_guest(standin):
    # wasmtime refuses proc_exit with 126 or more; the guest's export keeps what it was given.
    for status in (126, 200, 255):
        done = run(["exit", str(status)], artifacts=standin)
        assert (done.exit_code, done.trapped) == (status, None)
    done = run(["exit", "255"], fuel=10**9, artifacts=standin)
    assert (done.exit_code, done.trapped) == (255, None) and done.fuel_used > 0


def test_the_status_is_read_even_with_no_fuel_left(standin):
    # Fuel is counted exactly, so a cap of what the program spent leaves none for the export.
    spent = run(["exit", "255"], fuel=10**9, artifacts=standin).fuel_used
    done = run(["exit", "255"], fuel=spent, artifacts=standin)
    assert (done.exit_code, done.trapped, done.fuel_used) == (255, None, spent)


def test_a_trap_is_never_read_as_an_exit(standin):
    # The guest has recorded a status, then traps: the trap stands.
    done = run(["trap-after-exit-status"], artifacts=standin)
    assert (done.exit_code, done.trapped) == (None, "unreachable")
    done = run(["spin-after-exit-status"], fuel=1_000_000, artifacts=standin)
    assert (done.exit_code, done.trapped) == (None, "out of fuel")
    done = run(["spin-after-exit-status"], deadline_s=0.2, artifacts=standin)
    assert (done.exit_code, done.trapped) == (None, "deadline")


def test_without_the_export_a_refused_status_is_wasmtimes_words(standin_without_status, tmp_path):
    from conftest import write_set
    from wasi_perl import Artifacts

    a = Artifacts.at(write_set(tmp_path / "old", standin_without_status))
    done = run(["exit", "255"], artifacts=a)
    assert (done.exit_code, done.trapped) == (
        None,
        "exit with invalid exit status outside of [0..126)",
    )
    assert run(["exit", "7"], artifacts=a).exit_code == 7


def test_longjmp_runs_through_exceptions(standin):
    assert run(["longjmp"], artifacts=standin).stdout == b"caught 7\n"


def test_a_trap_is_named(standin):
    done = run(["trap"], artifacts=standin)
    assert (done.exit_code, done.trapped) == (None, "unreachable")


def test_a_deadline_ends_an_endless_loop(standin):
    done = run(["spin"], deadline_s=0.2, artifacts=standin)
    assert (done.exit_code, done.trapped, done.fuel_used) == (None, "deadline", None)


def test_the_default_deadline_holds(standin):
    old = wasi_perl.set_defaults(dataclasses.replace(wasi_perl.defaults(), deadline_s=0.2))
    try:
        assert run(["spin"], artifacts=standin).trapped == "deadline"
    finally:
        wasi_perl.set_defaults(old)
    assert wasi_perl.defaults() == old


def test_fuel_ends_an_endless_loop(standin):
    done = run(["spin"], fuel=1_000_000, deadline_s=None, artifacts=standin)
    assert (done.exit_code, done.trapped, done.fuel_used) == (None, "out of fuel", 1_000_000)


def test_fuel_counts_a_bounded_program(standin):
    done = run(["count", "1000"], fuel=10_000_000, artifacts=standin)
    assert done.exit_code == 0 and done.trapped is None
    assert 0 < done.fuel_used < 10_000_000
    again = run(["count", "1000"], fuel=10_000_000, artifacts=standin)
    assert again.fuel_used == done.fuel_used  # the count is exact
    more = run(["count", "2000"], fuel=10_000_000, artifacts=standin)
    assert more.fuel_used > done.fuel_used


def test_without_fuel_none_is_counted(standin):
    assert run(["count", "10"], artifacts=standin).fuel_used is None


def test_output_is_capped_per_stream(standin):
    done = run(["print", str(1 << 20)], output_bytes=4096, artifacts=standin)
    assert done.exit_code == 0
    assert done.stdout == b"x" * 4096
    assert done.stderr == b"done\n"
    assert done.truncated == ("stdout",)


def test_output_at_the_cap_is_not_truncated(standin):
    done = run(["print", "4096"], output_bytes=4096, artifacts=standin)
    assert (len(done.stdout), done.truncated) == (4096, ())


def test_endless_output_keeps_only_the_cap(standin):
    done = run(["flood"], output_bytes=1000, fuel=200_000, deadline_s=None, artifacts=standin)
    assert done.trapped == "out of fuel"
    assert done.stdout == b"y" * 1000
    assert done.truncated == ("stdout",)


def test_the_default_output_cap_holds(standin):
    old = wasi_perl.set_defaults(dataclasses.replace(wasi_perl.defaults(), output_bytes=10))
    try:
        done = run(["print", "100"], artifacts=standin)
    finally:
        wasi_perl.set_defaults(old)
    assert (done.stdout, done.stderr, done.truncated) == (b"x" * 10, b"done\n", ("stdout",))


def test_memory_is_capped(standin):
    done = run(["alloc", str(256 << 20)], memory_bytes=64 << 20, artifacts=standin)
    assert (done.exit_code, done.stderr) == (3, b"out of memory\n")
    done = run(["alloc", str(16 << 20)], memory_bytes=64 << 20, artifacts=standin)
    assert (done.exit_code, done.stdout) == (0, b"allocated %d\n" % (16 << 20))


def test_the_default_memory_cap_holds(standin):
    old = wasi_perl.set_defaults(dataclasses.replace(wasi_perl.defaults(), memory_bytes=64 << 20))
    try:
        assert run(["alloc", str(256 << 20)], artifacts=standin).exit_code == 3
    finally:
        wasi_perl.set_defaults(old)


def test_a_memory_cap_below_the_initial_memory_fails_to_instantiate(standin):
    done = run(["hello"], memory_bytes=1 << 16, artifacts=standin)
    assert done.exit_code is None
    assert done.trapped  # wasmtime's own words


def test_stdin_reaches_the_guest(standin):
    assert run(["stdin"], stdin=b"abc\x00\xff", artifacts=standin).stdout == b"abc\x00\xff"
    assert run(["stdin"], stdin=bytearray(b"z"), artifacts=standin).stdout == b"z"
    assert run(["stdin"], artifacts=standin).stdout == b""


def test_argv_is_perl_then_the_callers(standin):
    assert run(["argv", "a b", "--"], artifacts=standin).stdout == b"perl\nargv\na b\n--\n"


def test_the_environment_is_only_the_callers(standin, monkeypatch):
    monkeypatch.setenv("HOST_SECRET", "leak")
    assert run(["env"], artifacts=standin).stdout == b""
    done = run(["env"], env={"PERL5LIB": "/lib", "A": "1"}, artifacts=standin)
    assert sorted(done.stdout.decode().splitlines()) == ["A=1", "PERL5LIB=/lib"]


def test_concurrent_calls_share_nothing(standin):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(4) as pool:
        spins = [pool.submit(run, ["spin"], deadline_s=0.3, artifacts=standin) for _ in range(2)]
        hellos = [pool.submit(run, ["argv", str(i)], artifacts=standin) for i in range(4)]
        assert [f.result().stdout for f in hellos] == [b"perl\nargv\n%d\n" % i for i in range(4)]
        assert [f.result().trapped for f in spins] == ["deadline", "deadline"]
