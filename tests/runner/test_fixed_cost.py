"""The runner's fixed cost: a call redoes a piece of host work only when what that work depends on
can have changed. Each check counts what the runner does (metadata reads, parses of SHA256SUMS,
rule inputs computed, linkers made, the way a module is loaded, engine settings), never time;
and each saving is held to the rule it must keep."""

from __future__ import annotations

import importlib.metadata
import os
import sys
from pathlib import Path

import pytest

from wasi_perl import ArtifactsMissing, Grant, _artifacts, _cache, _engines, run, runner

wasmtime = pytest.importorskip("wasmtime")

POSIX = pytest.mark.skipif(os.name == "nt", reason="the POSIX checks of the module cache")
FD_PREFIX = {"linux": "/proc/self/fd/", "darwin": "/dev/fd/"}.get(sys.platform)


def _counted(monkeypatch: pytest.MonkeyPatch, owner: object, name: str, calls: list) -> None:
    """`owner.name` replaced by a wrapper that records each call's arguments in `calls`."""
    real = getattr(owner, name)

    def counted(*args, **kwargs):
        calls.append((name, args))
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, counted)


# Version reads.

_METADATA = ("version", "metadata", "distribution", "distributions", "packages_distributions")


def _count_metadata(monkeypatch: pytest.MonkeyPatch) -> list:
    calls: list = []
    for name in _METADATA:
        _counted(monkeypatch, importlib.metadata, name, calls)
    real_from_name = importlib.metadata.Distribution.from_name

    def from_name(cls, name):
        calls.append(("Distribution.from_name", (name,)))
        return real_from_name(name)

    monkeypatch.setattr(importlib.metadata.Distribution, "from_name", classmethod(from_name))
    return calls


def _site(tmp_path: Path, versions: list[str]) -> Path:
    """A folder laid out as site-packages: a wasmtime package and a dist-info per version."""
    site = tmp_path / "site-packages"
    (site / "wasmtime").mkdir(parents=True)
    (site / "wasmtime" / "__init__.py").write_text("")
    for v in versions:
        info = site / f"wasmtime-{v}.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: wasmtime\nVersion: {v}\n\nVersion: 0.0.0 in the body\n"
        )
    return site


def test_a_call_after_the_first_reads_no_package_metadata(standin, monkeypatch):
    assert run(["hello"], artifacts=standin).stdout == b"hello\n"
    calls = _count_metadata(monkeypatch)
    assert run(["hello"], artifacts=standin).stdout == b"hello\n"
    assert calls == []


def test_the_version_comes_from_the_one_dist_info_beside_wasmtime(monkeypatch):
    monkeypatch.setattr(_engines, "_version", [])  # this process has read no version yet
    calls = _count_metadata(monkeypatch)
    found = _engines.wasmtime_version()
    assert calls == []
    assert _engines._wasmtime_site() == Path(wasmtime.__file__).resolve().parent.parent
    assert found == importlib.metadata.version("wasmtime")


def test_a_dist_info_folder_gives_its_metadata_version(monkeypatch, tmp_path):
    site = _site(tmp_path, ["51.0.9"])
    monkeypatch.setattr(_engines, "_version", [])
    monkeypatch.setattr(_engines, "_wasmtime_site", lambda: site)
    calls = _count_metadata(monkeypatch)
    assert _engines.wasmtime_version() == "51.0.9"
    assert calls == []


@pytest.mark.parametrize("versions", [[], ["51.0.9", "50.0.0"]], ids=["none", "two"])
def test_not_exactly_one_dist_info_leaves_the_version_to_importlib_metadata(
    monkeypatch, tmp_path, versions
):
    site = _site(tmp_path, versions)
    monkeypatch.setattr(_engines, "_version", [])
    monkeypatch.setattr(_engines, "_wasmtime_site", lambda: site)
    asked = []

    def version(name):
        asked.append(name)
        return "51.0.7"

    monkeypatch.setattr(importlib.metadata, "version", version)
    assert _engines.wasmtime_version() == "51.0.7"
    assert asked == ["wasmtime"]


