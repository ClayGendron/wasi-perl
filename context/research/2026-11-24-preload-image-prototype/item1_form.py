"""Research prototype, not shipped: perl-reactor.wasm against spec 022's Decision 1, step 3.

  uv run python context/research/2026-11-24-preload-image-prototype/item1_form.py <out.json>

For wasm-ld's output and for wasm-opt's: its sections, `image_module.check_form`, and the
number of `memory.init` and `data.drop` instructions binaryen's wasm-dis prints. Then an image
module written from wasm-opt's output and a one-page image, compiled. Then stand-ins written in
WAT: one each with a passive segment, two mutable globals and a start section, refused; one
whose code holds `memory.init` and one whose code holds `data.drop`, whose written modules
wasmtime refuses to compile; one with a data-count section and no such instruction, whose written
module compiles.
"""

from __future__ import annotations

import dataclasses
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as h  # noqa: E402
import image_module as im  # noqa: E402
import wasmtime as wt  # noqa: E402

from wasi_perl import _engines  # noqa: E402

WASM_DIS = h.ROOT / "build" / "tools" / "binaryen-version_133" / "bin" / "wasm-dis"
EXPORTS6 = """
  (func (export "_initialize")) (func (export "prepare") (result i32) i32.const 0)
  (func (export "run")) (func (export "wasi_perl_open_files") (result i32) i32.const 0)
  (func (export "wasi_perl_exit_status") (result i32) i32.const 0)
"""
STANDINS = {
    "passive_segment": f'(module (memory (export "memory") 1) (global (mut i32) (i32.const 1024)) {EXPORTS6} (data "x"))',
    "two_mutable_globals": f'(module (memory (export "memory") 1) (global (mut i32) (i32.const 1024)) (global (mut i32) (i32.const 0)) {EXPORTS6})',
    "start_section": f'(module (memory (export "memory") 1) (global (mut i32) (i32.const 1024)) {EXPORTS6} (func $s) (start $s))',
    "memory_init_in_code": f'(module (memory (export "memory") 1) (global (mut i32) (i32.const 1024)) {EXPORTS6} (func (memory.init 0 (i32.const 0) (i32.const 0) (i32.const 1))) (data (i32.const 0) "x"))',
    "data_drop_in_code": f'(module (memory (export "memory") 1) (global (mut i32) (i32.const 1024)) {EXPORTS6} (func (data.drop 0)) (data (i32.const 0) "x"))',
}


def dis_count(path: Path) -> dict[str, int]:
    text = subprocess.run(
        [str(WASM_DIS), str(path)], capture_output=True, text=True, check=True
    ).stdout
    return {
        "memory.init": len(re.findall(r"\(memory\.init\b", text)),
        "data.drop": len(re.findall(r"\(data\.drop\b", text)),
    }


def reactor_row(path: Path) -> dict:
    data = path.read_bytes()
    row: dict = {"path": str(path.relative_to(h.ROOT)), "bytes": len(data)}
    row["sections"] = [im.NAMES[s.id] for s in im.sections(data)]
    try:
        row["form"] = dataclasses.asdict(im.check_form(data))
        row["form"].pop("section_ids")
        row["form_ok"] = True
    except im.FormError as e:
        row["form_ok"], row["form_error"] = False, str(e)
    row["dis"] = dis_count(path)
    return row


def main(out: Path) -> None:
    result: dict = {
        "reactors": [reactor_row(h.PROTO / "perl-reactor.wasm"), reactor_row(h.REACTOR)]
    }
    result["command_for_comparison"] = reactor_row(h.ART / "perl.wasm")
    data, stats = im.write(h.REACTOR.read_bytes(), bytes(im.PAGE))
    wt.Module(_engines.engine(False), data)
    result["one_page_image_module"] = {
        **stats,
        "compiles": True,
        "sections": [im.NAMES[s.id] for s in im.sections(data)],
    }
    standins = {}
    for name, wat in STANDINS.items():
        mod = wt.wat2wasm(wat)
        # wat2wasm writes a data-count section only when the code needs one.
        row: dict = {"has_datacount": 12 in [s.id for s in im.sections(mod)]}
        try:
            written, _ = im.write(mod, bytes(im.PAGE))
        except im.FormError as e:
            row["refused"] = str(e)
        else:
            row["written_has_datacount"] = 12 in [s.id for s in im.sections(written)]
            try:
                wt.Module(_engines.engine(False), written)
                row["compiles"] = True
            except wt.WasmtimeError as e:
                row["compiles"] = False
                row["compile_error"] = [ln.strip() for ln in str(e).splitlines() if ln.strip()][-1]
        standins[name] = row
    # A stand-in with a data-count section and no segment instruction: binaryen keeps the section
    # when bulk memory is on, as it did over the reactor.
    plain = wt.wat2wasm(
        f'(module (memory (export "memory") 1) (global (mut i32) (i32.const 1024)) {EXPORTS6} (data (i32.const 0) "x"))'
    )
    secs = im.sections(plain)
    with_dc = bytearray(plain[:8])
    for s in secs:
        if s.id == 10:
            with_dc += im.section(12, im.enc_uleb(1))
        with_dc += s.raw
    written, _ = im.write(bytes(with_dc), bytes(im.PAGE))
    wt.Module(_engines.engine(False), written)
    standins["datacount_section_only"] = {
        "has_datacount": True,
        "written_has_datacount": 12 in [s.id for s in im.sections(written)],
        "compiles": True,
    }
    result["standins"] = standins
    out.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
