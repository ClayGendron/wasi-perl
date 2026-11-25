"""Every call this package makes into wasmtime-py's private API, in one place: the C functions
it binds but does not wrap, the store context and the raw function handle. The runner and the
mounts name none of that themselves. Each is wasmtime 51's signature; `_engines.wasmtime()`
refuses any other major version before one runs.

At import, `check` asserts that every private name in `NEEDED` exists, and raises one
`ImportError` naming the wasmtime-py version, the names it lacks and the pin when one does not.
`_engines.wasmtime()` imports this module after wasmtime-py itself, so a moved symbol is seen at
the first `run()`, before any engine, store or instance exists; importing `wasi_perl` loads
neither.

Adapted from wasi-python's `_wasmtime_ffi.py` at f9bf678 (same owner, Apache-2.0). Kept:
`PIN`, `NEEDED`, `version`, `missing`, `check` and its call, `context_of`, `context_address`,
`set_epoch_deadline`, `get_fuel`, `set_fuel`, `raw_func`, `PreparedCall` and
`set_custom_outputs`, the last given one write and a NULL free (the module docstring of
`_outputs` says why). Trimmed: the rest (the async stack, the epoch callback's shared object,
the judge's readers), which wasi-perl does not call. `NEEDED` is cut to the private names this
module reaches, and holds the three wasi-perl had before: `set_stdin_bytes`, `error` and
`on_epoch`.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, cast

from wasmtime import (
    Caller,
    Func,
    Store,
    Trap,
    WasiConfig,
    WasmtimeError,
    _bindings,
    _ffi,
)

from wasi_perl import _outputs

__all__ = [
    "NEEDED",
    "PIN",
    "Context",
    "PreparedCall",
    "Storelike",
    "check",
    "context_address",
    "context_of",
    "error",
    "get_fuel",
    "missing",
    "on_epoch",
    "raw_func",
    "set_epoch_deadline",
    "set_fuel",
    "set_custom_outputs",
    "set_stdin_bytes",
    "version",
]

PIN = "wasmtime>=51,<52"

# The private names this module reads, as (holder, name) pairs: the holder is the dotted name of
# the module or class, as the table given to `check` spells it.
NEEDED: tuple[tuple[str, str], ...] = (
    ("wasmtime._ffi", "wasmtime_context_set_epoch_deadline"),
    ("wasmtime._ffi", "wasmtime_error_new"),
    ("wasmtime._ffi", "wasmtime_func_call"),
    ("wasmtime._ffi", "wasmtime_context_get_fuel"),
    ("wasmtime._ffi", "wasmtime_context_set_fuel"),
    ("wasmtime._ffi", "wasmtime_val_t"),
    ("wasmtime._ffi", "WASMTIME_I32"),
    ("wasmtime._ffi", "WASMTIME_I64"),
    ("wasmtime._ffi", "wasm_trap_t"),
    ("wasmtime._ffi", "wasmtime_context_t"),
    ("wasmtime._ffi", "wasm_byte_vec_t"),
    ("wasmtime._ffi", "wasm_byte_vec_new"),
    ("wasmtime._ffi", "wasi_config_set_stdin_bytes"),
    ("wasmtime._ffi", "wasi_config_set_stdout_custom"),
    ("wasmtime._ffi", "wasi_config_set_stderr_custom"),
    ("wasmtime._bindings", "_wasmtime_store_epoch_deadline_callback"),
    ("wasmtime._bindings", "_wasi_config_set_stdout_custom"),
    ("wasmtime.Trap", "_from_ptr"),
    ("wasmtime.WasmtimeError", "_from_ptr"),
    ("wasmtime.Caller", "_context"),
    ("wasmtime.Store", "_context"),
    ("wasmtime.Store", "ptr"),
    ("wasmtime.WasiConfig", "ptr"),
    ("wasmtime.Func", "_func"),
)

_RECORDS = "wasmtime._bindings"  # its names are binding records, read for their argtypes
_HANDLE = ("wasmtime.Func", "_func")  # an instance attribute, read as a class annotation


def version() -> str:
    """The installed wasmtime-py's version, from its metadata, read only for a refusal: the
    import of `importlib.metadata` is one of the slowest of a process's first call."""
    from importlib.metadata import version as _installed

    return _installed("wasmtime")


