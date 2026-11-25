"""The image module: the module the runner writes from a reactor and an image of its memory.

Most checks run the writer over stand-in reactors written here in WAT, each the smallest module
of the form, or of a form the writer must refuse; two run the real reactor through the runner.
Nothing here is timed."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import sys

import pytest
import wasmtime as wt

from snapshot.images import call, image_set
from wasi_perl import ArtifactsMissing, SnapshotMismatch, _engines, _image_module, make_snapshot
from wasi_perl._image_module import BLOCK, PAGE

EXPORTS = """
  (func (export "_initialize"))
  (func (export "prepare") (result i32) (i32.const 0))
  (func (export "run"))
  (func (export "wasi_perl_open_files") (result i32) (i32.const 0))
  (func (export "wasi_perl_exit_status") (result i32) (i32.const -1))
"""


def standin(
    *, memory: str = "2", globals_: str = "", data: str = '(data (i32.const 16) "kept")',
    body: str = "", run: str = "",
) -> bytes:  # fmt: skip
    """A reactor of the image's form: one exported memory, the stack pointer, the six exports."""
    exports = EXPORTS if not run else EXPORTS.replace('(func (export "run"))', run)
    wat = f"""(module
  (memory (export "memory") {memory})
  (global $sp (mut i32) (i32.const 65536))
  {globals_}
  {exports}
  {body}
  {data})"""
    return wt.wat2wasm(wat)


def engine() -> wt.Engine:
    config = wt.Config()
    config.wasm_exceptions = True
    return wt.Engine(config)


def image_of(pages: int, blocks: dict[int, bytes]) -> bytes:
    image = bytearray(pages * PAGE)
    for b, fill in blocks.items():
        image[b * BLOCK : b * BLOCK + len(fill)] = fill
    return bytes(image)


IMAGE = image_of(4, {0: b"first", 1: b"\x01" * BLOCK, 5: b"x", 30: b"\xff" * 100, 63: b"end"})


def section(module: bytes, sid: int) -> list[bytes]:
    return [s.raw for s in _image_module.sections(module) if s.id == sid]


def exports(module: bytes) -> list[str]:
    return [e.name for e in wt.Module(engine(), module).exports]


def with_data_count(module: bytes) -> bytes:
    """`module` with a data-count section put before its code, as wasm-opt writes one."""
    out = bytearray(module[:8])
    n = _image_module.check_form(module).data_segments
    for s in _image_module.sections(module):
        if s.id == 10:
            out += bytes([12, 1, n])
        out += s.raw
    return bytes(out)


# 1


def test_the_module_keeps_the_code_and_takes_the_image() -> None:
    reactor = standin()
    written, stats = _image_module.write(reactor, IMAGE)
    assert section(written, 10) == section(reactor, 10)  # the code, byte for byte
    for sid in (1, 3, 6):  # types, functions, globals
        assert section(written, sid) == section(reactor, sid)
    mod = wt.Module(engine(), written)
    memory = next(e.type for e in mod.exports if e.name == "memory")
    assert memory.limits.min == len(IMAGE) // PAGE == stats["pages"] == 4
    assert sorted(exports(written)) == sorted(
        {"run", "wasi_perl_open_files", "wasi_perl_exit_status", "memory"}
    )
    assert _image_module.write(reactor, IMAGE)[0] == written  # the same bytes every time
    assert section(written, 0) == []  # no custom section


# 2


def test_an_instance_of_it_holds_the_image() -> None:
    written, stats = _image_module.write(standin(), IMAGE)
    assert stats["segments"] == 4  # blocks 0 and 1, then 5, 30 and 63, alone
    eng = engine()
    store = wt.Store(eng)
    instance = wt.Instance(store, wt.Module(eng, written), [])
    memory = instance.exports(store)["memory"]
    held = ctypes.string_at(memory.data_ptr(store), memory.data_len(store))
    assert held == IMAGE  # every byte, the zero blocks among them


# 3


@pytest.mark.parametrize(
    ("kind", "reactor"),
    [
        ("passive", lambda: standin(data='(data "passive")')),
        ("two mutable globals", lambda: standin(globals_="(global (mut i32) (i32.const 0))")),
        ("start", lambda: standin(body='(func $s) (start $s)')),
        ("an imported memory", lambda: wt.wat2wasm(
            '(module (import "m" "memory" (memory 1)) (global (mut i32) (i32.const 0))'
            + EXPORTS + ")"
        )),
    ],
)  # fmt: skip
def test_a_reactor_of_another_form_is_refused(kind, reactor) -> None:
    with pytest.raises(ArtifactsMissing):
        _image_module.write(reactor(), IMAGE)


@pytest.mark.parametrize(
    "instruction",
    [
        "(memory.init 0 (i32.const 0) (i32.const 0) (i32.const 1))",
        "(data.drop 0)",
    ],
)
def test_a_reactor_whose_code_indexes_a_segment_fails_to_compile(tmp_path, instruction) -> None:
    reactor = standin(run=f'(func (export "run") {instruction})')
    assert 12 in _image_module.section_ids(reactor)  # the instruction needs a data count
    path = tmp_path / "perl-reactor.wasm"
    path.write_bytes(reactor)
    digest = hashlib.sha256(reactor).hexdigest()
    image_sha = hashlib.sha256(IMAGE).hexdigest()
    with pytest.raises(ArtifactsMissing, match="data count section required"):
        _engines.image_module(False, path, digest, IMAGE, image_sha, None)


