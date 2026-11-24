"""Checks on how many host calls the guest's reads and writes make, and on the memchr they use.

Every WASI call that reaches the host's files is far dearer than work the guest does itself, so
the guest reads and writes in 64 KiB pieces, answers isatty itself for a regular file its
descriptor table looked at before the open, and finds a line's end sixteen bytes a step. The
checks count calls and instructions, never time.

The calls are counted from the wasmtime command line's trace of every WASI call
(WASMTIME_LOG=wasmtime_wasi=trace), the same wasmtime-wasi as wasmtime-py 51's. A Python host
function cannot count and pass a call on: wasmtime-py's WASI functions read the calling
instance's memory and refuse a call from the host ("missing required memory export"). The
command line grants every folder writable, so the traced runs get a throwaway /dev and a
folder of their own, and no library: none of their programs loads a module.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from _build_helpers import (
    BUILD,
    DEV,
    DEV_GUEST,
    LIBRARY,
    LIBRARY_GUEST,
    NATIVE_PERL,
)

SDK_BIN = BUILD / "tools" / "wasi-sdk-34.0-x86_64-linux" / "bin"
MEMCHR_TEST = BUILD / "tests" / "memchr-test.wasm"
# wasm-ld's output, before the build's wasm-opt pass. Its functions keep their names, and each
# is the one the compiler wrote: the pass inlines and drops the names.
LINKED = BUILD / "link" / "perl.wasm"
SIMD = r"\b(v128|i8x16|i16x8|i32x4|i64x2|f32x4|f64x2)\."
SIMD_OP = r"\b(?:v128|i8x16|i16x8|i32x4|i64x2|f32x4|f64x2)\.[a-z0-9_]+"

# One trace line: the function's name, then either its arguments or "result=...".
TRACE = re.compile(r'function="([a-z_0-9]+)"\}: \S+: (.*)$')
FD = re.compile(r"\bfd=Fd\((\d+)\)")
OPENED = re.compile(r"result=Ok\(Fd\((\d+)\)\)")


class Trace:
    """The WASI calls of one guest run, in order: (function, arguments, result)."""

    def __init__(self, text: str) -> None:
        self.calls: list[tuple[str, str, str]] = []
        args: dict[str, str] = {}
        for line in text.splitlines():
            m = TRACE.search(line)
            if not m:
                continue
            fn, rest = m.groups()
            if rest.startswith("result="):
                self.calls.append((fn, args.pop(fn, ""), rest))
            else:
                args[fn] = rest

    def count(self, fn: str) -> int:
        return sum(1 for f, _, _ in self.calls if f == fn)

    def on_fd(self, fn: str, fd: int) -> int:
        return sum(1 for f, a, _ in self.calls if f == fn and _fd(a) == fd)

    def on_opened(self, fn: str) -> int:
        """Calls of fn on a host descriptor while it is one path_open gave. (The trace shows a
        path only as a guest address, so the traced programs open nothing but their files.)"""
        held: set[int] = set()
        n = 0
        for f, a, r in self.calls:
            if f == "path_open":
                m = OPENED.match(r)
                if m:
                    held.add(int(m.group(1)))
            elif f == "fd_close":
                held.discard(_fd(a))
            elif f == fn and _fd(a) in held:
                n += 1
        return n


def _fd(args: str) -> int | None:
    m = FD.search(args)
    return int(m.group(1)) if m else None


def _traced(wasmtime_cli: Path, perl_wasm: Path, folder: Path, *args: str):
    """Runs perl with args under the wasmtime command line, with folder at /g as the current
    folder. Returns the finished process and its Trace."""
    dev = folder.parent / "dev"
    dev.mkdir(exist_ok=True)
    (dev / "null").write_bytes(b"")
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
            "--argv0",
            "perl",
            str(perl_wasm),
            *args,
        ],
        env={"WASMTIME_LOG": "wasmtime_wasi=trace", "RUST_BACKTRACE": "0", "NO_COLOR": "1"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=120,  # a fail-safe for the suite, not a measurement
        check=False,
    )
    return p, Trace(p.stderr.decode("utf-8", "replace"))


def _program_stderr(p) -> bytes:
    """The guest's own stderr, without the trace's lines."""
    return b"".join(
        ln for ln in p.stderr.splitlines(keepends=True) if b" TRACE " not in ln and ln.strip()
    )


# A file of exactly 1 MiB: 16,384 lines of 64 bytes, every fourth holding an "x".
LINES = [(f"{i:06d} " + ("x" if i % 4 == 0 else "a") * 56 + "\n").encode() for i in range(16384)]


@pytest.fixture
def mebibyte(tmp_path: Path) -> Path:
    folder = tmp_path / "g"
    folder.mkdir()
    data = b"".join(LINES)
    assert len(data) == 1 << 20
    (folder / "big.txt").write_bytes(data)
    return folder


def test_a_line_read_over_a_mebibyte_makes_at_most_17_reads(wasmtime_cli, perl_wasm, mebibyte):
    p, t = _traced(wasmtime_cli, perl_wasm, mebibyte, "-ne", "print if /x/", "/g/big.txt")
    assert (p.returncode, _program_stderr(p)) == (0, b"")
    assert p.stdout == b"".join(ln for ln in LINES if b"x" in ln)
    # 16 reads of 64 KiB and one that finds the end; 128 and one with 8 KiB. The lower bound
    # keeps a trace that was not read from passing with no calls.
    assert 16 <= t.count("fd_read") <= 17, t.count("fd_read")


def test_an_in_place_edit_of_a_mebibyte_makes_at_most_17_writes(wasmtime_cli, perl_wasm, mebibyte):
    p, t = _traced(wasmtime_cli, perl_wasm, mebibyte, "-pi", "-e", "s/a/b/g", "/g/big.txt")
    assert (p.returncode, p.stdout, _program_stderr(p)) == (0, b"", b"")
    assert (mebibyte / "big.txt").read_bytes() == b"".join(LINES).replace(b"a", b"b")
    assert 16 <= t.count("fd_write") <= 17, t.count("fd_write")
    assert 16 <= t.count("fd_read") <= 17, t.count("fd_read")


def test_memchr_matches_a_byte_loop(guest_engine, tmp_path):
    # The build compiles tests/build/memchr-test.c against the recipe's memchr.
    assert MEMCHR_TEST.exists(), f"{MEMCHR_TEST} is missing: scripts/20_target.sh builds it"
    wt = guest_engine.wasmtime
    out = tmp_path / "memchr.stdout"
    wasi = wt.WasiConfig()
    wasi.argv = ["memchr-test"]
    wasi.stdout_file = str(out)
    store = wt.Store(guest_engine.engine)
    store.set_wasi(wasi)
    linker = wt.Linker(guest_engine.engine)
    linker.define_wasi()
    module = wt.Module.from_file(guest_engine.engine, str(MEMCHR_TEST))
    code = 0
    try:
        linker.instantiate(store, module).exports(store)["_start"](store)
    except wt.ExitTrap as e:
        code = e.code
    # 32 start offsets, lengths 0 to 79, and every target position from before the range to
    # two past its end: 32 * (3 + 4 + ... + 82) cases. Then ranges that end at the last byte of
    # linear memory: lengths 0 to 80 with every target position, the last byte found from up to
    # a page away, and two empty ranges past the end.
    assert (code, out.read_bytes()) == (0, b"ok 108800\nend of memory ok 13979\n")


def _disassembly(wasm: Path, symbol: str) -> str:
    objdump = SDK_BIN / "llvm-objdump"
    if not objdump.exists():
        pytest.skip(f"{objdump} is missing; run scripts/00_download.sh")
    p = subprocess.run(
        [str(objdump), "-d", f"--disassemble-symbols={symbol}", str(wasm)],
        capture_output=True,
        text=True,
        check=True,
    )
    return p.stdout


@pytest.mark.parametrize("which", ["perl.wasm", "memchr-test.wasm"])
def test_the_memchr_linked_compares_sixteen_bytes_a_step(perl_wasm, which):
    # The recipe's memchr, not wasi-libc's word loop: it compares with SIMD128. perl.wasm's is
    # read where wasm-ld linked it, by its name.
    wasm = LINKED if which == "perl.wasm" else MEMCHR_TEST
    assert wasm.exists(), f"{wasm} is missing: scripts/20_target.sh or 40_link.sh builds it"
    text = _disassembly(wasm, "memchr")
    assert "<memchr>:" in text, text[:400]
    assert "i8x16.eq" in text and "i8x16.bitmask" in text, text[:2000]


def _whole_disassembly(wasm: Path) -> str:
    _disassembly(wasm, "memchr")  # skips when llvm-objdump is missing
    objdump = SDK_BIN / "llvm-objdump"
    return subprocess.run(
        [str(objdump), "-d", str(wasm)], capture_output=True, text=True, check=True
    ).stdout


def test_only_memchr_uses_simd(perl_wasm):
    # Only the recipe's memchr is compiled for SIMD128: the rest of the module wasm-ld linked
    # has no v128 code.
    assert LINKED.exists(), f"{LINKED} is missing: scripts/40_link.sh builds it"
    assert "i8x16" in _disassembly(LINKED, "memchr")
    users, current = set(), None
    for line in _whole_disassembly(LINKED).splitlines():
        m = re.match(r"^[0-9a-f]+ <(.+)>:$", line)
        if m:
            current = m.group(1)
        elif current and re.search(SIMD, line):
            users.add(current)
    assert users == {"memchr"}, sorted(users)


# What the build's wasm-opt pass adds to the SIMD of the module wasm-ld linked: it writes a
# 16-byte store of a constant as one v128.store. Pinned exactly, so a binaryen that vectorised
# more would be seen, and a review would decide.
PASS_SIMD = {"v128.const", "v128.store"}
WASM_DIS = BUILD / "tools" / "binaryen-version_133" / "bin" / "wasm-dis"


def _simd_by_function(wasm: Path) -> tuple[set[str], int]:
    """The SIMD ops of the module and how many functions use any, by binaryen's disassembler,
    which numbers the functions a pass left unnamed."""
    assert WASM_DIS.is_file(), f"{WASM_DIS} is missing: scripts/00_download.sh unpacks it"
    text = subprocess.run(
        [str(WASM_DIS), str(wasm)], capture_output=True, text=True, check=True
    ).stdout
    ops: set[str] = set()
    users: set[str] = set()
    current = None
    for line in text.splitlines():
        m = re.match(r"^ \(func \$(\S+)", line)
        if m:
            current = m.group(1)
        found = re.findall(SIMD_OP, line)
        if found and current is not None:
            ops |= set(found)
            users.add(current)
    return ops, len(users)


@pytest.mark.parametrize("module", ["perl.wasm", "perl-reactor.wasm"])
def test_the_shipped_module_keeps_memchr_s_sixteen_byte_compare(perl_wasm, module):
    # The pass drops memchr's name, but keeps its SIMD128 compare: the shipped module still
    # compares sixteen bytes a step, with exactly the linked module's SIMD and the pass's own
    # store, in at most two functions.
    linked = BUILD / "link" / module
    shipped = perl_wasm.parent / module
    assert linked.exists() and shipped.exists(), "scripts/40_link.sh builds both"
    linked_ops, linked_users = _simd_by_function(linked)
    shipped_ops, shipped_users = _simd_by_function(shipped)
    assert {"i8x16.eq", "i8x16.bitmask"} <= linked_ops
    assert linked_users == 1
    assert shipped_ops == linked_ops | PASS_SIMD
    assert shipped_users <= 2


# The baseline's 10 MB log: 200,000 lines, every seventh an ERROR.
def _write_log(path: Path) -> None:
    with open(path, "w", newline="\n") as f:
        for i in range(200_000):
            level = f"ERROR {i % 500}" if i % 7 == 0 else "INFO ok"
            f.write(f"2026-11-24T10:{i % 60:02d}:{i % 59:02d} worker{i % 16} {level} request {i}\n")


def _fuel(module_path: Path, data: Path, tmp: Path, *args: str) -> tuple[int, bytes]:
    """The instructions one run executes, under the default read-only grants and /data, with
    the hash seed fixed so that the hash order does not change the work."""
    import wasmtime as wt

    config = wt.Config()
    config.wasm_exceptions = True
    config.consume_fuel = True
    engine = wt.Engine(config)
    module = wt.Module.from_file(engine, str(module_path))
    out, err = tmp / "fuel.stdout", tmp / "fuel.stderr"
    wasi = wt.WasiConfig()
    wasi.argv = ["perl", *args]
    wasi.env = [("PERL_HASH_SEED", "0")]
    wasi.stdout_file = str(out)
    wasi.stderr_file = str(err)
    wasi.preopen_dir(str(LIBRARY), LIBRARY_GUEST, False)
    wasi.preopen_dir(str(DEV), DEV_GUEST, False)
    wasi.preopen_dir(str(data), "/data", False)
    store = wt.Store(engine)
    store.set_wasi(wasi)
    store.set_fuel(10**13)
    linker = wt.Linker(engine)
    linker.define_wasi()
    code = 0
    try:
        linker.instantiate(store, module).exports(store)["_start"](store)
    except wt.ExitTrap as e:
        code = e.code
    assert (code, err.read_bytes()) == (0, b"")
    return 10**13 - store.get_fuel(), out.read_bytes()


GREP_LOG = ("-ne", "$n++ if /ERROR \\d+/; END {print $n}", "/data/t.log")
# The 0.1.0 module's count for GREP_LOG, from _fuel on the perl.wasm built from 0.1.0's
# recipes (context/research/2026-11-24-the-guests-reads-and-writes-measured.md has the command).
FUEL_0_1_0 = 643_780_719


def test_line_work_uses_at_least_8_percent_less_fuel_than_0_1_0(perl_wasm, tmp_path):
    pytest.importorskip("wasmtime")
    data = tmp_path / "data"
    data.mkdir()
    _write_log(data / "t.log")
    used, out = _fuel(perl_wasm, data, tmp_path, *GREP_LOG)
    assert out == b"28572"
    assert used <= FUEL_0_1_0 * 0.92, (used, used / FUEL_0_1_0)


@pytest.fixture
def many(tmp_path: Path) -> Path:
    """200 files of 50 lines, about 2.5 KB each: an agent's grep over a folder."""
    folder = tmp_path / "g"
    folder.mkdir()
    for k in range(200):
        lines = [
            f"2026-11-24 worker{i % 16} {'ERROR' if (i + k) % 7 == 0 else 'INFO'} request {i}\n"
            for i in range(50)
        ]
        (folder / f"f{k:03d}.log").write_text("".join(lines))
    return folder


