"""Checks on the pass of binaryen's wasm-opt that scripts/40_link.sh runs over the linked module.

build/link/perl.wasm is what wasm-ld wrote, and the shipped perl.wasm is the pass's output. The
pass leaves no mark of its own in the module, but it is deterministic, so the pinned wasm-opt run
again with the script's flags over the first must give the second byte for byte. The rest holds
the output to the input: the same features, imports and exports, fewer instructions, and the
same answers where a die unwinds through eval.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from _build_helpers import BUILD, ROOT, Guest, default_grants

SCRIPT = ROOT / "scripts" / "40_link.sh"
LIB_SH = ROOT / "scripts" / "lib.sh"
LINKED = BUILD / "link" / "perl.wasm"

# What each feature of the target_features section is called by wasm-opt's flags.
FLAG_OF = {
    "mutable-globals": "--enable-mutable-globals",
    "nontrapping-fptoint": "--enable-nontrapping-float-to-int",
    "bulk-memory": "--enable-bulk-memory",
    "bulk-memory-opt": "--enable-bulk-memory-opt",
    "sign-ext": "--enable-sign-ext",
    "exception-handling": "--enable-exception-handling",
    "reference-types": "--enable-reference-types",
    "multivalue": "--enable-multivalue",
    "extended-const": "--enable-extended-const",
    "call-indirect-overlong": "--enable-call-indirect-overlong",
    "simd128": "--enable-simd",
}


def _assigned(text: str, name: str) -> str:
    m = re.search(rf"^{name}='([^']*)'$", text, re.M)
    assert m, f"no {name}='...' in the script"
    return m.group(1)


def script_pass() -> list[str]:
    """The pass's own flags and the features it names, as 40_link.sh writes them."""
    text = SCRIPT.read_text()
    return _assigned(text, "PASS").split() + script_features()


def script_features() -> list[str]:
    return _assigned(SCRIPT.read_text(), "FEATURES").split()


def binaryen_name() -> str:
    m = re.search(r"^BINARYEN_NAME=(\S+)$", LIB_SH.read_text(), re.M)
    assert m, "lib.sh names no BINARYEN_NAME"
    return m.group(1)


@pytest.fixture(scope="session")
def wasm_opt() -> Path:
    tool = BUILD / "tools" / binaryen_name() / "bin" / "wasm-opt"
    if not tool.exists():
        pytest.skip(f"{tool.relative_to(ROOT)} is missing; run scripts/00_download.sh")
    return tool


@pytest.fixture(scope="session")
def linked(perl_wasm: Path) -> Path:
    if not LINKED.exists():
        pytest.skip("build/link/perl.wasm is missing; run scripts/build.sh")
    return LINKED


