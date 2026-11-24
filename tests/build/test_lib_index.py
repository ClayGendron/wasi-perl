"""Checks on the library index and on the build steps that make it.

The index is a sorted list of every file and folder of the library, compiled into perl.wasm.
With it the descriptor table answers "no such file" itself for a library path the library does
not hold, where the host would answer the same, and asks the host about every other.

The lookup is checked at the C level by lib_index_probe.c, linked with the recipe's table, whose
two calls to the host are renamed to counting functions that pass them on, and with an index
that scripts/lib_index.sh makes from a folder of the check's own. The shipped module is not
built this way. The rest checks the built set: its index is the library MANIFEST lists, and the
link that put it there is the build's last.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from _build_helpers import ARTIFACTS, BUILD, LIBRARY_GUEST, ROOT

HERE = Path(__file__).resolve().parent
CC = ROOT / "recipes" / "wasi-cc"
LIB_INDEX = ROOT / "scripts" / "lib_index.sh"
MARKER = b"wasi-perl-lib-index-sha256:"
FLAGS = ["-O2", "-Wall", "-Wextra", "-Werror"]


def _names_of(source: str) -> list[str]:
    """The names array of a C source lib_index.sh wrote, in its order."""
    array = source.split("names[] = {", 1)[1].split("};", 1)[0]
    return re.findall(r'^\s*"([^"]*)",$', array, re.M)


def _marker_of(source: str) -> str:
    m = re.search(r'"wasi-perl-lib-index-sha256:([0-9a-f]{64})"', source)
    assert m, source[-400:]
    return m.group(1)


def _sha_of_lines(names: list[str]) -> str:
    return hashlib.sha256("".join(n + "\n" for n in names).encode()).hexdigest()


@pytest.fixture(scope="session")
def probe_lib(tmp_path_factory) -> Path:
    """The check's library: 5.42/A.pm and 5.42/A.pmc."""
    lib = tmp_path_factory.mktemp("probe-lib")
    (lib / "5.42").mkdir()
    (lib / "5.42" / "A.pm").write_text("1;\n")
    (lib / "5.42" / "A.pmc").write_text("1;\n")
    return lib


@pytest.fixture(scope="session")
def probes(tmp_path_factory, probe_lib, wasmtime_cli) -> dict[str, Path]:
    """The probe linked with the index of probe_lib, and linked with the empty index."""
    if not (BUILD / "stubs" / "libwasi-perl-fd-table.a").exists():
        pytest.skip("build/stubs is missing; run scripts/build.sh")
    out = tmp_path_factory.mktemp("lib-index-probe")
    table = out / "table.o"
    renames = [
        "-D__real___wasi_path_filestat_get=counted_stat",
        "-D__real___wasi_path_open=counted_open",
    ]
    src = ROOT / "recipes" / "wasi-fd-table.c"
    subprocess.run([str(CC), *FLAGS, *renames, "-c", str(src), "-o", str(table)], check=True)
    index_c, index_o = out / "lib-index.c", out / "lib-index.o"
    subprocess.run(["sh", str(LIB_INDEX), str(probe_lib), str(index_c)], check=True)
    subprocess.run([str(CC), "-O2", "-c", str(index_c), "-o", str(index_o)], check=True)
    built = {}
    for name, extra in (("indexed", [str(index_o)]), ("empty", [])):
        wasm = out / f"{name}.wasm"
        subprocess.run(
            [str(CC), *FLAGS, str(HERE / "lib_index_probe.c"), str(table), *extra, "-o", str(wasm)],
            check=True,
        )
        built[name] = wasm
    return built


def _probe(wasmtime_cli: Path, wasm: Path, lib: Path, *args: str) -> str:
    p = subprocess.run(
        [str(wasmtime_cli), "run", "-W", "exceptions=y", "--dir", f"{lib}::{LIBRARY_GUEST}"]
        + [str(wasm), *args],
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert (p.returncode, p.stderr) == (0, b""), p
    return p.stdout.decode().strip()


def _lib(tmp_path: Path, *files: str) -> Path:
    lib = tmp_path / "lib"
    for f in files:
        (lib / f).parent.mkdir(parents=True, exist_ok=True)
        (lib / f).write_text("1;\n")
    return lib


def _index_of(lib: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(LIB_INDEX), str(lib), str(out)], capture_output=True)


