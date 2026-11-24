"""Research prototype, not shipped: the image module's sections against the checked reactor's.

  uv run python context/research/2026-11-24-preload-image-prototype/item4_security.py <out.json>

One making of the package prelude, with an env value and a grant, so the image can be searched
for both. Then, for every section id, the SHA-256 of its payload in the reactor and in the image
module, and whether they are equal. Then what the image holds: the build host's paths, the
making's env value, the granted file's text, the stack region's written blocks, and its size.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as h  # noqa: E402
import image_module as im  # noqa: E402

SECRET_ENV = "prototype-env-marker-6f1d"
SECRET_FILE = "prototype-file-marker-93ab"


def digests(module: bytes) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for s in im.sections(module):
        out.setdefault(im.NAMES[s.id], []).append(hashlib.sha256(s.payload).hexdigest())
    return out


def main(out: Path) -> None:
    reactor = h.REACTOR.read_bytes()
    with tempfile.TemporaryDirectory(prefix="proto022-grant-") as tmp:
        Path(tmp, "f.txt").write_text(SECRET_FILE + "\n")
        made = h.make(
            h.PRELUDE + 'open my $f, "<", "/data/f.txt" or die; our $kept = <$f>; close $f;',
            env={"MARKER": SECRET_ENV},
            grants=((tmp, "/data", False),),
        )
    assert made.status == 0 and made.open_files == 0
    module, stats = im.write(reactor, made.image)
    r, m = digests(reactor), digests(module)
    rows = {}
    for name in im.NAMES.values():
        if name in r or name in m:
            rows[name] = {
                "reactor": len(r.get(name, [])),
                "image": len(m.get(name, [])),
                "identical": r.get(name) == m.get(name),
            }
    must = ["type", "import", "function", "table", "global", "tag", "element", "code"]
    form = im.check_form(reactor)
    stack_blocks = sum(1 for a, b in im.runs(made.image[: form.stack_init]) for _ in range(a, b))
    result = {
        "reactor_sha256": hashlib.sha256(reactor).hexdigest(),
        "image_sha256": made.sha256,
        "module_sha256": hashlib.sha256(module).hexdigest(),
        "module": stats,
        "sections": rows,
        "must_be_identical": {n: rows[n]["identical"] for n in must if n in rows},
        "all_must_identical": all(rows[n]["identical"] for n in must if n in rows),
        "absent_in_reactor": [n for n in must if n not in rows],
        "image_holds": {
            "build_host_path": made.image.count(str(h.ROOT).encode()),
            "home_user": made.image.count(b"/home/user"),
            "making_env_value": made.image.count(SECRET_ENV.encode()),
            "granted_file_text": made.image.count(SECRET_FILE.encode()),
            "grant_host_folder": made.image.count(b"proto022-grant-"),
        },
        "stack_region_bytes": form.stack_init,
        "stack_region_written_4k_blocks": stack_blocks,
        "image_written_4k_blocks": sum(b - a for a, b in im.runs(made.image)),
    }
    out.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
