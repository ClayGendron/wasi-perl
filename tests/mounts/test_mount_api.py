"""The mount API and the checks of its arguments: wasi-perl's public names are wasi-python's,
line for line in its own listing's format, and every bad mount is refused before any work.

The ports keep wasi-python's test names at f9bf678 (`tests/unit/test_runner_mounts.py`,
`tests/unit/test_wasmtime_ffi.py`), so the two suites can be diffed; each says which it ports.
The checks that run a guest use the perl.wasm of the set `$WASI_PERL_ARTIFACTS` names, else the
build's; nothing here is timed."""

from __future__ import annotations

import errno
import importlib.metadata
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import wasmtime as wt
from wasmtime import _bindings, _ffi

import wasi_perl
from mounts import api
from mounts.raw import MemoryProvider
from wasi_perl import (
    Defaults,
    Errno,
    Grant,
    Mount,
    Stat,
    _artifacts,
    _cache,
    _engines,
    _fs_table,
    _grants,
    _limits,
    run,
    runner,
)
from wasi_perl import _wasmtime_ffi as ffi
from wasi_perl import mounts as mounts_module

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "wasi_python_public_api.txt"
MOUNT_NAMES = (
    "Errno",
    "FileFault",
    "FileProvider",
    "FileRecord",
    "Mount",
    "RefusedWrite",
    "Stat",
    "WritableFileProvider",
)


def _theirs() -> list[str]:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# ") and "f9bf678" in lines[0]
    return lines[1:]


def _subsequence(ours: list[str], theirs: list[str]) -> bool:
    found = iter(theirs)
    return all(line in found for line in ours)


# A1


def test_the_mount_api_is_wasi_pythons() -> None:
    theirs = api.blocks(_theirs())
    ours = api.blocks(api.listing(wasi_perl))
    for name in MOUNT_NAMES:
        assert ours[name] == theirs[name], name
    # run's two parameters, each line as wasi-python's, and placed where the decision puts them
    run_lines = ours["run"]
    names = [line.split()[1].rstrip(":") for line in run_lines[1:]]
    for param in ("mounts", "max_file_calls"):
        mine = [line for line in run_lines if line.startswith(f"  param {param}:")]
        assert mine and mine == [line for line in theirs["run"] if line.startswith(mine[0][:20])]
        assert mine[0] in theirs["run"], param
    assert names.index("mounts") == names.index("grants") + 1
    assert names.index("max_file_calls") == names.index("artifacts") + 1
    # Completed's record, right after `truncated`, as in wasi-python
    files = "  field files: FileRecord | None = None"
    assert files in theirs["Completed"]
    completed = ours["Completed"]
    assert completed[-1] == files and completed[-2].startswith("  field truncated:")
    assert theirs["Completed"].index(files) == theirs["Completed"].index(completed[-2]) + 1
    # Defaults: wasi-python's block with whole lines absent and none changed
    assert ours["Defaults"][0] == theirs["Defaults"][0]
    assert _subsequence(ours["Defaults"], theirs["Defaults"])
    assert "  field file_calls: int | None = 10000" in ours["Defaults"]
    defaults = [line for line in api.listing(wasi_perl, wasi_perl.defaults()) if "default " in line]
    assert _subsequence(defaults, [line for line in _theirs() if line.startswith("default ")])


# A2: a port of wasi-python's test of the same name, with wasi-perl's reserved folders.


class Async:
    async def stat(self, path):
        return Stat("dir")

    def list(self, path):
        return []

    def read(self, path):
        return b""


class NoRead:
    def stat(self, path):
        return Stat("dir")

    def list(self, path):
        return []

    read = None


class NoStat:
    def list(self, path):
        return []

    def read(self, path):
        return b""


@pytest.fixture
def no_work(monkeypatch: pytest.MonkeyPatch):
    """Fails the check if a refused call does any work: finds the artifact set, which reads its
    files, lists the module caches, or reaches the module. So an argument check that slips
    behind the set fails on every machine, with a set built or without one."""
    calls: list = []
    for owner, name in ((_artifacts, "checked"), (_cache, "every"), (_engines, "module")):
        monkeypatch.setattr(owner, name, lambda *a, n=name, **k: calls.append(n) or 1 / 0)
    yield
    assert calls == []