def test_lib_index_lists_every_file_and_folder_in_folded_order(tmp_path):
    lib = _lib(tmp_path, "5.42/A.pm", "5.42/A.pmc", "5.42/A-B/c.pm", "5.42/Z.pm", "5.42/b.pm")
    out = tmp_path / "index.c"
    p = _index_of(lib, out)
    assert p.returncode == 0, p
    source = out.read_text()
    names = _names_of(source)
    # The order of the names with A to Z made a to z, in bytes: "-" (0x2d) and "." (0x2e) sort
    # before "/" (0x2f), and b.pm before Z.pm. Each name keeps its own case.
    assert names == [
        "5.42",
        "5.42/A-B",
        "5.42/A-B/c.pm",
        "5.42/A.pm",
        "5.42/A.pmc",
        "5.42/b.pm",
        "5.42/Z.pm",
    ], source
    assert re.search(rf"^\s*{len(names)},$", source, re.M), source
    assert _marker_of(source) == _sha_of_lines(names)
    assert str(lib) not in source  # the bytes do not depend on where the build ran


@pytest.mark.parametrize(
    ("files", "error"),
    [
        (("5.42/A.pm", "5.42/Empty/"), b"empty folder"),
        (("5.42/A.pm", "5.42/L.pm@A.pm"), b"neither a file nor a folder"),
        (("5.42/A.pm", "5.42/x\nb.pm"), b"a newline"),
        (("5.42/A.pm", "5.42/a.pm"), b"differ only in case"),
        (("5.42/A.pm", "5.42/Lib/x.pm", "5.42/LIB/y.pm"), b"differ only in case"),
    ],
    ids=["an empty folder", "a link", "a newline", "two files by case", "two folders by case"],
)
def test_lib_index_refuses(tmp_path, files, error):
    lib = tmp_path / "lib"
    for f in files:
        if f.endswith("/"):
            (lib / f).mkdir(parents=True)
        elif "@" in f:
            name, target = f.split("@")
            (lib / name).symlink_to(target)
        else:
            (lib / f).parent.mkdir(parents=True, exist_ok=True)
            (lib / f).write_text("1;\n")
    p = _index_of(lib, tmp_path / "out.c")
    assert p.returncode != 0 and error in p.stderr, p
    assert not (tmp_path / "out.c").exists()


def test_a_pmc_the_index_lists_is_asked_of_the_host(probes, probe_lib, wasmtime_cli):
    got = _probe(wasmtime_cli, probes["indexed"], probe_lib, "stat", "5.42/A.pmc")
    assert got == "ok, 1 stats, 0 opens"


def test_a_pmc_the_index_does_not_list_is_answered_without_the_host(
    probes, probe_lib, wasmtime_cli
):
    got = _probe(wasmtime_cli, probes["indexed"], probe_lib, "stat", "5.42/B.pmc")
    assert got == "ENOENT, 0 stats, 0 opens"


def test_a_read_below_a_file_is_answered_without_the_host(probes, probe_lib, wasmtime_cli):
    got = _probe(wasmtime_cli, probes["indexed"], probe_lib, "read", "5.42/A.pm/x")
    assert got == "ENOTDIR, 0 stats, 0 opens"


def test_a_listed_read_is_asked_of_the_host(probes, probe_lib, wasmtime_cli):
    # The look's stat before the open, then the open.
    got = _probe(wasmtime_cli, probes["indexed"], probe_lib, "read", "5.42/A.pm")
    assert got == "ok, 1 stats, 1 opens"