def test_a_dist_info_of_another_name_leaves_the_version_to_importlib_metadata(
    monkeypatch, tmp_path
):
    site = _site(tmp_path, ["51.0.9"])
    meta = site / "wasmtime-51.0.9.dist-info" / "METADATA"
    meta.write_text(meta.read_text().replace("Name: wasmtime", "Name: not-wasmtime"))
    monkeypatch.setattr(_engines, "_version", [])
    monkeypatch.setattr(_engines, "_wasmtime_site", lambda: site)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "51.0.7")
    assert _engines.wasmtime_version() == "51.0.7"


# SHA256SUMS reads.


@pytest.fixture
def found(standin_dir, monkeypatch):
    monkeypatch.setenv("WASI_PERL_ARTIFACTS", str(standin_dir))
    _artifacts.forget()
    return standin_dir


def _count_reads(monkeypatch: pytest.MonkeyPatch, path: Path, then=None) -> list:
    """Each read of `path` by `Path.read_text`; `then` runs after a read, before it returns."""
    reads: list = []
    real = Path.read_text

    def read_text(self, *args, **kwargs):
        text = real(self, *args, **kwargs)
        if self == path:
            reads.append(self)
            if then is not None:
                then(len(reads))
        return text

    monkeypatch.setattr(Path, "read_text", read_text)
    return reads


def _wrong_sums(found: Path) -> str:
    """SHA256SUMS with perl.wasm listed under another digest of the same length."""
    lines = []
    for line in (found / "SHA256SUMS").read_text().splitlines():
        if line.endswith(" perl.wasm"):
            line = "0" * 64 + line[64:]
        lines.append(line)
    return "\n".join(lines) + "\n"


def test_ten_calls_parse_an_unchanged_sha256sums_once(found, monkeypatch):
    reads = _count_reads(monkeypatch, found / "SHA256SUMS")
    for _ in range(10):
        assert run(["hello"]).stdout == b"hello\n"
    assert len(reads) == 1