BAD = [
    ({"mounts": "/work"}, TypeError, "iterable of Mount"),
    ({"mounts": Mount(MemoryProvider(), "/w")}, TypeError, "iterable of Mount"),
    ({"mounts": [("/w", MemoryProvider())]}, TypeError, r"mounts\[0\] must be a Mount"),
    ({"mounts": [Mount(NoRead(), "/w")]}, TypeError, r"mounts\[0\] at /w: .*read"),
    ({"mounts": [Mount(object(), "/w")]}, TypeError, r"no callable stat"),
    ({"mounts": [Mount(Async(), "/w")]}, TypeError, r"stat\(\) is a coroutine function"),
    ({"mounts": [Mount(MemoryProvider(), b"/w")]}, TypeError, "must be a str"),
    ({"mounts": [Mount(MemoryProvider(), "w")]}, ValueError, "must be absolute"),
    ({"mounts": [Mount(MemoryProvider(), "/w/")]}, ValueError, "normalized"),
    ({"mounts": [Mount(MemoryProvider(), "/a/../b")]}, ValueError, "normalized"),
    ({"mounts": [Mount(MemoryProvider(), "/a\\b")]}, ValueError, "normalized"),
    ({"mounts": [Mount(MemoryProvider(), "/a\0")]}, ValueError, "null byte"),
    ({"mounts": [Mount(MemoryProvider(), "/\ud800")]}, ValueError, "UTF-8"),
    ({"mounts": [Mount(MemoryProvider(), "/usr/local/lib/perl5")]}, ValueError, "runtime's own"),
    ({"mounts": [Mount(MemoryProvider(), "/usr/local/lib/perl5/x")]}, ValueError, "runtime's"),
    ({"mounts": [Mount(MemoryProvider(), "/dev")]}, ValueError, "runtime's own"),
    ({"mounts": [Mount(MemoryProvider(), "/dev/x")]}, ValueError, "runtime's own"),
    ({"mounts": [Mount(MemoryProvider(), "/w", writable="yes")]}, TypeError, "writable"),
    ({"mounts": [Mount(NoRead(), "/w", writable=True)]}, TypeError, "no callable read"),
    ({"mounts": [Mount(Stat("dir"), "/w", writable=True)]}, TypeError, "no callable stat"),
    (
        {"mounts": [Mount(MemoryProvider(), "/w"), Mount(MemoryProvider(), "/w")]},
        ValueError,
        r"mounts\[1\] at /w: a grant or another mount",
    ),
    # Which error wins when a mount has two faults, as wasi-python's order gives it.
    ({"mounts": [Mount(MemoryProvider(), "w", writable="yes")]}, ValueError, "absolute"),
    ({"mounts": [Mount(NoStat(), "/dev")]}, ValueError, "runtime's own"),
    ({"mounts": [Mount(NoStat(), "/w", writable="yes")]}, TypeError, "writable must be"),
    (
        {"mounts": [Mount(MemoryProvider(), "/w"), Mount(NoRead(), "/w")]},
        TypeError,
        r"mounts\[1\] at /w: the provider has no callable read",
    ),
    ({"max_file_calls": True}, TypeError, "max_file_calls"),
    ({"max_file_calls": "10"}, TypeError, "max_file_calls"),
    ({"max_file_calls": -1}, ValueError, "max_file_calls"),
    ({"max_file_calls": 2**63}, ValueError, "max_file_calls"),
    ({"cwd": "/elsewhere", "mounts": [Mount(MemoryProvider(), "/w")]}, ValueError, "mount"),
    # A cwd in the runtime's own folders, which no grant may hold, though a mount at / or /usr
    # lies above them: the guest would stand in the library or /dev, not in the mount.
    *[
        ({"cwd": cwd, "mounts": [Mount(MemoryProvider(), top)]}, ValueError, "runtime's own")
        for top in ("/", "/usr")
        for cwd in ("/usr/local/lib/perl5", "/usr/local/lib/perl5/5.42", "/dev", "/dev/x")
        if cwd.startswith(top)
    ],
]


@pytest.mark.parametrize(("kw", "error", "match"), BAD)
def test_a_bad_mount_is_refused_before_any_work(tmp_path, kw, error, match, no_work) -> None:
    """Port of wasi-python's test of this name (`test_runner_mounts.py`, f9bf678)."""
    given = kw.get("mounts", ())
    given = [given] if isinstance(given, Mount) else given if isinstance(given, list) else []
    providers = [m.provider for m in given if isinstance(m, Mount)]
    with pytest.raises(error, match=match):
        run(["-e", "1"], **kw)
    for p in providers:
        assert getattr(p, "total", lambda: 0)() == 0
    provider = MemoryProvider()
    grant = Grant(tmp_path, "/w")
    with pytest.raises(ValueError, match="a grant or another mount"):
        run(["-e", "1"], grants=[grant], mounts=[Mount(provider, "/w")])
    assert provider.total() == 0