def _has(holder: object, name: str, where: str) -> bool:
    if (where, name) == _HANDLE:
        return name in getattr(holder, "__annotations__", {})
    if not hasattr(holder, name):
        return False
    if where == _RECORDS:
        # Four arguments, the second a callback and the fourth its free: C function types, which
        # this module calls at import to make the pointers it passes.
        kinds = list(getattr(getattr(holder, name), "argtypes", None) or ())
        return len(kinds) == 4 and all(_is_function_type(kinds[i]) for i in (1, 3))
    return True


def _is_function_type(kind: object) -> bool:
    return isinstance(kind, type) and issubclass(kind, ctypes._CFuncPtr)


def missing(holders: dict[str, object]) -> list[str]:
    """The dotted names of `NEEDED` absent from `holders`, in `NEEDED`'s order; every name of a
    holder that is `None` or not in the table is absent. Pure: reads, never raises."""
    absent = []
    for where, name in NEEDED:
        holder = holders.get(where)
        if holder is None or not _has(holder, name, where):
            absent.append(where + "." + name)
    return absent


def check(holders: dict[str, object]) -> None:
    """One `ImportError` naming the version, every missing name and the pin; else nothing."""
    absent = missing(holders)
    if absent:
        raise ImportError(
            "wasi_perl drives wasmtime-py through its private API, and wasmtime-py "
            f"{version()} lacks: {', '.join(absent)}. wasi_perl was written against {PIN}; "
            "install a wasmtime-py inside that range, or update wasi_perl"
        )


check(
    {
        "wasmtime._ffi": _ffi,
        "wasmtime._bindings": _bindings,
        "wasmtime.Trap": Trap,
        "wasmtime.WasmtimeError": WasmtimeError,
        "wasmtime.Caller": Caller,
        "wasmtime.Store": Store,
        "wasmtime.WasiConfig": WasiConfig,
        "wasmtime.Func": Func,
    }
)

# A store's context pointer: what the C API takes for the store's state. `ctypes.POINTER`
# makes the type at run time, a value the checker cannot read as a type, so the checker reads
# the class it makes.
if TYPE_CHECKING:
    Context: TypeAlias = ctypes._Pointer[_ffi.wasmtime_context_t]
else:
    Context = ctypes.POINTER(_ffi.wasmtime_context_t)


class Storelike(Protocol):
    """What a `Store` and a `Caller` have in common: the store's context."""

    def _context(self) -> Any: ...


def context_of(storelike: Storelike) -> Context:
    """The store's context pointer, from a `Store` or from the `Caller` of a host function."""
    context: Context = storelike._context()
    return context


def context_address(storelike: Storelike) -> int:
    """The context's address: the store's identity, which the host functions compare."""
    return ctypes.addressof(storelike._context().contents)


def set_epoch_deadline(storelike: Storelike, ticks: int) -> None:
    """The store's epoch deadline, `ticks` after the engine's current epoch; 0 stops the guest
    at its next function entry. Public on a `Store`; the binding on a `Caller`."""
    public = getattr(storelike, "set_epoch_deadline", None)
    if public is not None:
        public(ticks)
        return
    _ffi.wasmtime_context_set_epoch_deadline(storelike._context(), ticks)


def get_fuel(storelike: Storelike) -> int:
    """The store's fuel; raises `WasmtimeError` on a store that does not meter."""
    public = getattr(storelike, "get_fuel", None)
    if public is not None:
        return cast(int, public())
    fuel = ctypes.c_uint64()
    error = _ffi.wasmtime_context_get_fuel(storelike._context(), ctypes.byref(fuel))
    if error:
        raise WasmtimeError._from_ptr(error)
    return fuel.value


def set_fuel(storelike: Storelike, fuel: int) -> None:
    """Set the store's fuel; never raises (a metered store never refuses, and an unmetered one's
    refusal is dropped, the error freed)."""
    public = getattr(storelike, "set_fuel", None)
    if public is not None:
        try:
            public(fuel)
        except WasmtimeError:
            pass
        return
    error = _ffi.wasmtime_context_set_fuel(storelike._context(), fuel)
    if error:
        WasmtimeError._from_ptr(error)  # owned, so freed


