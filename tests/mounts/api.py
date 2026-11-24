"""A listing of a package's public names in wasi-python's `tests/public_api.txt` format, made by
the same introspection: ported from wasi-python's `tests/harness/api.py` at f9bf678 (same owner,
Apache-2.0), trimmed to the classes, functions and `Defaults` lines the mount API check compares.
The two packages' listings can then be compared line for line."""

from __future__ import annotations

import dataclasses
import enum
import inspect
import typing
from typing import Any

KINDS = {
    inspect.Parameter.POSITIONAL_ONLY: "positional-only",
    inspect.Parameter.POSITIONAL_OR_KEYWORD: "positional",
    inspect.Parameter.KEYWORD_ONLY: "keyword-only",
    inspect.Parameter.VAR_POSITIONAL: "var-positional",
    inspect.Parameter.VAR_KEYWORD: "var-keyword",
}


def _annotation(value: Any) -> str | None:
    if value is inspect.Parameter.empty:
        return None
    if not isinstance(value, str):
        raise AssertionError(f"annotation {value!r} is not a stored string")
    return value


def _params(fn: Any, *, skip_self: bool = False) -> list[dict[str, Any]]:
    out = []
    sig = inspect.signature(fn)
    for i, p in enumerate(sig.parameters.values()):
        if skip_self and i == 0 and p.name == "self":
            continue
        out.append(
            {
                "name": p.name,
                "kind": KINDS[p.kind],
                "annotation": _annotation(p.annotation),
                "default": None if p.default is inspect.Parameter.empty else repr(p.default),
            }
        )
    return out


def _returns(fn: Any) -> str | None:
    return _annotation(inspect.signature(fn).return_annotation)


def _bases(cls: type) -> list[str]:
    skip = {object, typing.Protocol, typing.Generic}
    return [b.__name__ for b in cls.__bases__ if b not in skip]


def _kind(obj: Any) -> str:
    if inspect.iscoroutinefunction(obj):
        return "async def"
    if inspect.isfunction(obj):
        return "def"
    if isinstance(obj, type):
        if dataclasses.is_dataclass(obj):
            return "dataclass"
        if getattr(obj, "_is_protocol", False):
            return "protocol"
        if issubclass(obj, enum.Enum):
            return "enum"
        if issubclass(obj, BaseException):
            return "exception"
        return "class"
    return "constant"


def _function(name: str, fn: Any, *, method: bool = False) -> dict[str, Any]:
    item = {
        "name": name,
        "kind": _kind(fn),
        "params": _params(fn, skip_self=method),
        "returns": _returns(fn),
        "overloads": [],
    }
    for o in typing.get_overloads(fn):
        item["overloads"].append({"params": _params(o), "returns": _returns(o)})
    return item


_PLAIN_DUNDERS = ("__init__", "__enter__", "__exit__")


def _members(cls: type) -> list[dict[str, Any]]:
    plain = _kind(cls) == "class"
    out = []
    for name, value in vars(cls).items():
        if name.startswith("_") and name != "__call__" and not (plain and name in _PLAIN_DUNDERS):
            continue
        if isinstance(value, property):
            out.append(
                {
                    "name": name,
                    "kind": "property",
                    "params": [],
                    "returns": _returns(value.fget),
                    "overloads": [],
                }
            )
        elif inspect.isfunction(value):
            out.append(_function(name, value, method=True))
        elif isinstance(value, classmethod):
            item = _function(name, value.__func__, method=True)
            out.append({**item, "kind": "classmethod", "params": item["params"][1:]})
    return out


def _class(name: str, cls: type) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": name,
        "kind": _kind(cls),
        "bases": _bases(cls),
        "members": _members(cls),
        "fields": [],
        "frozen": None,
        "options": [],
        "enum": [],
    }
    if dataclasses.is_dataclass(cls):
        params = cls.__dataclass_params__  # type: ignore[attr-defined]
        item["frozen"] = params.frozen
        item["options"] = [
            f"{k}={getattr(params, k)}"
            for k, default in (("init", True), ("repr", True), ("eq", True), ("order", False),
                               ("unsafe_hash", False))
            if getattr(params, k) != default
        ]  # fmt: skip
        for f in dataclasses.fields(cls):
            if f.name.startswith("_"):
                continue
            if f.default is not dataclasses.MISSING:
                default = repr(f.default)
            elif f.default_factory is not dataclasses.MISSING:
                default = "<factory>"
            else:
                default = "required"
            item["fields"].append(
                {
                    "name": f.name,
                    "annotation": _annotation(f.type),
                    "default": default,
                    "kw_only": bool(f.kw_only),
                }
            )
    if issubclass(cls, enum.Enum):
        item["enum"] = [[m.name, repr(m.value)] for m in cls]
    return item


def _param_line(p: dict[str, Any], depth: int) -> str:
    star = {"var-positional": "*", "var-keyword": "**"}.get(p["kind"], "")
    text = " " * depth + f"param {star}{p['name']}"
    if p["annotation"] is not None:
        text += f": {p['annotation']}"
    if p["default"] is not None:
        text += f" = {p['default']}"
    return text + f"  {p['kind']}"


def _function_lines(f: dict[str, Any], depth: int) -> list[str]:
    pad = " " * depth
    lines = [f"{pad}{f['kind']} {f['name']} -> {f['returns']}"]
    lines += [_param_line(p, depth + 2) for p in f["params"]]
    for o in f["overloads"]:
        lines.append(f"{pad}  overload -> {o['returns']}")
        lines += [_param_line(p, depth + 4) for p in o["params"]]
    return lines


def _class_lines(c: dict[str, Any]) -> list[str]:
    bases = f"({', '.join(c['bases'])})" if c["bases"] else ""
    head = f"{c['kind']} {c['name']}{bases}"
    if c["kind"] == "dataclass":
        head += ": frozen" if c["frozen"] else ": not frozen"
        head += "".join(f", {p}" for p in c["options"])
    lines = [head]
    for f in c["fields"]:
        kw = "  kw_only" if f["kw_only"] else ""
        lines.append(f"  field {f['name']}: {f['annotation']} = {f['default']}{kw}")
    for name, value in c["enum"]:
        lines.append(f"  member {name} = {value}")
    for m in c["members"]:
        lines += _function_lines(m, 2)
    return lines


def listing(module: Any, defaults: Any = None) -> list[str]:
    """The module's `__all__` in the snapshot's format: each class's and function's block, then
    a `default` line for each field of `defaults`, as wasi-python's snapshot ends its module."""
    lines = [f"module {module.__name__}"]
    for name in module.__all__:
        obj = getattr(module, name)
        if isinstance(obj, type):
            lines += _class_lines(_class(name, obj))
        elif inspect.isfunction(obj):
            lines += _function_lines(_function(name, obj), 0)
        else:
            lines.append(f"constant {name} = {obj!r}")
    if defaults is not None:
        for f in dataclasses.fields(defaults):
            value = getattr(defaults, f.name)
            lines.append(f"default {f.name} = {value!r}")
    return lines


def blocks(lines: list[str]) -> dict[str, list[str]]:
    """Each top-level name's block, by name: its head line and the indented lines under it."""
    out: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in lines:
        if line.startswith(("dataclass ", "enum ", "protocol ", "class ", "exception ", "def ",
                            "async def ")):  # fmt: skip
            head = line.split(" -> ")[0].split(":")[0].split("(")[0]
            name = head.rsplit(" ", 1)[-1]
            current = out[name] = [line]
        elif line.startswith(" ") and current is not None:
            current.append(line)
        else:
            current = None
    return out
