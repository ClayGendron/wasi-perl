"""Does a call from a preload image run a program as perl.wasm does? The startup study's 41
drift cases, ported, with the cases added since. Each case runs twice from perl.wasm and twice
from an image, each run in a fresh call with a fresh writable copy of three small files at /data.

A case of kind "same" must give the same exit, output and trap from both. A case of kind
"varies" must give different output in two runs, as native perl's does (hash order, rand, the
hash seed). The sets of cases that differ are exact: a new difference fails, and so does an
accepted one that disappears, so `docs/snapshots.md`, which lists the accepted ones, and this
file change together."""

from __future__ import annotations

from pathlib import Path

import pytest

from snapshot.images import call, shown
from wasi_perl import Grant, make_snapshot

pytestmark = pytest.mark.slow

PHASES = 'BEGIN { print "b\\n" } INIT { print "i\\n" } print "m\\n"; END { print "e\\n" }'
ISA = (
    'package My; our @ISA = ("JSON::PP::Boolean"); package main; print My->can("TO_JSON") ? 1 : 0,'
    ' " ", scalar(@{mro::get_isarev("JSON::PP::Boolean")}), "\\n"'
)
CHECK = 'print defined $POSIX::planted ? "LEAK" : "clean", " ", floor(1.5), "\\n"'
H = 'my %h = map { $_ => 1 } "a" .. "p"; print join("", keys %h), "\\n";'
SEED = ["-MHash::Util=hash_seed", "-e", 'print unpack("H*", hash_seed()), "\\n"']
ZERO_SEED = {"PERL_HASH_SEED": "0", "PERL_PERTURB_KEYS": "0"}
# (name, argv, env, cwd, kind)
CASES = [
    ("argv_0", ["-e", 'print "@ARGV|$0|", scalar(@ARGV), "\\n"', "a", "b c"], {}, None, "same"),
    ("n_file", ["-ne", "print if /b/", "/data/f.txt"], {}, None, "same"),
    ("p_i", ["-pi", "-e", "s/a/A/", "/data/g.txt"], {}, None, "same"),
    ("lane", ["-lane", "print $F[1]", "/data/f.txt"], {}, None, "same"),
    ("0777", ["-0777", "-ne", "print length, qq{\\n}", "/data/f.txt"], {}, None, "same"),
    ("l_0", ["-l", "-0072", "-ne", "print", "/data/f.txt"], {}, None, "same"),
    ("M_import", ["-MPOSIX=floor", "-e", 'print floor(2.5), "\\n"'], {}, None, "same"),
    ("use_posix", ["-e", 'use POSIX; print floor(2.5), " ", INT_MAX, "\\n"'], {}, None, "same"),
    ("no_use_posix", ["-e", 'print POSIX::floor(2.5), "\\n"'], {}, None, "same"),
    ("defined_sub", ["-e", 'print defined &POSIX::floor ? 1 : 0, "\\n"'], {}, None, "same"),
    ("inc_keys", ["-e", 'print join(",", sort keys %INC), "\\n"'], {}, None, "same"),
    ("inc_dirs", ["-e", 'print scalar(@INC), " @INC\\n"'], {}, None, "same"),
    ("env", ["-e", 'print join(",", map {"$_=$ENV{$_}"} sort keys %ENV), "\\n"'],
     {"FOO": "bar", "LANG": "C"}, None, "same"),
    ("pwd", ["-e", 'open my $f, "<", "f.txt" or die "no: $!"; print scalar <$f>'],
     {}, "/data", "same"),
    ("basetime", ["-e", "print time - $^T < 5 && $^T > 1.7e9 ? qq{ok\\n} : qq{bad $^T\\n}"],
     {}, None, "same"),
    ("phases", ["-e", PHASES],
     {}, None, "same"),
    ("strict_err", ["-e", "use strict; $x = 1;"], {}, None, "same"),
    ("die", ["-e", 'die "x\\n"'], {}, None, "same"),
    ("exit3", ["-e", "exit 3"], {}, None, "same"),
    ("warn_w", ["-w", "-e", 'my $x; print "a" . $x, "\\n"'], {}, None, "same"),
    ("use_warnings", ["-e", 'use warnings; my $x; print "a" . $x, "\\n"'], {}, None, "same"),
    ("file_line", ["-e", 'print __FILE__, " ", __LINE__, "\\n"'], {}, None, "same"),
    ("seps", ["-e", 'print $/ eq "\\n" ? 1 : 0, defined $\\ ? 1 : 0, defined $, ? 1 : 0, "\\n"'],
     {}, None, "same"),
    ("json", ["-MJSON::PP", "-e", 'print JSON::PP->new->canonical->encode({b=>1,a=>[1,2]}), "\\n"'],
     {}, None, "same"),
    ("dumper", ["-MData::Dumper", "-e", '$Data::Dumper::Sortkeys=1; print Dumper({a=>1,b=>[2]})'],
     {}, None, "same"),
    ("list_util", ["-MList::Util=sum,max", "-e", 'print sum(1..10), " ", max(3,9,2), "\\n"'],
     {}, None, "same"),
    ("encode", ["-MEncode", "-e", 'print length(encode("UTF-8", "\\x{263a}")), "\\n"'],
     {}, None, "same"),
    ("storable", ["-MStorable=dclone", "-e", 'print dclone([1,{a=>2}])->[1]{a}, "\\n"'],
     {}, None, "same"),
    ("locale", ["-MPOSIX", "-e", 'print setlocale(LC_ALL), "\\n"'], {"LC_ALL": "C.UTF-8"}, None,
     "same"),
    ("redefine_w", ["-w", "-MPOSIX", "-e", "print 1"], {}, None, "same"),
    ("glob_main", ["-e", 'print scalar(grep { /^POSIX::$/ } keys %main::), "\\n"'], {}, None,
     "same"),
    ("isa_cache", ["-MJSON::PP", "-Mmro", "-e", ISA],
     {}, None, "same"),
    ("rand_seed_env", ["-e", 'print rand(), "\\n"'], {"PERL_RAND_SEED": "42"}, None, "same"),
    ("lang_utf8", ["-CS", "-e", 'print lc("\\x{C9}"), "\\n"'], {"LANG": "C.UTF-8"}, None, "same"),
    ("hash_order", ["-e", H], {}, None, "varies"),
    ("rand", ["-e", 'print rand(), "\\n"'], {}, None, "varies"),
    ("hash_seed", SEED, {}, None, "varies"),
    ("hash_seed_env", SEED, ZERO_SEED, None, "same"),
    ("hash_order_env", ["-e", H], ZERO_SEED, None, "same"),
    ("leak_set", ["-e", '$POSIX::planted = "secret"; *POSIX::floor = sub { 666 }; print "set\\n"'],
     {}, None, "same"),
    ("leak_check", ["-MPOSIX=floor", "-e", CHECK],
     {}, None, "same"),
    # Added with the image: the variables an image cannot honour, so the call runs perl.wasm.
    ("perturb_env", ["-e", H], {"PERL_PERTURB_KEYS": "0"}, None, "falls back"),
    ("json_pp_use_b", ["-MJSON::PP", "-e", 'print JSON::PP->new->encode([1, "1"]), "\\n"'],
     {"PERL_JSON_PP_USE_B": "1"}, None, "falls back"),
    ("hash_seed_empty", SEED, {"PERL_HASH_SEED": ""}, None, "falls back"),
    # A croak while the switches are read exits with errno, which must be perl.wasm's.
    ("bad_switch", ["-Q", "-e", "1"], {}, None, "same"),
    ("no_code_e", ["-e"], {}, None, "same"),
    ("version", ["-v"], {}, None, "same"),
]  # fmt: skip
FALLS_BACK = {"hash_seed_env", "hash_order_env", "perturb_env", "json_pp_use_b", "hash_seed_empty"}
ACCEPTED_EMPTY = {"hash_seed"}
ACCEPTED_PACKAGES = {"no_use_posix", "defined_sub", "inc_keys", "hash_seed"}