def _leb(b: bytes, i: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        byte = b[i]
        i += 1
        n |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return n, i


def target_features(wasm: bytes) -> list[str]:
    """The names the target_features custom section lists as used ("+")."""
    i, found = 8, None
    while i < len(wasm):
        section, i = wasm[i], i + 1
        size, i = _leb(wasm, i)
        end = i + size
        if section == 0:
            n, j = _leb(wasm, i)
            if wasm[j : j + n] == b"target_features":
                assert found is None, "two target_features sections"
                j += n
                count, j = _leb(wasm, j)
                found = []
                for _ in range(count):
                    prefix, j = wasm[j], j + 1
                    n, j = _leb(wasm, j)
                    if prefix == ord("+"):
                        found.append(wasm[j : j + n].decode())
                    j += n
        i = end
    assert found is not None, "no target_features section"
    return found


def print_features(wasm_opt: Path, module: Path) -> list[str]:
    p = subprocess.run(
        [str(wasm_opt), "--print-features", str(module)], capture_output=True, text=True
    )
    assert p.returncode == 0, p
    return p.stdout.split()


# 1. The shipped module is the pass's output.


def test_the_shipped_module_is_the_pass_run_over_the_linked_one(
    perl_wasm: Path, linked: Path, wasm_opt: Path, tmp_path: Path
):
    again = tmp_path / "perl.wasm"
    subprocess.run(
        [str(wasm_opt), *script_pass(), str(linked), "-o", str(again)],
        check=True,
        capture_output=True,
    )
    shipped = perl_wasm.read_bytes()
    assert linked.read_bytes() != shipped, "the shipped module is the linked one: no pass ran"
    assert again.read_bytes() == shipped


def test_the_pass_is_O3_once():
    # -O4's flatten pass does not take try_table, and --converge adds nothing.
    assert _assigned(SCRIPT.read_text(), "PASS").split() == ["-O3"]


# 3. The features.


def test_the_script_names_the_features_the_module_uses(
    perl_wasm: Path, linked: Path, wasm_opt: Path
):
    want = sorted(script_features())
    assert len(set(want)) == len(want), want
    assert sorted(print_features(wasm_opt, linked)) == want
    assert sorted(print_features(wasm_opt, perl_wasm)) == want
    for module in (linked, perl_wasm):
        used = target_features(module.read_bytes())
        assert sorted(FLAG_OF[f] for f in used) == want, (module, used)


def _guard(wasm_opt: Path, module: Path, flags: list[str]) -> subprocess.CompletedProcess:
    """lib.sh's same_features, the guard 40_link.sh runs before the pass."""
    return subprocess.run(
        ["sh", "-c", '. "$0"; same_features "$@"', str(LIB_SH), str(wasm_opt), str(module), *flags],
        capture_output=True,
        text=True,
    )


def test_the_guard_passes_the_scripts_list(linked: Path, wasm_opt: Path):
    p = _guard(wasm_opt, linked, script_features())
    assert (p.returncode, p.stdout, p.stderr) == (0, "", ""), p


@pytest.mark.parametrize(
    ("change", "named"),
    [
        ("drop --enable-simd", "--enable-simd"),
        ("add --enable-tail-call", "--enable-tail-call"),
    ],
)
def test_the_guard_stops_on_a_list_that_differs(linked: Path, wasm_opt: Path, change, named):
    verb, flag = change.split()
    flags = script_features()
    flags = [f for f in flags if f != flag] if verb == "drop" else [*flags, flag]
    p = _guard(wasm_opt, linked, flags)
    assert p.returncode == 1 and p.stdout == "", p
    lines = p.stderr.splitlines()
    assert len(lines) == 1 and lines[0].startswith("FAILED: ") and named in lines[0], p


def test_the_link_step_runs_the_guard_before_the_pass():
    text = SCRIPT.read_text()
    guard = text.index('same_features "$WASM_OPT"')
    run = text.index('"$WASM_OPT" $PASS $FEATURES')
    sums = text.index("SHA256SUMS.tmp")
    assert guard < run < sums


# The tool itself: binaryen's folder as unpacked holds only regular files and folders, none
# setuid or setgid, so a later pin cannot bring a link or a privileged file in unseen.


def test_binaryens_folder_holds_only_files_and_folders(wasm_opt: Path):
    import stat

    top = wasm_opt.parent.parent
    odd = []
    for path in [top, *top.rglob("*")]:
        st = path.lstat()
        if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
            odd.append((str(path.relative_to(top)), "not a file or a folder"))
        elif st.st_mode & (stat.S_ISUID | stat.S_ISGID):
            odd.append((str(path.relative_to(top)), "setuid or setgid"))
    assert odd == []
    assert (top / "bin" / "wasm-opt").is_file()


# 4. The imports and the exports.


def _describe(t) -> tuple:
    import wasmtime as wt

    if isinstance(t, wt.FuncType):
        return ("func", tuple(map(str, t.params)), tuple(map(str, t.results)))
    if isinstance(t, wt.MemoryType):
        return ("memory", t.limits.min, t.limits.max, t.is_64, t.is_shared)
    return (type(t).__name__,)


def test_the_imports_and_exports_are_unchanged(guest_engine, linked: Path):
    import wasmtime as wt

    before = wt.Module.from_file(guest_engine.engine, str(linked))
    after = guest_engine.module
    assert [(i.module, i.name, _describe(i.type)) for i in after.imports] == [
        (i.module, i.name, _describe(i.type)) for i in before.imports
    ]
    assert [(e.name, _describe(e.type)) for e in after.exports] == [
        (e.name, _describe(e.type)) for e in before.exports
    ]


# 2. Fuel: fewer instructions on the baseline workloads.

# The baseline workloads of the study that measured the pass, on inputs made here.
WORKLOADS = {
    "hello": ["-e", 'print "hello\\n"'],
    "sum_csv": ["-F,", "-lane", "$s += $F[2]; END { print $s }", "/data/t.csv"],
    "grep_log": ["-ne", "$n++ if /ERROR \\d+/; END { print $n }", "/data/t.log"],
    "loop": ["-e", 'my $s = 0; $s += $_ * $_ for 1 .. 1_000_000; print "$s\\n"'],
}
# The study saw 2.6 to 2.9% fewer on these four; a seeded hash makes each count exact.
LEAST_SAVING = 0.015


@pytest.fixture(scope="session")
def workload_data(tmp_path_factory) -> Path:
    data = tmp_path_factory.mktemp("workload-data")
    (data / "t.csv").write_text(
        "".join(f"row{i},{i % 97},{(i * 7919) % 10007}\n" for i in range(50_000))
    )
    levels = ("INFO", "WARN", "ERROR", "DEBUG")
    (data / "t.log").write_text(
        "".join(
            f"2026-11-24 12:00:{i % 60:02d} {levels[i % 4]} {i} request served\n"
            for i in range(50_000)
        )
    )
    return data


def _fuel(module: Path, args: list[str], data: Path, tmp: Path) -> tuple[int, bytes]:
    import wasmtime as wt

    config = wt.Config()
    config.wasm_exceptions = True
    config.consume_fuel = True
    engine = wt.Engine(config)
    tmp.mkdir()
    wasi = wt.WasiConfig()
    wasi.argv = ["perl", *args]
    wasi.env = [("PERL_HASH_SEED", "0")]
    wasi.stdout_file = str(tmp / "out")
    wasi.stderr_file = str(tmp / "err")
    for host, guest, writable in [*default_grants(), (data, "/data", False)]:
        wasi.preopen_dir(str(host), guest, writable)
    store = wt.Store(engine)
    store.set_wasi(wasi)
    budget = 10**12
    store.set_fuel(budget)
    linker = wt.Linker(engine)
    linker.define_wasi()
    try:
        instance = linker.instantiate(store, wt.Module.from_file(engine, str(module)))
        instance.exports(store)["_start"](store)
    except wt.ExitTrap as e:
        assert e.code == 0, e
    assert (tmp / "err").read_bytes() == b""
    return budget - store.get_fuel(), (tmp / "out").read_bytes()


@pytest.mark.parametrize("name", WORKLOADS)
def test_the_pass_saves_fuel(perl_wasm: Path, linked: Path, workload_data: Path, tmp_path, name):
    pytest.importorskip("wasmtime")
    before, out_before = _fuel(linked, WORKLOADS[name], workload_data, tmp_path / "linked")
    after, out_after = _fuel(perl_wasm, WORKLOADS[name], workload_data, tmp_path / "shipped")
    assert out_after == out_before and out_after.strip()
    assert after <= before * (1 - LEAST_SAVING), (
        f"{name}: {after:,} instructions against {before:,}, {after / before - 1:+.2%}"
    )


# 5. Every die and eval case gives the same answers.


def _die_and_eval_cases() -> dict[str, str]:
    from test_modules import SAME_AS_NATIVE
    from test_perl_wasm import DIE_IN_EVAL, NESTED

    cases = {"die-in-eval": DIE_IN_EVAL, "nested-object-and-sort": NESTED}
    for name in ("storable-corrupt", "posix-strtol-junk", "die-through-first"):
        cases[name] = SAME_AS_NATIVE[name]
    cases["uncaught"] = 'die "bye\\n"'
    cases["uncaught-with-end"] = 'END { my $s = $?; $? = 0; print "status=$s\\n" } die "bye\\n"'
    return cases


DIE_AND_EVAL = _die_and_eval_cases()


@pytest.fixture(scope="session")
def linked_guest(linked: Path) -> Guest:
    return Guest(linked)


@pytest.mark.parametrize("name", DIE_AND_EVAL)
def test_die_and_eval_answer_as_before_the_pass(guest_engine, linked_guest, tmp_path, name):
    runs = []
    for label, g in (("linked", linked_guest), ("shipped", guest_engine)):
        d = tmp_path / label
        d.mkdir()
        r = g.run("-e", DIE_AND_EVAL[name], tmp=d)
        # The error's backtrace names the functions only where the name section is kept.
        runs.append((r.code, r.stdout, r.stderr, r.status, r.error.rsplit("Caused by:", 1)[-1]))
    assert runs[1] == runs[0]
    assert runs[0][1] or runs[0][2]