def test_a_create_reaches_the_host(probes, probe_lib, wasmtime_cli):
    # The index answers no open that asks to write, nor the look's stat before it, which
    # refuses a special file whatever the index holds.
    try:
        got = _probe(wasmtime_cli, probes["indexed"], probe_lib, "create", "5.42/B.pm")
    finally:
        (probe_lib / "5.42" / "B.pm").unlink(missing_ok=True)
    assert got == "ok, 1 stats, 1 opens"


# Each a path the host may answer otherwise than the index would: a name in another case, which
# a host whose names ignore case finds; a name Windows rewrites (a trailing "." or blank, a "~"
# of a short name, a "\\" or a ":"), cannot hold (a "*", "?", "<", ">", "|" or '"', or a byte
# below 0x20) or takes for a device (CON, NUL, COM1 and the rest, with or without blanks after
# the stem); a component longer than the host's 255 bytes; a byte that is not ASCII, which
# wasmtime refuses when it is not UTF-8.
HOST_PATHS = [
    ("stat", "5.42/a.pmc"),
    ("stat", "5.42/A.PM"),
    ("read", "5.42/a.pm"),
    ("stat", "5.42/a.pm/x"),
    ("stat", "5.42/A.pm."),
    ("stat", "5.42/A.pm "),
    ("stat", "5.42/A~1.PM"),
    ("stat", "5.42/B~1/x.pm"),
    ("stat", "5.42/A\\B.pm"),
    ("stat", "5.42/A:B.pm"),
    # A Windows device name, whatever follows its first ".".
    ("stat", "5.42/CON"),
    ("stat", "5.42/con.pm"),
    ("stat", "5.42/LPT1.x"),
    ("stat", "5.42/Nul.txt/x"),
    ("stat", "5.42/conout$"),
    # A device name with blanks after its stem, which Windows trims.
    ("stat", "5.42/CON .pm"),
    ("stat", "5.42/nul  .txt"),
    ("stat", "5.42/LPT9 .x/y"),
    ("stat", "5.42/conin$ .pm"),
    # A byte Windows cannot hold in a name.
    ("stat", "5.42/A*.pm"),
    ("stat", "5.42/A?.pm"),
    ("stat", "5.42/A<B.pm"),
    ("stat", "5.42/A>B.pm"),
    ("stat", "5.42/A|B.pm"),
    ("stat", '5.42/A"B.pm'),
    ("read", "5.42/x*/A.pm"),
    # A control byte, which the probe reads from "%01" and the rest.
    ("stat", "5.42/A%01.pm"),
    ("stat", "5.42/%09.pm"),
    ("stat", "5.42/A%1f/x"),
    ("stat", "5.42/" + "a" * 256),
    ("stat", "a" * 300 + "/x"),
    # The byte 0xe9 alone, which the probe reads from "%e9".
    ("stat", "5.42/%e9.pm"),
    ("read", "5.42/A.pm/%e9"),
]


@pytest.mark.parametrize(("op", "path"), HOST_PATHS, ids=[f"{o} {p[:40]!r}" for o, p in HOST_PATHS])
def test_a_path_the_host_may_answer_otherwise_reaches_the_host(
    probes, probe_lib, wasmtime_cli, op, path
):
    got = _probe(wasmtime_cli, probes["indexed"], probe_lib, op, path)
    # A read is the look's stat, then the open.
    want = "1 stats, 0 opens" if op == "stat" else "1 stats, 1 opens"
    assert got.split(", ", 1)[1] == want, (path, got)


def test_a_name_in_no_case_is_answered_without_the_host(probes, probe_lib, wasmtime_cli):
    # In no case does the library hold these, so every host answers ENOENT or ENOTDIR.
    for op, path, want in (
        ("stat", "5.42/b.PMC", "ENOENT"),
        # Not a device: the stem is longer, or a blank stands inside it, not after it.
        ("stat", "5.42/CONX.pm", "ENOENT"),
        ("stat", "5.42/CO N.pm", "ENOENT"),
        ("stat", "5.42/COM0 .pm", "ENOENT"),
        # A byte at or above 0x20 that Windows takes: a blank inside, "-", "=", "%" and DEL.
        ("stat", "5.42/A B-C=D%25%7f.pm", "ENOENT"),
        ("stat", "NOPE/x", "ENOENT"),
        ("read", "5.42/A.pm/X", "ENOTDIR"),
        ("stat", "5.42/A.pm/x/y", "ENOTDIR"),
    ):
        got = _probe(wasmtime_cli, probes["indexed"], probe_lib, op, path)
        assert got == f"{want}, 0 stats, 0 opens", path


