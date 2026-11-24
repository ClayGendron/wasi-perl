"""Shared pieces of study E: the data, the workloads, and a WASI call counter.

The counter shadows every WASI import of perl.wasm but proc_exit with a Python function that
records the call (its name, its descriptor, its path, the bytes asked and moved) and forwards it
to wasmtime's own WASI or, for a mount's number, serves it from the prototype's table
(`src/wasi_perl/_mounts.py`). It uses the runner's `OBSERVE` hook, which only the prototype has.

Run with the worktree's code first on the path, and WASI_PERL_ARTIFACTS set.
"""

from __future__ import annotations

import ctypes
import functools
import os
import random
import struct
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import wasmtime

from wasi_perl import _artifacts, runner
from wasi_perl._mounts import MemoryProvider, Mount, Stat

SIZES = {"row50k": 50_000, "row5m": 5_000_000}
SMALL_ROWS = 500


def make_data(seed: int = 7) -> dict[str, bytes]:
    """The rows, by path within the mount: a 50 KB log, a 5 MB CSV and 500 small notes."""
    rnd = random.Random(seed)
    files: dict[str, bytes] = {}
    lines, size, i = [], 0, 0
    while size < SIZES["row50k"]:
        level = "ERROR" if i % 50 == 0 else "INFO"
        line = f"2026-11-24T10:{i % 60:02d}:00 {level} worker-{i % 16} req={rnd.randrange(10**6)}\n"
        lines.append(line)
        size += len(line)
        i += 1
    files["/logs/app.log"] = "".join(lines).encode()
    lines, size, i = ["id,name,amount,region\n"], 0, 0
    while size < SIZES["row5m"]:
        line = f"{i},name{i % 997},{rnd.randrange(10000)},r{i % 7}\n"
        lines.append(line)
        size += len(line)
        i += 1
    files["/data/sales.csv"] = "".join(lines).encode()
    for k in range(SMALL_ROWS):
        body = "".join(
            f"note {k} line {j} {'needle' if (k * 7 + j) % 41 == 0 else 'hay'}\n" for j in range(12)
        )
        files[f"/notes/n{k:03d}.txt"] = body.encode()
    return files


def write_tree(files: dict[str, bytes], root: Path) -> None:
    for path, data in files.items():
        target = root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


@dataclass(frozen=True)
class Workload:
    name: str
    argv: tuple[str, ...]
    writable: bool = False


def workloads(root: str) -> list[Workload]:
    """The vfs-shaped programs, over the files at the guest folder `root`."""
    notes = [f"{root}/notes/n{k:03d}.txt" for k in range(SMALL_ROWS)]
    return [
        Workload("hello", ("-e", "1")),
        Workload("ne_50k", ("-ne", "print if /ERROR/", f"{root}/logs/app.log")),
        Workload(
            "lane_5m",
            ("-F,", "-lane", "$s += $F[2]; END { print $s }", f"{root}/data/sales.csv"),
        ),
        Workload("slurp_5m", ("-0777", "-ne", "print length, qq{\\n}", f"{root}/data/sales.csv")),
        Workload("pi_50k", ("-pi", "-e", "s/ERROR/WARN/g", f"{root}/logs/app.log"), True),
        Workload("grep_args_500", ("-ne", 'print "$ARGV:$_" if /needle/', *notes)),
        Workload(
            "grep_glob_500",
            (
                "-e",
                f'for my $p (glob "{root}/notes/*.txt") {{ open my $f, "<", $p or die "$p: $!";'
                ' while (<$f>) { print "$p:$_" if /needle/ } }',
            ),
        ),
        Workload(
            "grep_opendir_500",
            (
                "-e",
                f'opendir my $d, "{root}/notes" or die $!; for my $n (sort grep {{ /\\.txt$/ }}'
                f' readdir $d) {{ my $p = "{root}/notes/$n"; open my $f, "<", $p or die $!;'
                ' while (<$f>) { print "$p:$_" if /needle/ } }',
            ),
        ),
        Workload(
            "write_new",
            (
                "-e",
                f'open my $o, ">", "{root}/data/report.txt" or die $!;'
                ' print $o "line $_ ", "x" x 60, "\\n" for 1 .. 2000; close $o or die $!',
            ),
            True,
        ),
    ]


@functools.lru_cache(maxsize=1)
def signatures() -> dict[str, tuple[str, ...]]:
    """Every WASI import of perl.wasm but proc_exit, with its parameter kinds."""
    found = _artifacts.checked()[0]
    module = wasmtime.Module.from_file(wasmtime.Engine(), str(found.perl))
    out: dict[str, tuple[str, ...]] = {}
    for imp in module.imports:
        if imp.module != "wasi_snapshot_preview1" or imp.name == "proc_exit":
            continue
        ty = imp.type
        out[imp.name] = tuple(str(p) for p in ty.params)
    return out


@dataclass
class Record:
    """One run's calls, in order: (name, fd, path, asked, moved, errno)."""

    events: list[tuple[str, int, str, int, int, int]] = field(default_factory=list)

    def counts(self) -> Counter[str]:
        return Counter(e[0] for e in self.events)

    def moved(self) -> Counter[str]:
        out: Counter[str] = Counter()
        for e in self.events:
            out[e[0]] += e[4]
        return out


