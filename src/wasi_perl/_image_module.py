"""The image module: a module the runner writes from the checked perl-reactor.wasm and an image of
its linear memory, so that a call from the image is one fresh instance whose memory the engine
maps from the module's own data, not a copy the host writes in.

`check_form` decodes every section of the reactor but the code, and refuses one that does not
have the form an image needs: one memory, defined and exported; every data segment active, in
memory 0, at an `i32.const` offset; exactly one mutable global, the stack pointer; no start
section; and exactly the reactor's six exports. `write` copies every section byte for byte but
four: the memory's initial size becomes the image's, the data section becomes the image's
non-zero 4 KiB blocks as active segments, the exports `_initialize` and `prepare` are dropped,
and the data-count and custom sections are dropped. The code is never decoded, and the bytes
written depend only on the reactor and the image.

Adapted from the research prototype of the preload image
(`context/research/2026-11-24-preload-image-prototype/image_module.py`), trimmed of its probe
export."""

from __future__ import annotations

import dataclasses

from wasi_perl._artifacts import ArtifactsMissing

PAGE = 65536
BLOCK = 4096
# wasmparser refuses a module with more than 100,000 data segments (MAX_WASM_DATA_SEGMENTS);
# the writer keeps to half of that.
MAX_SEGMENTS = 50_000
EXPORTS = frozenset(
    {"_initialize", "prepare", "run", "wasi_perl_open_files", "wasi_perl_exit_status", "memory"}
)
DROPPED_EXPORTS = frozenset({"_initialize", "prepare"})
MAGIC = b"\0asm\x01\0\0\0"
CUSTOM, IMPORT, MEMORY, GLOBAL, EXPORT, START, DATA, DATA_COUNT = 0, 2, 5, 6, 7, 8, 11, 12
NAMES = {
    0: "custom", 1: "type", 2: "import", 3: "function", 4: "table", 5: "memory", 6: "global",
    7: "export", 8: "start", 9: "element", 10: "code", 11: "data", 12: "data count", 13: "tag",
}  # fmt: skip


class FormError(ArtifactsMissing):
    """The reactor does not have the form an image module needs: a fault of the build."""