MANY = "$n++ if /ERROR/; END { print $n }"


def test_opening_200_files_makes_200_fewer_fdstat_calls(wasmtime_cli, perl_wasm, many):
    names = [f"/g/f{k:03d}.log" for k in range(200)]
    p, t = _traced(wasmtime_cli, perl_wasm, many, "-ne", MANY, *names)
    one, t1 = _traced(wasmtime_cli, perl_wasm, many, "-ne", MANY, names[0])
    want = sum(1 for f in many.iterdir() for ln in f.read_text().splitlines() if "ERROR" in ln)
    assert (p.returncode, p.stdout, _program_stderr(p)) == (0, str(want).encode(), b"")
    assert one.returncode == 0, one.stderr[-400:]
    # No file opened is asked whether it is a terminal: each costed one fd_fdstat_get before.
    # The trace must show the opens, or the zero proves nothing.
    assert (t.count("path_open") >= 200, t1.count("path_open") >= 1) == (True, True)
    assert t.on_opened("fd_fdstat_get") == 0
    # What is left per file is wasi-libc's look at the folder's rights: one call.
    per_199 = t.count("fd_fdstat_get") - t1.count("fd_fdstat_get")
    assert per_199 <= 199, per_199


TTY = (
    'open(my $f, "<", $ARGV[0]) or die "$!\\n"; '
    'open(my $w, ">>", $ARGV[0]) or die "$!\\n"; '
    'open(my $d, "<&", $f) or die "$!\\n"; '
    "for my $h ($f, $w, $d) { $! = 0; my $t = -t $h; "
    'print $t ? "tty" : "notty", " ", $!{ENOTTY} ? "ENOTTY" : $! + 0, "\\n" } '
    'open(STDIN, "<", $ARGV[0]) or die "$!\\n"; $! = 0; '
    'print -t STDIN ? "tty" : "notty", " ", $!{ENOTTY} ? "ENOTTY" : $! + 0, "\\n"; '
    'print POSIX::isatty(fileno $f) ? "tty" : "notty", "\\n"'
)