def test_a_bad_file_call_default_is_refused() -> None:
    with pytest.raises(ValueError, match="file_calls"):
        Defaults(1 << 30, 60.0, 16 << 20, file_calls=-1)
    with pytest.raises(TypeError, match="file_calls"):
        Defaults(1 << 30, 60.0, 16 << 20, file_calls=True)


# A3


def test_nesting_is_allowed_and_the_mounts_are_plain() -> None:
    """Port of wasi-python's test of this name: mounts at /, /usr (above the runtime's own
    folders) and /work inside it, beside a grant at /work/g, are accepted, as plain Mounts."""

    class Sub(Mount):
        pass

    provider = MemoryProvider()
    grants = _grants.check([Grant("/h", "/work/g")])
    mounts = _grants.check_mounts(
        iter([Mount(provider, "/"), Sub(provider, "/usr"), Mount(provider, "/work")]), grants
    )
    assert [m.guest for m in mounts] == ["/", "/usr", "/work"]
    assert all(type(m) is Mount for m in mounts)
    assert runner.DEFAULTS.file_calls == 10_000
    assert wasi_perl.defaults().file_calls == 10_000


# A4


def test_the_errno_values_are_preview1s() -> None:
    """Port of wasi-python's test of this name: the Errno set, by value (wasi-libc's wasip1.h)."""
    assert {e.name: e.value for e in Errno} == {
        "EACCES": 2, "EBUSY": 10, "ECANCELED": 11, "EDQUOT": 19, "EEXIST": 20, "EFBIG": 22,
        "EINVAL": 28, "EIO": 29, "EISDIR": 31, "ENAMETOOLONG": 37, "ENOENT": 44, "ENOSPC": 51,
        "ENOSYS": 52, "ENOTDIR": 54, "ENOTEMPTY": 55, "ENOTSUP": 58, "EPERM": 63, "EROFS": 69,
        "ESTALE": 72, "ETIMEDOUT": 73, "EXDEV": 75,
    }  # fmt: skip


# wasi-python's host tables, by name: macOS's, Linux's and Windows's numbers for each
# member's name, and EOPNOTSUPP.
_ERRNO_NAMES = (
    "EPERM ENOENT EIO EACCES EBUSY EEXIST EXDEV ENOTDIR EISDIR EINVAL EFBIG ENOSPC EROFS "
    "ENAMETOOLONG ENOTEMPTY ENOSYS EDQUOT ESTALE ETIMEDOUT ECANCELED ENOTSUP EOPNOTSUPP"
).split()
_HOSTS = {
    "macOS": (1, 2, 5, 13, 16, 17, 18, 20, 21, 22, 27, 28, 30, 63, 66, 78, 69, 70, 60, 89, 45, 102),
    "Linux": (
        1,
        2,
        5,
        13,
        16,
        17,
        18,
        20,
        21,
        22,
        27,
        28,
        30,
        36,
        39,
        38,
        122,
        116,
        110,
        125,
        95,
        95,
    ),
    "Windows": (
        *(1, 2, 5, 13, 16, 17, 18, 20, 21, 22, 27, 28, 30, 38, 41, 40),
        *(10069, 10070, 10060, 105, 129, 10045),
    ),
}


def test_the_host_errno_table_is_by_name() -> None:
    """Port of wasi-python's test of this name: the table from a host's errno names to the
    members is built by name, reaches every member (EOPNOTSUPP as ENOTSUP), holds no refused
    number, takes only plain ints, and skips a name the host lacks, so that importing
    `wasi_perl` never fails for one."""
    table = mounts_module._errno_table(vars(errno))
    assert set(table.values()) == set(Errno)
    keys = {getattr(errno, e.name) for e in Errno}
    if errno.EOPNOTSUPP != errno.ENOTSUP:
        keys.add(errno.EOPNOTSUPP)
    assert set(table) == keys
    assert all(table[getattr(errno, e.name)] is e for e in Errno)
    for refused in ("EINTR", "EAGAIN", "EBADF", "EFAULT"):
        assert getattr(errno, refused) not in table, refused
    assert mounts_module._HOST_ERRNO == table

    for host, numbers in _HOSTS.items():
        names = dict(zip(_ERRNO_NAMES, numbers, strict=True))
        hosted = mounts_module._errno_table(names)
        assert set(hosted.values()) == set(Errno), host
        assert hosted[names["EOPNOTSUPP"]] is Errno.ENOTSUP, host
        assert len(hosted) == (21 if host == "Linux" else 22), (host, len(hosted))

    class Sneaky(int):
        pass

    for bad in (True, Sneaky(errno.ENOENT)):
        assert (
            Errno.ENOENT not in mounts_module._errno_table({**vars(errno), "ENOENT": bad}).values()
        )
    without = {k: v for k, v in vars(errno).items() if k != "ESTALE"}
    lacking = mounts_module._errno_table(without)
    assert Errno.ESTALE not in lacking.values() and len(lacking) == len(table) - 1

    code = (
        "import errno\n"
        "del errno.ESTALE\n"
        "import wasi_perl\n"
        "from wasi_perl import mounts\n"
        "print(mounts.Errno.ESTALE in mounts._HOST_ERRNO.values())\n"
    )
    child = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False
    )
    assert (child.returncode, child.stdout.strip()) == (0, "False"), child.stderr[-500:]


