"""How deep Cpanel::JSON::XS can nest once a program raises max_depth past its default of 512:
the deepest level the guest decodes before its stack runs out, and what native perl does at
10,000 and 100,000. Usage, from the worktree: uv run python -I <this file>"""

import subprocess
from pathlib import Path

import wasi_perl

root = Path(__file__).resolve().parents[3]
PROG = r"""use Cpanel::JSON::XS; my $d = shift; my $j = Cpanel::JSON::XS->new->max_depth(1 << 30);
my $r = eval { $j->decode(("[" x $d) . ("]" x $d)); "ok" } // "died: $@"; print "$r\n";"""


def guest(depth: int):
    r = wasi_perl.run(["-e", PROG, str(depth)], deadline_s=60)
    return r.exit_code, r.trapped, r.stdout


lo, hi = 512, 10_000
assert guest(lo)[1] is None and guest(hi)[1] is not None
while hi - lo > 1:
    mid = (lo + hi) // 2
    lo, hi = (lo, mid) if guest(mid)[1] is not None else (mid, hi)
print(f"guest: decodes {lo} levels; at {hi}: {guest(hi)}")
for depth in (10_000, 100_000):
    n = subprocess.run(
        [str(root / "build/native-install/bin/perl"), "-e", PROG, str(depth)],
        env={"PERL5LIB": str(root / "build/native-cpan/lib/perl5")},
        capture_output=True,
    )
    print(f"native {depth}: exit {n.returncode} {n.stdout!r}")