def test_t_is_false_on_a_regular_file(guest, tmp_path):
    folder = tmp_path / "g"
    folder.mkdir()
    (folder / "file.txt").write_bytes(b"text\n")
    if not NATIVE_PERL.exists():
        pytest.skip("native perl is missing; run scripts/10_native.sh")
    n = subprocess.run(
        [str(NATIVE_PERL), "-MPOSIX", "-e", TTY, str(folder / "file.txt")],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env={},
        check=False,
    )
    assert n.returncode == 0, n.stderr
    g = guest(
        "-MPOSIX",
        "-e",
        TTY,
        "/g/file.txt",
        grants=[(LIBRARY, LIBRARY_GUEST, False), (DEV, DEV_GUEST, False), (folder, "/g", True)],
    )
    assert (g.code, g.stdout, g.stderr) == (0, n.stdout, n.stderr)
    assert g.stdout == b"notty ENOTTY\n" * 4 + b"notty\n"


def test_a_regular_file_is_not_asked_whether_it_is_a_terminal(wasmtime_cli, perl_wasm, tmp_path):
    folder = tmp_path / "g"
    folder.mkdir()
    (folder / "old.txt").write_bytes(b"text\n")
    code = (
        'open(my $r, "<", "old.txt") or die; open(my $a, ">>", "old.txt") or die; '
        'print -t $r ? 1 : 0, -t $a ? 1 : 0, "\\n"'
    )
    p, t = _traced(wasmtime_cli, perl_wasm, folder, "-e", code)
    assert (p.returncode, p.stdout, _program_stderr(p)) == (0, b"00\n", b"")
    assert t.count("path_open") >= 2, t.count("path_open")
    assert t.on_opened("fd_fdstat_get") == 0


