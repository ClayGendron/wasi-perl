"""Research prototype, not shipped: state Decision 4's table does not name, probed.

  uv run python context/research/2026-11-24-preload-image-prototype/item2_resets.py <out.json>

For each prelude, a call prints Perl's special variables, the working folder and the default
output handle, from the image and from perl.wasm, and the two are compared. The default prelude
is Decision 5's; the others set the state on purpose, to see which of it a call inherits.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as h  # noqa: E402

from wasi_perl import Grant  # noqa: E402

PROBE = (
    "use Cwd (); my $o = select; select STDOUT; $| = 1;"
    ' print join("|", "at=[$@]", "errno=" . (0+$!), "status=$?", "rs=" . (defined $/ ? length $/ : "undef"),'
    ' "ofs=" . (defined $, ? "[$,]" : "undef"), "ors=" . (defined $\\ ? "[$\\]" : "undef"),'
    ' "under=" . (defined $_ ? "[$_]" : "undef"), "sel=$o", "cwd=" . (Cwd::getcwd() // "undef"),'
    ' "sig=" . scalar(grep { defined $SIG{$_} } keys %SIG), "args=" . scalar(@ARGV)), "\\n"'
)

PRELUDES = {
    "default": h.PRELUDE,
    "dirty": h.PRELUDE
    + 'eval { die "x\\n" }; $! = 3; $? = 5 << 8; $/ = "X"; $, = "-"; $\\ = "!"; $_ = "u"; @ARGV = (1, 2);',
    "select": h.PRELUDE + "select STDERR;",
}


def main(out: Path) -> None:
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, code in PRELUDES.items():
            made = h.make(code)
            mods = h.image_modules(made, (False,))
            a = h.run_image(["-e", PROBE], mods, cache_dir=False)
            b = h.run_plain(["-e", PROBE], cache_dir=False)
            rows.append(
                {
                    "prelude": name,
                    "status": made.status,
                    "same": h.same(a, b),
                    "image": h.shown(a),
                    "plain": h.shown(b),
                }
            )
        # A making with a working folder, then a call with none.
        made = h.make(h.PRELUDE, env={"PWD": "/data"}, grants=((tmp, "/data", False),))
        mods = h.image_modules(made, (False,))
        kw = {"grants": [Grant(tmp, "/data")]}
        a = h.run_image(["-e", PROBE], mods, cache_dir=False, **kw)
        b = h.run_plain(["-e", PROBE], cache_dir=False, **kw)
        rows.append(
            {
                "prelude": "default, made with PWD=/data; call without cwd",
                "status": made.status,
                "same": h.same(a, b),
                "image": h.shown(a),
                "plain": h.shown(b),
            }
        )
    out.write_text(json.dumps(rows, indent=1) + "\n")
    for r in rows:
        print(
            r["same"],
            r["prelude"],
            "\n  image:",
            r["image"]["stdout"],
            r["image"]["stderr"],
            "\n  plain:",
            r["plain"]["stdout"],
            r["plain"]["stderr"],
        )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