@pytest.mark.parametrize("how", ["replaced", "in place"])
def test_a_rewritten_sha256sums_is_parsed_again_and_held_to(found, monkeypatch, how):
    assert run(["hello"]).stdout == b"hello\n"
    sums = found / "SHA256SUMS"
    wrong = _wrong_sums(found)
    reads = _count_reads(monkeypatch, sums)
    assert run(["hello"]).stdout == b"hello\n"
    assert reads == []  # kept from the first call
    if how == "replaced":
        fresh = found / "SHA256SUMS.new"
        fresh.write_text(wrong)
        os.replace(fresh, sums)
    else:  # the same inode and size; its mtime moved, as a write moves it
        st = sums.stat()
        with open(sums, "r+") as f:
            f.write(wrong)
        os.utime(sums, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    with pytest.raises(ArtifactsMissing, match="perl.wasm does not match"):
        run(["hello"])
    assert len(reads) == 1


def test_a_sha256sums_changed_while_it_is_read_is_not_kept(found, monkeypatch):
    sums = found / "SHA256SUMS"
    wrong = _wrong_sums(found)

    def replace_once(n: int) -> None:  # after the first read returns, another file takes its name
        if n == 1:
            fresh = found / "SHA256SUMS.new"
            fresh.write_text(wrong)
            os.replace(fresh, sums)

    reads = _count_reads(monkeypatch, sums, then=replace_once)
    assert run(["hello"]).stdout == b"hello\n"  # held to what it read
    with pytest.raises(ArtifactsMissing, match="perl.wasm does not match"):
        run(["hello"])  # what it read is not kept under the new file's stamp
    assert len(reads) == 2


# Rule inputs.

_INPUTS = ((runner, "_runtime_folders"), (runner, "_runtime_files"), (_cache, "every"))


@pytest.mark.parametrize(
    ("kind", "want"),
    [
        ("none", set()),
        ("read-only", {"every"}),
        ("writable", {"_runtime_folders", "_runtime_files", "every"}),
    ],
)
def test_a_rules_inputs_are_computed_only_when_a_grant_can_break_it(
    standin, tmp_path, monkeypatch, kind, want
):
    calls: list = []
    for owner, name in _INPUTS:
        _counted(monkeypatch, owner, name, calls)
    grants = {
        "none": [],
        "read-only": [Grant(tmp_path, "/d")],
        "writable": [Grant(tmp_path, "/d", writable=True)],
    }[kind]
    assert run(["hello"], grants=grants, artifacts=standin).stdout == b"hello\n"
    assert sorted(name for name, _ in calls) == sorted(want)


# Linking.


def _count_linkers(monkeypatch: pytest.MonkeyPatch) -> list:
    made: list = []
    real = wasmtime.Linker

    class Counted(real):
        def __init__(self, *args, **kwargs):
            made.append(args)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(wasmtime, "Linker", Counted)
    return made


def test_ten_calls_on_one_module_make_one_linker(standin, monkeypatch):
    made = _count_linkers(monkeypatch)
    _engines.forget()
    for _ in range(10):
        assert run(["hello"], artifacts=standin).stdout == b"hello\n"
    assert len(made) == 1


def test_calls_from_one_linking_see_only_their_own_grants_and_environment(
    standin, tmp_path, monkeypatch
):
    made = _count_linkers(monkeypatch)
    _engines.forget()
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        d.mkdir()
        (d / "f").write_text(f"from {d.name}\n")
    for _ in range(2):
        done = run(["cat", "/data/f"], grants=[Grant(a, "/data")], artifacts=standin)
        assert done.stdout == b"from a\n"
        done = run(["cat", "/data/f"], grants=[Grant(b, "/other")], artifacts=standin)
        assert (done.exit_code, done.stdout) == (1, b"open /data/f: No such file or directory\n")
        done = run(["cat", "/other/f"], grants=[Grant(b, "/other")], artifacts=standin)
        assert done.stdout == b"from b\n"
        assert run(["env"], env={"WHO": "a"}, artifacts=standin).stdout == b"WHO=a\n"
        assert run(["env"], env={"WHO": "b"}, artifacts=standin).stdout == b"WHO=b\n"
        assert run(["env"], artifacts=standin).stdout == b""
        w = [Grant(a, "/data", writable=True)]
        assert run(["write", "/data/out"], grants=w, artifacts=standin).stdout == b"ok\n"
        done = run(["write", "/data/out"], grants=[Grant(a, "/data")], artifacts=standin)
        assert done.stdout == b"open /data/out: Operation not permitted\n"
    assert len(made) == 1


# The mapping.


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    """A module cache that holds the stand-in's .cwasm, written by a first call."""
    _engines.forget()
    return tmp_path / "cache"


def _fresh_call(standin, cache):
    _engines.forget()
    before = dict(_engines.counts())
    done = run(["hello"], cache_dir=cache, artifacts=standin)
    assert done.stdout == b"hello\n"
    return {k: _engines.counts()[k] - before[k] for k in before}


def _one(cache: Path) -> Path:
    (cwasm,) = [p for p in cache.iterdir() if p.suffix == ".cwasm"]
    return cwasm


def _count_loads(monkeypatch: pytest.MonkeyPatch) -> tuple[list, list]:
    by_name: list = []
    by_bytes: list = []
    _counted(monkeypatch, wasmtime.Module, "deserialize_file", by_name)
    _counted(monkeypatch, wasmtime.Module, "deserialize", by_bytes)
    return by_name, by_bytes


@pytest.mark.skipif(sys.platform != "linux", reason="Linux's /proc/self/fd")
def test_on_linux_a_cwasm_is_mapped_by_its_proc_self_fd_name(standin, cache, monkeypatch):
    assert _fresh_call(standin, cache)["written"] == 1
    by_name, by_bytes = _count_loads(monkeypatch)
    assert _fresh_call(standin, cache) == {"compiled": 0, "loaded": 1, "written": 0}
    assert by_bytes == []
    ((_, (_, name)),) = by_name
    assert name.startswith("/proc/self/fd/")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS's /dev/fd, run in CI")
def test_on_macos_a_cwasm_is_mapped_by_its_dev_fd_name(perl, cache, monkeypatch):
    # The real set, which CI's macOS job has inside the installed wheel; it builds no stand-in.
    def call() -> dict[str, int]:
        _engines.forget()
        before = dict(_engines.counts())
        done = run(["-e", "print 1"], cache_dir=cache, artifacts=perl)
        assert (done.exit_code, done.stdout) == (0, b"1")
        return {k: _engines.counts()[k] - before[k] for k in before}

    assert call()["written"] == 1
    by_name, by_bytes = _count_loads(monkeypatch)
    assert call() == {"compiled": 0, "loaded": 1, "written": 0}
    assert by_bytes == []
    ((_, (_, name)),) = by_name
    assert name.startswith("/dev/fd/")


@POSIX
def test_without_proc_the_bytes_are_read_through_the_descriptor(
    standin, cache, monkeypatch, tmp_path
):
    assert _fresh_call(standin, cache)["written"] == 1
    monkeypatch.setattr(_cache, "_FD_FOLDER", str(tmp_path / "no-proc" / "self" / "fd"))
    by_name, by_bytes = _count_loads(monkeypatch)
    assert _fresh_call(standin, cache) == {"compiled": 0, "loaded": 1, "written": 0}
    assert by_name == []
    assert len(by_bytes) == 1


def _decoy(tmp_path: Path) -> Path:
    """Another module's .cwasm, as the runner makes one: a `_start` that prints nothing."""
    mod = wasmtime.Module(_engines.engine(False), '(module (func (export "_start")))')
    decoy = tmp_path / "decoy.cwasm"
    decoy.write_bytes(mod.serialize())
    decoy.chmod(0o600)
    return decoy


@POSIX
def test_a_name_that_opens_another_file_is_not_given_to_wasmtime(
    standin, cache, monkeypatch, tmp_path
):
    assert _fresh_call(standin, cache)["written"] == 1
    decoy = _decoy(tmp_path)
    names = tmp_path / "fd"
    names.mkdir()
    for n in range(1024):  # whichever descriptor the .cwasm gets, its name here is the decoy
        (names / str(n)).symlink_to(decoy)
    monkeypatch.setattr(_cache, "_FD_FOLDER", str(names))
    by_name, by_bytes = _count_loads(monkeypatch)
    assert _fresh_call(standin, cache) == {"compiled": 0, "loaded": 1, "written": 0}
    assert by_name == []
    assert len(by_bytes) == 1


@POSIX
@pytest.mark.parametrize("way", ["mapped", "read"])
def test_a_cwasm_swapped_after_its_check_still_loads_the_checked_file(
    standin, cache, monkeypatch, tmp_path, way
):
    if way == "mapped" and FD_PREFIX is None:
        pytest.skip("a system with no name for a descriptor")
    assert _fresh_call(standin, cache)["written"] == 1
    cwasm = _one(cache)
    decoy = _decoy(tmp_path)
    swapped = decoy.read_bytes()
    if way == "read":
        monkeypatch.setattr(_cache, "_FD_FOLDER", str(tmp_path / "no-proc" / "self" / "fd"))
    real = _cache._fd_name

    def swap_then_name(fd: int) -> str | None:  # between the check and the load
        os.replace(decoy, cwasm)
        return real(fd)

    monkeypatch.setattr(_cache, "_fd_name", swap_then_name)
    by_name, by_bytes = _count_loads(monkeypatch)
    assert _fresh_call(standin, cache) == {"compiled": 0, "loaded": 1, "written": 0}
    assert cwasm.read_bytes() == swapped  # the swap happened, and was not what ran
    assert len(by_name if way == "mapped" else by_bytes) == 1


# The engine.


def test_the_engine_states_the_settings_the_speed_depends_on(monkeypatch):
    made: list[dict] = []

    class Config:
        def __init__(self) -> None:
            object.__setattr__(self, "settings", {})
            made.append(self.settings)

        def __setattr__(self, name, value) -> None:
            self.settings[name] = value

    monkeypatch.setattr(wasmtime, "Config", Config)
    monkeypatch.setattr(wasmtime, "Engine", lambda cfg: ("engine", cfg))
    monkeypatch.setattr(_engines, "_engines", {})
    _engines.engine(False)
    _engines.engine(True)
    _engines.engine(False)  # kept: one engine per flavour
    common = {
        "wasm_exceptions": True,
        "epoch_interruption": True,
        "cranelift_opt_level": "speed",
        "parallel_compilation": True,
        "memory_init_cow": True,
        "signals_based_traps": True,
    }
    assert made == [{**common, "consume_fuel": False}, {**common, "consume_fuel": True}]
