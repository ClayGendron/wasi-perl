"""Run the hostile-input program of tests/build/test_cpan_modules.py under wasi-perl and under
native perl with build/native-cpan, and print the import list of each perl.wasm.
Usage, from the worktree: uv run python -I context/research/2026-11-24-json-csv-and-diff-measured/edge.py OUT_DIR"""

import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(root / "tests" / "build"), str(root / "src")]
import wasi_perl  # noqa: E402
from test_cpan_modules import HOSTILE  # noqa: E402
from test_perl_wasm import _imports  # noqa: E402

out = Path(sys.argv[1])
g = wasi_perl.run(["-e", HOSTILE])  # the set WASI_PERL_ARTIFACTS names, else build/artifacts
(out / "edge-guest.txt").write_bytes(
    g.stdout + f"exit {g.exit_code} trapped {g.trapped} stderr {g.stderr!r}\n".encode()
)
n = subprocess.run(
    [str(root / "build/native-install/bin/perl"), "-e", HOSTILE],
    env={"PERL5LIB": str(root / "build/native-cpan/lib/perl5")},
    capture_output=True,
)
(out / "edge-native.txt").write_bytes(n.stdout + f"exit {n.returncode} stderr {n.stderr!r}\n".encode())
for name, wasm in (("before", "build/before/artifacts/perl.wasm"), ("after", "build/artifacts/perl.wasm")):
    lines = sorted(f"{m}.{f}" for m, f in _imports((root / wasm).read_bytes()))
    (out / f"imports-{name}.txt").write_text("\n".join(lines) + "\n")
