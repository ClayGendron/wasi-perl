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
    return re.findall(r'^\s*"([^"]*)",$', source, re.M)


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


def test_lib_index_lists_every_file_and_folder_in_byte_order(probe_lib, tmp_path):
    (probe_lib / "5.42" / "A-B").mkdir(exist_ok=True)
    (probe_lib / "5.42" / "A-B" / "c.pm").write_text("1;\n")
    try:
        out = tmp_path / "index.c"
        subprocess.run(["sh", str(LIB_INDEX), str(probe_lib), str(out)], check=True)
        source = out.read_text()
    finally:
        (probe_lib / "5.42" / "A-B" / "c.pm").unlink()
        (probe_lib / "5.42" / "A-B").rmdir()
    names = _names_of(source)
    # Byte order, strcmp's: "-" (0x2d) and "." (0x2e) sort before "/" (0x2f).
    assert names == ["5.42", "5.42/A-B", "5.42/A-B/c.pm", "5.42/A.pm", "5.42/A.pmc"], source
    assert re.search(rf"^\s*{len(names)},$", source, re.M), source
    assert _marker_of(source) == _sha_of_lines(names)
    assert str(probe_lib) not in source  # the bytes do not depend on where the build ran


def test_lib_index_refuses_an_empty_folder_and_a_link(tmp_path):
    lib = tmp_path / "lib"
    (lib / "5.42" / "Empty").mkdir(parents=True)
    (lib / "5.42" / "A.pm").write_text("1;\n")
    p = subprocess.run(
        ["sh", str(LIB_INDEX), str(lib), str(tmp_path / "a.c")], capture_output=True, check=False
    )
    assert p.returncode != 0 and b"empty folder" in p.stderr, p
    (lib / "5.42" / "Empty").rmdir()
    (lib / "5.42" / "L.pm").symlink_to("A.pm")
    p = subprocess.run(
        ["sh", str(LIB_INDEX), str(lib), str(tmp_path / "b.c")], capture_output=True, check=False
    )
    assert p.returncode != 0 and b"neither a file nor a folder" in p.stderr, p


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
    # The look finds no B.pm in the index, so only the open itself reaches the host.
    try:
        got = _probe(wasmtime_cli, probes["indexed"], probe_lib, "create", "5.42/B.pm")
    finally:
        (probe_lib / "5.42" / "B.pm").unlink(missing_ok=True)
    assert got == "ok, 0 stats, 1 opens"


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
    names = sorted(set(files) | folders, key=lambda n: n.encode())
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
    # Every command make runs with the target's compiler names recipes/wasi-cc.
    commands = [ln for ln in log.read_text().splitlines() if "recipes/wasi-cc" in ln]
    assert len(commands) == 1, commands
    words = commands[0].split()
    assert "-o" in words and words[words.index("-o") + 1] == "perl", commands[0]
    assert "-c" not in words, commands[0]
    assert str(BUILD / "index" / "lib-index.o") in words, commands[0]


def _stamp_of(*paths: Path) -> str:
    return hashlib.sha256(b"".join(p.read_bytes() for p in paths)).hexdigest()


def test_the_link_step_is_stamped_by_its_own_inputs_and_the_library(perl_wasm):
    stamp = BUILD / ".link.stamp"
    if not stamp.exists():
        pytest.skip("build/.link.stamp is missing; run scripts/build.sh")
    scripts = ROOT / "scripts"
    want = _stamp_of(
        scripts / "40_link.sh",
        scripts / "lib_index.sh",
        scripts / "lib.sh",
        BUILD / ".library.stamp",
    )
    assert stamp.read_text().strip() == want
    # So a change to the library's step relinks, and a change to the index's generator reaches
    # no step before the link: neither earlier step reads it.
    for earlier in ("20_target.sh", "30_library.sh"):
        assert "lib_index.sh" not in (scripts / earlier).read_text(), earlier
    assert '"$BUILD/.target.stamp"' in (scripts / "30_library.sh").read_text()
