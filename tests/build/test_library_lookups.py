"""Checks that perl.wasm asks the host only about library paths the library holds, and that every
answer a program sees is the one it saw before.

The guest's descriptor table holds an index of the library's files and folders, compiled into
perl.wasm. For a plain path under the library's grant that the index does not hold, it answers
ENOENT, or ENOTDIR below a file, without a host call, where the host's answer is already known: a
stat, the look's stat before an open, and an open that asks for nothing the read-only grant
could refuse. Every other call is the host's.

The host's calls are counted from the wasmtime command line's trace of every WASI call, which
shows each call's descriptor and result. The command line grants every folder writable, so the
traced runs see a copy of the library, never the build's own. The errors a program sees are
read under wasmtime-py with the library read-only, as the runner grants it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from _build_helpers import DEV, DEV_GUEST, LIBRARY, LIBRARY_GUEST
from test_reads_and_writes import Trace, _fd

FIVE = "use strict; use warnings; use POSIX; use JSON::PP; use List::Util; 1"
# The command line numbers its grants from 3 in the order given; the library is the first.
LIB_FD = 3


@pytest.fixture(scope="session")
def library_copy(tmp_path_factory, perl_wasm) -> Path:
    copy = tmp_path_factory.mktemp("traced") / "perl5"
    shutil.copytree(LIBRARY, copy)
    return copy


def _traced(wasmtime_cli: Path, perl_wasm: Path, library: Path, *args: str):
    p = subprocess.run(
        [str(wasmtime_cli), "run", "-W", "exceptions=y"]
        + ["--dir", f"{library}::{LIBRARY_GUEST}", "--dir", f"{DEV}::{DEV_GUEST}"]
        + ["--argv0", "perl", str(perl_wasm), *args],
        env={"WASMTIME_LOG": "wasmtime_wasi=trace", "RUST_BACKTRACE": "0", "NO_COLOR": "1"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=120,  # a fail-safe for the suite, not a measurement
        check=False,
    )
    assert p.returncode == 0, p.stderr[-2000:]
    return Trace(p.stderr.decode("utf-8", "replace"))


def _on_library(t: Trace, fn: str, ok: bool | None = None) -> int:
    return sum(
        1
        for f, a, r in t.calls
        if f == fn and _fd(a) == LIB_FD and (ok is None or r.startswith("result=Ok") == ok)
    )


def test_five_modules_make_no_failed_stat_under_the_library(
    wasmtime_cli, perl_wasm, library_copy
):
    t = _traced(wasmtime_cli, perl_wasm, library_copy, "-e", FIVE)
    assert _on_library(t, "path_filestat_get", ok=False) == 0
    assert _on_library(t, "path_open", ok=False) == 0
    assert _on_library(t, "path_filestat_get", ok=True) > 0  # the modules were found


def test_posix_makes_no_failed_stat_under_the_library(wasmtime_cli, perl_wasm, library_copy):
    t = _traced(wasmtime_cli, perl_wasm, library_copy, "-MPOSIX", "-e", "1")
    assert _on_library(t, "path_filestat_get", ok=False) == 0
    assert _on_library(t, "path_open", ok=False) == 0


def test_a_missing_module_dies_as_natively(guest, native):
    # The message is native perl's, with the guest's own @INC, which is unchanged.
    code = "require Not::There"
    g, n = guest("-e", code), native("-e", code)
    assert (g.code, n.code) == (2, 2), (g, n)
    inc = {}
    for name, run in (("guest", guest), ("native", native)):
        r = run("-e", 'print join " ", @INC')
        assert r.code == 0, r
        inc[name] = r.stdout
    assert inc["guest"] == (
        b"/usr/local/lib/perl5/5.42/site_perl/wasm32-wasi /usr/local/lib/perl5/5.42/site_perl "
        b"/usr/local/lib/perl5/5.42/wasm32-wasi /usr/local/lib/perl5/5.42"
    )
    assert g.stderr == n.stderr.replace(inc["native"], inc["guest"]), (g.stderr, n.stderr)


def test_library_files_and_folders_are_seen_as_before(guest):
    code = r"""
        my $l = "/usr/local/lib/perl5/5.42";
        print -e "$l/strict.pm" ? 1 : 0, "\n";
        print -e "$l/nope.pm" ? 1 : 0, " ", ($!{ENOENT} ? "ENOENT" : 0+$!), "\n";
        print -d "$l/File" ? 1 : 0, "\n";
        print -f "$l/File/Find.pm" ? 1 : 0, "\n";
        opendir(my $d, "$l/Digest") or die "$!";
        print join(" ", sort grep { !/^\.\.?$/ } readdir $d), "\n";
    """
    r = guest("-e", code)
    assert (r.code, r.stderr) == (0, b""), r
    listed = sorted(p.name for p in (LIBRARY / "5.42" / "Digest").iterdir())
    assert r.stdout.decode().splitlines() == ["1", "0 ENOENT", "1", "1", " ".join(listed)]


# Each row: a program's call under the library, the errno it gives, and the host call it makes:
# None when the index answers it, else the WASI function that must reach the host once.
L = "/usr/local/lib/perl5"
OPEN, STAT = "path_open", "path_filestat_get"
ROWS = [
    ("read-below-file", f'open(my $f, "<", "{L}/5.42/strict.pm/x")', "ENOTDIR", None),
    ("stat-below-file", f'stat("{L}/5.42/strict.pm/x")', "ENOTDIR", None),
    ("read-missing", f'open(my $f, "<", "{L}/5.42/nope.pm")', "ENOENT", None),
    ("opendir-missing", f'opendir(my $d, "{L}/5.42/Nope")', "ENOENT", None),
    ("read-trailing-slash", f'open(my $f, "<", "{L}/5.42/strict.pm/")', "ENOTDIR", OPEN),
    ("lib-new", f'open(my $f, ">", "{L}/x")', "EPERM", OPEN),
    ("create-in-lib", f'open(my $f, ">", "{L}/5.42/Evil.pm")', "EPERM", OPEN),
    ("append-missing", f'open(my $f, ">>", "{L}/5.42/Evil.pm")', "EPERM", OPEN),
    ("read-write-missing", f'open(my $f, "+<", "{L}/5.42/Evil.pm")', "EPERM", OPEN),
    ("create-below-missing", f'open(my $f, ">", "{L}/nope/x")', "EPERM", OPEN),
    # A host whose names ignore case finds these; this one does not.
    ("stat-other-case", f'stat("{L}/5.42/Strict.pm")', "ENOENT", STAT),
    ("exists-other-case", f'-e "{L}/5.42/STRICT.PM"', "ENOENT", STAT),
    ("read-other-case", f'open(my $f, "<", "{L}/5.42/Strict.pm")', "ENOENT", OPEN),
    ("below-other-case", f'stat("{L}/5.42/Strict.pm/x")', "ENOENT", STAT),
    # Windows rewrites these to a name it may find.
    ("stat-trailing-dot", f'stat("{L}/5.42/strict.pm.")', "ENOENT", STAT),
    ("stat-short-name", f'stat("{L}/5.42/STRICT~1.PM")', "ENOENT", STAT),
    # The host refuses these before it looks the name up.
    ("stat-long-component", f'stat("{L}/5.42/" . ("a" x 256))', "ENAMETOOLONG", STAT),
    ("read-long-component", f'open(my $f, "<", "{L}/5.42/" . ("a" x 256))', "ENAMETOOLONG", OPEN),
    ("stat-not-utf8", f'stat("{L}/5.42/\\xe9.pm")', "EILSEQ", STAT),
    ("read-not-utf8", f'open(my $f, "<", "{L}/5.42/strict.pm/\\xe9")', "EILSEQ", OPEN),
]


@pytest.mark.parametrize(("name", "code", "errno", "host"), ROWS, ids=[r[0] for r in ROWS])
def test_each_answer_is_as_before(guest, name, code, errno, host):
    r = guest("-e", f'{code} and die "succeeded\\n"; print((grep {{ $!{{$_}} }} keys %!)[0])')
    assert (r.code, r.stderr, r.stdout.decode()) == (0, b"", errno), r


@pytest.mark.parametrize(("name", "code", "errno", "host"), ROWS, ids=[r[0] for r in ROWS])
def test_each_call_reaches_the_host_or_not(
    wasmtime_cli, perl_wasm, library_copy, name, code, errno, host
):
    try:
        t = _traced(wasmtime_cli, perl_wasm, library_copy, "-e", code)
    finally:
        # The traced copy is writable; what a row made in it is taken away again.
        for made in ("x", "5.42/Evil.pm"):
            (library_copy / made).unlink(missing_ok=True)
    if host is None:
        assert _on_library(t, OPEN) + _on_library(t, STAT) == 0, t.calls
    else:
        assert _on_library(t, host) == 1, t.calls


def test_a_require_in_another_case_asks_the_host_for_the_name_it_finds_in_some_case(
    wasmtime_cli, perl_wasm, library_copy
):
    # Of the eight names `require Strict` tries, only 5.42/Strict.pm is in the library in some
    # case, so that one stat is the host's and the other seven are the index's.
    t = _traced(wasmtime_cli, perl_wasm, library_copy, "-e", "eval { require Strict }")
    assert _on_library(t, STAT) == 1, t.calls
    assert _on_library(t, OPEN) == 0, t.calls


def test_a_set_passed_as_given_serves_only_the_library_it_was_built_with(perl, tmp_path):
    from wasi_perl import Artifacts, Grant, run

    copy = tmp_path / "set"
    shutil.copytree(perl.perl.parent, copy)
    (copy / "lib" / "perl5" / "5.42" / "Added.pm").write_text("package Added; print qq(added\\n); 1;\n")
    added = run(["-e", "require Added"], artifacts=Artifacts.at(copy))
    assert added.exit_code == 2, added
    assert added.stderr.startswith(b"Can't locate Added.pm in @INC"), added.stderr
    own = tmp_path / "own"
    own.mkdir()
    (own / "Added.pm").write_text("package Added; print qq(added\\n); 1;\n")
    granted = run(
        ["-I/own", "-e", "require Added"],
        grants=[Grant(own, "/own")],
        artifacts=Artifacts.at(copy),
    )
    assert (granted.exit_code, granted.stdout, granted.stderr) == (0, b"added\n", b""), granted
