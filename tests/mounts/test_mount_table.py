"""The mount table through raw preview1 calls: the runner's own boundary code, which does not
depend on the host. Raw preview1 calls through the mount linker, with the call on the thread's
stack (`mounts/raw.py`); fuel and provider calls are counted, nothing is timed.

A port of wasi-python's `tests/unit/test_runner_mounts.py` at f9bf678 (same owner, Apache-2.0),
test for test and name for name, with these left out: the counted linker's and the cap on open
files' tests, which wasi-perl does not have (`test_a_counted_call_leaves_the_other_calls_native`,
`test_a_call_without_mounts_is_unchanged_but_for_the_two_fixes`,
`test_open_files_are_counted_on_a_call_without_mounts`,
`test_mount_files_count_toward_the_open_file_cap`), and the cancel cases, since wasi-perl has
no `cancel`: the deadline drives the owed close instead. The argument checks and the errno
tables are in `test_mount_api.py`. The tests whose guest was Python run a Perl guest, of the set
`$WASI_PERL_ARTIFACTS` names, else the build's."""

from __future__ import annotations

import struct
import threading
from pathlib import Path

import pytest
import wasmtime as wt

from mounts import raw as files
from mounts.raw import MemoryProvider, Mounted
from wasi_perl import (
    Errno,
    Mount,
    Stat,
    _engines,
    _fs_descriptors,
    _fs_memory,
    _fs_paths,
    _fs_table,
    _grants,
    _limits,
    _stop,
    runner,
)
from wasi_perl import mounts as mounts_module

EFAULT, EINVAL, ENAMETOOLONG, ENOTSUP, EBADF = 21, 28, 37, 58, 8


@pytest.fixture
def placeholder(tmp_path) -> Path:
    path = tmp_path / "placeholder"
    path.mkdir()
    return path


def test_guest_arguments_never_reach_the_provider(placeholder) -> None:
    provider = MemoryProvider()
    m = Mounted(provider, placeholder)
    r, end = m.raw, files.MEMORY_END
    f = r.open(3, "a.txt")[1]
    d = r.open(3, "d", files.O_DIRECTORY)[1]
    assert f is not None and d is not None
    before = dict(provider.calls)
    rows = {
        "path past memory": r("path_open", 3, 0, end - 2, 4, 0, files.READ_RIGHTS, 0, 0, 0),
        "path length past memory": r("path_filestat_get", 3, 0, files.PATH, end, files.RESULT),
        "path of 4,097 bytes": r.open(3, "x/" * 2048 + "y")[0],
        "stat result past memory": r("path_filestat_get", 3, 0, *r.path("a.txt"), end - 8),
        "open result past memory": r("path_open", 3, 0, *r.path("b.txt"), 0, 0, 0, 0, end),
        "iovec array past memory": r("fd_read", f, end - 4, 1, files.RESULT),
        "iovec past memory": r("fd_read", f, r.iovecs([8], buffer=end - 4), 1, files.RESULT),
        "a second iovec past memory": r(
            "fd_pread", f, r.iovecs([2, 8], buffer=end - 6), 2, 0, files.RESULT
        ),
        "1,025 iovecs": r("fd_read", f, r.iovecs([1] * 1025), 1025, files.RESULT),
        "nread past memory": r("fd_read", f, r.iovecs([4]), 1, end),
        "readdir buffer past memory": r("fd_readdir", d, end - 10, 100, 0, files.RESULT),
        "readdir length past memory": r("fd_readdir", 3, files.DIRBUF, end, 0, files.RESULT),
        "readdir used past memory": r("fd_readdir", 3, files.DIRBUF, 64, 0, end),
        "readlink buffer past memory": r("path_readlink", 3, *r.path("a.txt"), end, 8, 0),
        "fdstat past memory": r("fd_fdstat_get", f, end - 8),
        "filestat past memory": r("fd_filestat_get", f, end - 8),
        "seek result past memory": r("fd_seek", f, 0, 0, end),
    }
    assert rows == {
        "path past memory": EFAULT,
        "path length past memory": EFAULT,
        "path of 4,097 bytes": ENAMETOOLONG,
        "stat result past memory": EFAULT,
        "open result past memory": EFAULT,
        "iovec array past memory": EFAULT,
        "iovec past memory": EFAULT,
        "a second iovec past memory": EFAULT,
        "1,025 iovecs": EINVAL,
        "nread past memory": EFAULT,
        "readdir buffer past memory": EFAULT,
        "readdir length past memory": EFAULT,
        "readdir used past memory": EFAULT,
        "readlink buffer past memory": EFAULT,
        "fdstat past memory": EFAULT,
        "filestat past memory": EFAULT,
        "seek result past memory": EFAULT,
    }
    assert provider.calls == before  # not one provider call
    assert r.tell(f) == (0, 0)  # and nothing moved


def test_a_path_is_normalized_before_the_provider_sees_it(placeholder) -> None:
    """Decision 5: `.`, empty parts and `..` within the mount are resolved by the runner; a
    climb above the descriptor's folder, an absolute path, a NUL, a long part and a path a more
    specific preopen owns are refused before any call."""
    provider = MemoryProvider()
    grant = placeholder.parent / "nested"
    grant.mkdir()
    m = Mounted(provider, placeholder, folders=[(grant, "/m/g", False)])
    r = m.raw
    assert r.path_filestat(3, "./d//x.txt/")[0] == 54  # a file named as a folder
    assert r.path_filestat(3, "d/../a.txt")[0] == 0
    d = r.open(3, "d", files.O_DIRECTORY)[1]
    for base, path, errno in [
        (3, "..", 63),
        (3, "d/../..", 63),
        (d, "..", 63),
        (d, "../a.txt", 63),
        (3, "/a.txt", 63),
        (3, "", 44),
        (3, "a\0", 28),
        (3, "x" * 256, 37),
        (3, "g", 63),
        (3, "g/inside", 63),
        (3, "d/../g/./x", 63),
    ]:
        assert r.path_filestat(base, path)[0] == errno, (base, path)
    assert provider.paths == [
        ("stat", "/d/x.txt"),
        ("stat", "/a.txt"),
        ("stat", "/d"),  # a folder open asks before it lists
        ("list", "/d"),
    ]


# Decision 10: the call's cancel and deadline are checked before every provider call.


def test_a_cancel_or_a_passed_deadline_stops_the_provider_call(placeholder, monkeypatch) -> None:
    """The deadline half: once the ticker reaches the call's stop, the provider call is not
    made and the guest gets ETIMEDOUT. wasi-perl has no cancel, so that half is not ported."""
    provider = MemoryProvider()
    m = Mounted(provider, placeholder, stop_at=100)
    monkeypatch.setattr(_engines.TICKER, "count", 99)
    assert m.raw.path_filestat(3, "a.txt")[0] == 0
    monkeypatch.setattr(_engines.TICKER, "count", 100)
    assert m.raw.path_filestat(3, "a.txt")[0] == 73  # ETIMEDOUT
    assert provider.calls["stat"] == 1


def test_the_file_call_limit_names_the_end(placeholder) -> None:
    """Decision 8: the call that would pass the limit is not made; it answers EIO, names the
    call's end "out of file calls" and stops the guest at its next entry; every later file call
    answers the stopped code without a call."""
    provider = MemoryProvider()
    m = Mounted(provider, placeholder, file_calls=2)
    r = m.raw
    assert [r.path_filestat(3, "a.txt")[0] for _ in range(3)] == [0, 0, 29]
    assert m.call.ended == "out of file calls" and m.call.stopped is None
    with pytest.raises(files.Ends, match="interrupt"):  # the deadline was set to 0 in that step
        r.path_filestat(3, "a.txt")
    m.store.set_epoch_deadline(2**40)
    assert r.path_filestat(3, "a.txt")[0] == 29  # the entry guard: an ended call runs no host
    assert provider.calls["stat"] == 2
    assert m.table.record().calls == {
        "stat": 2,
        "list": 0,
        "read": 0,
        "write": 0,
        "mkdir": 0,
        "remove": 0,
        "rmdir": 0,
        "rename": 0,
    }


