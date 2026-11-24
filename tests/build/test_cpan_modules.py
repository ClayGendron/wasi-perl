"""Checks on the modules that come from CPAN rather than from Perl's own tarball.

Two XS distributions, Cpanel-JSON-XS and Text-CSV_XS, are linked into perl.wasm; three pure-Perl
ones, JSON-MaybeXS, Text-CSV and Algorithm-Diff, are in the library. Each must do one real
thing exactly as native perl 5.42.3 does with the same five installed from the same pinned
tarballs (scripts/10_native.sh puts them in build/native-cpan, apart from the oracle's own
library). Hostile input must end as it ends natively, in an error the program can catch or a
result, never a trap. The parsers must add no import, and must earn their place: the JSON
decode of a 1.4 MB file of 20,000 rows must take under a tenth of JSON::PP's instructions.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from _build_helpers import BUILD, NATIVE_PERL, ROOT, Result

NATIVE_CPAN = BUILD / "native-cpan" / "lib" / "perl5"

# The five distributions at the versions recipes/cpan-xs and recipes/cpan-pure pin.
VERSIONS = {
    "Cpanel::JSON::XS": "4.83",
    "Text::CSV_XS": "1.65",
    "JSON::MaybeXS": "1.004008",
    "Text::CSV": "2.06",
    "Algorithm::Diff": "1.201",
}


@pytest.fixture(scope="session")
def native_cpan() -> Path:
    for need in (NATIVE_PERL, NATIVE_CPAN):
        if not need.exists():
            pytest.skip(f"{need.relative_to(ROOT)} is missing; run scripts/10_native.sh")
    return NATIVE_CPAN


@pytest.fixture
def native_with_cpan(native_cpan: Path):
    """native perl 5.42.3 with the five distributions on PERL5LIB, and nothing else."""

    def run(*args: str) -> Result:
        p = subprocess.run(
            [str(NATIVE_PERL), *args],
            env={"PERL5LIB": str(native_cpan)},
            capture_output=True,
            cwd="/",
            check=False,
        )
        return Result(p.returncode, p.stdout, p.stderr)

    return run


def test_native_has_the_pinned_versions(native_with_cpan):
    prog = 'for (@ARGV) { eval "require $_; 1" or die $@; print "$_ ", $_->VERSION, "\\n" }'
    n = native_with_cpan("-e", prog, *VERSIONS)
    assert (n.code, n.stderr) == (0, b""), n
    assert n.stdout.decode() == "".join(f"{m} {v}\n" for m, v in VERSIONS.items())


def test_the_oracle_perl_alone_has_none_of_them(native):
    # The five are installed apart, so the one-liner oracle's native perl sees no new module.
    for module in VERSIONS:
        n = native(f"-M{module}", "-e", "1")
        assert n.code != 0 and b"Can't locate" in n.stderr, (module, n)


# One real thing per module. Each program also prints the versions it ran, so equal output
# means equal versions as well as equal behaviour.
PROGRAMS = {
    "cpanel-round-trip": (
        'use Cpanel::JSON::XS; binmode STDOUT, ":utf8"; my $j = Cpanel::JSON::XS->new->canonical;'
        'my $d = $j->decode(q({"b":[1,2.5,"x",null,true,false],"a":{"c d":"\\u00e9\\u263a"},"n":-0.125e3}));'
        'print $j->encode($d), "\\n"; print ref $d->{b}[4], " ", $d->{b}[4] ? 1 : 0, " ", ord $d->{a}{"c d"}, "\\n";'
        'my $u = Cpanel::JSON::XS->new->utf8->canonical; my $t = $u->encode({k => "\\x{263a}", l => [1 .. 3]});'
        'print unpack("H*", $t), "\\n"; print $u->decode($t)->{k} eq "\\x{263a}" ? "same" : "differs", "\\n";'
        'print "$Cpanel::JSON::XS::VERSION\\n";'
    ),
    "maybexs-backend": (
        "use JSON::MaybeXS qw(encode_json decode_json JSON);"
        'print JSON, " $JSON::MaybeXS::VERSION\\n";'
        'my $d = decode_json(q([1,{"k":"v"},null])); print scalar @$d, " $d->[1]{k}\\n";'
        'print JSON::MaybeXS->new(canonical => 1)->encode({b => 2, a => [1]}), "\\n";'
        'print encode_json([JSON::MaybeXS::true, JSON::MaybeXS::false]), "\\n";'
    ),
    "text-csv-quoted-newlines": (
        "use Text::CSV; my $c = Text::CSV->new({binary => 1, auto_diag => 0});"
        'print Text::CSV->backend, " ", Text::CSV->VERSION, " ", Text::CSV->backend->VERSION, "\\n";'
        'open my $fh, "<", \\qq{id,note,n\\n1,"two\\nlines, with a comma",3\\n2,"said ""hi""",4\\n} or die;'
        'while (my $r = $c->getline($fh)) { print join("|", map { s/\\n/\\\\n/gr } @$r), "\\n" }'
        'print $c->eof ? "eof" : "not eof", "\\n";'
        '$c->combine("a b", q{x"y}, "1,2") or die; print $c->string, "\\n";'
    ),
    "algorithm-diff-hunks": (
        "use Algorithm::Diff qw(diff sdiff LCS);"
        "my @a = qw(a b c e h j l m n p); my @b = qw(b c d e f j k l m r s t);"
        'for my $h (diff(\\@a, \\@b)) { print join(" ", map { "$_->[0]$_->[1]$_->[2]" } @$h), "\\n" }'
        'print join("", LCS(\\@a, \\@b)), "\\n";'
        'print join(" ", map { join("", @$_) } sdiff([qw(x y)], [qw(x z)])), "\\n";'
        'print "$Algorithm::Diff::VERSION\\n";'
    ),
}


