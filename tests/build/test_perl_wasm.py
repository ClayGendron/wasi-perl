"""Checks on perl.wasm: it starts, prints, unwinds die through eval, and needs nothing but WASI."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from _build_helpers import DEV, DEV_GUEST, LIBRARY, LIBRARY_GUEST, assert_exit_refused

HELLO = 'print "hello, WebAssembly\\n"'
DIE_IN_EVAL = 'eval { die "boom\\n" }; print "caught: $@"; print "after\\n"'
NESTED = (
    'eval { eval { die "inner\\n" }; print "inner: $@"; die "outer\\n" }; print "outer: $@";'
    'eval { die { code => 42 } }; print "object: $@->{code}\\n";'
    "package Err { sub new { bless { m => $_[1] }, $_[0] } sub m { $_[0]{m} } }"
    'eval { die Err->new("blessed") }; print "class: ", ref $@, " ", $@->m, "\\n";'
    'my @s = eval { sort { die "in sort\\n" } 3, 1, 2 }; print "sort: $@";'
    'print join(",", sort { $a <=> $b } 3, 1, 2), "\\n";'
    'my $n = 0; for my $i (1 .. 1000) { eval { die "x\\n" }; $n++ if $@ } print "loops: $n\\n";'
    'sub deep { my $d = shift; $d ? deep($d - 1) : die "deep\\n" } eval { deep(200) }; print "deep: $@";'
)


def test_hello(guest):
    r = guest("-e", HELLO)
    assert (r.code, r.stdout, r.stderr) == (0, b"hello, WebAssembly\n", b"")


def test_hello_on_the_wasmtime_command_line(
    perl_wasm: Path, wasmtime_cli: Path, tmp_path: Path
):
    # The wasmtime command line grants every folder writable, so it is given only a throwaway
    # /dev holding an empty null, and no artifact folder: print needs no library.
    dev = tmp_path / "dev"
    dev.mkdir()
    (dev / "null").write_bytes(b"")
    p = subprocess.run(
        [
            str(wasmtime_cli),
            "run",
            "-W",
            "exceptions=y",
            "--dir",
            f"{dev}::{DEV_GUEST}",
            str(perl_wasm),
            "-e",
            HELLO,
        ],
        capture_output=True,
        check=False,
    )
    assert (p.returncode, p.stdout, p.stderr) == (0, b"hello, WebAssembly\n", b"")


def test_die_in_eval(guest):
    r = guest("-e", DIE_IN_EVAL)
    assert (r.code, r.stdout, r.stderr) == (0, b"caught: boom\nafter\n", b"")


def test_die_in_eval_nested_object_and_sort_match_native(guest, native):
    g, n = guest("-e", NESTED), native("-e", NESTED)
    assert n.code == 0 and b"deep: deep" in n.stdout
    assert (g.code, g.stdout, g.stderr) == (n.code, n.stdout, n.stderr)


def test_die_uncaught(guest, native):
    # Perl ends an uncaught die with status 255. wasmtime refuses a WASI exit status of 126 or
    # more and reports an error instead, so the module keeps the status for the embedder to read
    # from its wasi_perl_exit_status export. Nothing traps inside the program.
    r = guest("-e", 'die "bye\\n"')
    assert (r.stdout, r.stderr) == (b"", b"bye\n")
    assert_exit_refused(r, 255)
    assert native("-e", 'die "bye\\n"').code == 255
    # END sees the same $? as natively.
    status = 'END { my $s = $?; $? = 0; print "status=$s\\n" } die "bye\\n"'
    g, n = guest("-e", status), native("-e", status)
    assert (g.code, g.stdout, g.stderr, g.status) == (0, b"status=255\n", b"bye\n", 0)
    assert (n.code, n.stdout, n.stderr) == (0, b"status=255\n", b"bye\n")


@pytest.mark.parametrize("status", [0, 1, 7, 125, 126, 200, 255])
def test_exit_status_export(guest, native, status):
    r = guest("-e", f"exit {status}")
    assert r.status == status == native("-e", f"exit {status}").code
    if status < 126:
        assert (r.code, r.error) == (status, "")
    else:
        assert_exit_refused(r, status)


@pytest.mark.parametrize(
    ("value", "status"), [(256, 0), (-1, 255), (300, 44), (511, 255)]
)
def test_exit_status_is_the_low_byte(guest, native, value, status):
    # exit takes the low eight bits, as natively; -1 stays the export's "never exited".
    n = native("-e", f"exit {value}")
    assert n.code == status
    r = guest("-e", f"exit {value}")
    assert r.status == status
    if status < 126:
        assert (r.code, r.error) == (status, "")
    else:
        assert_exit_refused(r, status)


def test_exit_status_export_before_exit(guest_engine):
    import wasmtime

    store = wasmtime.Store(guest_engine.engine)
    store.set_wasi(wasmtime.WasiConfig())
    linker = wasmtime.Linker(guest_engine.engine)
    linker.define_wasi()
    instance = linker.instantiate(store, guest_engine.module)
    assert instance.exports(store)["wasi_perl_exit_status"](store) == -1


def test_exit_code(guest):
    r = guest("-e", "exit 7")
    assert (r.code, r.stdout, r.stderr) == (7, b"", b"")


def test_version(guest):
    r = guest("-v")
    assert (
        r.code == 0
        and b"This is perl 5, version 42, subversion 3 (v5.42.3) built for wasm32-wasi"
        in r.stdout
    )
    r = guest("-V:osname")
    assert (r.code, r.stdout) == (0, b"osname='wasi';\n")


def _imports(wasm: bytes) -> list[tuple[str, str]]:
    """The (module, name) of every import, read from the binary's import section."""

    def leb(b: bytes, i: int) -> tuple[int, int]:
        n = shift = 0
        while True:
            byte = b[i]
            i += 1
            n |= (byte & 0x7F) << shift
            shift += 7
            if byte < 0x80:
                return n, i

    def name(b: bytes, i: int) -> tuple[str, int]:
        n, i = leb(b, i)
        return b[i : i + n].decode(), i + n

    assert wasm[:8] == b"\0asm\x01\0\0\0", "not a WebAssembly module"
    i, found = 8, []
    while i < len(wasm):
        section, i = wasm[i], i + 1
        size, i = leb(wasm, i)
        if section == 2:
            j = i
            count, j = leb(wasm, j)
            for _ in range(count):
                mod, j = name(wasm, j)
                field, j = name(wasm, j)
                kind, j = wasm[j], j + 1
                if kind == 0:  # function: a type index
                    _, j = leb(wasm, j)
                elif kind == 1:  # table: element type and limits
                    j += 1
                    flags, j = leb(wasm, j)
                    _, j = leb(wasm, j)
                    if flags & 1:
                        _, j = leb(wasm, j)
                elif kind == 2:  # memory: limits
                    flags, j = leb(wasm, j)
                    _, j = leb(wasm, j)
                    if flags & 1:
                        _, j = leb(wasm, j)
                elif kind == 3:  # global: value type and mutability
                    j += 2
                elif kind == 4:  # tag: attribute and type index
                    j += 1
                    _, j = leb(wasm, j)
                found.append((mod, field))
        i += size
    return found