def test_a_named_end_holds_under_the_epoch_callback(placeholder, monkeypatch) -> None:
    """The epoch callback keeps an end already named: a call ended "out of file calls" whose
    deadline has also passed stops with the first name, and the name is kept."""
    m = Mounted(MemoryProvider(), placeholder, stop_at=None)
    m.watch.ended = _stop.FILE_CALLS
    m.watch.stop_at = 0
    monkeypatch.setattr(_engines.TICKER, "count", 10)
    _stop.arm(m.store, m.watch)
    m.store.set_epoch_deadline(0)
    with pytest.raises((wt.Trap, wt.WasmtimeError)) as info:
        m.instance.exports(m.store)["fd_close"](m.store, 3)
    assert _stop.word(str(info.value)) == _stop.FILE_CALLS
    assert m.watch.ended == _stop.FILE_CALLS


# Acceptance 12, the exact part: a forward is fuel-neutral and keeps wasmtime's traps.

P1 = "wasi_snapshot_preview1"
LOOP = f"""(module
  (import "{P1}" "fd_fdstat_get" (func $g (param i32 i32) (result i32)))
  (memory (export "memory") 1)
  (func $leaf (result i32) (i32.const 7))
  (func (export "run") (param $n i32) (result i32) (local $e i32)
    (block $done (loop $l
      (br_if $done (i32.eqz (local.get $n)))
      (local.set $e (i32.or (local.get $e) (call $g (i32.const 1) (i32.const 64))))
      (drop (call $leaf))
      (local.set $n (i32.sub (local.get $n) (i32.const 1)))
      (br $l)))
    (local.get $e)))"""
BAD_POINTER = f"""(module
  (import "{P1}" "fd_read" (func $r (param i32 i32 i32 i32) (result i32)))
  (memory (export "memory") 1)
  (func (export "run") (result i32)
    (drop (call $r (i32.const 4) (i32.const 64) (i32.const 1) (i32.const 0)))
    (call $r (i32.const 4) (i32.const 65536) (i32.const 1) (i32.const 0))))"""


def _loop(placeholder: Path, mounted: bool, cap: int, n: int = 200) -> tuple[str, int]:
    """How the loop ends and the fuel it used, on the mount linker or today's."""
    provider = MemoryProvider()
    m = Mounted(provider, placeholder, fuel=cap, wat=LOOP)
    if mounted:
        run, enter = m.instance.exports(m.store)["run"], m.entered
    else:  # the same store's configuration on today's linker
        instance = m.plain.instantiate(m.store, wt.Module(m.engine, LOOP))
        run, enter = instance.exports(m.store)["run"], m.entered
    try:
        with enter():
            end = f"returned {run(m.store, n)}"
    except wt.Trap as e:
        end = f"trap {e.trap_code}"
    except files.Ends as e:
        end = f"stopped {e.message}"
    assert provider.total() == 0
    return end, cap - m.store.get_fuel()


BAD_POINTER_START = f"""(module
  (import "{P1}" "fd_read" (func $r (param i32 i32 i32 i32) (result i32)))
  (memory (export "memory") 1)
  (func (export "_start")
    (drop (call $r (i32.const 4) (i32.const 64) (i32.const 1) (i32.const 0)))
    (drop (call $r (i32.const 4) (i32.const 65536) (i32.const 1) (i32.const 0)))))"""
FUEL = _limits.Limits(10**6, None, None, None)


def _driven(m: Mounted, mounted: bool, wat: str) -> tuple:
    """How the runner's driver ends `wat`'s `_start` on `m`'s store: on the mount linker, with
    `m`'s call on the stack, or on today's linker with no call."""
    module = wt.Module(m.engine, wat)
    if mounted:
        m.call.stopped = None
        pre = m.linker.instantiate_pre(module)
        return runner._drive(wt, m.store, pre, FUEL, m.call)
    return runner._drive(wt, m.store, m.plain.instantiate_pre(module), FUEL)


def test_forwards_cost_no_fuel_and_keep_traps(placeholder, tmp_path) -> None:
    """A guest that calls a WASI function N times
    spends exactly the fuel it spends on today's linker, and ends the same way with the same
    fuel used at every cap from 1 up to past its need; a forwarded bad pointer ends the call
    with the same `trapped` and fuel used, and the error is wasmtime-py's own wrapper."""
    full = _loop(placeholder, False, 10**9)
    assert full == _loop(placeholder, True, 10**9)
    assert full[0] == "returned 0"
    need = full[1]
    for cap in [*range(1, 60), *range(need - 40, need + 3)]:
        assert _loop(placeholder, True, cap) == _loop(placeholder, False, cap), cap
    folder = tmp_path / "folder"
    folder.mkdir()
    ends = []
    for mounted in (False, True):
        m = Mounted(MemoryProvider(), placeholder, folders=[(folder, "/f", False)], fuel=10**6)
        ends.append(_driven(m, mounted, BAD_POINTER_START))
        if mounted:
            assert isinstance(m.call.stopped, wt.WasmtimeError) and str(m.call.stopped)
    assert ends[0] == ends[1], ends
    assert ends[0][1] and ends[0][2] and ends[0][2] > 0, ends


def _imports(binary: Path) -> set[str]:
    """The names a module imports from preview1, read from its import section (id 2) without
    compiling it."""

    def leb(data: bytes, at: int) -> tuple[int, int]:
        value = shift = 0
        while True:
            byte = data[at]
            at += 1
            value |= (byte & 0x7F) << shift
            shift += 7
            if byte < 0x80:
                return value, at

    data, at, names = binary.read_bytes(), 8, set()
    while at < len(data):
        section, size, at = data[at], *leb(data, at + 1)
        if section == 2:
            count, pos = leb(data, at)
            for _ in range(count):
                n, pos = leb(data, pos)
                module, pos = data[pos : pos + n].decode(), pos + n
                n, pos = leb(data, pos)
                name, pos = data[pos : pos + n].decode(), pos + n
                kind, pos = data[pos], pos + 1
                assert kind == 0, (module, name)  # perl.wasm imports functions only
                _, pos = leb(data, pos)
                if module == "wasi_snapshot_preview1":
                    names.add(name)
            return names
        at += size
    return names


def test_every_descriptor_import_is_shadowed(perl) -> None:
    """Every call perl.wasm imports that takes a descriptor is shadowed, or reviewed
    forward-safe: 22 of the 29 shadowed calls, and the preopen pair and `poll_oneoff`."""
    imported = _imports(perl.perl)
    takes_a_descriptor = {
        n for n in imported if n.startswith(("fd_", "path_", "sock_")) or n == "poll_oneoff"
    }
    allowed = set(_fs_memory.SHADOWED) | set(_fs_memory.FORWARD_SAFE)
    assert takes_a_descriptor - allowed == set()
    assert len(_fs_memory.SHADOWED) == 29
    assert set(_fs_memory.SHADOWED).isdisjoint(_fs_memory.FORWARD_SAFE)
    assert len(takes_a_descriptor & set(_fs_memory.SHADOWED)) == 22
    assert takes_a_descriptor - set(_fs_memory.SHADOWED) == {
        "fd_prestat_get",
        "fd_prestat_dir_name",
        "poll_oneoff",
    }


def test_the_mount_linker_shadows_every_call(placeholder) -> None:
    """Each of the 29 reaches the mount table: on a number of the table it is served (EBADF from
    the table's answer for a call on a closed provider number is wasmtime's own)."""
    seen = []
    m = Mounted(MemoryProvider(), placeholder)
    real = m.table.__class__.__call__

    def counting(self, caller, name, *args):
        seen.append(name)
        return real(self, caller, name, *args)

    m.table.__class__.__call__ = counting  # type: ignore[method-assign]
    try:
        for name, params in _fs_memory.SIGNATURES.items():
            try:
                m.raw(name, *([2**31 - 1] * len(params)))
            except files.Ends:
                pass
    finally:
        m.table.__class__.__call__ = real  # type: ignore[method-assign]
    assert sorted(set(seen)) == sorted(_fs_memory.SHADOWED)


