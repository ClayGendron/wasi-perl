"""Does a snapshot run a program as perl.wasm does? Each case runs in a fresh instance of each
module, twice, and the outputs are compared.

    python drift.py <research folder> <artifacts folder> OUT.json

A case is (name, argv, env, kind). kind "same": both modules must print the same bytes and exit
the same way. kind "varies": the output must differ between the two runs of one module, as
native perl's does (hash order, rand, the hash seed), and the result says whether it did on each.
Every call has the runner's preopens plus /data, a fresh writable copy of three small files, and
PWD unset unless the case sets it.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import wasmtime as wt

H = 'my %h = map { $_ => 1 } "a" .. "p"; print join("", keys %h), "\\n";'
CASES = [
    ("argv_0", ["-e", 'print "@ARGV|$0|", scalar(@ARGV), "\\n"', "a", "b c"], {}, "same"),
    ("n_file", ["-ne", "print if /b/", "/data/f.txt"], {}, "same"),
    ("p_i", ["-pi", "-e", "s/a/A/", "/data/g.txt"], {}, "same"),
    ("lane", ["-lane", "print $F[1]", "/data/f.txt"], {}, "same"),
    ("0777", ["-0777", "-ne", "print length, qq{\\n}", "/data/f.txt"], {}, "same"),
    ("l_0", ["-l", "-0072", "-ne", "print", "/data/f.txt"], {}, "same"),
    ("M_import", ["-MPOSIX=floor", "-e", 'print floor(2.5), "\\n"'], {}, "same"),
    ("use_posix", ["-e", 'use POSIX; print floor(2.5), " ", INT_MAX, "\\n"'], {}, "same"),
    ("no_use_posix", ["-e", 'print POSIX::floor(2.5), "\\n"'], {}, "same"),
    ("defined_sub", ["-e", 'print defined &POSIX::floor ? 1 : 0, "\\n"'], {}, "same"),
    ("inc_keys", ["-e", 'print join(",", sort keys %INC), "\\n"'], {}, "same"),
    ("inc_dirs", ["-e", 'print scalar(@INC), " @INC\\n"'], {}, "same"),
    ("env", ["-e", 'print join(",", map {"$_=$ENV{$_}"} sort keys %ENV), "\\n"'],
     {"FOO": "bar", "LANG": "C"}, "same"),
    ("pwd", ["-e", 'open my $f, "<", "f.txt" or die "no: $!"; print scalar <$f>'],
     {"PWD": "/data"}, "same"),
    ("basetime", ["-e", "print time - $^T < 5 && $^T > 1.7e9 ? qq{ok\\n} : qq{bad $^T\\n}"],
     {}, "same"),
    ("phases", ["-e", 'BEGIN { print "b\\n" } INIT { print "i\\n" } print "m\\n"; END { print "e\\n" }'],
     {}, "same"),
    ("strict_err", ["-e", "use strict; $x = 1;"], {}, "same"),
    ("die", ["-e", 'die "x\\n"'], {}, "same"),
    ("exit3", ["-e", "exit 3"], {}, "same"),
    ("warn_w", ["-w", "-e", 'my $x; print "a" . $x, "\\n"'], {}, "same"),
    ("use_warnings", ["-e", 'use warnings; my $x; print "a" . $x, "\\n"'], {}, "same"),
    ("file_line", ["-e", 'print __FILE__, " ", __LINE__, "\\n"'], {}, "same"),
    ("seps", ["-e", 'print $/ eq "\\n" ? 1 : 0, defined $\\ ? 1 : 0, defined $, ? 1 : 0, "\\n"'],
     {}, "same"),
    ("json", ["-MJSON::PP", "-e", 'print JSON::PP->new->canonical->encode({b=>1,a=>[1,2]}), "\\n"'],
     {}, "same"),
    ("dumper", ["-MData::Dumper", "-e", '$Data::Dumper::Sortkeys=1; print Dumper({a=>1,b=>[2]})'],
     {}, "same"),
    ("list_util", ["-MList::Util=sum,max", "-e", 'print sum(1..10), " ", max(3,9,2), "\\n"'],
     {}, "same"),
    ("encode", ["-MEncode", "-e", 'print length(encode("UTF-8", "\\x{263a}")), "\\n"'], {}, "same"),
    ("storable", ["-MStorable=dclone", "-e", 'print dclone([1,{a=>2}])->[1]{a}, "\\n"'], {}, "same"),
    ("locale", ["-MPOSIX", "-e", 'print setlocale(LC_ALL), "\\n"'], {"LC_ALL": "C.UTF-8"}, "same"),
    ("redefine_w", ["-w", "-MPOSIX", "-e", "print 1"], {}, "same"),
    ("glob_main", ["-e", 'print scalar(grep { /^POSIX::$/ } keys %main::), "\\n"'], {}, "same"),
    ("isa_cache", ["-MJSON::PP", "-Mmro", "-e", 'package My; our @ISA = ("JSON::PP::Boolean"); package main; print My->can("TO_JSON") ? 1 : 0, " ", scalar(@{mro::get_isarev("JSON::PP::Boolean")}), "\\n"'],
     {}, "same"),
    ("rand_seed_env", ["-e", 'print rand(), "\\n"'], {"PERL_RAND_SEED": "42"}, "same"),
    ("lang_utf8", ["-CS", "-e", 'print lc("\\x{C9}"), "\\n"'], {"LANG": "C.UTF-8"}, "same"),
    ("hash_order", ["-e", H], {}, "varies"),
    ("rand", ["-e", 'print rand(), "\\n"'], {}, "varies"),
    ("hash_seed", ["-MHash::Util=hash_seed", "-e", 'print unpack("H*", hash_seed()), "\\n"'],
     {}, "varies"),
    ("hash_seed_env", ["-MHash::Util=hash_seed", "-e", 'print unpack("H*", hash_seed()), "\\n"'],
     {"PERL_HASH_SEED": "0", "PERL_PERTURB_KEYS": "0"}, "same"),
    ("hash_order_env", ["-e", H], {"PERL_HASH_SEED": "0", "PERL_PERTURB_KEYS": "0"}, "same"),
    ("leak_set", ["-e", '$POSIX::planted = "secret"; *POSIX::floor = sub { 666 }; print "set\\n"'],
     {}, "same"),
    ("leak_check", ["-MPOSIX=floor", "-e", 'print defined $POSIX::planted ? "LEAK" : "clean", " ", floor(1.5), "\\n"'],
     {}, "same"),
]


def run(eng, pre, entry, argv, env, lib, dev, data):
    with tempfile.TemporaryDirectory() as t:
        w = wt.WasiConfig()
        w.argv = ["perl", *argv]
        w.env = sorted(env.items())
        w.stdout_file = str(Path(t, "out"))
        w.stderr_file = str(Path(t, "err"))
        w.preopen_dir(lib, "/usr/local/lib/perl5", False)
        w.preopen_dir(dev, "/dev", False)
        w.preopen_dir(str(data), "/data", True)
        store = wt.Store(eng)
        store.set_wasi(w)
        inst = pre.instantiate(store)
        code = 0
        try:
            inst.exports(store)[entry](store)
        except wt.ExitTrap as e:
            code = e.code
        except wt.Trap as e:
            code = f"trap: {str(e).splitlines()[0]}"
        except wt.WasmtimeError as e:  # a status of 126 or more: read it as the runner does
            if "invalid exit status" not in str(e):
                raise
            code = inst.exports(store)["wasi_perl_exit_status"](store)
        return code, Path(t, "out").read_text(errors="replace"), Path(t, "err").read_text(errors="replace")


def fresh_data(root: Path) -> Path:
    d = root / "data"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir()
    (d / "f.txt").write_text("a b c\nb c d\n:x:y\n")
    (d / "g.txt").write_text("abc\naaa\n")
    return d


def main() -> None:
    research, art, dest = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    lib, dev = str(art / "lib" / "perl5"), str(art / "dev")
    cfg = wt.Config()
    cfg.wasm_exceptions = True
    eng = wt.Engine(cfg)
    mods = {}
    for name, path, entry in [
        ("plain", art / "perl.wasm", "_start"),
        ("construct", research / "snap-construct.wasm", "run"),
        ("preload", research / "snap-preload.wasm", "run"),
    ]:
        linker = wt.Linker(eng)
        linker.define_wasi()
        mods[name] = (linker.instantiate_pre(wt.Module.from_file(eng, str(path))), entry)
    results = []
    with tempfile.TemporaryDirectory() as root:
        for case, argv, env, kind in CASES:
            row = {"case": case, "argv": argv, "env": env, "kind": kind}
            for name, (pre, entry) in mods.items():
                outs = []
                for _ in range(2):
                    d = fresh_data(Path(root))
                    r = run(eng, pre, entry, argv, env, lib, dev, d)
                    after = (d / "g.txt").read_text() if case == "p_i" else ""
                    outs.append([r[0], r[1] + after, r[2]])
                    time.sleep(0.002)  # the seed is the clock's microseconds
                row[name] = outs
            if kind == "same":
                for name in ("construct", "preload"):
                    row[f"{name}_same"] = row[name][0] == row["plain"][0]
            else:
                for name in mods:
                    row[f"{name}_varies"] = row[name][0][1] != row[name][1][1]
            results.append(row)
            flags = {k: v for k, v in row.items() if k.endswith(("_same", "_varies"))}
            print(case, flags, flush=True)
    Path(dest).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