def _data(root: Path) -> Path:
    d = root / f"data{len(list(root.iterdir()))}"
    d.mkdir()
    (d / "f.txt").write_text("a b c\nb c d\n:x:y\n")
    (d / "g.txt").write_text("abc\naaa\n")
    return d


def _twice(image, argv, env, cwd, root: Path, name: str) -> list[tuple]:
    outs = []
    for _ in range(2):
        d = _data(root)
        done = call(argv, image, env=env, cwd=cwd, grants=[Grant(d, "/data", writable=True)])
        after = (d / "g.txt").read_text() if name == "p_i" else ""
        outs.append((*shown(done), after, done.image_sha256))
    return outs


def differences(image, root: Path) -> tuple[set[str], dict[str, tuple]]:
    """The cases where the image does not run as perl.wasm does, and each case's runs."""
    differ, runs = set(), {}
    for name, argv, env, cwd, kind in CASES:
        plain = _twice(False, argv, env, cwd, root, name)
        from_image = _twice(image, argv, env, cwd, root, name)
        runs[name] = (plain, from_image)
        assert all(r[-1] is None for r in plain)
        if name in FALLS_BACK:
            # The call ran perl.wasm: no image is named, and its answer is perl.wasm's.
            assert all(r[-1] is None for r in from_image), name
        else:
            assert all(r[-1] == image.origin.image_sha256 for r in from_image), name
        if kind == "falls back" and plain[0][:-1] != plain[1][:-1]:
            kind = "varies"  # perl.wasm's own answer varies: the image's must too
        if kind == "varies":
            assert plain[0][1] != plain[1][1], name  # native perl varies here
            if from_image[0][1] == from_image[1][1]:
                differ.add(name)
        elif from_image[0][:-1] != plain[0][:-1]:
            differ.add(name)
    return differ, runs