def test_renumber_and_close_keep_the_table_true(placeholder) -> None:
    """Decision 1: fd_renumber onto or from a provider number, or the mount's preopen, answers
    ENOTSUP and changes nothing; closing the mount's preopen drops it from the table, and a later
    open that reuses its number is wasmtime's."""
    folder = placeholder.parent / "real"
    folder.mkdir()
    (folder / "r.txt").write_bytes(b"real\n")
    m = Mounted(MemoryProvider(), placeholder, folders=[(folder, "/r", False)])
    r = m.raw
    f = r.open(3, "a.txt")[1]
    g = r.open(4, "r.txt")[1]
    assert f is not None and g is not None and f >= 2**30 and g < 2**30
    table = dict(m.table.table)
    for a, b in [(g, f), (f, g), (g, 3), (3, g), (g, 2**30 + 5), (2**31 - 1, g)]:
        assert r("fd_renumber", a, b) == ENOTSUP, (a, b)
    assert m.table.table == table
    assert r.read(f, [5])[2] == b"hello" and r.read(g, [4])[2] == b"real"
    assert r("fd_close", 3) == 0
    assert 3 not in m.table.table
    again = r.open(4, "r.txt")[1]
    assert again == 3  # wasmtime reuses the freed number
    assert r.read(3, [4])[2] == b"real"
    assert m.table.calls["read"] == 1


def test_the_staging_bound_and_the_open_limit(placeholder) -> None:
    """Decision 8: a read that would pass the staging bound is EFBIG and dropped; the open after
    MAX_MOUNT_FILES is EMFILE with no call; a closed number is reused, the lowest first."""
    provider = MemoryProvider({"/big": b"x" * 100, "/small": b"y"})
    m = Mounted(provider, placeholder, staging=150)
    r = m.raw
    big = r.open(3, "big")[1]
    assert big is not None
    assert r.open(3, "big")[0] == 22  # EFBIG: 200 > 150
    assert m.table.staged == 100
    assert r("fd_close", big) == 0 and m.table.staged == 0
    r = Mounted(provider, placeholder).raw
    numbers = [r.open(3, "small")[1] for _ in range(_fs_memory.MAX_MOUNT_FILES)]
    assert numbers == list(range(2**30, 2**30 + 1024))
    before = provider.calls["read"]
    assert r.open(3, "small")[0] == 33  # EMFILE
    assert provider.calls["read"] == before
    assert r("fd_close", 2**30 + 7) == 0 and r("fd_close", 2**30 + 3) == 0
    assert [r.open(3, "small")[1] for _ in range(2)] == [2**30 + 3, 2**30 + 7]


def test_a_listing_is_kept_and_listed_again_from_cookie_zero(placeholder) -> None:
    """Decision 6: the listing made at open serves the first pass; fd_readdir from cookie 0 after
    that lists again, as rewinddir expects; a folder made from EISDIR is listed at its first
    fd_readdir."""
    provider = MemoryProvider()
    r = Mounted(provider, placeholder).raw
    d = r.open(3, "d", files.O_DIRECTORY)[1]
    assert provider.calls["list"] == 1
    first = r.readdir(d)
    assert r.readdir(d, cookie=2) == (0, 29, first[2][51:])
    assert provider.calls["list"] == 1
    assert r.readdir(d) == first and provider.calls["list"] == 2
    e = r.open(3, "d")[1]
    assert provider.calls == {
        "stat": 1,  # the folder open's, before its listing
        "list": 2,
        "read": 1,
        "write": 0,
        "mkdir": 0,
        "remove": 0,
        "rmdir": 0,
        "rename": 0,
    }
    assert r.readdir(e) == first and provider.calls["list"] == 3


def test_a_folder_stat_comes_from_the_mount(placeholder) -> None:
    """dev hashes the mount's guest path and ino the provider path (zero-keyed SipHash-1-3, as
    `_wasi_ino`), so a run repeats exactly; an open file's fstat needs no provider call."""
    provider = MemoryProvider()
    r = Mounted(provider, placeholder).raw
    f = r.open(3, "a.txt")[1]
    calls = dict(provider.calls)
    assert r.filestat(f)[0] == 0 and provider.calls == calls
    dev, ino = struct.unpack("<QQ", r.get(files.RESULT, 16))
    assert dev == _grants._siphash13(b"/m") and ino == _grants._siphash13(b"/a.txt")
    assert r.get(files.RESULT + 40, 24) == bytes(24)  # no times from fstat


# Acceptance 10: a call without mounts is unchanged.


def test_a_listing_is_judged_as_it_is_gathered(placeholder) -> None:
    """Decision 8: a listing is kept as its encoded entries (24 bytes and the name each) and
    judged against the staging bound while it is gathered: past it, EFBIG, and the provider's
    iterator is closed, not drained."""
    drawn = []

    class Many(MemoryProvider):
        def list(self, path):
            self._count("list", path)

            def names():
                for i in range(10**6):
                    drawn.append(i)
                    yield (f"n{i:04}", "file")

            return names()

    m = Mounted(Many(), placeholder, staging=1000)
    assert m.raw.open(3, ".", files.O_DIRECTORY)[0] == 22
    assert len(drawn) < 40 and m.table.staged == 0
    assert m.table.record().faults == ()


def test_the_record_keeps_the_first_thousand_faults(placeholder) -> None:
    class Broken(MemoryProvider):
        def stat(self, path):
            self._count("stat", path)
            raise OSError("x" * 500)

    provider = Broken()
    m = Mounted(provider, placeholder)
    for _ in range(1001):
        assert m.raw.path_filestat(3, "a.txt")[0] == 29
    record = m.table.record()
    assert len(record.faults) == 1000 and record.calls["stat"] == 1001 == provider.calls["stat"]
    assert record.faults[0] == mounts_module.FileFault(
        "stat", "/a.txt", ("OSError: " + "x" * 500)[:200]
    )
    with pytest.raises(TypeError):
        record.calls["stat"] = 0  # type: ignore[index]  # read-only


def test_a_finished_call_holds_nothing(perl) -> None:
    """When run() returns, no call, mount table or staged file of it is
    left for the cyclic collector, whatever ended the guest: an exit with a mount file still
    open, or running out of fuel while holding one. Counted with the collector off."""
    import gc

    provider = MemoryProvider({"/big": b"x" * (1 << 20)})
    hold = 'open my $f, "<", "/m/big" or die; read $f, my $b, 1; '
    endings = {
        "an exit": (hold + "exit 0", None, None),
        "out of fuel": (hold + "1 while 1", 400_000_000, None),
        "out of file calls": (hold + '1 while -e "/m/big"', None, 5),
    }

    def alive() -> int:
        kinds = (runner._Call, _fs_table._Files, mounts_module._Open)
        return sum(type(o) in kinds for o in gc.get_objects())

    for name, (code, fuel, calls) in endings.items():
        gc.collect()
        before = alive()
        gc.disable()
        try:
            mounts = [Mount(provider, "/m")]
            done = runner.run(
                ["-e", code], mounts=mounts, fuel=fuel, max_file_calls=calls, artifacts=perl
            )
            left = alive() - before
        finally:
            gc.enable()
        assert done.exit_code == 0 or done.trapped in ("out of fuel", name), (name, done)
        assert left == 0, (name, left)


def test_an_abi_error_keeps_the_grants_fuel(placeholder, tmp_path) -> None:
    """A served call that meets an argument its ABI cannot carry ends
    the run with the fuel a grant reports for the same guest, not None."""
    guest = f"""(module
  (import "{P1}" "path_open"
    (func $o (param i32 i32 i32 i32 i32 i64 i64 i32 i32) (result i32)))
  (memory (export "memory") 1)
  (data (i32.const 100) "a.txt")
  (func (export "_start")
    (drop (call $o (i32.const 3) (i32.const 0) (i32.const 100) (i32.const 5) (i32.const 16)
      (i64.const 2) (i64.const 2) (i32.const 0) (i32.const 0)))))"""
    tree = files.write_tree(tmp_path / "tree")
    ends = []
    for mounted in (False, True):
        # the same layout on both sides: the mount's placeholder, or the tree's folder, at 3
        m = Mounted(MemoryProvider(), placeholder if mounted else tree, fuel=10**6)
        exit_code, _, used = _driven(m, mounted, guest)
        ends.append((exit_code, used))
    assert ends[0] == ends[1] and ends[0][1] is not None and ends[0][1] > 0, ends


_LISTED_NAMES = 20_000