# A5 and A6: a guest that runs; the real perl.wasm when one is built.


def _counted(monkeypatch, owner, name, calls) -> None:
    real = getattr(owner, name)

    def counted(*args, **kwargs):
        calls.append(name)
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, counted)


def test_a_call_without_mounts_is_unchanged(perl, monkeypatch) -> None:
    """Port of wasi-python's test of this name, trimmed of its counted linker: a call without
    mounts never makes the mount linker or the trampoline, invokes no shadow, reports no record
    and computes no rule input it did not compute before; a call with one does use them."""
    calls: list[str] = []
    for owner, name in (
        (_engines, "mount_linker"),
        (_engines, "trampoline"),
        (_fs_table._Files, "__call__"),
        (runner, "_runtime_folders"),
        (runner, "_runtime_files"),
        (_cache, "every"),
    ):
        _counted(monkeypatch, owner, name, calls)
    done = run(["-e", 'print "hi\\n"'], artifacts=perl)
    assert (done.exit_code, done.stdout, done.files) == (0, b"hi\n", None)
    assert calls == []
    provider = MemoryProvider({"/x": b"from a\n"})
    done = run(["-ne", "print", "/a/x"], mounts=[Mount(provider, "/a")], artifacts=perl)
    assert (done.exit_code, done.stdout) == (0, b"from a\n")
    assert done.files is not None and done.files.calls["read"] == 1
    assert {"trampoline", "__call__"} <= set(calls)  # the control: the mounted call used them
    assert not {"_runtime_folders", "_runtime_files", "every"} & set(calls)


def test_a_writable_mount_binds_no_host_rule(perl, monkeypatch) -> None:
    """A call whose only writable place is a mount runs no host rule's input, enters nothing in
    the registry of live grants and nothing in the set of folders once granted writable."""
    calls: list[str] = []
    for owner, name in (
        (runner, "_runtime_folders"),
        (runner, "_runtime_files"),
        (_cache, "every"),
    ):
        _counted(monkeypatch, owner, name, calls)
    entered: list = []
    real_hold = _grants.hold
    monkeypatch.setattr(
        _grants, "hold", lambda placed, slot: entered.append(placed) or real_hold(placed, slot)
    )
    ever = dict(_grants._ever_writable)
    provider = MemoryProvider({})
    code = 'open my $f, ">", "/w/new" or die "$!"; print $f "made\\n"; close $f or die "$!"'
    done = run(["-e", code], mounts=[Mount(provider, "/w", writable=True)], artifacts=perl)
    assert (done.exit_code, done.stderr) == (0, b"")
    assert provider.tree["/new"] == b"made\n"
    assert calls == []
    assert entered == [()]  # the registry is given no grant
    assert _grants._ever_writable == ever


# A7: ports of wasi-python's `tests/unit/test_wasmtime_ffi.py` at f9bf678, U1, U2, U3 and U5.


def real_holders() -> dict[str, object]:
    return {
        "wasmtime._ffi": _ffi,
        "wasmtime._bindings": _bindings,
        "wasmtime.Trap": wt.Trap,
        "wasmtime.WasmtimeError": wt.WasmtimeError,
        "wasmtime.Caller": wt.Caller,
        "wasmtime.Store": wt.Store,
        "wasmtime.WasiConfig": wt.WasiConfig,
        "wasmtime.Func": wt.Func,
    }


def dotted(pairs) -> list[str]:
    return [where + "." + name for where, name in pairs]


