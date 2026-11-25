"""Run Perl's own t/ suite with every call started from a preload image. The image is made
once; then `wasi_perl.run` is wrapped so each call the harness makes passes `snapshot=` that
image. Nothing else changes: grants, env, limits, exclusions and the scoreboard are the
harness's own. The count printed last is of the calls that did start from the image.

  uv run python context/research/2026-11-24-the-preload-image-measured/on_image.py \
      {empty|packages} perl_t [run_suite.py's arguments]

(The one-liner oracle's tests pin PERL_HASH_SEED, so their calls run perl.wasm whatever they
are given: `oneliners_on_image.py` compares the corpus instead.)
"""

from __future__ import annotations

import functools
import os
import sys
from pathlib import Path

import wasi_perl
from wasi_perl import Artifacts, make_package_snapshot, make_snapshot, runner

ROOT = Path(__file__).resolve().parents[3]
USED: list[int] = []


def main() -> int:
    which, suite, rest = sys.argv[1], sys.argv[2], sys.argv[3:]
    env = os.environ.get("WASI_PERL_ARTIFACTS")
    found = Artifacts.at(Path(env) if env else ROOT / "build" / "artifacts")
    image = (
        make_snapshot("", artifacts=found, cache_dir=False)
        if which == "empty"
        else make_package_snapshot(artifacts=found, cache_dir=False)
    )
    real = runner.run

    @functools.wraps(real)
    def run(argv, **kw):
        kw.setdefault("snapshot", image)
        done = real(argv, **kw)
        if done.image_sha256 == image.origin.image_sha256:
            USED.append(1)
        return done

    wasi_perl.run = runner.run = run
    if suite != "perl_t":
        raise SystemExit("the one suite is perl_t")
    sys.path.insert(0, str(ROOT / "tests" / "perl_t"))
    import run_suite

    code = run_suite.main(rest)
    print(f"calls from the image: {len(USED)} ({image!r})")
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