def _listing_gathered(staging: int, neighbour=None) -> tuple[int, int, int, int]:
    """(errno, bytes held, bytes the listing's own code keeps, peak) for one listing of
    _LISTED_NAMES generated names under `staging`. tracemalloc counts every allocation the
    process makes while it traces, by any thread or any finalizer the collector runs, so what the
    listing keeps is counted as the traces allocated in the mount table's own code, where its
    buffer and its offsets are made, with the collector run first and paused: what an earlier
    check left alive in the process is no part of it. `neighbour`, when given, runs on another
    thread as the listing starts, as such a leftover would."""
    import gc
    import tracemalloc

    from wasi_perl._fs_table import _Files

    def generated():
        if neighbour is not None:
            thread = threading.Thread(target=neighbour)
            thread.start()
            thread.join()
        for i in range(_LISTED_NAMES):
            yield f"n{i:07}", "file"

    class Generated:
        def stat(self, path):
            return Stat("dir")

        def read(self, path):
            return Errno.EISDIR

        def list(self, path):
            return generated()

    table = _Files(
        [(Mount(Generated(), "/m"), 3)], [], None, staging, False, context=files.FakeContext()
    )
    paused = gc.isenabled()
    gc.collect()
    gc.disable()
    tracemalloc.start()
    try:
        errno, listing, held = table._list(None, table.mounts[0], "/", 1)
        _, peak = tracemalloc.get_traced_memory()
        kept = tracemalloc.take_snapshot().filter_traces(
            [tracemalloc.Filter(True, _fs_table.__file__)]
        )
        now = sum(stat.size for stat in kept.statistics("filename"))
    finally:
        tracemalloc.stop()
        if paused:
            gc.enable()
    del listing
    return errno, held, now, peak


def test_a_listing_is_counted_as_what_it_holds() -> None:
    """A kept listing holds what the staging bound counts for it (its
    encoded entries and their offsets, measured by tracemalloc, within a small constant), and
    gathering one past the bound peaks near the bound, not several times it."""
    errno, held, now, peak = _listing_gathered(1 << 30)
    assert errno == 0 and held > _LISTED_NAMES * 32
    assert held <= now <= held + 4096, (held, now)  # what is kept is what is counted
    bound = held // 2
    errno, _, now, peak = _listing_gathered(bound)
    assert errno == 22  # EFBIG, judged while gathering
    assert peak <= bound * 1.25 + 65536, (bound, peak)  # the gathering is counted too


def test_what_another_thread_keeps_is_no_part_of_a_listings_count() -> None:
    """Another thread that allocates 48 KiB and keeps it while the listing is gathered, as a
    thread an earlier check left running would, moves the listing's count by nothing."""
    kept: list[bytearray] = []
    errno, held, now, _ = _listing_gathered(1 << 30, lambda: kept.append(bytearray(48 * 1024)))
    assert errno == 0 and len(kept) == 1
    assert held <= now <= held + 4096, (held, now)


class _StandInMemory:
    """The guest's memory as the readers see it, `fits`, `read` and `write` over a bytearray:
    no caller and no engine."""

    def __init__(self, size: int) -> None:
        self.data = bytearray(size)
        self.size = size

    def fits(self, ptr: int, length: int) -> bool:
        return ptr + length <= self.size

    def read(self, ptr: int, length: int) -> bytes:
        return bytes(self.data[ptr : ptr + length])

    def write(self, ptr: int, data: bytes) -> None:
        self.data[ptr : ptr + len(data)] = data


def test_the_memory_readers_answer_over_a_stand_in() -> None:
    """The four readers of the guest's memory answer as wasmtime reads a pointer, over a
    stand-in with no caller: a path past the end is EFAULT, one longer than MAX_PATH is
    ENAMETOOLONG, bytes that are not UTF-8 are EILSEQ, an iovec count over MAX_IOVECS is EINVAL,
    an iovec past the end is EFAULT, an unaligned result pointer ends the run with an ABI error,
    and a result that fits is 0."""
    m = _fs_memory
    memory = _StandInMemory(64)
    memory.write(0, b"a.txt\xff")
    assert m._path(memory, 0, 5) == b"a.txt"
    assert m._path(memory, 60, 5) == EFAULT
    assert m._path(_StandInMemory(m.MAX_PATH + 1), 0, m.MAX_PATH + 1) == ENAMETOOLONG
    assert m._text(b"a.txt") == "a.txt"
    assert m._text(memory.read(4, 2)) == m.EILSEQ
    memory.write(16, struct.pack("<IIII", 40, 0, 40, 8))  # an empty buffer, then 8 bytes at 40
    assert m._iovecs(memory, 16, 2) == (40, 8)
    assert m._iovecs(memory, 16, m.MAX_IOVECS + 1) == EINVAL
    assert m._iovecs(memory, 60, 1) == EFAULT
    memory.write(16, struct.pack("<II", 60, 8))
    assert m._iovecs(memory, 16, 1) == EFAULT
    assert m._result(memory, 56, 8, 8) == 0
    assert m._result(memory, 60, 8, 8) == EFAULT
    with pytest.raises(wt.WasmtimeError) as raised:
        m._result(memory, 4, 8, 8)
    assert getattr(raised.value, "abi", False) is True


def test_the_table_reuses_numbers_lowest_first() -> None:
    """A table built by hand, with a fake context and no handler: a new row takes the lowest
    free provider number, a dropped row frees its number for the next, and an owed row is closed
    for the guest but keeps its number. The cap on open files is wasi-python's alone."""
    base = _fs_memory.PROVIDER_BASE
    table = _fs_table._Files(
        [(Mount(MemoryProvider(), "/m"), 3)],
        [],
        None,
        1 << 30,
        False,
        context=files.FakeContext(),
    )
    mount = table.mounts[0]

    def row(path: str) -> mounts_module._Open:
        return mounts_module._Open(mount, path, "file")

    assert [table._take(row(f"/{n}")) for n in "abc"] == [base, base + 1, base + 2]
    assert table.opened == 3
    table._drop(base)
    table._drop(base + 1)
    assert table.opened == 1
    assert [table._take(row(f"/{n}")) for n in "de"] == [base, base + 1]
    table._owe(base + 2, table.table[base + 2])
    assert base + 2 not in table.table and (table.opened, len(table.owed)) == (3, 1)
    assert table._take(row("/f")) == base + 3  # an owed number is not reused before the ending


def test_every_shadowed_call_has_one_handler_function() -> None:
    """The dispatch is a table equal to the shadowed signatures: each `fd_*` call is one function
    of the descriptor module and each `path_*` call one of the path module, named `_` and the
    call, taking the table as `files` first; the two tables are disjoint, and the table class
    has no method of a handler's name, so no call is reached two ways."""
    import inspect
    import types

    fd, path = _fs_descriptors.HANDLERS, _fs_paths.HANDLERS
    assert set(fd) | set(path) == set(_fs_memory.SIGNATURES)
    assert not set(fd) & set(path)
    assert all(name.startswith("fd_") for name in fd)
    assert all(name.startswith("path_") for name in path)
    for name, handler in {**fd, **path}.items():
        assert type(handler) is types.FunctionType, name
        assert handler.__name__ == "_" + name, name
        assert next(iter(inspect.signature(handler).parameters)) == "files", name
        assert not hasattr(_fs_table._Files, "_" + name), name
    assert _fs_table.HANDLERS == {**fd, **path}


def test_a_mount_writes_zero_dirent_padding(placeholder) -> None:
    """Every fd_readdir entry a mount writes has its three padding bytes after d_type zeroed,
    over a buffer filled with 0xFF first, on full and cut-short buffers alike: the runner never
    copies host memory into the guest there, as wasmtime writes zeros on a grant."""
    r = Mounted(MemoryProvider(), placeholder).raw
    d = r.open(3, "d", files.O_DIRECTORY)[1]
    for fd in (3, d):
        for size in (4096, 22, 23, 25, 47, 48, 50, 80):  # 22, 23, 47, 48 end in a header's padding
            r.put(files.DIRBUF, b"\xff" * 4096)
            assert r("fd_readdir", fd, files.DIRBUF, size, 0, files.RESULT) == 0
            data, pos, entries = r.get(files.DIRBUF, r.u32(files.RESULT)), 0, 0
            while pos < len(data):
                header = data[pos : pos + 24]
                assert header[21:24] == b"\x00" * len(header[21:24]), (fd, size, pos, header)
                entries += 1
                if len(header) < 24:
                    break
                pos += 24 + struct.unpack_from("<I", header, 16)[0]
            assert entries >= 1
            assert r.get(files.DIRBUF + len(data), 1) == b"\xff"  # nothing past what it used