def test_missing_names_each_absent_name_in_order_and_all_lists_the_table() -> None:
    assert ffi.missing(real_holders()) == []
    assert isinstance(ffi.NEEDED, tuple) and len(set(dotted(ffi.NEEDED))) == len(ffi.NEEDED)
    empty = {holder: SimpleNamespace() for holder in real_holders()}
    assert ffi.missing(empty) == dotted(ffi.NEEDED)
    partial = real_holders()
    partial["wasmtime._bindings"] = SimpleNamespace(
        _wasmtime_store_epoch_deadline_callback=SimpleNamespace(argtypes=[1, 2, 3]),
        _wasi_config_set_stdout_custom=_bindings._wasi_config_set_stdout_custom,
    )
    partial["wasmtime.Func"] = type("F", (), {"__annotations__": {}})
    assert ffi.missing(partial) == [
        "wasmtime._bindings._wasmtime_store_epoch_deadline_callback",
        "wasmtime.Func._func",
    ]
    assert ffi.missing({}) == dotted(ffi.NEEDED)
    # Every private name the module reaches is in the table: each `_ffi.` and `_bindings.` name
    # its source spells, and the methods it calls on wasmtime-py's classes.
    source = Path(ffi.__file__).read_text(encoding="utf-8")
    spelled = set(re.findall(r"\b_ffi\.(\w+)", source))
    spelled |= {"_" + n for n in re.findall(r"\b_bindings\._(\w+)", source)}
    held = {name for where, name in ffi.NEEDED if where in ("wasmtime._ffi", "wasmtime._bindings")}
    assert spelled == held


def test_check_raises_one_import_error_naming_the_version_the_names_and_the_pin() -> None:
    holders = real_holders()
    holders["wasmtime.Trap"] = SimpleNamespace()
    holders["wasmtime.Caller"] = SimpleNamespace()
    with pytest.raises(ImportError) as info:
        ffi.check(holders)
    text = str(info.value)
    assert f"wasmtime-py {ffi.version()} lacks" in text
    assert "wasmtime.Trap._from_ptr, wasmtime.Caller._context" in text
    assert ffi.PIN in text and "install a wasmtime-py inside that range" in text
    ffi.check(real_holders())  # nothing missing: nothing raised


def test_the_runner_lets_the_adapters_import_error_through(monkeypatch) -> None:
    """A moved symbol is seen at the first run(), before any engine exists, naming the version
    and the names; with no wasmtime-py at all, the "needs wasmtime-py" error, its cause
    chained."""
    monkeypatch.delattr(_ffi, "wasmtime_error_new")
    monkeypatch.delitem(sys.modules, "wasi_perl._wasmtime_ffi")
    monkeypatch.delattr(wasi_perl, "_wasmtime_ffi")
    version = re.escape(importlib.metadata.version("wasmtime"))
    with pytest.raises(ImportError, match=rf"wasmtime-py {version} .*wasmtime_error_new"):
        run(["-e", "1"])
    assert "wasi_perl._wasmtime_ffi" not in sys.modules
    monkeypatch.undo()
    assert sys.modules["wasi_perl._wasmtime_ffi"] is ffi
    assert _engines.wasmtime() is wt
    monkeypatch.setitem(sys.modules, "wasmtime", None)
    monkeypatch.setattr(_engines, "_version", ["51.0.0"])
    with pytest.raises(ImportError, match=r"needs wasmtime-py") as info:
        _engines.wasmtime()
    assert isinstance(info.value.__cause__, ImportError)


def test_the_bindings_the_runner_calls_exist() -> None:
    """Every name of `NEEDED` exists in the pinned wasmtime-py, and the shapes the seam reads
    are the ones it was written for."""
    import ctypes

    assert ffi.missing(real_holders()) == []
    assert ffi.PIN == "wasmtime>=51,<52"
    assert _bindings._wasmtime_context_set_epoch_deadline.argtypes == [
        ctypes.POINTER(_ffi.wasmtime_context_t),
        ctypes.c_uint64,
    ]
    argtypes = _bindings._wasmtime_store_epoch_deadline_callback.argtypes
    assert len(argtypes) == 4
    assert argtypes[0] is ctypes.POINTER(_ffi.wasmtime_store_t)
    assert callable(_ffi.wasmtime_func_call)
    assert "_func" in wt.Func.__annotations__


def test_importing_wasi_perl_loads_no_wasmtime() -> None:
    code = "import sys, wasi_perl; print('wasmtime' in sys.modules)"
    child = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False
    )
    assert (child.returncode, child.stdout.strip()) == (0, "False"), child.stderr[-500:]


def test_the_limit_check_is_the_integer_rule() -> None:
    assert _limits.integer_limit("max_file_calls", 0) == 0
    assert _limits.integer_limit("max_file_calls", None) is None
