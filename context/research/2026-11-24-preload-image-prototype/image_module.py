"""Research prototype, not shipped: the image module of spec 022, Decision 1.

It walks a reactor's section headers, checks the form the image needs, and writes a module from
those sections in which the memory's initial size is the image's, the data section is the image
as active segments, the data-count section, the custom sections and the exports `_initialize`
and `prepare` are gone, and every other section is the reactor's, byte for byte. The code is
never decoded.

Also here, for the prototype's own checks: `with_global_export`, the reactor with one more
export naming its mutable global, so a host can read the stack pointer after `prepare`.
"""

from __future__ import annotations

import dataclasses

PAGE = 65536
BLOCK = 4096
MAX_SEGMENTS = 50_000  # half of wasmparser's MAX_WASM_DATA_SEGMENTS (100,000)
EXPORTS = {
    "_initialize",
    "prepare",
    "run",
    "wasi_perl_open_files",
    "wasi_perl_exit_status",
    "memory",
}
DROPPED_EXPORTS = {"_initialize", "prepare"}

NAMES = {
    0: "custom",
    1: "type",
    2: "import",
    3: "function",
    4: "table",
    5: "memory",
    6: "global",
    7: "export",
    8: "start",
    9: "element",
    10: "code",
    11: "data",
    12: "datacount",
    13: "tag",
}


class FormError(ValueError):
    """The reactor does not have the form an image module needs."""