# A call without mounts runs on the counted linker, which shadows only the three calls the cap on
# open files counts (path_open, fd_close, fd_renumber) and forwards them; every other call,
# fd_readdir and path_rename among them, is wasmtime's own.

ENOTDIR, ENOENT, EPERM = 54, 44, 63


def _pads(data: bytes, skip: range = range(0)) -> list[tuple[int, bytes]]:
    """Each entry's offset and its padding bytes (21 to 24 of the header, as far as `data`
    reaches), walking by 24 + d_namlen; bytes at offsets in `skip` are left out."""
    out, pos = [], 0
    while pos < len(data):
        header = data[pos : pos + 24]
        out.append((pos, bytes(header[i] for i in range(21, len(header)) if pos + i not in skip)))
        if len(header) < 24:
            break
        pos += 24 + struct.unpack_from("<I", header, 16)[0]
    return out


WRITE_HALF = ("write", "mkdir", "remove", "rmdir", "rename")
ALL = files.RIGHTS_ALL


def _written(provider: MemoryProvider) -> dict[str, int]:
    return {k: provider.calls[k] for k in WRITE_HALF}


def test_a_writable_mount_is_asked_for(placeholder) -> None:
    """Guards the writable check: a mount not made writable, over a
    provider that has the write half, answers every write-shaped call EPERM and never calls the
    write half."""
    provider = MemoryProvider()
    r = Mounted(provider, placeholder).raw
    f = r.open(3, "a.txt")[1]
    assert f is not None
    rows = {
        "x": r.open(3, "n", files.O_CREAT | files.O_EXCL, ALL)[0],
        "w": r.open(3, "a.txt", files.O_CREAT | files.O_TRUNC, ALL)[0],
        "truncate": r.open(3, "a.txt", files.O_TRUNC)[0],
        "create": r.open(3, "n", files.O_CREAT)[0],
        "r+": r.open(3, "a.txt", 0, ALL)[0],
        "mkdir": r.path_call("path_create_directory", 3, "n"),
        "remove": r.path_call("path_unlink_file", 3, "a.txt"),
        "rmdir": r.path_call("path_remove_directory", 3, "e"),
        "rename": r.rename(3, "a.txt", 3, "c"),
        "link": r.link(3, "a.txt", 3, "c"),
        "symlink": r.symlink("a.txt", 3, "l"),
        "path set-times": r.path_set_times(3, "a.txt", 0),
        "file set-times": r("fd_filestat_set_times", f, 0, 0, 0),
        "preopen set-times": r("fd_filestat_set_times", 3, 0, 0, 0),
        "set size": r("fd_filestat_set_size", f, 0),
    }
    assert rows == dict.fromkeys(rows, 63), rows
    assert _written(provider) == dict.fromkeys(WRITE_HALF, 0)


class _Missing(MemoryProvider):
    def __init__(self, method: str) -> None:
        super().__init__()
        setattr(self, method, None)


class _AsyncWrite(MemoryProvider):
    async def write(self, path, data):  # type: ignore[override]
        return None


@pytest.mark.parametrize(
    ("mount", "match"),
    [
        (Mount(MemoryProvider(), "/w", writable="yes"), r"mounts\[0\] at /w: writable must be"),
        (Mount(MemoryProvider(), "/w", writable=1), "writable must be True or False"),
        (Mount(MemoryProvider(), "/w", writable=None), "writable must be True or False"),
        *[
            (Mount(_Missing(m), "/w", writable=True), rf"mounts\[0\] at /w: .*no callable {m}")
            for m in WRITE_HALF
        ],
        (Mount(_AsyncWrite(), "/w", writable=True), r"write\(\) is a coroutine function"),
    ],
)
def test_a_writable_mount_is_checked_before_any_work(mount, match) -> None:
    """`writable` must be a real bool, and a writable mount's provider
    must have the whole write half, none of it a coroutine function; each is a TypeError naming
    the mount, raised by run before the provider is called."""
    with pytest.raises(TypeError, match=match):
        runner.run(["-e", "1"], mounts=[mount])
    assert mount.provider.total() == 0


def test_writes_are_bounded(placeholder) -> None:
    """Guards the growth bound: with a staging bound of 1,000 bytes, a
    size past it, a write after a seek to it, a second file whose bytes would take the sum past
    it, and a one-byte `fd_pwrite` at 2**40 each answer EFBIG and change nothing; the table's
    staged count never passes the bound. (Each is asserted before the next, so a runner without
    the bound fails before it tries to allocate 2**40 bytes.)"""
    provider = MemoryProvider({"/f": b"x" * 10})
    m = Mounted(provider, placeholder, writable=True, staging=1000)
    r = m.raw
    f = r.open(3, "f", 0, ALL)[1]
    assert f is not None and m.table.staged == 10
    assert r("fd_filestat_set_size", f, 1001) == 22
    assert (r.filestat(f)[3], m.table.staged) == (10, 10)
    assert r.seek(f, 1000, 0) == (0, 1000)
    assert r.write(f, b"y") == (22, 0)
    assert (r.filestat(f)[3], m.table.staged) == (10, 10)
    assert r("fd_filestat_set_size", f, 990) == 0
    assert (r.filestat(f)[3], m.table.staged) == (990, 990)
    g = r.open(3, "g", files.O_CREAT, ALL)[1]
    assert g is not None
    assert r.write(g, b"z" * 11) == (22, 0)
    assert (r.filestat(g)[3], m.table.staged) == (0, 990)
    assert r.write(g, b"z" * 10) == (0, 10)
    assert (r.filestat(g)[3], m.table.staged) == (10, 1000)
    assert r.write(f, b"y", 2**40) == (22, 0)
    assert (r.filestat(f)[3], m.table.staged) == (990, 1000)


def test_an_unlinked_folder_holds_nothing(placeholder) -> None:
    """Guards the unlinked-folder rule, raw: a folder open while
    the guest removes it and makes it again answers every path call through the old descriptor
    ENOENT and lists only `.` and `..` from cookie 0, each with no provider call; a file open
    while the guest removes it keeps its buffer and is never written back."""
    provider = MemoryProvider()
    m = Mounted(provider, placeholder, writable=True)
    r = m.raw
    d = r.open(3, "e", files.O_DIRECTORY)[1]
    f = r.open(3, "a.txt", 0, ALL)[1]
    assert d is not None and f is not None
    assert r.write(f, b"kept") == (0, 4)
    rows = {
        "rmdir": r.path_call("path_remove_directory", 3, "e"),
        "mkdir": r.path_call("path_create_directory", 3, "e"),
        "remove": r.path_call("path_unlink_file", 3, "a.txt"),
    }
    y = r.open(3, "e/y", files.O_CREAT, ALL)[1]
    assert y is not None and r("fd_close", y) == 0
    assert rows == {"rmdir": 0, "mkdir": 0, "remove": 0}
    before = provider.total()
    rows = {
        "open": r.open(d, "y")[0],
        "open to write": r.open(d, "z", files.O_CREAT, ALL)[0],
        "stat": r.path_filestat(d, "y")[0],
        "readlink": r.readlink(d, "y")[0],
        "set-times": r.path_set_times(d, "y", 0),
        "mkdir": r.path_call("path_create_directory", d, "z"),
        "remove": r.path_call("path_unlink_file", d, "y"),
        "rmdir": r.path_call("path_remove_directory", d, "z"),
        "rename": r.rename(d, "y", 3, "z"),
        "re-list": r.readdir_names(d),
        "the file's bytes": (r.seek(f, 0, 0), r.read(f, [4])),
        "the file's sync": r("fd_sync", f),
        "the file's close": r("fd_close", f),
    }
    assert rows == {
        "open": 44,
        "open to write": 44,
        "stat": 44,
        "readlink": 44,
        "set-times": 44,
        "mkdir": 44,
        "remove": 44,
        "rmdir": 44,
        "rename": 44,
        "re-list": (0, [(".", 3), ("..", 3)]),
        "the file's bytes": ((0, 0), (0, 4, b"kept")),
        "the file's sync": 0,
        "the file's close": 0,
    }, rows
    assert provider.total() == before
    assert provider.tree["/e/y"] == b"" and "/a.txt" not in provider.tree


