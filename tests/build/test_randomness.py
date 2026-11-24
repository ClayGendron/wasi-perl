"""Checks that Perl's seeds come from the host's randomness, and what the guest's /dev/urandom is.

Natively Perl seeds its hash randomisation, its internal random state and srand from
/dev/urandom, and falls back to the time of day only when that open fails. WASI has no device
files, so the guest's descriptor table serves /dev/urandom and /dev/random itself, from WASI's
random_get, and the host never sees the name. Everything else under /dev, and every name in a
grant, is the host's as before.

The seeds are traced to random_get by a linker of this file's own, whose random_get answers a
fixed byte pattern: two runs with the same pattern print the same seeds, and a run with another
pattern prints other seeds. Anything seeded from the clock would differ between the first two.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _build_helpers import DEV_GUEST, NATIVE_PERL, default_grants

# rand, the hash seed and the order of a hash's keys: each is drawn from a different one of
# Perl's seeds (srand's, the internal state's, and the hash's order bits).
SEEDS = (
    'print rand(), " ", unpack("H*", Hash::Util::hash_seed()), " ", '
    'join(",", keys %{{map {$_ => 1} "a".."p"}}), "\\n"'
)


def _probe(guest_engine, tmp: Path, start: int, env: dict[str, str] | None = None):
    """Runs SEEDS with random_get answering bytes start, start+1, ... (mod 256) on every call.
    Returns the stdout, the stderr and how many times random_get was called."""
    wt = guest_engine.wasmtime
    calls = []

    def random_get(caller, ptr: int, length: int) -> int:
        calls.append(length)
        data = bytes((start + i) % 256 for i in range(length))
        caller["memory"].write(caller, data, ptr)
        return 0

    out, err = tmp / "probe.stdout", tmp / "probe.stderr"
    wasi = wt.WasiConfig()
    wasi.argv = ["perl", "-MHash::Util", "-e", SEEDS]
    wasi.env = sorted((env or {}).items())
    wasi.stdout_file = str(out)
    wasi.stderr_file = str(err)
    for host, guest, writable in default_grants():
        wasi.preopen_dir(str(host), guest, writable)
    store = wt.Store(guest_engine.engine)
    store.set_wasi(wasi)
    linker = wt.Linker(guest_engine.engine)
    linker.define_wasi()
    linker.allow_shadowing = True
    i32 = wt.ValType.i32()
    linker.define_func(
        "wasi_snapshot_preview1",
        "random_get",
        wt.FuncType([i32, i32], [i32]),
        random_get,
        access_caller=True,
    )
    instance = linker.instantiate(store, guest_engine.module)
    try:
        instance.exports(store)["_start"](store)
    except wt.ExitTrap as e:
        assert e.code == 0, err.read_bytes()
    return out.read_bytes(), err.read_bytes(), len(calls)


def test_the_seeds_come_from_random_get(guest_engine, tmp_path):
    runs = []
    for k, start in enumerate((1, 1, 101)):
        d = tmp_path / f"run{k}"
        d.mkdir()
        runs.append(_probe(guest_engine, d, start))
    (a, a_err, a_calls), (b, b_err, b_calls), (c, c_err, _) = runs
    assert (a_err, b_err, c_err) == (b"", b"", b"")
    assert a == b, (a, b)  # nothing from the clock
    assert a_calls > 0 and a_calls == b_calls
    assert a != c, a  # the bytes random_get gives are what seeds them
    assert len(a.split()) == 3, a


def test_perl_hash_seed_zero_fixes_the_key_order(guest_engine, guest, tmp_path):
    code = 'print join(",", keys %{{map {$_ => 1} "a".."p"}}), "\\n"'
    env = {"PERL_HASH_SEED": "0"}
    seen = {guest("-e", code, env=env).stdout for _ in range(3)}
    for k, start in enumerate((1, 101)):
        d = tmp_path / f"probe{k}"
        d.mkdir()
        # The probe prints rand, the seed and the keys; with the seed fixed, only rand varies.
        out, err, _ = _probe(guest_engine, d, start, env)
        assert err == b"", err
        seen.add(out.split()[-1] + b"\n")
    assert len(seen) == 1, seen
    native = {
        subprocess.run([str(NATIVE_PERL), "-e", code], env=env, capture_output=True).stdout
        for _ in range(3)
    }
    assert len(native) == 1, native


def test_the_hash_seed_and_srand_controls_work_as_natively(guest, native):
    # srand(N) is Perl's own drand48, and PERL_HASH_SEED sets the seed's bytes: both are the
    # same numbers in the guest as natively.
    code = 'srand(42); print rand(), " ", unpack("H*", Hash::Util::hash_seed()), "\\n"'
    env = {"PERL_HASH_SEED": "0123456789abcdef"}
    g = guest("-MHash::Util", "-e", code, env=env)
    n = native("-MHash::Util", "-e", code, env=env)
    assert (g.code, g.stdout, g.stderr) == (n.code, n.stdout, n.stderr)


# The device, as the descriptor table serves it. Each line is one fact.
DEVICE = r"""
for my $p ("/dev/urandom", "/dev/random", "/dev/./urandom") {
    open(my $f, "<", $p) or die "$p: $!\n";
    my $n = read($f, my $x, 32);
    read($f, my $y, 32);
    print "$p read $n ", length($x), " ", ($x eq $y ? "same" : "differ"), "\n";
    print "$p big ", sysread($f, my $big, 1 << 20), "\n";
    print "$p char ", (-c $p ? 1 : 0), (-e $p ? 1 : 0), (-c $f ? 1 : 0), " size ", -s $f || 0, "\n";
    print "$p sysseek ", (defined sysseek($f, 0, 0) ? "moved" : "$!"), "\n";
    open(my $d, "<&", $f) or die "dup: $!\n";
    print "$p dup ", read($d, my $z, 8), "\n";
    close $f or die "close: $!\n";
    print "$p after ", read($d, $z, 8), " ", (close($d) ? "closed" : "$!"), "\n";
    for my $mode (">", ">>", "+<", "+>") {
        print "$p $mode ", (open(my $w, $mode, $p) ? "opened" : "$!"), "\n";
    }
}
print "opendir ", (opendir(my $u, "/dev/urandom") ? "opened" : "$!"), "\n";
opendir(my $dev, "/dev") or die; print "list ", join(",", sort readdir $dev), "\n";
"""


def _device_lines(p: str) -> list[str]:
    lines = [
        f"{p} read 32 32 differ",
        f"{p} big 1048576",
        f"{p} char 111 size 0",
        f"{p} sysseek Illegal seek",
        f"{p} dup 8",
        f"{p} after 8 closed",
    ]
    return lines + [f"{p} {m} Permission denied" for m in (">", ">>", "+<", "+>")]


def test_the_device_reads_the_hosts_randomness(guest):
    r = guest("-e", DEVICE)
    assert (r.code, r.stderr) == (0, b""), r
    want = [*_device_lines("/dev/urandom"), *_device_lines("/dev/random")]
    want += [*_device_lines("/dev/./urandom"), "opendir Not a directory", "list .,..,null"]
    assert r.stdout.decode().splitlines() == want


def test_without_a_dev_grant_there_is_no_device(guest, tmp_path):
    # perl -e reads /dev/null, so the program is a file in a grant of its own.
    folder = tmp_path / "s"
    folder.mkdir()
    (folder / "s.pl").write_text('print open(my $f, "<", "/dev/urandom") ? "opened\\n" : "$!\\n";')
    r = guest("/s/s.pl", grants=[default_grants()[0], (folder, "/s", False)])
    assert (r.code, r.stdout, r.stderr) == (0, b"No such file or directory\n", b"")


# Only the two names, opened through the /dev grant, are the guest's. Every other name keeps
# the host's answer: /dev/zero is missing, a climb out of /dev and back is refused, and a
# grant's own file named urandom is a regular file.
OTHERS = r"""
for my $p ("/dev/zero", "/dev/../dev/urandom", "/g/../dev/urandom", "../dev/urandom") {
    print "$p ", (open(my $f, "<", $p) ? "opened" : "$!"), " ",
        (open(my $w, ">", $p) ? "written" : "$!"), "\n";
}
open(my $w, ">", "/g/urandom") or die "urandom: $!\n"; print $w "plain\n"; close $w;
open(my $r, "<", "urandom") or die; print "grant ", scalar(<$r>);
print "grant char ", (-c "urandom" ? 1 : 0), (-f "/g/urandom" ? 1 : 0), "\n";
"""


def test_nothing_else_became_openable(guest, tmp_path):
    folder = tmp_path / "g"
    folder.mkdir()
    r = guest("-e", OTHERS, env={"PWD": "/g"}, grants=[*default_grants(), (folder, "/g", True)])
    perm = "Operation not permitted"
    assert (r.code, r.stderr) == (0, b""), r
    assert r.stdout.decode().splitlines() == [
        "/dev/zero No such file or directory Operation not permitted",
        f"/dev/../dev/urandom {perm} {perm}",
        f"/g/../dev/urandom {perm} {perm}",
        f"../dev/urandom {perm} {perm}",
        "grant plain",
        "grant char 01",
    ]
    assert (folder / "urandom").read_bytes() == b"plain\n"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs")
def test_a_fifo_named_urandom_in_a_grant_is_refused(wasmtime_cli, perl_wasm, tmp_path):
    # Under the wasmtime command line in a child with a fail-safe timeout: an open that reached
    # the host would wait for the FIFO's other end.
    folder, dev = tmp_path / "g", tmp_path / "dev"
    folder.mkdir()
    dev.mkdir()
    (dev / "null").write_bytes(b"")
    os.mkfifo(folder / "urandom")
    code = (
        'for my $p ("urandom", "/g/urandom", "/g/./urandom") { '
        'print "$p ", (open(my $f, "<", $p) ? "opened" : "$!"), "\\n" }'
    )
    p = subprocess.run(
        [
            str(wasmtime_cli),
            "run",
            "-W",
            "exceptions=y",
            "--env",
            "PWD=/g",
            "--dir",
            f"{dev}::{DEV_GUEST}",
            "--dir",
            f"{folder}::/g",
            str(perl_wasm),
            "-e",
            code,
        ],
        capture_output=True,
        timeout=20,  # a fail-safe for the suite, not a measurement
        check=False,
    )
    nxio = "No such device or address"
    want = f"urandom {nxio}\n/g/urandom {nxio}\n/g/./urandom {nxio}\n".encode()
    assert (p.returncode, p.stdout, p.stderr) == (0, want, b"")