def _string(caller: Any, ptr: int, length: int) -> str:
    memory = caller.get("memory")
    base = ctypes.cast(memory.data_ptr(caller), ctypes.c_void_p).value
    return ctypes.string_at(base + ptr, length).decode("utf-8", "replace")


def _u32(caller: Any, ptr: int) -> int:
    memory = caller.get("memory")
    base = ctypes.cast(memory.data_ptr(caller), ctypes.c_void_p).value
    return int(struct.unpack("<I", ctypes.string_at(base + ptr, 4))[0])


def _asked(caller: Any, iovs: int, count: int) -> int:
    memory = caller.get("memory")
    base = ctypes.cast(memory.data_ptr(caller), ctypes.c_void_p).value
    raw = ctypes.string_at(base + iovs, 8 * count)
    return sum(size for _, size in struct.iter_unpack("<II", raw))


class Observer:
    """The runner's OBSERVE hook: records every call into `self.record`."""

    def __init__(self) -> None:
        self.record = Record()

    def __call__(self, name: str, caller: Any, args: tuple[int, ...], serve: Any) -> int:
        fd = args[0] & 0xFFFFFFFF if args and name not in ("args_get", "environ_get") else -1
        path, asked = "", 0
        if name in ("path_open", "path_filestat_get"):
            path = _string(caller, args[2], args[3])
        elif name in ("path_unlink_file", "path_create_directory", "path_remove_directory"):
            path = _string(caller, args[1], args[2])
        elif name == "path_rename":
            path = _string(caller, args[1], args[2]) + " -> " + _string(caller, args[4], args[5])
        elif name in ("fd_read", "fd_write", "fd_pread", "fd_pwrite"):
            asked = _asked(caller, args[1], args[2])
        elif name == "fd_readdir":
            asked = args[2]
        errno = serve()
        moved = 0
        if errno == 0:
            if name in ("fd_read", "fd_write"):
                moved = _u32(caller, args[3])
            elif name in ("fd_pread", "fd_pwrite"):
                moved = _u32(caller, args[4])
            elif name == "fd_readdir":
                moved = _u32(caller, args[4])
        self.record.events.append((name, fd, path, asked, moved, errno))
        return errno


def observed(argv: tuple[str, ...], **kw: Any) -> tuple[Any, Record]:
    obs = Observer()
    runner.OBSERVE = (signatures(), obs)
    try:
        done = runner.run(argv, **kw)
    finally:
        runner.OBSERVE = None
    return done, obs.record


def mount_for(files: dict[str, bytes], guest: str, writable: bool, **kw: Any) -> Mount:
    return Mount(MemoryProvider(files, kw.pop("delay_s", 0.0)), guest, writable=writable, **kw)


ARTIFACTS = os.environ.get("WASI_PERL_ARTIFACTS", "")


class RoundTrips:
    """Design (e): the provider-side cache vfs plans, over a MemoryProvider, counting database
    round trips rather than protocol calls. Bodies are kept by path for the call (vfs keys them
    by entry and version). Two warmings, each one round trip: `prefetch(paths)`, one batched
    read of the paths the embedder sees in argv before the call (vfs's `read(observations=)`),
    and a `list` that brings the bodies of entries up to `small` bytes in the same query. A
    `stat` or `read` the cache can answer costs nothing; a write costs one trip and updates it."""

    def __init__(self, files: dict[str, bytes], small: int = 64 << 10) -> None:
        self.inner = MemoryProvider(files)
        self.small = small
        self.bodies: dict[str, bytes] = {}
        self.trips = 0

    def prefetch(self, paths: list[str]) -> None:
        if not paths:
            return
        self.trips += 1
        for p in paths:
            got = self.inner.read(p)
            if isinstance(got, bytes):
                self.bodies[p] = got

    def stat(self, path: str):  # type: ignore[no-untyped-def]
        if path in self.bodies:
            return Stat("file", len(self.bodies[path]))
        self.trips += 1
        return self.inner.stat(path)

    def list(self, path: str):  # type: ignore[no-untyped-def]
        self.trips += 1
        got = self.inner.list(path)
        if isinstance(got, list):
            prefix = path.rstrip("/") + "/"
            for name, kind in got:
                body = self.inner.files.get(prefix + name)
                if kind == "file" and body is not None and len(body) <= self.small:
                    self.bodies[prefix + name] = body
        return got

    def read(self, path: str):  # type: ignore[no-untyped-def]
        if path in self.bodies:
            return self.bodies[path]
        self.trips += 1
        got = self.inner.read(path)
        if isinstance(got, bytes):
            self.bodies[path] = got
        return got

    def _write_half(self, method: str, *args: Any):  # type: ignore[no-untyped-def]
        self.trips += 1
        for p in args[:1] if method == "write" else args[:2]:
            if isinstance(p, str):
                self.bodies.pop(p, None)
        return getattr(self.inner, method)(*args)

    def write(self, path: str, data: bytes):  # type: ignore[no-untyped-def]
        return self._write_half("write", path, data)

    def mkdir(self, path: str):  # type: ignore[no-untyped-def]
        return self._write_half("mkdir", path)

    def remove(self, path: str):  # type: ignore[no-untyped-def]
        return self._write_half("remove", path)

    def rmdir(self, path: str):  # type: ignore[no-untyped-def]
        return self._write_half("rmdir", path)

    def rename(self, src: str, dst: str):  # type: ignore[no-untyped-def]
        return self._write_half("rename", src, dst)