def test_no_wasm_opt(perl_wasm: Path):
    # The module is what wasm-ld wrote: binaryen's wasm-opt never ran on it.
    assert b"binaryen" not in perl_wasm.read_bytes()


def test_imports(perl_wasm: Path):
    imports = _imports(perl_wasm.read_bytes())
    assert imports, "the module imports nothing, which a WASI command cannot do"
    assert {m for m, _ in imports} == {"wasi_snapshot_preview1"}, imports


def test_exceptions_are_required(perl_wasm: Path, wasmtime_cli: Path):
    p = subprocess.run(
        [str(wasmtime_cli), "run", "-W", "exceptions=n", str(perl_wasm), "-e", "1"],
        capture_output=True,
        check=False,
    )
    assert p.returncode != 0
    assert b"exceptions proposal not enabled" in p.stderr
    wasmtime = pytest.importorskip("wasmtime")
    config = wasmtime.Config()
    config.wasm_exceptions = False
    with pytest.raises(wasmtime.WasmtimeError, match="exceptions proposal not enabled"):
        wasmtime.Module.from_file(wasmtime.Engine(config), str(perl_wasm))


def test_library_is_read(guest):
    r = guest("-e", 'use strict; use warnings; print "ok\\n"')
    assert (r.code, r.stdout, r.stderr) == (0, b"ok\n", b"")
    r = guest("-e", 'print "$_\\n" for @INC')
    assert r.stdout.split() == [
        b"/usr/local/lib/perl5/5.42/site_perl/wasm32-wasi",
        b"/usr/local/lib/perl5/5.42/site_perl",
        b"/usr/local/lib/perl5/5.42/wasm32-wasi",
        b"/usr/local/lib/perl5/5.42",
    ]


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"LC_ALL": "C"},
        {"LC_CTYPE": "C.UTF-8", "LC_COLLATE": "C"},
        {"LANG": "C.UTF-8", "LC_NUMERIC": "C"},
    ],
    ids=["unset", "LC_ALL=C", "mixed", "LANG-and-numeric"],
)
def test_locales(guest, env):
    r = guest("-e", HELLO, env=env)
    assert (r.code, r.stdout, r.stderr) == (0, b"hello, WebAssembly\n", b"")


