"""Every call this package makes into wasmtime-py's private API, in one place: the C functions
it binds but does not wrap. Each is wasmtime 51's signature; `_engines.wasmtime()` refuses any
other major version before one runs. Adapted from wasi-python's `_wasmtime_ffi.py` (same owner,
Apache-2.0), trimmed to the two calls this runner needs."""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from typing import Any


def set_stdin_bytes(wasi: Any, data: bytes) -> None:
    """The guest's stdin as bytes, through `wasi_config_set_stdin_bytes`: no file holds it. The
    vector is handed over, and wasmtime frees it."""
    from wasmtime import _ffi as ffi

    vec = ffi.wasm_byte_vec_t()
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    ffi.wasm_byte_vec_new(ctypes.byref(vec), len(data), buf)
    ffi.wasi_config_set_stdin_bytes(wasi.ptr(), ctypes.byref(vec))


def error(text: str) -> int:
    """A new wasmtime error naming `text`, as the address an epoch callback returns to stop."""
    from wasmtime import _ffi as ffi

    err = ffi.wasmtime_error_new(text.encode())
    return ctypes.cast(err, ctypes.c_void_p).value or 0


def on_epoch(store: Any, fn: Callable[[Any, Any], int]) -> list[Any]:
    """`fn(delta, kind)` called at each of `store`'s epoch deadlines, through
    `wasmtime_store_epoch_deadline_callback`: it returns 0 to go on, having set `delta[0]` to the
    ticks until the next and `kind[0]` to 0 (continue), or an `error()` to stop. Returns the C
    callbacks, which the caller keeps alive as long as the store."""
    from wasmtime import _bindings

    binding = _bindings._wasmtime_store_epoch_deadline_callback
    callback_type, finalizer_type = binding.argtypes[1], binding.argtypes[3]
    callback = callback_type(lambda _context, _data, delta, kind: fn(delta, kind))
    finalizer = finalizer_type(lambda _data: None)
    binding(store.ptr(), callback, None, finalizer)
    return [callback, finalizer]
