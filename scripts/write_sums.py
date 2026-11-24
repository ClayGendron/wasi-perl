"""Write an artifact set's SHA256SUMS: one `<sha256>  <path>` line per file of the set but
SHA256SUMS itself, paths relative to the set and `/`-separated, sorted, in `sha256sum`'s own
format, so `sha256sum -c SHA256SUMS` checks it too. The runner checks a set it finds against
this file before using it.

    uv run python scripts/write_sums.py build/artifacts
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

NAME = "SHA256SUMS"


def sums(folder: Path) -> str:
    lines = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file() or p.is_symlink()):
        rel = path.relative_to(folder).as_posix()
        if rel == NAME:
            continue
        if path.is_symlink():
            raise SystemExit(f"{path}: an artifact set holds no links")
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {rel}\n")
    return "".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: write_sums.py ARTIFACT_DIR", file=sys.stderr)
        return 2
    folder = Path(argv[0])
    (folder / NAME).write_text(sums(folder), encoding="ascii", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