def test_the_runner_answers_namespace_calls_it_can(placeholder, tmp_path) -> None:
    """The calls the runner answers with no provider call: the mount
    itself (Linux's answers, wasmtime's reference), a rename into its own subfolder, a rename or
    rmdir strictly above a more specific preopen (EBUSY; a slash-named rmdir, which removes a
    folder by its plain name, after one `stat` finds the folder), a rename or link to another
    descriptor (EPERM when that side is read-only, EXDEV when both may be written), a link or
    symlink within (ENOTSUP); and a rename onto itself, one `stat`."""
    rw, ro = tmp_path / "rw", tmp_path / "ro"
    rw.mkdir()
    ro.mkdir()
    (rw / "w.txt").write_bytes(b"w")
    (ro / "r.txt").write_bytes(b"r")
    provider = MemoryProvider()
    folders = [(rw, "/rw", True), (ro, "/ro", False), (tmp_path / "rw", "/m/d/inner", True)]
    m = Mounted(provider, placeholder, writable=True, folders=folders)
    r = m.raw
    rows = {
        "mkdir of the mount": r.path_call("path_create_directory", 3, "."),
        "remove of the mount": r.path_call("path_unlink_file", 3, "."),
        "rmdir of the mount": r.path_call("path_remove_directory", 3, "."),
        "rename of the mount": r.rename(3, ".", 3, "q"),
        "rename onto the mount": r.rename(3, "a.txt", 3, "."),
        "rename into its own subfolder": r.rename(3, "e", 3, "e/sub"),
        "rmdir above a nested preopen": r.path_call("path_remove_directory", 3, "d"),
        "rmdir above a nested preopen, slash-named": r.path_call("path_remove_directory", 3, "d/"),
        "rename above a nested preopen": r.rename(3, "d", 3, "q"),
        "rename onto a folder above one": r.rename(3, "e", 3, "d"),
        "rmdir at a nested preopen": r.path_call("path_remove_directory", 3, "d/inner"),
        "rename to a writable grant": r.rename(3, "a.txt", 4, "c"),
        "rename from a writable grant": r.rename(4, "w.txt", 3, "c"),
        "rename to a read-only grant": r.rename(3, "a.txt", 5, "c"),
        "link to a writable grant": r.link(3, "a.txt", 4, "c"),
        "link to a read-only grant": r.link(3, "a.txt", 5, "c"),
        "link within": r.link(3, "a.txt", 3, "c"),
        "symlink within": r.symlink("a.txt", 3, "l"),
    }
    assert rows == {
        "mkdir of the mount": 20,
        "remove of the mount": 31,
        "rmdir of the mount": 28,
        "rename of the mount": 10,
        "rename onto the mount": 10,
        "rename into its own subfolder": 28,
        "rmdir above a nested preopen": 10,
        "rmdir above a nested preopen, slash-named": 10,
        "rename above a nested preopen": 10,
        "rename onto a folder above one": 10,
        "rmdir at a nested preopen": 63,
        "rename to a writable grant": 75,
        "rename from a writable grant": 75,
        "rename to a read-only grant": 63,
        "link to a writable grant": 75,
        "link to a read-only grant": 63,
        "link within": 58,
        "symlink within": 58,
    }, rows
    assert (provider.total(), provider.calls["stat"]) == (1, 1)  # the slash-named rmdir's
    assert (r.rename(3, "a.txt", 3, "./a.txt"), r.rename(3, "nope", 3, "nope")) == (0, 44)
    assert dict(provider.calls) == {**dict.fromkeys(provider.calls, 0), "stat": 3}
    assert sorted(p.name for p in rw.iterdir()) == ["w.txt"]


# The guest leaves its files open and ends with `_exit`, so Perl closes none of them and every
# write-back is the walk's. Each open costs the guest's look (a `stat`) and a create.
SPIN_DIRTY = (
    "use Fcntl; use POSIX (); my @fh; "
    "for my $n (0 .. {files} - 1) {{ "
    'sysopen my $f, "/w/$n.txt", O_WRONLY | O_CREAT | O_TRUNC or die "$!"; push @fh, $f }} '
    'syswrite $_, "dirty" for @fh; '
)
EXIT = "POSIX::_exit(0);"


def test_the_walk_is_bounded_and_keeps_its_record(placeholder, perl) -> None:
    """Guards the limit in the walk, the fault catch in the walk and
    `refused_total`: when the walk's write-backs would pass `max_file_calls`, the provider's
    calls stop exactly at the limit, each unmade write-back is one RefusedWrite for the limit,
    and the run keeps its exit and its fuel count (the walk names no end and spends no fuel:
    exactly, on the table, below; a guest's fuel differs run to run, so `run()` shows only that
    it is counted); a walk `write` that raises does not stop the walk; 1,001 refused `mkdir`s
    and one refused write-back are 1,000 kept and 1,002 counted."""
    from wasi_perl import RefusedWrite

    code = SPIN_DIRTY.format(files=4) + EXIT
    ends = []
    for limit in (None, 10):  # 4 looks and 4 creates, then 2 of the 4 write-backs
        provider = MemoryProvider({})
        mounts = [Mount(provider, "/w", writable=True)]
        done = runner.run(
            ["-e", code], mounts=mounts, max_file_calls=limit, fuel=10**12, artifacts=perl
        )
        ends.append((done.exit_code, done.trapped, done.fuel_used is not None))
    assert ends == [(0, None, True)] * 2, ends
    assert provider.total() == 10 and provider.writes() == [b""] * 4 + [b"dirty"] * 2
    assert done.files.refused == tuple(
        RefusedWrite("/w", "write", (f"/{n}.txt",), Errno.EIO, "exit", "limit") for n in (2, 3)
    )
    provider = MemoryProvider({})
    m = Mounted(provider, placeholder, writable=True, file_calls=1, fuel=10**6)
    f = m.raw.open(3, "n", files.O_CREAT | files.O_TRUNC, ALL)[1]
    assert f is not None and m.raw.write(f, b"n") == (0, 1)
    fuel = m.store.get_fuel()
    m.table.walk()
    assert (m.store.get_fuel(), m.call.ended, m.call.stopped) == (fuel, None, None)
    assert provider.writes() == [b""] and m.table.record().refused_total == 1
    provider = MemoryProvider({})

    def raises(path, data):
        if data:
            raise ValueError("broke")

    provider.answers[("write", "/0.txt")] = raises
    done = runner.run(
        ["-e", SPIN_DIRTY.format(files=2) + EXIT],
        mounts=[Mount(provider, "/w", writable=True)],
        artifacts=perl,
    )
    assert done.exit_code == 0 and provider.writes("/1.txt") == [b"", b"dirty"]
    assert [r.cause for r in done.files.refused] == ["fault"]
    provider = MemoryProvider({})
    provider.answers[("mkdir", "/m")] = Errno.EACCES
    provider.answers[("write", "/0.txt")] = lambda path, data: Errno.ENOSPC if data else None
    code = SPIN_DIRTY.format(files=1) + 'for (1 .. 1001) { mkdir "/w/m" and die } ' + EXIT
    done = runner.run(["-e", code], mounts=[Mount(provider, "/w", writable=True)], artifacts=perl)
    assert done.exit_code == 0, done
    assert (len(done.files.refused), done.files.refused_total) == (1000, 1002)
    assert {r.method for r in done.files.refused} == {"mkdir"}


