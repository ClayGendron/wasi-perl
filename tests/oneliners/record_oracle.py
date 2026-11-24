"""Record what native perl 5.42.3 does with every case, for hosts that have no native perl.

    uv run python tests/oneliners/record_oracle.py [--perl PATH] --out build/oracle.json

The file carries the corpus's SHA-256; the tests refuse it once cases.toml changes. The module
cases run with build/native-cpan on PERL5LIB, and the file records the CPAN versions they ran
with; an oracle without the pinned versions is refused. Point WASI_PERL_ORACLE_JSON at the
file to compare the guest against it.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import oneliner_oracle as oo


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="where to write the JSON")
    parser.add_argument(
        "--perl",
        type=Path,
        help="native perl 5.42.3 (default: WASI_PERL_ORACLE, else build/native-install/bin/perl)",
    )
    args = parser.parse_args()
    try:
        oracle = oo.find_oracle(args.perl)
    except oo.OracleRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    if oracle is None:
        print(
            "no native perl 5.42.3 at build/native-install/bin/perl and no WASI_PERL_ORACLE",
            file=sys.stderr,
        )
        return 2
    cases = oo.load_corpus()
    with tempfile.TemporaryDirectory() as scratch:
        try:
            data = oo.record(oracle, cases, Path(scratch))
        except oo.OracleRefused as e:
            print(f"refused: {e}", file=sys.stderr)
            return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    sha = data["corpus_sha256"][:12]
    print(f"recorded {len(cases)} cases from {oracle} to {args.out} (corpus {sha})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