def _uleb(buf: bytes, at: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        b = buf[at]
        at += 1
        value |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return value, at


def _sleb(buf: bytes, at: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        b = buf[at]
        at += 1
        value |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            if b & 0x40:
                value -= 1 << shift
            return value, at


def _enc_uleb(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _enc_sleb(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if (n == 0 and not b & 0x40) or (n == -1 and b & 0x40):
            out.append(b)
            return bytes(out)
        out.append(b | 0x80)


@dataclasses.dataclass(frozen=True)
class Section:
    id: int
    raw: bytes  # the whole section: its id, its size and its payload
    payload: bytes


def sections(module: bytes) -> list[Section]:
    """The module's sections, in order."""
    if module[:8] != MAGIC:
        raise FormError("the reactor is not a WebAssembly module of version 1")
    out, at = [], 8
    try:
        while at < len(module):
            start = at
            sid = module[at]
            size, at = _uleb(module, at + 1)
            out.append(Section(sid, module[start : at + size], module[at : at + size]))
            at += size
    except IndexError:
        raise FormError("a section of the reactor runs past its end") from None
    if at != len(module):
        raise FormError("a section of the reactor runs past its end")
    return out


def section_ids(module: bytes) -> tuple[int, ...]:
    return tuple(s.id for s in sections(module))


def _name(buf: bytes, at: int) -> tuple[str, int]:
    n, at = _uleb(buf, at)
    return buf[at : at + n].decode(), at + n


def _limits(buf: bytes, at: int) -> tuple[int, int | None, int]:
    flags = buf[at]
    if flags not in (0, 1):
        raise FormError(
            f"the reactor's memory has limit flags {flags:#x}, not a 32-bit unshared one"
        )
    minimum, at = _uleb(buf, at + 1)
    maximum = None
    if flags & 1:
        maximum, at = _uleb(buf, at)
    return minimum, maximum, at


def _const_expr(buf: bytes, at: int) -> tuple[list[tuple[int, int]], int]:
    """A constant expression's instructions, as (opcode, immediate), through its end."""
    ops = []
    while True:
        op = buf[at]
        at += 1
        if op == 0x0B:
            return ops, at
        if op in (0x41, 0x42):  # i32.const, i64.const
            v, at = _sleb(buf, at)
        elif op == 0x43:
            v, at = 0, at + 4
        elif op == 0x44:
            v, at = 0, at + 8
        elif op in (0x23, 0xD2):  # global.get, ref.func
            v, at = _uleb(buf, at)
        elif op == 0xD0:  # ref.null t
            v, at = buf[at], at + 1
        elif op in (0x6A, 0x6B, 0x6C, 0x7C, 0x7D, 0x7E):  # extended-const arithmetic
            v = 0
        elif op == 0xFD:  # v128.const
            sub, at = _uleb(buf, at)
            if sub != 12:
                raise FormError(f"a constant expression of the reactor has SIMD opcode {sub}")
            v, at = 0, at + 16
        else:
            raise FormError(f"a constant expression of the reactor has opcode {op:#x}")
        ops.append((op, v))


def _valtype(buf: bytes, at: int) -> int:
    t = buf[at]
    if t in (0x63, 0x64):  # (ref null ht), (ref ht)
        _, at = _sleb(buf, at + 1)
        return at
    return at + 1


@dataclasses.dataclass(frozen=True)
class Form:
    """What `check_form` read of a reactor."""

    memory_min: int  # pages
    memory_max: int | None
    stack_global: int  # the index of the one mutable global
    stack_init: int
    exports: tuple[str, ...]
    data_segments: int
    has_data_count: bool


def _imports(payload: bytes) -> tuple[int, int]:
    """The imported memories and globals; FormError for an imported mutable global."""
    memories = globals_ = 0
    n, at = _uleb(payload, 0)
    for _ in range(n):
        _, at = _name(payload, at)
        _, at = _name(payload, at)
        kind = payload[at]
        at += 1
        if kind == 0:  # a function
            _, at = _uleb(payload, at)
        elif kind == 1:  # a table
            at = _valtype(payload, at)
            flags = payload[at]
            _, at = _uleb(payload, at + 1)
            if flags & 1:
                _, at = _uleb(payload, at)
        elif kind == 2:
            memories += 1
            _, _, at = _limits(payload, at)
        elif kind == 3:
            globals_ += 1
            at = _valtype(payload, at)
            if payload[at]:
                raise FormError("the reactor imports a mutable global")
            at += 1
        elif kind == 4:  # a tag
            _, at = _uleb(payload, at + 1)
        else:
            raise FormError(f"the reactor has an import of kind {kind}")
    return memories, globals_


def check_form(module: bytes) -> Form:
    """The form an image module needs, read from the reactor; FormError (an `ArtifactsMissing`)
    naming what differs."""
    secs = sections(module)
    by_id: dict[int, Section] = {}
    for s in secs:
        if s.id != CUSTOM:
            if s.id in by_id:
                raise FormError(f"the reactor has two {NAMES.get(s.id, s.id)} sections")
            by_id[s.id] = s
    try:
        return _form(by_id)
    except (IndexError, UnicodeDecodeError):
        raise FormError("a section of the reactor is cut short") from None


def _form(by_id: dict[int, Section]) -> Form:
    if START in by_id:
        raise FormError("the reactor has a start section")
    memories, imported_globals = _imports(by_id[IMPORT].payload) if IMPORT in by_id else (0, 0)
    if memories:
        raise FormError("the reactor imports its memory")
    if MEMORY not in by_id:
        raise FormError("the reactor defines no memory")
    p = by_id[MEMORY].payload
    n, at = _uleb(p, 0)
    if n != 1:
        raise FormError(f"the reactor defines {n} memories, not one")
    mem_min, mem_max, _ = _limits(p, at)
    mutable = []
    p = by_id[GLOBAL].payload if GLOBAL in by_id else b"\0"
    n, at = _uleb(p, 0)
    for i in range(n):
        at = _valtype(p, at)
        mut = p[at]
        ops, at = _const_expr(p, at + 1)
        if mut:
            mutable.append((imported_globals + i, ops))
    if len(mutable) != 1:
        raise FormError(f"the reactor has {len(mutable)} mutable globals, not one")
    sp_index, sp_ops = mutable[0]
    if len(sp_ops) != 1 or sp_ops[0][0] != 0x41:
        raise FormError("the reactor's mutable global does not start as an i32.const")
    exports = []
    p = by_id[EXPORT].payload if EXPORT in by_id else b"\0"
    n, at = _uleb(p, 0)
    for _ in range(n):
        name, at = _name(p, at)
        kind = p[at]
        index, at = _uleb(p, at + 1)
        exports.append(name)
        if name == "memory" and (kind != 2 or index != 0):
            raise FormError("the reactor's export memory is not its memory 0")
    if sorted(exports) != sorted(EXPORTS):
        raise FormError(f"the reactor's exports are {sorted(exports)}, not {sorted(EXPORTS)}")
    segments = 0
    if DATA in by_id:
        p = by_id[DATA].payload
        n, at = _uleb(p, 0)
        for _ in range(n):
            flag, at = _uleb(p, at)
            if flag == 2:
                memory, at = _uleb(p, at)
                if memory != 0:
                    raise FormError("a data segment of the reactor is in another memory")
            elif flag != 0:
                raise FormError(f"a data segment of the reactor is passive (flag {flag})")
            ops, at = _const_expr(p, at)
            if len(ops) != 1 or ops[0][0] != 0x41:
                raise FormError("a data segment of the reactor has an offset not an i32.const")
            size, at = _uleb(p, at)
            at += size
            segments += 1
    return Form(
        mem_min, mem_max, sp_index, sp_ops[0][1], tuple(exports), segments, DATA_COUNT in by_id
    )


def runs(image: bytes | memoryview, block: int = BLOCK) -> list[tuple[int, int]]:
    """Each longest stretch of blocks that hold a non-zero byte, as (first block, end block)."""
    out: list[tuple[int, int]] = []
    zero = bytes(block)
    view = memoryview(image)
    n = len(view) // block
    start = None
    for b in range(n):
        nonzero = view[b * block : (b + 1) * block] != zero
        if nonzero and start is None:
            start = b
        elif not nonzero and start is not None:
            out.append((start, b))
            start = None
    if start is not None:
        out.append((start, n))
    return out


def coalesce(rs: list[tuple[int, int]], cap: int) -> list[tuple[int, int]]:
    """Runs merged until at most `cap` are left: the shortest gap of zero blocks closes first,
    the one at the lower address first among gaps of one length. So exactly `len(rs) - cap` gaps
    close, the fewest zeros are written that such a merge can give, and the result depends only
    on the runs."""
    if len(rs) <= cap:
        return list(rs)
    gaps = sorted(range(len(rs) - 1), key=lambda i: (rs[i + 1][0] - rs[i][1], rs[i][1]))
    closed = set(gaps[: len(rs) - max(cap, 1)])
    out = []
    first = rs[0][0]
    for i in range(len(rs)):
        if i in closed:
            continue
        out.append((first, rs[i][1]))
        if i + 1 < len(rs):
            first = rs[i + 1][0]
    return out


def data_section(image: bytes | memoryview, cap: int = MAX_SEGMENTS) -> tuple[bytes, int, int]:
    """The data section's payload for `image`, the segments in it, and the bytes they hold."""
    view = memoryview(image)
    segs = coalesce(runs(view), cap)
    body = bytearray(_enc_uleb(len(segs)))
    held = 0
    for a, b in segs:
        start, end = a * BLOCK, b * BLOCK
        signed = start - (1 << 32) if start >= 1 << 31 else start
        body += b"\x00\x41" + _enc_sleb(signed) + b"\x0b" + _enc_uleb(end - start)
        body += view[start:end]
        held += end - start
    return bytes(body), len(segs), held


def _section(sid: int, payload: bytes) -> bytes:
    return bytes([sid]) + _enc_uleb(len(payload)) + payload


def write(reactor: bytes, image: bytes, cap: int = MAX_SEGMENTS) -> tuple[bytes, dict[str, int]]:
    """The image module of `image` over `reactor`, and what went into it: its pages, segments,
    the bytes they hold, and the module's size.

    Raises:
        FormError: The reactor does not have the form (an `ArtifactsMissing`).
        SnapshotMismatch: The image is not whole pages, or past the memory's maximum.
    """
    from wasi_perl._snapshot import SnapshotMismatch

    form = check_form(reactor)
    if len(image) % PAGE:
        raise SnapshotMismatch(f"the image of {len(image):,} bytes is not whole 64 KiB pages")
    pages = len(image) // PAGE
    if pages < form.memory_min:
        raise SnapshotMismatch(
            f"the image of {pages} pages is smaller than the reactor's memory of "
            f"{form.memory_min}: it was not made from this reactor"
        )
    if form.memory_max is not None and pages > form.memory_max:
        raise SnapshotMismatch(
            f"the image of {pages} pages is past the reactor's memory maximum of {form.memory_max}"
        )
    out = bytearray(MAGIC)
    stats: dict[str, int] = {}
    for s in sections(reactor):
        if s.id in (CUSTOM, DATA_COUNT):
            continue
        if s.id == MEMORY:
            flag = 0 if form.memory_max is None else 1
            payload = b"\x01" + bytes([flag]) + _enc_uleb(pages)
            if form.memory_max is not None:
                payload += _enc_uleb(form.memory_max)
            out += _section(MEMORY, payload)
        elif s.id == EXPORT:
            p = s.payload
            n, at = _uleb(p, 0)
            kept = []
            for _ in range(n):
                start = at
                name, at = _name(p, at)
                _, at = _uleb(p, at + 1)
                if name not in DROPPED_EXPORTS:
                    kept.append(p[start:at])
            out += _section(EXPORT, _enc_uleb(len(kept)) + b"".join(kept))
        elif s.id == DATA:
            payload, count, held = data_section(image, cap)
            stats.update(segments=count, data_bytes=held)
            out += _section(DATA, payload)
        else:
            out += s.raw
    if DATA not in section_ids(reactor):
        # A reactor with no data section still gets one, after the code, where it belongs.
        payload, count, held = data_section(image, cap)
        stats.update(segments=count, data_bytes=held)
        out += _section(DATA, payload)
    stats.update(pages=pages, module_bytes=len(out))
    return bytes(out), stats