@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_each_module_does_its_work_as_natively(guest, native_with_cpan, name):
    prog = PROGRAMS[name]
    g, n = guest("-e", prog), native_with_cpan("-e", prog)
    assert (n.code, n.stderr) == (0, b""), n
    assert (g.code, g.stdout, g.stderr, g.error) == (n.code, n.stdout, n.stderr, "")


def test_the_selectors_pick_the_linked_parsers(guest):
    prog = (
        "use JSON::MaybeXS (); use Text::CSV ();"
        'print JSON::MaybeXS::JSON(), " ", Text::CSV->backend, "\\n";'
        'print join(" ", map { eval "require $_; 1" or die $@; "$_=" . $_->VERSION } @ARGV), "\\n";'
    )
    r = guest("-e", prog, *VERSIONS)
    assert (r.code, r.stderr) == (0, b""), r
    assert r.stdout.decode().splitlines() == [
        "Cpanel::JSON::XS Text::CSV_XS",
        " ".join(f"{m}={v}" for m, v in VERSIONS.items()),
    ]


# Hostile input: each case must end in a Perl error the program catches, or a result, exactly
# as natively.
HOSTILE = r"""
use strict; use warnings; use Cpanel::JSON::XS (); use Text::CSV_XS (); use Text::CSV ();
my $j = Cpanel::JSON::XS->new; my $u = Cpanel::JSON::XS->new->utf8;
for my $case (["deep 100000", $j, "[" x 100000], ["deep 512", $j, ("[" x 512) . ("]" x 512)],
              ["deep 513", $j, ("[" x 513) . ("]" x 513)], ["truncated", $j, '{"a": [1, 2'],
              ["bad utf8", $j, "[\"\xff\xfe\"]"], ["bad utf8 strict", $u, "[\"\xff\xfe\"]"],
              ["huge number", $j, "[" . ("9" x 5000) . "]"], ["huge exponent", $j, "[1e999999]"],
              ["nul", $j, "[\"a\x00b\"]"], ["trailing", $j, "[1] x"], ["empty", $j, ""],
              ["lone surrogate", $u, '["\ud800"]']) {
    my ($name, $p, $text) = @$case;
    my $r = eval { my $d = $p->decode($text); "ok " . length($p->encode($d)) } // "died: " . ($@ =~ s/ at .*//sr);
    print "json $name: $r\n";
}
for my $class ("Text::CSV_XS", "Text::CSV") {
    my $c = $class->new({binary => 1});
    for my $case (["unterminated quote", qq{a,"b,c\n}], ["quote in field", qq{a,b"c,d\n}],
                  ["nul", "a,\x00,b\n"], ["long field", ("x" x 1_000_000) . ",y\n"],
                  ["many fields", join(",", 1 .. 100_000) . "\n"]) {
        my ($name, $text) = @$case;
        my $ok = $c->parse($text =~ s/\n\z//r);
        print "$class $name: ", ($ok ? "ok " . scalar($c->fields) : "error " . (0 + $c->error_diag)), "\n";
    }
}
print "json true is JSON::PP::Boolean: ", (Cpanel::JSON::XS::true()->isa("JSON::PP::Boolean") ? 1 : 0), "\n";
print "encode 5.0: ", Cpanel::JSON::XS->new->encode([5 * 1.0, 10 * 0.5, "5"]), "\n";
"""


def test_hostile_input_ends_as_natively(guest, native_with_cpan):
    g, n = guest("-e", HOSTILE), native_with_cpan("-e", HOSTILE)
    assert (n.code, n.stderr) == (0, b""), n
    lines = n.stdout.decode().splitlines()
    assert (
        "json deep 100000: died: json text or perl structure exceeds maximum nesting level"
        in (lines[0])
    )
    assert "Text::CSV_XS unterminated quote: error 2027" in lines
    assert 'encode 5.0: [5,5.0,"5"]' in lines
    assert (g.code, g.stdout, g.stderr, g.error) == (n.code, n.stdout, n.stderr, "")


def test_the_import_list_is_unchanged(perl_wasm):
    from test_perl_wasm import _imports
    from test_randomness import IMPORTS_BEFORE, WASI

    imports = _imports(perl_wasm.read_bytes())
    assert {m for m, _ in imports} == {WASI}, imports
    assert sorted(n for _, n in imports) == sorted({*IMPORTS_BEFORE, "random_get"})


def _rows_json(path: Path) -> None:
    """A JSON array of 20,000 objects, 1,355,560 bytes."""
    rows = [
        {"id": i, "name": f"n{i}", "tags": ["a", "b", i % 5], "v": i * 0.5} for i in range(20_000)
    ]
    path.write_text(json.dumps(rows))


JSON_SUM = 'my $d = decode_json($_); my $s = 0; $s += $_->{v} for @$d; print scalar(@$d), " $s\\n"'


def test_the_linked_decoder_takes_under_a_tenth_of_json_pps_instructions(perl_wasm, tmp_path):
    pytest.importorskip("wasmtime")
    from test_reads_and_writes import _fuel

    data = tmp_path / "data"
    data.mkdir()
    _rows_json(data / "t.json")
    assert (data / "t.json").stat().st_size == 1_355_560
    used = {}
    for module in ("JSON::PP", "Cpanel::JSON::XS"):
        args = (f"-M{module}", "-0777", "-ne", JSON_SUM, "/data/t.json")
        used[module], out = _fuel(perl_wasm, data, tmp_path, *args)
        assert out == b"20000 99995000\n", (module, out)
    assert used["Cpanel::JSON::XS"] * 10 < used["JSON::PP"], used
