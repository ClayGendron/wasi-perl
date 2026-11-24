"""docs/fast-perl.md's ratios, measured as its checks measure them, on the set
$WASI_PERL_ARTIFACTS names (or build/artifacts).

    WASI_PERL_ARTIFACTS=<set> uv run python ratios.py

Prints one line per ratio: slow and fast instructions, the ratio, the page's figure, and where
the ratio stands in the checks' window of 20% either way. Also the cost of `use POSIX`.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[3] / "tests"),
                str(Path(__file__).resolve().parents[3] / "tests" / "oneliners")]

import wasi_perl  # noqa: E402
from test_fast_perl import MARGIN, POSIX_PAIR, RATIOS, measure, write_inputs  # noqa: E402


def main() -> None:
    work = Path(tempfile.mkdtemp())
    inputs = work / "inputs"
    write_inputs(inputs)
    for i, (name, (slow, fast, figure, _)) in enumerate(RATIOS.items()):
        s, f = measure(wasi_perl, inputs, work / f"r{i}", slow, fast)
        claim = float(figure)
        r = s / f
        print(f"{name}\t{s}\t{f}\t{r:.3f}\t{figure}\t{r / claim - 1:+.1%}"
              f"\t(window {claim * (1 - MARGIN):.2f}..{claim * (1 + MARGIN):.2f})", flush=True)
    a, n = measure(wasi_perl, inputs, work / "posix", *POSIX_PAIR)
    print(f"use POSIX import\t{a}\t{n}\t{(a - n) / 1e6:.1f} M", flush=True)


if __name__ == "__main__":
    main()