def uleb(buf: bytes, at: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        b = buf[at]
        at += 1
        value |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return value, at


def sleb(buf: bytes, at: int) -> tuple[int, int]:
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


def enc_uleb(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def enc_sleb(n: int) -> bytes:
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
    raw: bytes  # the whole section: id, size and payload
    payload: bytes


def sections(module: bytes) -> list[Section]:
    if module[:8] != b"\0asm\x01\0\0\0":
        raise FormError("not a WebAssembly module, version 1")
    out, at = [], 8
    while at < len(module):
        start = at
        sid = module[at]
        size, at = uleb(module, at + 1)
        out.append(Section(sid, module[start : at + size], module[at : at + size]))
        at += size
    if at != len(module):
        raise FormError("a section runs past the end of the module")
    return out


def name(buf: bytes, at: int) -> tuple[str, int]:
    n, at = uleb(buf, at)
    return buf[at : at + n].decode(), at + n


def limits(buf: bytes, at: int) -> tuple[int, int, int | None, int]:
    flags = buf[at]
    if flags not in (0, 1):
        raise FormError(f"memory limits flags {flags:#x}: only a 32-bit, unshared memory is taken")
    minimum, at = uleb(buf, at + 1)
    maximum = None
    if flags & 1:
        maximum, at = uleb(buf, at)
    return flags, minimum, maximum, at


def const_expr(buf: bytes, at: int) -> tuple[list[tuple[int, int]], int]:
    """A constant expression's instructions, as (opcode, immediate), up to its end."""
    ops = []
    while True:
        op = buf[at]
        at += 1
        if op == 0x0B:
            return ops, at
        if op == 0x41:  # i32.const
            v, at = sleb(buf, at)
        elif op == 0x42:  # i64.const
            v, at = sleb(buf, at)
        elif op == 0x43:
            v, at = 0, at + 4
        elif op == 0x44:
            v, at = 0, at + 8
        elif op in (0x23, 0xD2):  # global.get, ref.func
            v, at = uleb(buf, at)
        elif op == 0xD0:  # ref.null t
            v, at = buf[at], at + 1
        elif op in (0x6A, 0x6B, 0x6C, 0x7C, 0x7D, 0x7E):  # extended-const arithmetic
            v = 0
        elif op == 0xFD:  # v128.const
            sub, at = uleb(buf, at)
            if sub != 12:
                raise FormError(f"constant expression has SIMD opcode {sub}")
            v, at = 0, at + 16
        else:
            raise FormError(f"constant expression has opcode {op:#x}")
        ops.append((op, v))


def valtype(buf: bytes, at: int) -> int:
    t = buf[at]
    if t in (0x63, 0x64):  # (ref null ht) / (ref ht)
        _, at = sleb(buf, at + 1)
        return at
    return at + 1


@dataclasses.dataclass(frozen=True)
class Form:
    memory_min: int
    memory_max: int | None
    stack_global: int  # index of the one mutable global
    stack_init: int
    exports: tuple[str, ...]
    data_segments: int
    has_datacount: bool
    section_ids: tuple[int, ...]


def check_form(module: bytes) -> Form:
    """Spec 022, Decision 1 step 3: what the reactor must be, or FormError."""
    secs = sections(module)
    by_id: dict[int, Section] = {}
    for s in secs:
        if s.id != 0:
            if s.id in by_id:
                raise FormError(f"two {NAMES.get(s.id, s.id)} sections")
            by_id[s.id] = s
    if 8 in by_id:
        raise FormError("the reactor has a start section")
    imported_globals = imported_memories = 0
    if 2 in by_id:
        p = by_id[2].payload
        n, at = uleb(p, 0)
        for _ in range(n):
            _, at = name(p, at)
            _, at = name(p, at)
            kind = p[at]
            at += 1
            if kind == 0:
                _, at = uleb(p, at)
            elif kind == 1:
                at = valtype(p, at)
                fl = p[at]
                _, at = uleb(p, at + 1)
                if fl & 1:
                    _, at = uleb(p, at)
            elif kind == 2:
                imported_memories += 1
                _, _, _, at = limits(p, at)
            elif kind == 3:
                imported_globals += 1
                at = valtype(p, at)
                if p[at]:
                    raise FormError("the reactor imports a mutable global")
                at += 1
            elif kind == 4:
                _, at = uleb(p, at + 1)
            else:
                raise FormError(f"import kind {kind}")
    if imported_memories:
        raise FormError("the reactor imports its memory")
    if 5 not in by_id:
        raise FormError("the reactor defines no memory")
    p = by_id[5].payload
    n, at = uleb(p, 0)
    if n != 1:
        raise FormError(f"the reactor defines {n} memories, not one")
    _, mem_min, mem_max, _ = limits(p, at)
    mutable = []
    p = by_id[6].payload if 6 in by_id else b"\0"
    n, at = uleb(p, 0)
    for i in range(n):
        at = valtype(p, at)
        mut = p[at]
        ops, at = const_expr(p, at + 1)
        if mut:
            mutable.append((imported_globals + i, ops))
    if len(mutable) != 1:
        raise FormError(f"the reactor has {len(mutable)} mutable globals, not one")
    (sp_index, sp_ops) = mutable[0]
    if len(sp_ops) != 1 or sp_ops[0][0] != 0x41:
        raise FormError("the mutable global's first value is not an i32.const")
    exports = []
    p = by_id[7].payload
    n, at = uleb(p, 0)
    for _ in range(n):
        nm, at = name(p, at)
        kind = p[at]
        idx, at = uleb(p, at + 1)
        exports.append(nm)
        if nm == "memory" and (kind != 2 or idx != 0):
            raise FormError("the export memory is not memory 0")
    if sorted(exports) != sorted(EXPORTS):
        raise FormError(f"the exports are {sorted(exports)}")
    segs = 0
    if 11 in by_id:
        p = by_id[11].payload
        n, at = uleb(p, 0)
        for _ in range(n):
            flag, at = uleb(p, at)
            if flag == 2:
                mem, at = uleb(p, at)
                if mem != 0:
                    raise FormError("a data segment is in another memory")
            elif flag != 0:
                raise FormError(f"a data segment is passive (flag {flag})")
            ops, at = const_expr(p, at)
            if len(ops) != 1 or ops[0][0] != 0x41:
                raise FormError("a data segment's offset is not an i32.const")
            size, at = uleb(p, at)
            at += size
            segs += 1
    return Form(
        mem_min,
        mem_max,
        sp_index,
        sp_ops[0][1],
        tuple(exports),
        segs,
        12 in by_id,
        tuple(s.id for s in secs),
    )


def runs(image: bytes, block: int = BLOCK) -> list[tuple[int, int]]:
    """Each longest stretch of blocks that hold a non-zero byte, as (first block, end block)."""
    out: list[tuple[int, int]] = []
    zero = bytes(block)
    n = len(image) // block
    view = memoryview(image)
    start = None
    for b in range(n):
        nz = view[b * block : (b + 1) * block] != zero
        if nz and start is None:
            start = b
        elif not nz and start is not None:
            out.append((start, b))
            start = None
    if start is not None:
        out.append((start, n))
    return out


def coalesce(rs: list[tuple[int, int]], cap: int) -> list[tuple[int, int]]:
    """Close the shortest gaps, the lower first among equals, until at most `cap` runs stay."""
    if len(rs) <= cap:
        return list(rs)
    gaps = sorted(range(len(rs) - 1), key=lambda i: (rs[i + 1][0] - rs[i][1], rs[i][1]))
    closed = set(gaps[: len(rs) - cap])
    out = []
    first = rs[0][0]
    for i in range(len(rs)):
        if i in closed:
            continue
        out.append((first, rs[i][1]))
        if i + 1 < len(rs):
            first = rs[i + 1][0]
    return out


def data_section(image: bytes, cap: int = MAX_SEGMENTS) -> tuple[bytes, int, int]:
    """The data section's payload for `image`; the segments; the bytes they hold."""
    segs = coalesce(runs(image), cap)
    body = bytearray(enc_uleb(len(segs)))
    held = 0
    for a, b in segs:
        off, end = a * BLOCK, b * BLOCK
        signed = off - (1 << 32) if off >= 1 << 31 else off
        body += b"\x00\x41" + enc_sleb(signed) + b"\x0b" + enc_uleb(end - off)
        body += image[off:end]
        held += end - off
    return bytes(body), len(segs), held


def section(sid: int, payload: bytes) -> bytes:
    return bytes([sid]) + enc_uleb(len(payload)) + payload


def write(reactor: bytes, image: bytes, cap: int = MAX_SEGMENTS) -> tuple[bytes, dict]:
    """The image module, and what went into it."""
    form = check_form(reactor)
    if len(image) % PAGE:
        raise ValueError("the image is not whole pages")
    pages = len(image) // PAGE
    if form.memory_max is not None and pages > form.memory_max:
        raise ValueError("the image exceeds the memory's maximum")
    out = bytearray(b"\0asm\x01\0\0\0")
    stats: dict = {}
    for s in sections(reactor):
        if s.id in (0, 12):
            continue
        if s.id == 5:
            flag = 1 if form.memory_max is not None else 0
            payload = b"\x01" + bytes([flag]) + enc_uleb(pages)
            if form.memory_max is not None:
                payload += enc_uleb(form.memory_max)
            out += section(5, payload)
        elif s.id == 7:
            p = s.payload
            n, at = uleb(p, 0)
            kept = []
            for _ in range(n):
                start = at
                nm, at = name(p, at)
                _, at = uleb(p, at + 1)
                if nm not in DROPPED_EXPORTS:
                    kept.append(p[start:at])
            out += section(7, enc_uleb(len(kept)) + b"".join(kept))
        elif s.id == 11:
            payload, nsegs, held = data_section(image, cap)
            stats.update(segments=nsegs, data_bytes=held)
            out += section(11, payload)
        else:
            out += s.raw
    stats.update(pages=pages, module_bytes=len(out))
    return bytes(out), stats


def with_global_export(reactor: bytes, index: int, export: str = "proto_stack_pointer") -> bytes:
    """The reactor with one more export, of global `index`: for the prototype's check of the
    stack pointer after prepare. The code is the reactor's."""
    out = bytearray(reactor[:8])
    for s in sections(reactor):
        if s.id == 7:
            n, at = uleb(s.payload, 0)
            extra = enc_uleb(len(export)) + export.encode() + b"\x03" + enc_uleb(index)
            out += section(7, enc_uleb(n + 1) + s.payload[at:] + extra)
        else:
            out += s.raw
    return bytes(out)