OWED = f"""(module
  (import "{P1}" "path_open"
    (func $open (param i32 i32 i32 i32 i32 i64 i64 i32 i32) (result i32)))
  (import "{P1}" "fd_write" (func $write (param i32 i32 i32 i32) (result i32)))
  (import "{P1}" "path_filestat_get" (func $stat (param i32 i32 i32 i32 i32) (result i32)))
  (import "{P1}" "fd_close" (func $close (param i32) (result i32)))
  (memory (export "memory") 1)
  (data (i32.const 100) "f.txt")
  (data (i32.const 200) "a.txt")
  (data (i32.const 220) "g.txt")
  (data (i32.const 300) "\\90\\01\\00\\00\\04\\00\\00\\00")
  (data (i32.const 400) "data")
  (func (export "run") (local $fd i32)
    (i32.store (i32.const 600) (call $open (i32.const 3) (i32.const 0) (i32.const 100)
      (i32.const 5) (i32.const 9) (i64.const {files.RIGHTS_ALL}) (i64.const 0) (i32.const 0)
      (i32.const 500)))
    (local.set $fd (i32.load (i32.const 500)))
    (i32.store (i32.const 604) (call $write (local.get $fd) (i32.const 300) (i32.const 1)
      (i32.const 504)))
    (i32.store (i32.const 608) (call $stat (i32.const 3) (i32.const 0) (i32.const 200)
      (i32.const 5) (i32.const 512)))
    (i32.store (i32.const 612) (call $close (local.get $fd)))
    (i32.store (i32.const 616) (call $open (i32.const 3) (i32.const 0) (i32.const 220)
      (i32.const 5) (i32.const 0) (i64.const {files.READ_RIGHTS}) (i64.const 0) (i32.const 0)
      (i32.const 580)))))"""


def test_a_close_stopped_by_a_cancel_is_owed(placeholder, monkeypatch) -> None:
    """Guards the reserved number, driven by the deadline, since wasi-perl has no cancel: a
    guest writes a provider file; a provider whose `stat` passes the call's deadline is called,
    and the file's `fd_close` follows with no function entry between: the close answers
    ETIMEDOUT, an open after it makes no call, the number stays reserved (not free, still
    counted), and the walk writes it back once, with nothing refused. With the file-call limit
    instead: the close answers EIO, no write is made, and the record names the file once, at
    "exit", for the limit."""
    from wasi_perl import RefusedWrite

    for case in ("deadline", "limit"):
        monkeypatch.setattr(_engines.TICKER, "count", 0)
        provider = MemoryProvider()
        if case == "deadline":

            def passes(path):
                _engines.TICKER.count = 1000
                return Stat("file")

            provider.answers[("stat", "/a.txt")] = passes
        m = Mounted(
            provider,
            placeholder,
            writable=True,
            wat=OWED,
            stop_at=1000,
            file_calls=2 if case == "limit" else None,
        )
        with m.entered():
            m.instance.exports(m.store)["run"](m.store)
        answers = struct.unpack("<5I", m.raw.get(600, 20))
        number = m.raw.u32(500)
        stopped = 73 if case == "deadline" else 29
        assert answers == (0, 0, 0, stopped, stopped), (case, answers)
        assert number not in m.table.table and number in m.table.owed, case
        assert number not in m.table.free and m.table.opened == 1, case
        assert provider.writes() == [b""] and provider.total() == 2, case
        m.table.walk()
        record = m.table.record()
        if case == "deadline":
            assert provider.writes() == [b"", b"data"]
            assert (record.refused, record.refused_total) == ((), 0)
        else:
            assert provider.writes() == [b""]
            assert record.refused == (
                RefusedWrite("/m", "write", ("/f.txt",), Errno.EIO, "exit", "limit"),
            )


def test_a_refused_rename_or_removal_moves_no_descriptor(placeholder) -> None:
    """Descriptors follow only a namespace call
    the provider accepted. A rename the provider refuses leaves a descriptor at its `src`
    writing back to `src` and one at its `dst` still writing back to `dst`; a refused `remove`
    unlinks nothing; a refused `rmdir` leaves a folder descriptor that still asks the provider."""
    provider = MemoryProvider()
    r = Mounted(provider, placeholder, writable=True).raw
    f, g = r.open(3, "a.txt", 0, ALL)[1], r.open(3, "b.txt", 0, ALL)[1]
    d = r.open(3, "d", files.O_DIRECTORY)[1]
    assert None not in (f, g, d)
    assert (r.write(f, b"A"), r.write(g, b"B")) == ((0, 1), (0, 1))
    provider.answers[("rename", "/a.txt")] = Errno.EACCES
    provider.answers[("remove", "/b.txt")] = Errno.EACCES
    provider.answers[("rmdir", "/d")] = Errno.EACCES
    assert r.rename(3, "a.txt", 3, "b.txt") == 2
    assert r.path_call("path_unlink_file", 3, "b.txt") == 2
    assert r.path_call("path_remove_directory", 3, "d") == 2
    reads = provider.calls["read"]
    assert r.open(d, "x.txt")[0] == 0 and provider.calls["read"] == reads + 1
    assert (r("fd_close", f), r("fd_close", g)) == (0, 0)
    assert provider.writes() == [b"Aello, mount\n", b"B\n"]
    assert [e[1] for e in provider.log if e[0] == "write"] == ["/a.txt", "/b.txt"]


GATED = f"""(module
  (import "{P1}" "path_open"
    (func $open (param i32 i32 i32 i32 i32 i64 i64 i32 i32) (result i32)))
  (import "{P1}" "path_filestat_get" (func $stat (param i32 i32 i32 i32 i32) (result i32)))
  (import "{P1}" "path_create_directory" (func $mkdir (param i32 i32 i32) (result i32)))
  (import "{P1}" "path_rename" (func $rename (param i32 i32 i32 i32 i32 i32) (result i32)))
  (memory (export "memory") 1)
  (data (i32.const 100) "new.txt")
  (data (i32.const 200) "a.txt")
  (data (i32.const 220) "newdir")
  (func (export "run")
    (i32.store (i32.const 600) (call $stat (i32.const 3) (i32.const 0) (i32.const 200)
      (i32.const 5) (i32.const 512)))
    (i32.store (i32.const 604) (call $mkdir (i32.const 3) (i32.const 220) (i32.const 6)))
    (i32.store (i32.const 608) (call $rename (i32.const 3) (i32.const 200) (i32.const 5)
      (i32.const 3) (i32.const 220) (i32.const 6)))
    (i32.store (i32.const 612) (call $open (i32.const 3) (i32.const 0) (i32.const 100)
      (i32.const 7) (i32.const 9) (i64.const {files.RIGHTS_ALL}) (i64.const 0) (i32.const 0)
      (i32.const 500)))))"""
PAST_THE_LIMIT = 'for (0 .. 49) {{ {call} }} print "after the loop\\n";'


def test_the_write_half_is_gated(placeholder, perl, monkeypatch) -> None:
    """A create at open and a
    namespace call are provider calls like any other. Past `max_file_calls`, `mkdir`s and
    writing opens stop at exactly the limit and the run ends "out of file calls"; once the
    call's deadline has passed (by a provider's `stat`, with no function entry after it), a
    `mkdir`, a `rename` and a create at open each answer ETIMEDOUT with no provider call."""
    for call in ('mkdir "/w/d$_"', 'open my $f, ">", "/w/f$_"'):
        provider = MemoryProvider({})
        done = runner.run(
            ["-e", PAST_THE_LIMIT.format(call=call)],
            mounts=[Mount(provider, "/w", writable=True)],
            max_file_calls=5,
            artifacts=perl,
        )
        assert (done.exit_code, done.trapped, done.stdout) == (None, "out of file calls", b"")
        assert provider.total() == 5 and done.files.calls == provider.calls, call
    monkeypatch.setattr(_engines.TICKER, "count", 0)
    provider = MemoryProvider()

    def passes(path):
        _engines.TICKER.count = 1000
        return Stat("file")

    provider.answers[("stat", "/a.txt")] = passes
    m = Mounted(provider, placeholder, writable=True, wat=GATED, stop_at=1000)
    with m.entered():
        m.instance.exports(m.store)["run"](m.store)
    assert struct.unpack("<4I", m.raw.get(600, 16)) == (0, 73, 73, 73)
    assert provider.total() == 1 and _written(provider) == dict.fromkeys(WRITE_HALF, 0)
    assert m.table.record().refused_total == 0  # an unmade call is not a refusal