def set_stdin_bytes(wasi: Any, data: bytes) -> None:
    """The guest's stdin as bytes, through `wasi_config_set_stdin_bytes`: no file holds it. The
    vector is handed over, and wasmtime frees it."""
    vec = _ffi.wasm_byte_vec_t()
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    _ffi.wasm_byte_vec_new(ctypes.byref(vec), len(data), buf)
    _ffi.wasi_config_set_stdin_bytes(wasi.ptr(), ctypes.byref(vec))


# A custom output's write (key, bytes, size -> written) and free (key) C types, from the binding's
# own record; the write made once and kept for the process's life, as wasmtime may call it while
# any store lives, and the free a NULL pointer, so wasmtime runs nothing of Python when it drops
# an output.
_OUTPUT_WRITE = _bindings._wasi_config_set_stdout_custom.argtypes[1](_outputs.write)
_NO_FREE = _bindings._wasi_config_set_stdout_custom.argtypes[3]()


def set_custom_outputs(wasi: WasiConfig, out_key: int, err_key: int) -> None:
    """The guest's stdout and stderr as `_outputs.write` under `out_key` and `err_key`, with no
    free: the caller drops the keys itself once the store is closed."""
    _ffi.wasi_config_set_stdout_custom(wasi.ptr(), _OUTPUT_WRITE, out_key, _NO_FREE)
    _ffi.wasi_config_set_stderr_custom(wasi.ptr(), _OUTPUT_WRITE, err_key, _NO_FREE)


def error(text: str) -> int:
    """A new wasmtime error naming `text`, as the address an epoch callback returns to stop."""
    err = _ffi.wasmtime_error_new(text.encode())
    return ctypes.cast(err, ctypes.c_void_p).value or 0


def on_epoch(store: Any, fn: Callable[[Any, Any], int]) -> list[Any]:
    """`fn(delta, kind)` called at each of `store`'s epoch deadlines, through
    `wasmtime_store_epoch_deadline_callback`: it returns 0 to go on, having set `delta[0]` to the
    ticks until the next and `kind[0]` to 0 (continue), or an `error()` to stop. Returns the C
    callback, which the caller keeps alive as long as the store. It has no data, so a NULL free:
    wasmtime runs nothing of Python when it drops the callback."""
    binding = _bindings._wasmtime_store_epoch_deadline_callback
    callback_type, finalizer_type = binding.argtypes[1], binding.argtypes[3]
    callback = callback_type(lambda _context, _data, delta, kind: fn(delta, kind))
    binding(store.ptr(), callback, None, finalizer_type())
    return [callback]


def raw_func(func: Func) -> Any:
    """The C handle of a `Func`, for a prepared call."""
    return func._func


class PreparedCall:
    """One function of the trampoline, with its argument array (its kinds set once) and its
    one-slot result array made once and reused per call (one forwarded file call is
    the hottest path the runner has). The trap out-pointer is made per call, since the `Trap`
    takes ownership of what it points to."""

    __slots__ = ("func", "kinds", "params", "results")

    def __init__(self, func: Any, kinds: tuple[str, ...]) -> None:
        self.func = func
        self.kinds = kinds
        self.params = (_ffi.wasmtime_val_t * len(kinds))()
        for i, kind in enumerate(kinds):
            tag = _ffi.WASMTIME_I32 if kind == "i32" else _ffi.WASMTIME_I64
            self.params[i].kind = ctypes.c_ubyte(tag.value)
        self.results = (_ffi.wasmtime_val_t * 1)()

    def call(self, context: Context, args: tuple[int, ...]) -> int:
        """The function's `i32` answer on `context`; raises its `Trap`, else its `WasmtimeError`
        (when both come back the trap is raised and the error is wrapped so that it is freed).
        Touches no fuel."""
        params = self.params
        for i, (kind, value) in enumerate(zip(self.kinds, args, strict=True)):
            if kind == "i32":
                params[i].of.i32 = value
            else:
                params[i].of.i64 = value
        trap = ctypes.POINTER(_ffi.wasm_trap_t)()
        error = _ffi.wasmtime_func_call(
            context, ctypes.byref(self.func), params, len(args), self.results, 1, ctypes.byref(trap)
        )
        if trap or error:
            raised: BaseException | None = WasmtimeError._from_ptr(error) if error else None
            if trap:
                raised = Trap._from_ptr(trap)  # owns and frees it; an error beside it is freed
            assert raised is not None
            raise raised
        return self.results[0].of.i32
