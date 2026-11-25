"""The `wasi-perl` command: `wasi-perl run [OPTIONS] [--] ARG...` runs one program through
`wasi_perl.run` and prints its result as one line of JSON. It adds no power `run` lacks: each
option is one of `run`'s arguments, and an option not given leaves `run`'s default. Adapted from
wasi-python's `cli.py` (same owner, Apache-2.0), trimmed to `run`'s arguments here.

    wasi-perl run -- -e 'print "hello\\n"'
    {"exit_code": 0, "fuel_used": null, "stderr": "", "stdout": "hello\\n", "trapped": null,
     "truncated": []}    (one line)

Exit codes say whether there is a result, not how the program ended: 0, the call returned (the
JSON says how the program ended); 1, `run` raised, or `--stdin PATH` could not be read (one JSON
line with `error` and `kind`); 2, a usage error (argparse's message on standard error); 130,
Ctrl-C.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from typing import Any

USAGE = "wasi-perl run [OPTIONS] [--] ARG..."
VALUED = {
    "--grant",
    "--grant-writable",
    "--cwd",
    "--env",
    "--stdin",
    "--deadline-s",
    "--memory-bytes",
    "--output-bytes",
    "--fuel",
    "--cache-dir",
}


def _run(argv: list[str], **kwargs: Any) -> Any:
    """`wasi_perl.run`, imported at the call, so the command parses without wasmtime."""
    from wasi_perl.runner import run

    return run(argv, **kwargs)


def _limit(kind: type) -> Any:
    """`none` for no limit, else a value of `kind`; anything else is a usage error."""

    def parse(text: str) -> Any:
        if text == "none":
            return None
        try:
            return kind(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not {kind.__name__} or none: {text!r}") from None

    return parse


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wasi-perl run",
        usage=USAGE,
        allow_abbrev=False,
        description="Run one Perl program in a fresh sandboxed perl.wasm and print its result "
        "as one JSON line.",
    )
    unset = argparse.SUPPRESS  # an option not given is absent, so run() keeps its default
    # Both grant options write to one list, each value tagged with its mode, so the order typed
    # is the order run() numbers them in.
    p.add_argument(
        "--grant", dest="grants", action="append", default=[], type=lambda v: ("ro", v),
        metavar="GUEST=HOST", help="a host folder the guest sees at GUEST, read-only",
    )  # fmt: skip
    p.add_argument(
        "--grant-writable", dest="grants", action="append", type=lambda v: ("rw", v),
        metavar="GUEST=HOST", help="a host folder the guest sees at GUEST, writable",
    )  # fmt: skip
    p.add_argument(
        "--cwd", metavar="GUEST", default=unset,
        help="the guest's working folder, a guest path inside a grant",
    )  # fmt: skip
    p.add_argument(
        "--env", action="append", default=[], metavar="KEY[=VALUE]",
        help="an environment variable for the guest: KEY=VALUE, or KEY alone to pass this "
        "command's own value, which keeps a secret off the command line",
    )  # fmt: skip
    p.add_argument("--stdin", metavar="PATH", help="a file, or - for this command's own input")
    p.add_argument(
        "--deadline-s", type=_limit(float), metavar="SECONDS", default=unset,
        help="the wall-clock seconds the call may run; none for no limit",
    )  # fmt: skip
    p.add_argument(
        "--memory-bytes", type=_limit(int), metavar="BYTES", default=unset,
        help="the most linear memory the guest may hold; none for no limit",
    )  # fmt: skip
    p.add_argument(
        "--output-bytes", type=_limit(int), metavar="BYTES", default=unset,
        help="the most bytes kept per stream; none for no limit",
    )  # fmt: skip
    p.add_argument(
        "--fuel", type=_limit(int), metavar="N", default=unset,
        help='a cap on the guest\'s executed instructions; past it the call ends "out of fuel"',
    )  # fmt: skip
    caches = p.add_mutually_exclusive_group()
    caches.add_argument(
        "--cache-dir", metavar="PATH", default=unset,
        help="where compiled modules are kept, a folder only you and the runner write",
    )  # fmt: skip
    caches.add_argument("--no-cache", action="store_true", help="keep no compiled module on disk")
    p.add_argument(
        "--no-snapshot", action="store_true",
        help="accepted as wasi-python's command line takes it; perl.wasm runs either way",
    )  # fmt: skip
    return p


def _split(args: list[str]) -> tuple[list[str], list[str]]:
    """One left-to-right scan: `--` gives the rest to the guest; an argument starting with `-`
    (not `-` itself) is this command's option, and one of `VALUED` consumes the next argument
    unless written `--name=value`; anything else starts the guest's argv."""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            return args[:i], args[i + 1 :]
        if arg.startswith("-") and arg != "-":
            i += 2 if arg in VALUED else 1
            continue
        return args[:i], args[i:]
    return args, []