def test_a_generator_listing_that_raises_answers_its_errno(placeholder) -> None:
    """Criterion 5: a `list` written as a generator runs its body while the runner iterates it,
    so an `OSError` raised there is the answer of its errno too, before or after a `yield`, and
    so is one an iterable's own `__iter__` raises; the listing is dropped whole, nothing is
    held, and any other exception stays a fault."""
    import errno

    def generator(make, after_one):
        def how(path):
            def listing():
                if after_one:
                    yield ("a", "file")
                raise make()

            return listing()

        return how

    class RaisingIterable:
        """Not a generator: its `__iter__` raises, so `iter()` runs provider code."""

        def __iter__(self):
            raise FileNotFoundError("x")

    cases = [
        (lambda path: RaisingIterable(), 44),
        (generator(lambda: FileNotFoundError(errno.ENOENT, "gone"), False), 44),
        (generator(lambda: FileNotFoundError(errno.ENOENT, "gone"), True), 44),
        (generator(lambda: PermissionError("no"), True), 2),
        (generator(lambda: ValueError("broke"), True), 29),
    ]
    for how, want in cases:
        provider = MemoryProvider()
        provider.answers[("list", "/d")] = how
        m = Mounted(provider, placeholder)
        staged = m.table.staged
        assert m.raw.open(3, "d", files.O_DIRECTORY) == (want, None)
        assert (m.table.staged, m.table.opened) == (staged, 0)
        assert provider.calls["list"] == 1
        record = m.table.record()
        assert len(record.faults) == (1 if want == 29 else 0), record.faults


def test_a_mapped_oserror_leaves_nothing_of_itself(placeholder) -> None:
    """Criterion 6: the runner holds no reference to a mapped exception (nor so to the
    provider's frame), and the record keeps neither its message nor its class's name."""
    import errno
    import gc
    import weakref

    class Local:
        pass

    class _SecretNotFound(FileNotFoundError):
        pass

    refs: list[weakref.ref] = []

    class Holding(MemoryProvider):
        def stat(self, path):
            self._count("stat", path)
            local = Local()
            refs.append(weakref.ref(local))
            e = _SecretNotFound(errno.ENOENT, "the-secret-message-061")
            refs.append(weakref.ref(e))
            raise e

    m = Mounted(Holding(), placeholder)
    assert m.raw.path_filestat(3, "a.txt")[0] == 44
    gc.collect()
    assert [r() for r in refs] == [None, None]
    record = repr(m.table.record())
    assert "the-secret-message-061" not in record and "_SecretNotFound" not in record, record

    # The listing's path: a raise from a generator `list`'s body.
    listed: list[weakref.ref] = []

    def listing(path):
        def body():
            e = _SecretNotFound(errno.ENOENT, "the-secret-message-061")
            listed.append(weakref.ref(e))
            yield ("a", "file")
            raise e

        return body()

    provider = MemoryProvider()
    provider.answers[("list", "/d")] = listing
    m = Mounted(provider, placeholder)
    assert m.raw.open(3, "d", files.O_DIRECTORY) == (44, None)
    gc.collect()
    assert [r() for r in listed] == [None]
    record = repr(m.table.record())
    assert "the-secret-message-061" not in record and "_SecretNotFound" not in record, record


# The errno table of a provider's OSError, as measured: macOS and Linux EXECUTED,
# Windows READ (CPython's errnomodule.c: the WSA values) and INFERRED (MSVC's small numbers).
HOST_SPELLINGS = ["a\\..\\..\\x", "C:x", "d\\x.txt", "d/C:", "e/..\\a.txt", "c:\\x"]


@pytest.mark.parametrize("writable", [False, True], ids=["read-only", "writable"])
def test_a_backslash_or_a_colon_in_a_component_is_refused(placeholder, writable) -> None:
    """A component with a backslash or a colon is EINVAL on every host, on a read-only and on a
    writable mount, for every path call, and the provider sees no call: a provider that joins
    the path onto a host folder never meets a name a host could reinterpret."""
    provider = MemoryProvider()
    r = Mounted(provider, placeholder, writable=writable).raw
    before = provider.total()
    rows = {}
    for path in HOST_SPELLINGS:
        rows[(path, "stat")] = r.path_filestat(3, path)[0]
        rows[(path, "open")] = r.open(3, path)[0]
        rows[(path, "open a folder")] = r.open(3, path, files.O_DIRECTORY)[0]
        rows[(path, "readlink")] = r.readlink(3, path)[0]
        if writable:
            rows[(path, "create")] = r.open(3, path, files.O_CREAT, ALL)[0]
            rows[(path, "mkdir")] = r.path_call("path_create_directory", 3, path)
            rows[(path, "remove")] = r.path_call("path_unlink_file", 3, path)
            rows[(path, "rmdir")] = r.path_call("path_remove_directory", 3, path)
            rows[(path, "rename from")] = r.rename(3, path, 3, "q")
            rows[(path, "rename to")] = r.rename(3, "a.txt", 3, path)
            rows[(path, "set-times")] = r.path_set_times(3, path, 0)
    assert rows == dict.fromkeys(rows, EINVAL), rows
    assert provider.total() == before == 0


def test_pread_writes_no_more_than_it_judged(placeholder) -> None:
    """`fd_pread` reads at most one chunk, as wasmtime's stream does on a grant: past the
    chunk it writes exactly the chunk's bytes, and the guest's buffer beyond is untouched."""
    chunk = _fs_memory.PREAD_CHUNK
    data = bytes(range(256)) * (chunk // 256 + 8)
    provider = MemoryProvider({"/big": data})
    r = Mounted(provider, placeholder).raw
    f = r.open(3, "big")[1]
    assert f is not None
    errno, n, buffer = r.pread(f, [chunk + 64], 0)
    assert (errno, n) == (0, chunk)
    assert buffer == data[:chunk] + bytes(64)


def test_a_folder_open_of_a_file_is_refused_before_any_listing(placeholder) -> None:
    """An `O_DIRECTORY` open asks `stat` first and answers ENOTDIR itself for a file, so a
    lenient provider that would list a file is never asked to; a missing name answers the
    provider's errno, again with no listing."""
    provider = MemoryProvider()
    provider.answers[("list", "/a.txt")] = []  # a lenient provider lists anything
    r = Mounted(provider, placeholder).raw
    rows = {
        "a file": r.open(3, "a.txt", files.O_DIRECTORY)[0],
        "a file named as a folder": r.open(3, "a.txt/", files.O_DIRECTORY)[0],
        "a missing name": r.open(3, "missing", files.O_DIRECTORY)[0],
    }
    assert rows == {"a file": 54, "a file named as a folder": 54, "a missing name": 44}, rows
    assert provider.calls["list"] == 0


def test_set_times_on_a_missing_path_answers_as_a_linux_grant(placeholder) -> None:
    """`path_filestat_set_times` on a writable mount answers as a Linux grant does: with a time
    flag set it asks the provider `stat` once and answers its errno for a missing path, and a
    path that is there succeeds with its times left to the provider; with no time flag it is
    nothing to do, 0 with no lookup, as Linux's `utimensat` answers before it reads the path."""
    provider = MemoryProvider()
    r = Mounted(provider, placeholder, writable=True).raw
    now = _fs_memory.ATIM_NOW | _fs_memory.MTIM_NOW
    rows = {
        "missing, a time flag": r.path_set_times(3, "missing", now),
        "there, a time flag": r.path_set_times(3, "a.txt", _fs_memory.MTIM),
    }
    assert rows == {"missing, a time flag": 44, "there, a time flag": 0}, rows
    assert provider.paths == [("stat", "/missing"), ("stat", "/a.txt")]


def test_set_times_with_no_time_flag_looks_nothing_up(placeholder) -> None:
    """With no time flag, set-times of a missing path answers 0 and the provider sees no call."""
    provider = MemoryProvider()
    r = Mounted(provider, placeholder, writable=True).raw
    assert r.path_set_times(3, "missing", 0) == 0
    assert provider.total() == 0


def test_set_times_on_a_file_named_as_a_folder_answers_enotdir(placeholder) -> None:
    """With a time flag, set-times of a file named with a trailing `/` answers ENOTDIR after its
    one `stat`, as a Linux grant does once it looks the path up; a folder so named succeeds."""
    provider = MemoryProvider()
    r = Mounted(provider, placeholder, writable=True).raw
    rows = {
        "a file as a folder": r.path_set_times(3, "a.txt/", _fs_memory.MTIM_NOW),
        "a folder as a folder": r.path_set_times(3, "d/", _fs_memory.MTIM_NOW),
    }
    assert rows == {"a file as a folder": 54, "a folder as a folder": 0}, rows
    assert provider.paths == [("stat", "/a.txt"), ("stat", "/d")]