def test_with_the_empty_index_every_stat_reaches_the_host(probes, probe_lib, wasmtime_cli):
    for path, want in (("5.42/A.pmc", "ok"), ("5.42/B.pmc", "ENOENT"), ("nope/x", "ENOENT")):
        got = _probe(wasmtime_cli, probes["empty"], probe_lib, "stat", path)
        assert got == f"{want}, 1 stats, 0 opens", path


# The built set.


def _manifest_library() -> list[str]:
    """The library's files MANIFEST lists, relative to lib/perl5."""
    files = []
    for line in (ARTIFACTS / "MANIFEST").read_text().splitlines():
        path = line.split("  ", 1)[1]
        if path.startswith("lib/perl5/"):
            files.append(path[len("lib/perl5/") :])
    return files


def _expected_index() -> tuple[list[str], set[str]]:
    files = _manifest_library()
    folders = {str(a) for f in files for a in PurePosixPath(f).parents if str(a) != "."}
    names = sorted(set(files) | folders, key=lambda n: n.encode().lower())
    return names, folders


@pytest.fixture(scope="session")
def built_index(perl_wasm) -> str:
    source = BUILD / "index" / "lib-index.c"
    if not source.exists():
        pytest.skip("build/index/lib-index.c is missing; run scripts/build.sh")
    return source.read_text()


def test_the_index_is_the_library_manifest_lists(built_index):
    names, folders = _expected_index()
    got = _names_of(built_index)
    assert got == names
    files = set(_manifest_library())
    # A name is a folder exactly when another name lies below it: the index holds no empty one.
    below = {n for n in got if any(m.startswith(n + "/") for m in got)}
    assert below == folders and set(got) - below == files
    assert _marker_of(built_index) == _sha_of_lines(names)


def test_the_shipped_perl_wasm_holds_that_index(perl_wasm):
    names, _ = _expected_index()
    data = perl_wasm.read_bytes()
    assert data.count(MARKER) == 1
    at = data.index(MARKER) + len(MARKER)
    assert data[at : at + 64].decode() == _sha_of_lines(names)


def _listed(path: Path) -> dict[str, str]:
    return {
        line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in path.read_text().splitlines()
    }


def test_sha256sums_is_written_last_and_pins_the_shipped_perl_wasm(perl_wasm):
    sums = ARTIFACTS / "SHA256SUMS"
    listed = _listed(sums)
    assert listed["perl.wasm"] == hashlib.sha256(perl_wasm.read_bytes()).hexdigest()
    assert listed["MANIFEST"] == hashlib.sha256((ARTIFACTS / "MANIFEST").read_bytes()).hexdigest()
    newest = max((ARTIFACTS / p).stat().st_mtime_ns for p in listed)
    assert sums.stat().st_mtime_ns >= newest


def test_the_relink_runs_only_the_link(perl_wasm):
    log = BUILD / "logs" / "40_link.log"
    if not log.exists():
        pytest.skip("build/logs/40_link.log is missing; run scripts/build.sh")
    # Every command make runs with the target's compiler names recipes/wasi-cc: one link of the
    # reactor, then one of the command, and no compile.
    commands = [ln for ln in log.read_text().splitlines() if "recipes/wasi-cc" in ln]
    assert len(commands) == 2, commands
    for command in commands:
        words = command.split()
        assert "-o" in words and words[words.index("-o") + 1] == "perl", command
        assert "-c" not in words, command
        assert str(BUILD / "index" / "lib-index.o") in words, command
    reactor, command = (c.split() for c in commands)
    assert "-mexec-model=reactor" in reactor and str(BUILD / "stubs" / "wasi-reactor.o") in reactor
    assert "-mexec-model=reactor" not in command and "perlmain.o" in command


