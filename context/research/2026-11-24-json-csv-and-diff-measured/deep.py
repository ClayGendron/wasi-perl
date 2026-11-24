"""How deep Cpanel::JSON::XS can nest once a program raises max_depth past its default of 512:
the deepest level the guest decodes, and encodes, before its stack runs out; that a call which
traps leaves the next call in the same process working; and what native perl does at 10,000
and 100,000 levels. Usage, from the worktree: uv run python -I <this file>"""

import subprocess
from pathlib import Path

import wasi_perl

root = Path(__file__).resolve().parents[3]
DECODE = r"""use Cpanel::JSON::XS; my $d = shift; my $j = Cpanel::JSON::XS->new->max_depth(1 << 30);
my $r = eval { $j->decode(("[" x $d) . ("]" x $d)); "ok" } // "died: $@"; print "$r\n";"""
ENCODE = r"""use Cpanel::JSON::XS; my $d = shift; my $j = Cpanel::JSON::XS->new->max_depth(1 << 30);
my $v = []; $v = [$v] for 2 .. $d;
my $r = eval { length $j->encode($v) } // "died: $@"; print "$r\n";"""


def guest(prog: str, depth: int):
    r = wasi_perl.run(["-e", prog, str(depth)], deadline_s=60)
    return r.exit_code, r.trapped, r.stdout


def deepest(prog: str) -> tuple[int, int]:
    lo, hi = 512, 10_000
    assert guest(prog, lo)[1] is None and guest(prog, hi)[1] is not None
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (lo, mid) if guest(prog, mid)[1] is not None else (mid, hi)
    return lo, hi


for what, prog in (("decodes", DECODE), ("encodes", ENCODE)):
    lo, hi = deepest(prog)
    print(f"guest: {what} {lo} levels; at {hi}: {guest(prog, hi)}")
    print(f"  the next call: {guest(prog, 100)}")
for what, prog in (("decode", DECODE), ("encode", ENCODE)):
    for depth in (10_000, 100_000):
        n = subprocess.run(
            [str(root / "build/native-install/bin/perl"), "-e", prog, str(depth)],
            env={"PERL5LIB": str(root / "build/native-cpan/lib/perl5")},
            capture_output=True,
        )
        print(f"native {what} {depth}: exit {n.returncode} {n.stdout!r}")
