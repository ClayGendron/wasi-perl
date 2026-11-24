"""Checks that Perl's seeds come from the host's randomness, and what the guest's /dev/urandom is.

Natively Perl seeds its hash randomisation, its internal random state and srand from
/dev/urandom, and falls back to the time of day only when that open fails. WASI has no device
files, so the guest's descriptor table serves /dev/urandom and /dev/random itself, from WASI's
random_get, and the host never sees the name. The device follows Linux's. Everything else under
/dev, and every name in a grant, is the host's as before.

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

WASI = "wasi_snapshot_preview1"

# What perl.wasm imported before the device: 32 functions of WASI preview1.
IMPORTS_BEFORE = {
    "args_get",
    "args_sizes_get",
    "clock_res_get",
    "clock_time_get",
    "environ_get",
    "environ_sizes_get",
    "fd_close",
    "fd_fdstat_get",
    "fd_fdstat_set_flags",
    "fd_filestat_get",
    "fd_filestat_set_size",
    "fd_filestat_set_times",
    "fd_prestat_dir_name",
    "fd_prestat_get",
    "fd_read",
    "fd_readdir",
    "fd_seek",
    "fd_sync",
    "fd_tell",
    "fd_write",
    "path_create_directory",
    "path_filestat_get",
    "path_filestat_set_times",
    "path_link",
    "path_open",
    "path_readlink",
    "path_remove_directory",
    "path_rename",
    "path_symlink",
    "path_unlink_file",
    "poll_oneoff",
    "proc_exit",
}

# rand, the hash seed and the order of a hash's keys: each is drawn from a different one of
# Perl's seeds (srand's, the internal state's, and the hash's order bits).
SEEDS = (
    'print rand(), " ", unpack("H*", Hash::Util::hash_seed()), " ", '
    'join(",", keys %{{map {$_ => 1} "a".."p"}}), "\\n"'
)


def _probe(guest_engine, tmp: Path, argv: list[str], env=None, *, start=1, count_close=False):
    """Runs perl with argv under a linker of its own: random_get answers bytes start, start+1,
    ... (mod 256), and with count_close fd_close records its argument and answers success
    without closing (a host function cannot call WASI's own). Returns the exit code, stdout,
    stderr, the number of random_get calls and the fd_close arguments."""
    wt = guest_engine.wasmtime
    randoms: list[int] = []
    closes: list[int] = []

    def random_get(caller, ptr: int, length: int) -> int:
        randoms.append(length)
        caller["memory"].write(caller, bytes((start + i) % 256 for i in range(length)), ptr)
        return 0

    def fd_close(fd: int) -> int:
        closes.append(fd)
        return 0

    out, err = tmp / "probe.stdout", tmp / "probe.stderr"
    wasi = wt.WasiConfig()
    wasi.argv = ["perl", *argv]
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
    linker.define_func(WASI, "random_get", wt.FuncType([i32, i32], [i32]), random_get, True)
    if count_close:
        linker.define_func(WASI, "fd_close", wt.FuncType([i32], [i32]), fd_close)
    instance = linker.instantiate(store, guest_engine.module)
    code = 0
    try:
        instance.exports(store)["_start"](store)
    except wt.ExitTrap as e:
        code = e.code
    return code, out.read_bytes(), err.read_bytes(), len(randoms), closes


def _dir(tmp: Path, name: str) -> Path:
    d = tmp / name
    d.mkdir()
    return d


def test_the_seeds_come_from_random_get(guest_engine, tmp_path):
    argv = ["-MHash::Util", "-e", SEEDS]
    a, b, c = (
        _probe(guest_engine, _dir(tmp_path, f"run{k}"), argv, start=start)
        for k, start in enumerate((1, 1, 101))
    )
    assert (a[0], a[2], b[0], b[2], c[0], c[2]) == (0, b"", 0, b"", 0, b"")
    assert a[1] == b[1], (a[1], b[1])  # nothing from the clock
    assert a[3] > 0 and a[3] == b[3]
    assert a[1] != c[1], a[1]  # the bytes random_get gives are what seeds them
    assert len(a[1].split()) == 3, a[1]


def test_perl_hash_seed_zero_fixes_the_key_order(guest_engine, guest, tmp_path):
    code = 'print join(",", keys %{{map {$_ => 1} "a".."p"}}), "\\n"'
    env = {"PERL_HASH_SEED": "0"}
    seen = {guest("-e", code, env=env).stdout for _ in range(3)}
    for k, start in enumerate((1, 101)):
        r = _probe(guest_engine, _dir(tmp_path, f"probe{k}"), ["-e", code], env, start=start)
        assert r[:3:2] == (0, b""), r
        seen.add(r[1])
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


# The device, as the descriptor table serves it, after Linux's. Each line is one fact.
DEVICE = r"""
use Fcntl qw(O_RDONLY O_WRONLY O_RDWR O_CREAT O_EXCL O_TRUNC O_APPEND O_DIRECTORY O_NONBLOCK
             F_GETFL F_SETFL O_ACCMODE);
my @dev = stat "/dev";
for my $p (@ARGV) {
    open(my $f, "<", $p) or die "$p: $!\n";
    my $n = read($f, my $x, 32);
    read($f, my $y, 32);
    print "$p read $n ", length($x), " ", ($x eq $y ? "same" : "differ"), "\n";
    print "$p big ", sysread($f, my $big, 1 << 20), "\n";
    print "$p tests ", join("", map { $_ ? 1 : 0 } -e $p, -c $p, -c $f, -t $f, -f $p), "\n";
    my @s = stat $f;
    print "$p stat ", join(" ", $s[0] == $dev[0] ? "dev" : "other", $s[1] ? "ino" : "0", $s[3],
        $s[7], $s[9] == $dev[9] ? "times" : "other"), "\n";
    my @l = stat $p;
    print "$p same ", ("@s[0,1,2,7]" eq "@l[0,1,2,7]" ? 1 : 0), "\n";
    print "$p sysseek ", sysseek($f, 0, 1), " ", sysseek($f, 100, 0), "\n";
    print "$p tell ", tell($f), "\n";
    my $r = ""; vec($r, fileno($f), 1) = 1;
    my $w = $r;
    my $found = select(my $ro = $r, my $wo = $w, undef, 0);
    print "$p select $found ", vec($ro, fileno($f), 1), vec($wo, fileno($f), 1), "\n";
    print "$p flags ", ((fcntl($f, F_GETFL, 0) & O_ACCMODE) == O_RDONLY ? "rdonly" : "other"), " ",
        (fcntl($f, F_SETFL, O_NONBLOCK) ? "set" : "$!"), " ", read($f, $x, 4), "\n";
    print "$p truncate ", (truncate($f, 0) ? "done" : "$!"), "\n";
    open(my $d, "<&", $f) or die "dup: $!\n";
    print "$p dup ", read($d, my $z, 8), "\n";
    close $f or die "close: $!\n";
    print "$p after ", read($d, $z, 8), " ", (close($d) ? "closed" : "$!"), "\n";
    for my $mode (">", ">>", "+<", "+>") {
        open(my $o, $mode, $p) or do { print "$p $mode $!\n"; next };
        print "$p $mode ", syswrite($o, "0123456789"), " ", (print $o "x" x 9000) ? 1 : 0, " ",
            (close($o) ? "closed" : "$!"), "\n";
    }
    sysopen(my $wo2, $p, O_WRONLY) or die; print "$p wronly ", (defined sysread($wo2, $x, 4) ? "read" : "$!"), "\n";
    for my $flags ([O_RDONLY | O_DIRECTORY, "dir"], [O_WRONLY | O_CREAT | O_EXCL, "excl"],
                   [O_WRONLY | O_CREAT | O_TRUNC, "creat"], [O_RDWR | O_APPEND, "append"]) {
        print "$p $flags->[1] ", (sysopen(my $o, $p, $flags->[0]) ? "opened" : "$!"), "\n";
    }
    print "$p slash ", (open(my $s, "<", "$p/") ? "opened" : "$!"), "\n";
    print "$p opendir ", (opendir(my $o, $p) ? "opened" : "$!"), "\n";
}
opendir(my $dev, "/dev") or die; print "list ", join(",", sort readdir $dev), "\n";
"""

PATHS = ["/dev/urandom", "/dev/random", "/dev/./urandom", "/dev//./random"]


def _device_lines(p: str) -> list[str]:
    return [
        f"{p} read 32 32 differ",
        f"{p} big 1048576",
        f"{p} tests 11100",
        f"{p} stat dev ino 1 0 times",
        f"{p} same 1",
        f"{p} sysseek 0 but true 0 but true",
        f"{p} tell 64",  # PerlIO's count of what it read, from the offset 0 seek answers
        f"{p} select 2 11",
        f"{p} flags rdonly set 4",
        f"{p} truncate Invalid argument",
        f"{p} dup 8",
        f"{p} after 8 closed",
        *(f"{p} {m} 10 1 closed" for m in (">", ">>", "+<", "+>")),
        f"{p} wronly Bad file descriptor",
        f"{p} dir Not a directory",
        f"{p} excl File exists",
        f"{p} creat opened",
        f"{p} append opened",
        f"{p} slash Not a directory",
        f"{p} opendir Not a directory",
    ]


def test_the_device_behaves_as_linuxs(guest):
    r = guest("-e", DEVICE, *PATHS)
    assert (r.code, r.stderr) == (0, b""), r
    want = [line for p in PATHS for line in _device_lines(p)] + ["list .,..,null"]
    assert r.stdout.decode().splitlines() == want


def test_two_devices_are_two_files(guest):
    code = 'my @u = stat "/dev/urandom"; my @r = stat "/dev/random"; print "$u[1] $r[1]\\n"'
    r = guest("-e", code)
    assert r.code == 0, r
    u, v = r.stdout.split()
    assert int(u) and int(v) and u != v


def test_a_close_of_the_device_makes_no_host_call(guest_engine, tmp_path):
    host = 'open(my $n, "<", "/dev/null") or die; close $n; '
    device = 'open(my $u, "<", "/dev/urandom") or die "$!\\n"; read($u, my $b, 4); close $u; '
    both = _probe(guest_engine, _dir(tmp_path, "a"), ["-e", host + device], count_close=True)
    alone = _probe(guest_engine, _dir(tmp_path, "b"), ["-e", host], count_close=True)
    assert (both[0], both[2], alone[0], alone[2]) == (0, b"", 0, b""), (both, alone)
    assert alone[4], "the probe saw no close at all"
    assert both[4] == alone[4]


# An open with no access mode is a read, as on Linux, where O_RDONLY is 0; O_WRONLY and O_RDWR
# are as before. Each line is the same in the guest as natively.
NO_MODE = r"""
use Fcntl qw(O_WRONLY O_RDWR O_CREAT);
sub show { my ($name, $ok) = @_; print "$name ", ($ok ? "ok" : "$!"), "\n" }
my ($u, $w, $rw, $m, $b, $t);
show("urandom", sysopen($u, "/dev/urandom", 0) && sysread($u, $b, 16) == 16);
show("file", sysopen(F, "f.txt", 0) && sysread(F, $t, 100) == 6 && $t eq "plain\n");
show("file-write", defined syswrite(F, "x"));
show("wronly", sysopen($w, "w.txt", O_WRONLY | O_CREAT) && syswrite($w, "w\n") == 2);
show("wronly-read", defined sysread($w, $t, 1));
show("rdwr", sysopen($rw, "f.txt", O_RDWR) && sysread($rw, $t, 2) == 2 && syswrite($rw, "X") == 1);
show("missing", sysopen($m, "nope", 0));
"""


def test_an_open_with_no_access_mode_reads(guest, tmp_path):
    outs = []
    for where in ("guest", "native"):
        folder = _dir(tmp_path, where)
        (folder / "f.txt").write_bytes(b"plain\n")
        if where == "guest":
            grants = [*default_grants(), (folder, "/work", True)]
            r = guest("-e", NO_MODE, env={"PWD": "/work"}, grants=grants)
            outs.append((r.code, r.stdout, r.stderr))
        else:
            p = subprocess.run(
                [str(NATIVE_PERL), "-e", NO_MODE], cwd=folder, env={}, capture_output=True
            )
            outs.append((p.returncode, p.stdout, p.stderr))
        outs.append({f.name: f.read_bytes() for f in sorted(folder.iterdir())})
    assert outs[0] == outs[2]
    assert outs[1] == outs[3] == {"f.txt": b"plXin\n", "w.txt": b"w\n"}
    assert outs[0][1].decode().splitlines() == [
        "urandom ok",
        "file ok",
        "file-write Bad file descriptor",
        "wronly ok",
        "wronly-read Bad file descriptor",
        "rdwr ok",
        "missing No such file or directory",
    ]


def test_perls_seed_reaches_the_device(guest_engine, tmp_path):
    # The seed opens /dev/urandom with flags 0: under the probe, random_get is called.
    r = _probe(guest_engine, _dir(tmp_path, "p"), ["-e", "1"])
    assert (r[0], r[2]) == (0, b""), r
    assert r[3] > 0, "Perl's seed did not read random_get"


def test_without_a_dev_grant_there_is_no_device(guest, tmp_path):
    # perl -e reads /dev/null, so the program is a file in a grant of its own.
    folder = _dir(tmp_path, "s")
    (folder / "s.pl").write_text('print open(my $f, "<", "/dev/urandom") ? "opened\\n" : "$!\\n";')
    r = guest("/s/s.pl", grants=[default_grants()[0], (folder, "/s", False)])
    assert (r.code, r.stdout, r.stderr) == (0, b"No such file or directory\n", b"")


def test_the_module_gains_one_import(perl_wasm):
    from test_perl_wasm import _imports

    imports = _imports(perl_wasm.read_bytes())
    assert {m for m, _ in imports} == {WASI}, imports
    assert sorted(n for _, n in imports) == sorted({*IMPORTS_BEFORE, "random_get"})


# Only the two names, opened through the /dev grant, are the guest's. Every other name keeps
# the host's answer: /dev/zero is missing, a climb out of a grant and into /dev is refused, and
# a grant's own file named urandom is a regular file.
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
    folder = _dir(tmp_path, "g")
    r = guest("-e", OTHERS, env={"PWD": "/g"}, grants=[*default_grants(), (folder, "/g", True)])
    perm = "Operation not permitted"
    assert (r.code, r.stderr) == (0, b""), r
    assert r.stdout.decode().splitlines() == [
        f"/dev/zero No such file or directory {perm}",
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
    folder, dev = _dir(tmp_path, "g"), _dir(tmp_path, "dev")
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
