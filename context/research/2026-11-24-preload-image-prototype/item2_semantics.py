"""Research prototype, not shipped: spec 022's choice (c) on wasmtime-py 51, checked by output.

  uv run python context/research/2026-11-24-preload-image-prototype/item2_semantics.py <out.json>

1. One making of the package prelude (Decision 5's eight `require` lines): its status, the
   descriptors it left open, and the stack pointer against its first value.
2. The image module written and compiled; each program run from it and from perl.wasm through
   `wasi_perl.run`, and compared byte for byte: stdout, stderr, exit code and trap.
3. Isolation: two fresh instances of one image, the first setting a global and redefining
   POSIX::floor, the second looking for both.
4. The seed: three makings give three images and three `hash_seed()` values; ten calls of one
   image give one `hash_seed()` value and more than one key order.
5. The four drifts the spec accepts, shown as drifts.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as h  # noqa: E402

from wasi_perl import Grant  # noqa: E402

FIVE_PRINT = (
    "use strict; use warnings; use POSIX; use JSON::PP; use List::Util qw(sum max);"
    " print JSON::PP->new->canonical->encode({s => sum(1..10), m => max(3,9,4),"
    " f => floor(7.9), c => ceil(7.1), l => [1,2,3]}), qq{\\n}"
)
SEED = "use Hash::Util qw(hash_seed); print unpack(q{H*}, hash_seed())"
ORDER = "my %h = map { $_ => 1 } q{a}..q{t}; print join(q{}, keys %h)"


def programs(data: str) -> list[tuple[str, list[str], dict]]:
    """(name, argv, keywords of run)."""
    grant = {"grants": [Grant(data, "/data")]}
    env = {"env": {"FOO": "bar", "EMPTY": "", "LANG": "C"}}
    return [
        ("posix_floor", ["-e", "use POSIX; print floor(2.5)"], {}),
        ("five_print", ["-e", FIVE_PRINT], {}),
        ("five_bare", ["-e", h.FIVE], {}),
        ("e_1", ["-e", "1"], {}),
        (
            "die_in_eval",
            [
                "-e",
                'eval { die "boom\\n" }; print "caught: $@"; eval { die { code => 7 } }; print "ref: $@->{code}\\n"; die "final\\n"',
            ],
            {},
        ),
        ("die_line", ["-e", "eval { die q{x} }; print $@; die"], {}),
        ("exit_3", ["-e", "print qq{a\\n}; exit 3"], {}),
        ("read_grant_ne", ["-ne", "print if /b/", "/data/f.txt"], grant),
        ("read_grant_lane", ["-lane", "print $F[1]", "/data/f.txt"], grant),
        ("read_grant_0777", ["-0777", "-ne", "print length, qq{\\n}", "/data/f.txt"], grant),
        ("read_missing", ["-e", "open my $f, q{<}, q{/data/nope} or die qq{no: $!\\n}"], grant),
        (
            "env_argv",
            [
                "-e",
                'print join(",", map { "$_=$ENV{$_}" } sort keys %ENV), "\\n"; print "@ARGV|$0|", scalar(@ARGV), "\\n"; $ENV{NEW} = "v"; delete $ENV{FOO}; print join(",", sort keys %ENV), "\\n"',
                "a",
                "b c",
                "",
            ],
            env,
        ),
        ("env_none", ["-e", "print scalar(keys %ENV), qq{\\n}"], {}),
        (
            "stdin",
            ["-e", "print scalar <STDIN>; print eof(STDIN) ? qq{eof\\n} : qq{more\\n}"],
            {"stdin": b"a\nb\n"},
        ),
        ("warn_w", ["-we", "my $x; print $x + 1, qq{\\n}"], {}),
        (
            "use_warnings",
            ["-e", "use warnings; my @a = (1); print $a[3] // q{u}, qq{\\n}; print undef . q{}"],
            {},
        ),
        ("strict_refs", ["-e", "use strict; my $n = q{x}; print $$n"], {}),
        ("no_strict_leak", ["-e", "$x = 1; print qq{ok $x\\n}"], {}),
        (
            "begin_end",
            [
                "-e",
                "BEGIN { print qq{b\\n} } INIT { print qq{i\\n} } END { print qq{e\\n} } print qq{m\\n}",
            ],
            {},
        ),
        ("refusal_system", ["-e", 'my $r = system("true"); print "r=$r err=$!\\n"'], {}),
        ("refusal_fork", ["-e", "fork"], {}),
        (
            "time",
            [
                "-e",
                "print time - $^T < 30 ? q{ok} : q{bad}, $^T == 1767225600 ? q{ PINNED} : q{}, qq{\\n}",
            ],
            {},
        ),
        (
            "phase",
            ["-e", "BEGIN { print qq{${^GLOBAL_PHASE}\\n} } print qq{${^GLOBAL_PHASE}\\n}"],
            {},
        ),
        ("inc_dirs", ["-le", "print for @INC"], {}),
        (
            "dumper",
            [
                "-e",
                "use Data::Dumper; $Data::Dumper::Sortkeys = 1; print Dumper({a => 1, b => [2]})",
            ],
            {},
        ),
        (
            "isa_mro",
            [
                "-e",
                "package A; sub f { q{A} } package B; our @ISA = (q{A}); package main; print B->f, qq{\\n}; print join(q{,}, @{mro::get_linear_isa(q{B})}), qq{\\n}",
            ],
            {},
        ),
        ("bad_switch", ["-Q", "-e", "1"], {}),
        ("no_code_e", ["-e"], {}),
        ("missing_script", ["/data/nope.pl"], grant),
        ("script_file", ["/data/s.pl", "x"], grant),
        ("missing_module", ["-MNo::Such::Mod", "-e", "1"], {}),
        ("version", ["-v"], {}),
        ("syntax_error", ["-e", "print 1 +;"], {}),
        (
            "rand_seed_env",
            ["-e", "print int(rand(1000000)), qq{\\n}"],
            {"env": {"PERL_RAND_SEED": "7"}},
        ),
        (
            "encode",
            ["-e", "use Encode; print length(encode(q{UTF-8}, qq{\\x{263a}})), qq{\\n}"],
            {},
        ),
        (
            "storable",
            ["-e", "use Storable qw(freeze thaw); print thaw(freeze([1,2,3]))->[2], qq{\\n}"],
            {},
        ),
    ]


DRIFTS = [
    ("no_use_posix", ["-e", "print POSIX::floor(2.5)"]),
    ("defined_sub", ["-e", "print defined &POSIX::floor ? 1 : 0"]),
    ("inc_keys", ["-e", "print scalar(keys %INC)"]),
]


def main(out: Path) -> None:
    result: dict = {}
    made = h.make(h.PRELUDE)
    result["making"] = {
        "status": made.status,
        "open_files": made.open_files,
        "sp_initial": made.sp_initial,
        "sp_after_prepare": made.sp_after,
        "sp_back": made.sp_initial == made.sp_after,
        "image_bytes": len(made.image),
        "image_sha256": made.sha256,
        "stdout": made.stdout.decode(),
        "stderr": made.stderr.decode(),
    }
    data, stats, _ = h.write_module(made)
    result["module"] = stats
    mods = h.image_modules(made, (False,))
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "f.txt").write_bytes(b"alpha one\nbeta two\ngamma three\nbob four\n")
        Path(tmp, "s.pl").write_bytes(b"use POSIX; print floor(9.5), qq{ $0 @ARGV\\n};\n")
        rows = []
        for name, argv, kw in programs(tmp):
            a = h.run_image(argv, mods, cache_dir=False, **kw)
            b = h.run_plain(argv, cache_dir=False, **kw)
            rows.append(
                {
                    "name": name,
                    "argv": argv,
                    "same": h.same(a, b),
                    "image": h.shown(a),
                    "plain": h.shown(b),
                }
            )
        result["programs"] = rows
    result["all_same"] = all(r["same"] for r in rows)

    # Isolation: two fresh instances of one image.
    first = h.run_image(
        [
            "-e",
            "$main::leak = 42; no warnings; *POSIX::floor = sub { 99 }; print POSIX::floor(1), qq{\\n}",
        ],
        mods,
        cache_dir=False,
    )
    second = h.run_image(
        ["-e", "print defined $main::leak ? q{LEAK} : q{clean}, q{ }, POSIX::floor(2.5), qq{\\n}"],
        mods,
        cache_dir=False,
    )
    result["isolation"] = {
        "first": h.shown(first),
        "second": h.shown(second),
        "clean": second.stdout == b"clean 2\n",
    }

    # The seed.
    seeds, images = [], []
    for _ in range(3):
        m = h.make(h.PRELUDE)
        images.append(m.sha256)
        mm = h.image_modules(m, (False,))
        seeds.append(h.run_image(["-e", SEED], mm, cache_dir=False).stdout.decode())
    calls = [h.run_image(["-e", SEED], mods, cache_dir=False).stdout.decode() for _ in range(10)]
    orders = [h.run_image(["-e", ORDER], mods, cache_dir=False).stdout.decode() for _ in range(10)]
    plain_seeds = [h.run_plain(["-e", SEED], cache_dir=False).stdout.decode() for _ in range(3)]
    result["seed"] = {
        "image_sha256_of_3_makings": images,
        "hash_seed_of_3_makings": seeds,
        "makings_differ": len(set(images)) == 3 and len(set(seeds)) == 3,
        "hash_seed_of_10_calls_one_image": sorted(set(calls)),
        "calls_share_seed": len(set(calls)) == 1,
        "distinct_key_orders_in_10_calls": len(set(orders)),
        "plain_hash_seeds_3_calls": plain_seeds,
    }

    # The accepted drifts.
    drifts = []
    for name, argv in DRIFTS:
        a = h.run_image(argv, mods, cache_dir=False)
        b = h.run_plain(argv, cache_dir=False)
        drifts.append(
            {"name": name, "differs": not h.same(a, b), "image": h.shown(a), "plain": h.shown(b)}
        )
    result["accepted_drifts"] = drifts

    # A prelude with `use strict` and END: no hint leaks; END order as one script.
    m2 = h.make('use strict; use warnings; END { print "pre\\n" }')
    mm2 = h.image_modules(m2, (False,))
    result["prelude_hints_and_end"] = {
        "making_status": m2.status,
        "no_strict": h.shown(
            h.run_image(["-e", "$y = 2; print qq{ok $y\\n}"], mm2, cache_dir=False)
        ),
        "end_order": h.shown(h.run_image(["-e", 'END { print "call\\n" }'], mm2, cache_dir=False)),
    }
    # A prelude that leaves a file open, and one that dies.
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "f.txt").write_text("x\n")
        m3 = h.make("open our $F, q{<}, q{/data/f.txt} or die $!", grants=((tmp, "/data", False),))
    m4 = h.make('die "nope\\n"')
    m5 = h.make("exit 0")
    result["making_failures"] = {
        "open_file": {"status": m3.status, "open_files": m3.open_files},
        "die": {"status": m4.status, "stderr": m4.stderr.decode()},
        "exit_0": {"status": m5.status, "open_files": m5.open_files},
    }
    out.write_text(json.dumps(result, indent=1) + "\n")
    print(
        json.dumps({k: result[k] for k in ("making", "module", "all_same", "isolation")}, indent=1)
    )
    for r in rows:
        if not r["same"]:
            print("DIFFERS", r["name"], r["image"], r["plain"])
    print(
        json.dumps(
            {k: result[k] for k in ("seed", "prelude_hints_and_end", "making_failures")}, indent=1
        )
    )
    print([(d["name"], d["differs"]) for d in drifts])


if __name__ == "__main__":
    main(Path(sys.argv[1]))
