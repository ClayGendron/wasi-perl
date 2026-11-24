"""Checks the rename over a file the program holds open, as a Windows host refuses it.

Perl's in-place edit (-i) renames its work file over the file ARGV still holds open. Linux
and macOS allow that; Windows refuses to replace an open file, and wasmtime answers EACCES.
The guest then closes its own descriptors on the destination, renames again, and opens the
new file in their place. On Linux the first rename succeeds and that path never runs, so
these checks use a test build: perl relinked from the same objects with the descriptor table
compiled with WASI_PERL_TEST_HELD_RENAME, under which the guest's host rename refuses, as
Windows does, a destination the program holds open. The shipped module has no such switch.
Each program's files, stdout, stderr and status are compared with native perl's.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from _build_helpers import BUILD, NATIVE_PERL, ROOT, Guest, default_grants

TARGET = BUILD / "target"
SHOW = "show:\n\t@echo '$(perlmain_objs)|$(static_ext)|$(LLIBPERL)|$(libs)|$(CLDFLAGS)'\n"


@pytest.fixture(scope="session")
def held_rename_guest(tmp_path_factory, perl_wasm) -> Guest:
    pytest.importorskip("wasmtime", reason="wasmtime-py 51 is needed")
    if not (TARGET / "libperl.a").exists():
        pytest.skip("build/target is missing; run scripts/build.sh")
    out = tmp_path_factory.mktemp("held-rename")
    obj = out / "wasi-fd-table-test.o"
    cc = str(ROOT / "recipes" / "wasi-cc")
    flags = ["-O2", "-Wall", "-Wextra", "-Werror", "-DWASI_PERL_TEST_HELD_RENAME"]
    src = str(ROOT / "recipes" / "wasi-fd-table.c")
    subprocess.run([cc, *flags, "-c", src, "-o", str(obj)], check=True)
    show = subprocess.run(
        ["make", "-s", "--no-print-directory", "-f", "Makefile", "-f", "-", "show"],
        cwd=TARGET,
        input=SHOW,
        capture_output=True,
        text=True,
        check=True,
    )
    mains, static_ext, libperl, libs, cldflags = show.stdout.strip().split("|")
    ext_libs = (TARGET / "ext.libs").read_text().split()
    wasm = out / "perl-held-rename.wasm"
    link = [cc, "-o", str(wasm), *cldflags.split(), str(obj), *mains.split()]
    link += [*static_ext.split(), *libperl.split(), *ext_libs, *libs.split()]
    subprocess.run(link, cwd=TARGET, check=True, capture_output=True)
    return Guest(wasm)


def _both(engine: Guest, args: list[str], tmp: Path, files: dict[str, str]):
    gdir, ndir = tmp / "guest", tmp / "native"
    for d in (gdir, ndir):
        d.mkdir()
        for name, text in files.items():
            (d / name).write_text(text)
    runs = tmp / "runs"
    runs.mkdir()
    g = engine.run(
        *args, env={"PWD": "/work"}, grants=[*default_grants(), (gdir, "/work", True)], tmp=runs
    )
    n = subprocess.run(
        [str(NATIVE_PERL), *args], cwd=ndir, env={}, capture_output=True, check=False
    )

    def tree(d: Path):
        return {f.name: f.read_bytes() for f in sorted(d.iterdir())}

    return g, n, tree(gdir), tree(ndir)


IN_PLACE = {
    "one-file": (["-pi", "-e", "s/a/b/g", "f"], {"f": "aaa\nxa\n"}),
    "two-files": (["-pi", "-e", "s/a/b/g", "f", "g"], {"f": "a\n", "g": "aa\n"}),
    "backup": (["-pi.bak", "-e", "s/a/b/g", "f"], {"f": "a\n"}),
    "lane": (["-lani", "-e", "print $F[1]", "f"], {"f": "1 2 3\n4 5 6\n"}),
    "slurp-shorter": (["-0777", "-pi", "-e", "s/a+/x/", "f"], {"f": "a" * 5000 + "\n"}),
    "continue-reading": (
        ["-ni", "-e", 'print uc; print STDOUT "eof $ARGV\\n" if eof', "f", "g"],
        {"f": "a\nb\n", "g": "c\n"},
    ),
}


@pytest.mark.parametrize("case", IN_PLACE.values(), ids=IN_PLACE.keys())
def test_in_place_edit_matches_native(held_rename_guest, tmp_path, case):
    args, files = case
    g, n, gfiles, nfiles = _both(held_rename_guest, args, tmp_path, files)
    assert n.returncode == 0, n.stderr
    assert (g.code, g.stdout, g.stderr) == (n.returncode, n.stdout, n.stderr)
    assert gfiles == nfiles


# The handle keeps its number and reads the new file from the old offset (clamped to the new
# size): the one difference from a POSIX host, where it would go on reading the old file.
# sysread, so that no PerlIO buffer stands between the handle and its descriptor.
HELD = (
    'open my $h, "<", "f" or die; my $n = fileno $h; sysread $h, my $buf, 2; '
    'rename "g", "f" or die "rename: $!\\n"; print fileno($h) == $n ? "same\\n" : "moved\\n"; '
    'sysread $h, my $rest, 100; print "$buf|$rest"; open my $c, "<", "f"; print <$c>'
)


def test_rename_over_a_held_file_reopens_it(held_rename_guest, perl_wasm, tmp_path):
    files = {"f": "old file\n", "g": "new file\n"}
    g, n, gfiles, nfiles = _both(held_rename_guest, ["-e", HELD], tmp_path, files)
    assert gfiles == nfiles == {"f": b"new file\n"}
    assert n.stdout == b"same\nol|d file\nnew file\n"
    assert (g.code, g.stdout, g.stderr) == (0, b"same\nol|w file\nnew file\n", b"")
    # The shipped module, where the host's rename succeeds, reads on in the old file as
    # natively: the retry never ran.
    (tmp_path / "shipped").mkdir()
    p = _both(Guest(perl_wasm), ["-e", HELD], tmp_path / "shipped", files)
    assert (p[0].code, p[0].stdout, p[0].stderr) == (0, n.stdout, b"")


def test_a_rename_that_still_fails_keeps_the_handle(held_rename_guest, tmp_path):
    # The held destination is in the read-only library grant: the retry fails too, the first
    # error comes back, and the handle reads on from where it was.
    lib = "/usr/local/lib/perl5/5.42/strict.pm"
    code = (
        f'open my $h, "<", "{lib}" or die "open: $!\\n"; sysread $h, my $a, 7; '
        f'print rename("f", "{lib}") ? "renamed\\n" : "failed: $!\\n"; '
        'sysread $h, my $b, 7; print "$a|$b\\n"; print -e "f" ? "kept\\n" : "gone\\n"'
    )
    g, _, gfiles, _ = _both(held_rename_guest, ["-e", code], tmp_path, {"f": "x\n"})
    assert (g.code, g.stdout, g.stderr) == (
        0,
        b"failed: Permission denied\npackage| strict\nkept\n",
        b"",
    )
    assert gfiles == {"f": b"x\n"}
