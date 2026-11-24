"""Checks that an open of a special file is refused before the host opens it.

A host's open(2) of a FIFO waits in the kernel for the other end, where no deadline or fuel
limit reaches. So the guest looks at what a name is (WASI's path_filestat_get, resolved as the
open would resolve it) and refuses anything that is not a folder, a regular file or a link the
open will not follow: ENXIO, "No such device or address", or ENOTDIR for an open that must
find a folder. A name that does not exist is opened as before, so creating files works.

Each guest runs under the wasmtime command line in a child process with a fail-safe timeout,
so a regression fails the check instead of holding the suite. The programs need no library,
so the only grants are a throwaway /dev and the folder holding the FIFO.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _build_helpers import DEV_GUEST, NATIVE_PERL

pytestmark = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs")

NXIO = "No such device or address"


def _guest(wasmtime_cli: Path, perl_wasm: Path, folder: Path, code: str):
    dev = folder.parent / "dev"
    dev.mkdir(exist_ok=True)
    (dev / "null").write_bytes(b"")
    return subprocess.run(
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


@pytest.fixture
def fifo_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "g"
    folder.mkdir()
    os.mkfifo(folder / "pipe")
    (folder / "file").write_text("text\n")
    return folder


OPENS = {
    "read": 'open(my $f, "<", "pipe")',
    "write": 'open(my $f, ">", "pipe")',
    "append": 'open(my $f, ">>", "pipe")',
    "read-write": 'open(my $f, "+<", "pipe")',
    "raw": 'open(my $f, "<:raw", "pipe")',
    "absolute": 'open(my $f, "<", "/g/pipe")',
    "dot": 'open(my $f, "<", "/g/./pipe")',
}


@pytest.mark.parametrize("code", OPENS.values(), ids=OPENS.keys())
def test_an_open_of_a_fifo_is_refused(wasmtime_cli, perl_wasm, fifo_folder, code):
    p = _guest(wasmtime_cli, perl_wasm, fifo_folder, f'{code} ? print "opened\\n" : print "$!\\n"')
    assert (p.returncode, p.stdout, p.stderr) == (0, f"{NXIO}\n".encode(), b"")


def test_a_link_to_a_fifo_is_refused(wasmtime_cli, perl_wasm, fifo_folder):
    code = (
        'symlink("pipe", "link") or die "symlink: $!\\n"; '
        'open(my $f, "<", "link") ? print "opened\\n" : print "$!\\n"; '
        'print readlink("link"), "\\n"'
    )
    p = _guest(wasmtime_cli, perl_wasm, fifo_folder, code)
    assert (p.returncode, p.stdout, p.stderr) == (0, f"{NXIO}\npipe\n".encode(), b"")


def test_a_die_on_the_refusal_has_the_native_status(wasmtime_cli, perl_wasm, fifo_folder):
    # ENXIO is 6 on Linux, and a die takes its status from $!.
    p = _guest(wasmtime_cli, perl_wasm, fifo_folder, 'open(my $f, "<", "pipe") or die "$!\\n"')
    assert (p.returncode, p.stdout, p.stderr) == (6, b"", f"{NXIO}\n".encode())


# What a program does with a FIFO short of opening it, and with ordinary names beside it, is
# as native: -e, opendir of it (ENOTDIR, which does not open it natively either), unlink,
# readdir, and opening, creating and appending regular files. (-p is false in the guest: WASI
# has no file type for a FIFO, so stat cannot say it is one.)
AROUND = (
    'print -e "pipe" ? "exists\\n" : "gone\\n"; '
    'print opendir(my $d, "pipe") ? "opendir\\n" : "opendir: $!\\n"; '
    'open(my $r, "<", "file") or die "file: $!\\n"; print scalar <$r>; '
    'open(my $n, ">", "new") or die "new: $!\\n"; print $n "made\\n"; close $n; '
    'open(my $a, ">>", "new") or die "append: $!\\n"; print $a "more\\n"; close $a; '
    'open(my $c, "<", "new") or die; print <$c>; '
    'opendir(my $g, ".") or die "dot: $!\\n"; '
    'print join(",", sort grep !/^\\./, readdir $g), "\\n"; '
    'unlink "pipe" or die "unlink: $!\\n"; print -e "pipe" ? "exists\\n" : "gone\\n"'
)


def test_around_a_fifo_is_native(wasmtime_cli, perl_wasm, fifo_folder, tmp_path):
    native = tmp_path / "native"
    native.mkdir()
    os.mkfifo(native / "pipe")
    (native / "file").write_text("text\n")
    n = subprocess.run(
        [str(NATIVE_PERL), "-e", AROUND],
        cwd=native,
        env={},
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert n.returncode == 0, n.stderr
    g = _guest(wasmtime_cli, perl_wasm, fifo_folder, AROUND)
    assert (g.returncode, g.stdout, g.stderr) == (n.returncode, n.stdout, n.stderr)
    assert sorted(p.name for p in fifo_folder.iterdir()) == ["file", "new"]


# Setting a FIFO's times opens nothing on the host when the call does not follow links, so the
# guest sends it that way: the times are set as natively. Through a link to the FIFO it is
# refused with ENXIO, as an open of it is (natively it sets the FIFO's times).
UTIME = (
    'print utime(undef, undef, "pipe") ? "now\\n" : "now: $!\\n"; '
    'print utime(1000, 2000, "pipe") ? "set\\n" : "set: $!\\n"; '
    'my @s = stat "pipe"; print "$s[8] $s[9]\\n"; '
    'print utime(3, 4, "file") ? "file\\n" : "file: $!\\n"; '
    '@s = stat "file"; print "$s[8] $s[9]\\n"'
)


def test_utime_on_a_fifo_is_native(wasmtime_cli, perl_wasm, fifo_folder, tmp_path):
    native = tmp_path / "native"
    native.mkdir()
    os.mkfifo(native / "pipe")
    (native / "file").write_text("text\n")
    n = subprocess.run(
        [str(NATIVE_PERL), "-e", UTIME], cwd=native, env={}, capture_output=True, timeout=20
    )
    assert (n.returncode, n.stdout) == (0, b"now\nset\n1000 2000\nfile\n3 4\n"), n
    g = _guest(wasmtime_cli, perl_wasm, fifo_folder, UTIME)
    assert (g.returncode, g.stdout, g.stderr) == (n.returncode, n.stdout, n.stderr)


def test_utime_through_a_link_to_a_fifo_is_refused(wasmtime_cli, perl_wasm, fifo_folder):
    code = (
        'symlink("pipe", "link") or die; symlink("file", "flink") or die; '
        'print utime(1, 1, "link") ? "set\\n" : "$!\\n"; '
        'print utime(5, 6, "flink") ? "file\\n" : "$!\\n"; my @s = stat "file"; print "$s[9]\\n"'
    )
    p = _guest(wasmtime_cli, perl_wasm, fifo_folder, code)
    assert (p.returncode, p.stdout, p.stderr) == (0, f"{NXIO}\nfile\n6\n".encode(), b"")
