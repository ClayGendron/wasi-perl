"""Print the guest instructions of each slow and fast example docs/fast-perl.md measures.

    uv run python -I context/research/2026-11-24-fast-perl-measured/measure.py [ITEM...]

It uses the checks' own pairs, inputs and runs (tests/oneliners/test_fast_perl.py), so its
numbers are the ones the checks compare, and it prints one row per pair as Markdown. ITEM names
a pair of the checks' RATIOS, or "posix"; with none, all run. A row "count, F's form" adds the
line loop study F measured, `$n++ if /ERROR/`, against the page's whole-file count.
WASI_PERL_ARTIFACTS picks the artifact set, as for the runner.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tests" / "oneliners"))

import test_fast_perl as fp  # noqa: E402
import wasi_perl  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        inputs = scratch / "inputs"
        fp.write_inputs(inputs)
        print("| item | slow case | fast case | slow fuel | fast fuel | ratio | page | within 20% |")
        print("|---|---|---|---:|---:|---:|---:|---|")
        want = sys.argv[1:] or [*fp.RATIOS, "count, F's form", "posix"]
        for name, (slow, fast, figure, _) in fp.RATIOS.items():
            if name not in want:
                continue
            a, b = fp.measure(wasi_perl, inputs, scratch / name.replace(" ", "-"), slow, fast)
            ratio, claim = a / b, float(figure)
            ok = claim * (1 - fp.MARGIN) <= ratio <= claim * (1 + fp.MARGIN)
            print(f"| {name} | `{slow}` | `{fast}` | {a:,} | {b:,} | {ratio:.2f}× | {figure}× | {'yes' if ok else 'NO'} |")
        if "count, F's form" in want:
            f_form = ("-ne", '$n++ if /ERROR/; END { print "$n\\n" }', "app.log")
            a, out, _ = fp.fuel_of(wasi_perl, f_form, inputs, scratch / "f-slow")
            fast = fp.CASES["fast-count-whole-file"].argv
            b, out_fast, _ = fp.fuel_of(wasi_perl, fast, inputs, scratch / "f-fast")
            assert out == out_fast
            print(f"| count, F's form | `-ne '$n++ if /ERROR/ …'` | `fast-count-whole-file` | {a:,} | {b:,} | {a / b:.2f}× | | |")
        if "posix" not in want:
            return
        a, b = fp.measure(wasi_perl, inputs, scratch / "posix", *fp.POSIX_PAIR)
        print(f"\n`use POSIX` against `use POSIX ()`: {a:,} - {b:,} = {(a - b) / 1e6:.1f} M (page: {fp.POSIX_IMPORT_M} M)")
        done = wasi_perl.run(["-MPOSIX", "-e", "print scalar @POSIX::EXPORT"])
        print(f"`scalar @POSIX::EXPORT`: {done.stdout.decode()} (page: {fp.POSIX_NAMES})")


if __name__ == "__main__":
    main()