def test_a_file_the_table_did_not_look_at_asks_the_host(wasmtime_cli, perl_wasm, tmp_path):
    # A new file had no name to look at before its open, so isatty asks the host as before:
    # once as PerlIO pushes its buffer, once for -t.
    folder = tmp_path / "g"
    folder.mkdir()
    code = 'open(my $n, ">", "new.txt") or die; print -t $n ? 1 : 0, "\\n"'
    p, t = _traced(wasmtime_cli, perl_wasm, folder, "-e", code)
    assert (p.returncode, p.stdout, _program_stderr(p)) == (0, b"0\n", b"")
    assert t.on_opened("fd_fdstat_get") == 2


@pytest.mark.parametrize("handle,fd", [("STDIN", 0), ("STDOUT", 1), ("STDERR", 2)])
def test_t_on_a_standard_handle_asks_the_host(wasmtime_cli, perl_wasm, tmp_path, handle, fd):
    folder = tmp_path / "g"
    folder.mkdir()
    p, t = _traced(wasmtime_cli, perl_wasm, folder, "-e", f"$x = -t {handle}; print 1")
    q, u = _traced(wasmtime_cli, perl_wasm, folder, "-e", "$x = 0; print 1")
    assert (p.returncode, p.stdout, q.returncode, q.stdout) == (0, b"1", 0, b"1")
    assert t.on_fd("fd_fdstat_get", fd) == u.on_fd("fd_fdstat_get", fd) + 1


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs")
def test_a_fifo_in_a_grant_is_still_refused(wasmtime_cli, perl_wasm, tmp_path):
    folder = tmp_path / "g"
    folder.mkdir()
    os.mkfifo(folder / "pipe")
    (folder / "file").write_bytes(b"text\n")
    code = 'open(my $f, "<", $ARGV[0]) ? print "opened\\n" : print "$!\\n"'
    p, t = _traced(wasmtime_cli, perl_wasm, folder, "-e", code, "pipe")
    q, u = _traced(wasmtime_cli, perl_wasm, folder, "-e", code, "file")
    assert (p.returncode, p.stdout) == (0, b"No such device or address\n")
    assert (q.returncode, q.stdout) == (0, b"opened\n")
    # The look refuses the FIFO before the host is asked to open it.
    assert t.count("path_filestat_get") == u.count("path_filestat_get")
    assert t.count("path_open") == u.count("path_open") - 1