def _link_stamp_of(root: Path) -> str:
    """The link step's stamp in the checkout at root: its own inputs and the library's stamp,
    then each line that names the pass's tool and flags: the tool's folder and tarball, the
    tarball's pinned SHA-256, the pass, and the features it names."""
    scripts = root / "scripts"
    files = (scripts / "40_link.sh", scripts / "lib_index.sh", scripts / "lib.sh")
    lib = (scripts / "lib.sh").read_text()
    name = re.search(r"^BINARYEN_NAME=(\S+)$", lib, re.M).group(1)
    tarball = re.search(r"^BINARYEN_TARBALL=(\S+)$", lib, re.M).group(1)
    pins = [
        ln.split()[0]
        for ln in (root / "downloads" / "SHA256SUMS").read_text().splitlines()
        if ln.split()[1:2] == [tarball]
    ]
    assert len(pins) == 1, pins
    link = (scripts / "40_link.sh").read_text()
    flags = [
        re.search(rf"^{v}='([^']*)'$", link, re.M).group(1).split() for v in ("PASS", "FEATURES")
    ]
    lines = [name, tarball, pins[0], *flags[0], *flags[1]]
    data = b"".join(p.read_bytes() for p in (*files, root / "build" / ".library.stamp"))
    return hashlib.sha256(data + "".join(f"{ln}\n" for ln in lines).encode()).hexdigest()


def test_the_link_step_is_stamped_by_its_own_inputs_the_library_and_the_pass(perl_wasm):
    stamp = BUILD / ".link.stamp"
    if not stamp.exists():
        pytest.skip("build/.link.stamp is missing; run scripts/build.sh")
    scripts = ROOT / "scripts"
    assert stamp.read_text().strip() == _link_stamp_of(ROOT)
    # So a change to the library's step relinks, and a change to the index's generator reaches
    # no step before the link: neither earlier step reads it.
    for earlier in ("20_target.sh", "30_library.sh"):
        assert "lib_index.sh" not in (scripts / earlier).read_text(), earlier
    assert '"$BUILD/.target.stamp"' in (scripts / "30_library.sh").read_text()


@pytest.mark.parametrize("module", ["perl.wasm", "perl-reactor.wasm"])
def test_the_link_step_is_not_up_to_date_without_perl_wasm(tmp_path, module):
    """40_link.sh in a scratch checkout whose stamp matches: up to date only while the set holds
    perl.wasm and perl-reactor.wasm. Without either the step runs again, and here stops at the
    reactor's object or the compiler, which the scratch checkout lacks."""
    root = tmp_path / "checkout"
    shutil.copytree(ROOT / "scripts", root / "scripts")
    shutil.copytree(ROOT / "recipes", root / "recipes")
    (root / "downloads").mkdir()
    shutil.copy(ROOT / "downloads" / "SHA256SUMS", root / "downloads" / "SHA256SUMS")
    build = root / "build"
    art = build / "artifacts"
    (art / "lib" / "perl5" / "5.42").mkdir(parents=True)
    (art / "lib" / "perl5" / "5.42" / "A.pm").write_text("1;\n")
    for f in ("MANIFEST", "SHA256SUMS", "perl.wasm", "perl-reactor.wasm"):
        (art / f).write_text("")
    (build / "target").mkdir()
    (build / "target" / "perl").write_text("")
    (build / "target" / "config.sh").write_text("libs='-lm'\n")
    (build / ".library.stamp").write_text("stand-in\n")
    scripts = root / "scripts"
    (build / ".link.stamp").write_text(_link_stamp_of(root) + "\n")

    def step() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["sh", str(scripts / "40_link.sh")], capture_output=True, text=True, check=False
        )

    p = step()
    assert (p.returncode, p.stdout) == (0, "40_link: up to date\n"), p
    (art / module).unlink()
    p = step()
    assert p.returncode == 1 and "up to date" not in p.stdout, p
    assert p.stderr.startswith("FAILED: "), p