def test_the_empty_image_differs_only_in_its_seed(empty_image, tmp_path) -> None:
    differ, runs = differences(empty_image, tmp_path)
    assert differ == ACCEPTED_EMPTY, {n: runs[n] for n in differ ^ ACCEPTED_EMPTY}


def test_the_package_image_differs_only_as_documented(package_image, tmp_path) -> None:
    differ, runs = differences(package_image, tmp_path)
    assert differ == ACCEPTED_PACKAGES, {n: runs[n] for n in differ ^ ACCEPTED_PACKAGES}


# Every variable of perlvar with a built-in meaning that perl.wasm starts with a fixed value and
# that the second parse does not set, each set by the prelude; the calls print each.
PUNCTUATION_PRELUDE = r"""
$_ = "prelude"; @_ = (1, 2); $, = "|"; $\ = "!\n"; $; = ":"; $^I = ".bak"; $^A = "acc";
$^L = "FF"; $: = "x"; $^F = 9; $^P = 0x100; $^D = 1;
"abc" =~ /(b)/;
our @kept = <STDIN>;
$| = 1; $= = 10; $- = 3; $% = 2; $~ = "FMT"; $^ = "TOP";
${^UTF8CACHE} = 0; ${^RE_TRIE_MAXBUF} = 7; ${^MAX_NESTED_EVAL_BEGIN_BLOCKS} = 5;
${^RE_COMPILE_RECURSION_LIMIT} = 99;
$^C = 1;
"""
PUNCTUATION_CALLS = {
    "default": r"print defined $_ ? 1 : 0, scalar(@_), defined $, ? 1 : 0, defined $\ ? 1 : 0;"
    r' print "\n";',
    "subsep": r'my %h; $h{1,2} = 1; print join(",", map { length } keys %h), "\n";',
    "inplace": r'print defined $^I ? "set" : "unset", "\n";',
    "accumulator": r'print length($^A), " ", $^L eq "\f" ? 1 : 0, " ", $: eq " \n-" ? 1 : 0, "\n";',
    "fd_max": r'print $^F, " ", $^P, " ", $^C, " ", $^D, "\n";',
    "match": r'print defined $1 ? "kept" : "none", " ", defined $& ? 1 : 0, "\n";',
    "line": r'print defined $. ? "line $." : "no line", "\n";',
    "handles": r'print "$| $= $- $% $~ $^\n";',
    "tunables": r'print "${^UTF8CACHE} ${^RE_TRIE_MAXBUF} ${^MAX_NESTED_EVAL_BEGIN_BLOCKS}\n";',
    "argv": r'print defined $ARGV ? "set" : "unset", "\n";',
    "stdin_line": r'my $x = <STDIN>; print "$x $.\n";',
}


@pytest.mark.parametrize("name", list(PUNCTUATION_CALLS))
def test_a_prelude_leaves_no_special_variable_set(name) -> None:
    from snapshot.images import image_set

    image = _punctuation_image(image_set())
    argv = ["-e", PUNCTUATION_CALLS[name]]
    stdin = b"one\n" if name == "stdin_line" else None
    plain = call(argv, False, stdin=stdin)
    from_image = call(argv, image, stdin=stdin)
    assert shown(from_image) == shown(plain), name


_punctuation: list = []


def _punctuation_image(found):
    if not _punctuation:
        _punctuation.append(make_snapshot(PUNCTUATION_PRELUDE, artifacts=found, cache_dir=False))
    return _punctuation[0]