def test_dev_null(guest):
    assert (DEV / "null").stat().st_size == 0
    read = 'open my $f, "<", "/dev/null" or die "open: $!\\n"; my @l = <$f>; print "lines=", scalar(@l), " eof=", (eof($f) ? 1 : 0), "\\n"'
    r = guest("-e", read)
    assert (r.code, r.stdout, r.stderr) == (0, b"lines=0 eof=1\n", b"")
    write = 'if (open my $f, ">", "/dev/null") { print "opened\\n" } else { print "refused\\n" }'
    r = guest("-e", write)
    assert (r.code, r.stdout, r.stderr) == (0, b"refused\n", b"")
    assert (DEV / "null").stat().st_size == 0
    # Without the stand-in /dev, -e has no empty script file to open.
    r = guest("-e", HELLO, grants=[(LIBRARY, LIBRARY_GUEST, False)])
    assert r.code != 0 and b"/dev/null" in r.stderr


def test_pwd_sets_the_working_directory(guest, tmp_path: Path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "data.txt").write_bytes(b"from data.txt\n")
    grants = [
        (LIBRARY, LIBRARY_GUEST, False),
        (DEV, DEV_GUEST, False),
        (work, "/work", False),
    ]
    prog = 'open my $f, "<", "data.txt" or die "no data.txt: $!\\n"; print <$f>'
    r = guest("-e", prog, env={"PWD": "/work"}, grants=grants)
    assert (r.code, r.stdout, r.stderr) == (0, b"from data.txt\n", b"")
    r = guest(
        "-e",
        'opendir my $d, "." or die; print join(",", sort grep { !/^\\./ } readdir $d), "\\n"',
        env={"PWD": "/work"},
        grants=grants,
    )
    assert (r.code, r.stdout) == (0, b"data.txt\n")
    # Without PWD, or with a relative one, the working directory stays "/".
    for env in ({}, {"PWD": "work"}):
        r = guest("-e", prog, env=env, grants=grants)
        assert r.stdout == b"" and r.stderr.startswith(b"no data.txt: ")


PROGRAMS = [
    HELLO,
    DIE_IN_EVAL,
    NESTED,
    "exit 7",
    'use strict; use warnings; print "ok\\n"',
    'my %h = (b => 2, a => 1); print join(",", map { "$_=$h{$_}" } sort keys %h), "\\n"',
    'print 2**53 + 1, " ", 9223372036854775807, " ", -9223372036854775808, " ", 0xFFFFFFFF + 1, "\\n"',
    'printf "%.15g %d %s\\n", 0.1 + 0.2, 1e15, 1/7',
    'my $s = "Hello World"; $s =~ s/(\\w+)/\\u\\L$1/g; print scalar reverse($s), " ", length $s, "\\n"',
    'print join(" ", map { sprintf "%03b", $_ } 0 .. 7), "\\n"',
    'warn "a warning\\n"; print STDERR "to stderr\\n"; print "to stdout\\n"',
    'print "$]\\n"; print "$^V\\n"',
]


@pytest.mark.parametrize("prog", PROGRAMS, ids=range(len(PROGRAMS)))
def test_matches_native(guest, native, prog):
    g, n = guest("-e", prog), native("-e", prog)
    assert (g.code, g.stdout, g.stderr) == (n.code, n.stdout, n.stderr)