def _grants(items: list[tuple[str, str]], parser: argparse.ArgumentParser) -> list[Any]:
    """GUEST=HOST, split at the first `=`: a guest path starts with `/`, and a host path may hold
    `=` or a drive letter (`C:\\data`). Whatever parses goes to run(), which judges it."""
    from wasi_perl._grants import Grant

    out = []
    for mode, value in items:
        guest, sep, host = value.partition("=")
        if not sep:
            option = "--grant-writable" if mode == "rw" else "--grant"
            parser.error(f"{option} needs GUEST=HOST")
        out.append(Grant(host, guest, writable=mode == "rw"))
    return out


def _env(items: list[str], parser: argparse.ArgumentParser) -> dict[str, str] | None:
    if not items:
        return None
    env: dict[str, str] = {}
    for i, item in enumerate(items, 1):
        key, sep, value = item.partition("=")
        if not sep:
            if key not in os.environ:
                # Never echo what was typed: a value given by mistake as a name is a secret.
                parser.error(f"--env number {i}: no such name in this command's environment")
            value = os.environ[key]
        env[key] = value
    return env


def _stream(name: str, data: bytes) -> dict[str, str]:
    try:
        return {name: data.decode("utf-8")}
    except UnicodeDecodeError:
        return {f"{name}_base64": base64.b64encode(data).decode("ascii")}


def _write(obj: dict[str, Any]) -> None:
    line = json.dumps(obj, sort_keys=True, ensure_ascii=True) + "\n"
    sys.stdout.buffer.write(line.encode("ascii"))
    sys.stdout.buffer.flush()


def main(argv: list[str] | None = None) -> int:
    """The command; returns its exit code. Ctrl-C at any point is 130."""
    try:
        return _main(list(sys.argv[1:] if argv is None else argv))
    except KeyboardInterrupt:
        return 130


def _main(args: list[str]) -> int:
    if args[:1] in (["-h"], ["--help"]):
        print(f"usage: {USAGE}\n\nRun `wasi-perl run --help` for its options.")
        return 0
    if args[:1] != ["run"]:
        print(f"usage: {USAGE}\nwasi-perl: the one command is `run`", file=sys.stderr)
        return 2
    options, guest = _split(args[1:])
    parser = _parser()
    try:
        ns = parser.parse_args(options)
        env = _env(ns.env, parser)
        grants = _grants(ns.grants, parser)
    except SystemExit as e:  # usage errors exit 2; --help exits 0
        return int(e.code or 0)
    given: dict[str, Any] = {}
    for name in ("deadline_s", "memory_bytes", "output_bytes", "fuel", "cwd", "cache_dir"):
        if hasattr(ns, name):
            given[name] = getattr(ns, name)
    if ns.no_cache:
        given["cache_dir"] = False
    if ns.no_snapshot:
        given["snapshot"] = False
    if env is not None:
        given["env"] = env
    if grants:
        given["grants"] = grants
    try:
        if ns.stdin is not None:
            if ns.stdin == "-":
                if sys.stdin is None or sys.stdin.closed:
                    raise OSError("--stdin -: this command's standard input is closed")
                given["stdin"] = sys.stdin.buffer.read()
            else:
                with open(ns.stdin, "rb") as f:
                    given["stdin"] = f.read()
        done = _run(guest, **given)
    except Exception as e:
        _write({"error": str(e), "kind": type(e).__name__})
        return 1
    _write(
        {
            "exit_code": done.exit_code,
            "trapped": done.trapped,
            "fuel_used": done.fuel_used,
            "truncated": list(done.truncated),
            **_stream("stdout", done.stdout),
            **_stream("stderr", done.stderr),
        }
    )
    return 0