# 4


def test_a_data_count_alone_is_dropped_and_the_module_compiles() -> None:
    reactor = with_data_count(standin())
    assert 12 in _image_module.section_ids(reactor)
    written, _ = _image_module.write(reactor, IMAGE)
    assert 12 not in _image_module.section_ids(written)
    wt.Module(engine(), written)


def test_an_image_past_the_memory_maximum_is_a_mismatch() -> None:
    reactor = standin(memory="2 3")
    _image_module.write(reactor, image_of(3, {0: b"x"}))
    with pytest.raises(SnapshotMismatch, match="maximum"):
        _image_module.write(reactor, image_of(4, {0: b"x"}))


# 5. The segment cap.


RUNS = [(0, 1), (4, 5), (6, 7), (9, 10), (11, 12)]  # gaps of 3, 1, 2 and 1 blocks


def test_the_coalescer_closes_the_shortest_gaps_first() -> None:
    assert _image_module.coalesce(RUNS, 3) == [(0, 1), (4, 7), (9, 12)]
    assert _image_module.coalesce(RUNS, 4) == [(0, 1), (4, 7), (9, 10), (11, 12)]  # lower first
    assert _image_module.coalesce(RUNS, 1) == [(0, 12)]
    assert _image_module.coalesce(RUNS, 5) == RUNS
    assert _image_module.coalesce(RUNS, 6) == RUNS


def test_the_cap_is_half_of_wasmparsers_limit() -> None:
    assert _image_module.MAX_SEGMENTS <= 100_000 // 2


@pytest.mark.slow
def test_a_striped_image_is_written_at_the_cap() -> None:
    blocks = 100_002
    pages = -(-blocks * BLOCK // PAGE)
    image = bytearray(pages * PAGE)
    for b in range(0, blocks, 2):
        image[b * BLOCK] = 1
    image = bytes(image)
    assert len(_image_module.runs(image)) == 50_001
    written, stats = _image_module.write(standin(data=""), image)
    assert stats["segments"] == _image_module.MAX_SEGMENTS == 50_000
    eng = engine()
    store = wt.Store(eng)
    instance = wt.Instance(store, wt.Module(eng, written), [])
    memory = instance.exports(store)["memory"]
    assert ctypes.string_at(memory.data_ptr(store), memory.data_len(store)) == image


# 6 and 7: the real reactor.


@pytest.mark.slow
def test_twenty_calls_compile_the_image_once_and_write_no_memory(monkeypatch) -> None:
    snap = make_snapshot("our $n = 41;", artifacts=image_set(), cache_dir=False)
    writes = []
    real_memmove = ctypes.memmove

    def memmove(*args):
        writes.append("memmove")
        return real_memmove(*args)

    monkeypatch.setattr(ctypes, "memmove", memmove)
    monkeypatch.setattr(wt.Memory, "write", lambda *a, **k: writes.append("write"))
    _engines.forget()
    before = _engines.counts()
    for _ in range(20):
        done = call(["-e", 'print $n + 1, "\\n"'], snap)
        assert (done.exit_code, done.stdout, done.image_sha256) == (
            0,
            b"42\n",
            snap.origin.image_sha256,
        )
    after = _engines.counts()
    made = (after["compiled"] - before["compiled"]) + (after["loaded"] - before["loaded"])
    assert made == 1
    assert after["snapshots_restored"] - before["snapshots_restored"] == 20
    assert writes == []


CHILD = """
import json, sys
from wasi_perl import Artifacts, Snapshot, _engines, _image_module, run
written = []
real = _image_module.write
_image_module.write = lambda *a, **k: written.append(1) or real(*a, **k)
snap = Snapshot.load(sys.argv[1])
done = run(["-e", 'print $n + 1, "\\\\n"'], snapshot=snap, artifacts=Artifacts.at(sys.argv[2]))
print(json.dumps([done.stdout.decode(), written, _engines.counts()]))
"""


@pytest.mark.slow
def test_a_second_process_loads_the_cached_module_and_writes_none(tmp_path, private_cache) -> None:
    found = image_set()
    snap = make_snapshot("our $n = 6;", artifacts=found)  # into the session's private cache
    saved = tmp_path / "n.wpsnap"
    snap.save(saved)
    child = subprocess.run(
        [sys.executable, "-c", CHILD, str(saved), str(found.perl.parent)],
        capture_output=True,
        text=True,
        env={**os.environ, "WASI_PERL_CACHE": str(private_cache)},
    )
    assert child.returncode == 0, child.stderr[-2000:]
    stdout, written, counts = json.loads(child.stdout)
    assert stdout == "7\n"
    assert written == []
    assert (counts["loaded"], counts["compiled"]) == (1, 0)
