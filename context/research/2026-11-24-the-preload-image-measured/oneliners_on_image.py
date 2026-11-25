"""The one-liner corpus, from a preload image against perl.wasm.

The oracle's own tests pin PERL_HASH_SEED=0 and PERL_PERTURB_KEYS=0 to match native perl, and a
call whose environment names either runs perl.wasm even when given an image (spec 022, Decision
6). So the oracle run unchanged never starts from the image. Here every case runs with the
corpus's environment minus those two names: twice from perl.wasm and once from the image, each
in a fresh work folder. A case whose two perl.wasm runs agree must give the same result from the
image; one whose runs differ depends on the hash order, and is counted, not compared.

  uv run python context/research/2026-11-24-the-preload-image-measured/oneliners_on_image.py \
      {empty|packages}
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests" / "oneliners"))

import oneliner_oracle as oo  # noqa: E402

from wasi_perl import Artifacts, Grant, make_package_snapshot, make_snapshot, run  # noqa: E402

SEEDS = ("PERL_HASH_SEED", "PERL_PERTURB_KEYS")


def one(case, image, found, root: Path):
    work = Path(tempfile.mkdtemp(dir=root))
    oo.lay_out(case, work)
    env = {k: v for k, v in {**oo.BASE_ENV, **case.env}.items() if k not in SEEDS}
    done = run(
        oo.expand_argv(case.argv, oo.GUEST_ROOT),
        grants=[Grant(str(work), oo.GUEST_ROOT, writable=True)],
        env=env,
        stdin=case.stdin if case.stdin is not None else b"",
        cwd=oo.GUEST_ROOT,
        fuel=10**11,
        deadline_s=None,
        snapshot=image,
        artifacts=found,
        cache_dir=False,
    )
    if image is not False:
        assert done.image_sha256 == image.origin.image_sha256, case.id
    return (done.exit_code, done.stdout, done.stderr, done.trapped, oo.snapshot(work))


def main() -> int:
    which = sys.argv[1]
    env = os.environ.get("WASI_PERL_ARTIFACTS")
    found = Artifacts.at(Path(env) if env else ROOT / "build" / "artifacts")
    image = (
        make_snapshot("", artifacts=found, cache_dir=False)
        if which == "empty"
        else make_package_snapshot(artifacts=found, cache_dir=False)
    )
    same = differ = order = 0
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        for case in oo.load_corpus():
            a, b = one(case, False, found, Path(tmp)), one(case, False, found, Path(tmp))
            if a != b:
                order += 1
                continue
            if one(case, image, found, Path(tmp)) == a:
                same += 1
            else:
                differ += 1
                diffs.append(case.id)
    print(f"{which}: {same} equal, {differ} differ {diffs}, {order} hash-order dependent")
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
