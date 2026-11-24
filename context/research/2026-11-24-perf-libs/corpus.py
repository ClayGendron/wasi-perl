"""An agent-workload corpus for wasi-perl: the inputs, the programs, and the runs that time them.

Subcommands (run with `uv run python corpus.py ...` from the worktree for wasi-perl and native
perl, and with the bench venv's python for wasi-python, which `uv run` does not install):

  data DIR                          write the inputs (deterministic: no clock, no randomness)
  fuel DIR OUT.json [NAME...]       guest instructions per program under wasi-perl (metered engine)
  wall DIR OUT.json [NAME...]       median wall ms per call under wasi-perl, in one warm process
  native DIR PERL EXTLIB OUT.json [NAME...]  median wall ms per program under native perl, as subprocesses
  py DIR OUT.json                   fuel and median wall ms under wasi-python (snapshot mode)

Every program prints a short answer. The wasi-perl and native answers are compared byte for byte
in `report.py`. A program whose name has a `+module` suffix needs that module, statically linked
(`WASI_PERL_ARTIFACTS` points at a set that has it) or, for a pure-Perl one, in EXTLIB.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

EXT = "/ext"  # where pure-Perl modules that are not in core are granted, read-only


class Lcg:
    """A small linear congruential generator, so the inputs never depend on Python's random."""

    def __init__(self, seed: int) -> None:
        self.x = seed

    def __call__(self, n: int) -> int:
        self.x = (self.x * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return (self.x >> 33) % n


WORDS = (
    "the of and to in is was for on that with as by at from his her it an were are which this be "
    "or has had not but they one all their there been have also first new more after who its into "
    "two time other some can than only over most such used would these between many may system "
    "state under during known city world school part later about made where number group three "
    "file error request worker value table record agent shell program parse token stream buffer"
).split()
UNI = (
    "Straße STRASSE Ärger ÄRGER Öl Übel ÉCOLE école Ça façon Σίσυφος ΣΊΣΥΦΟΣ μάθημα Москва МОСКВА "
    "привет ПРИВЕТ İstanbul ǅemal ﬁle Ångström ΑΘΗΝΑ αθηνα naïve NAÏVE crème CRÈME"
).split()


def make_data(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    r = Lcg(42)
    # The baseline's inputs, unchanged: a simple CSV, a log and a JSON array.
    with open(d / "t.csv", "w", newline="\n") as f:
        for i in range(200_000):
            f.write(f"{i},name{i % 977},{(i * 7919) % 10007},{'yes' if i % 3 else 'no'}\n")
    with open(d / "t.log", "w", newline="\n") as f:
        for i in range(200_000):
            level = f"ERROR {i % 500}" if i % 7 == 0 else "INFO ok"
            f.write(f"2026-11-24T10:{i % 60:02d}:{i % 59:02d} worker{i % 16} {level} request {i}\n")
    rows = [{"id": i, "name": f"n{i}", "tags": ["a", "b", i % 5], "v": i * 0.5} for i in range(20_000)]
    (d / "t.json").write_text(json.dumps(rows))
    (d / "small.json").write_text(json.dumps(rows[:30]))  # one row's worth: about 2 KB
    # A CSV with quoted fields: commas and doubled quotes inside them, as exports have.
    with open(d / "q.csv", "w", newline="\n") as f:
        f.write('id,name,note,amount,flag\n')
        for i in range(50_000):
            name = f'"{WORDS[r(len(WORDS))]}, {WORDS[r(len(WORDS))]} {i % 97}"'
            note = f'"said ""{WORDS[r(len(WORDS))]}"" twice"' if i % 4 == 0 else WORDS[r(len(WORDS))]
            f.write(f"{i},{name},{note},{r(100000) / 100:.2f},{'yes' if r(3) else 'no'}\n")
    # English-like prose for word counts: 2 MB.
    with open(d / "words.txt", "w", newline="\n") as f:
        size = 0
        while size < 2_000_000:
            line = " ".join(WORDS[r(len(WORDS))] for _ in range(4 + r(12)))
            line = line[0].upper() + line[1:] + ".\n"
            f.write(line)
            size += len(line)
    # Mixed-script UTF-8 text for case folding: about 1 MB.
    with open(d / "uni.txt", "w", encoding="utf-8", newline="\n") as f:
        for _ in range(20_000):
            f.write(" ".join(UNI[r(len(UNI))] if r(3) == 0 else WORDS[r(len(WORDS))] for _ in range(8)) + "\n")
    # Two versions of a 5,000-line file, the second with about 2% of its lines changed.
    a = [f"line {i}: {' '.join(WORDS[r(len(WORDS))] for _ in range(6))}\n" for i in range(5_000)]
    b = list(a)
    for _ in range(100):
        k = r(len(b))
        op = r(3)
        if op == 0:
            b[k] = b[k].replace("line", "LINE")
        elif op == 1:
            del b[k]
        else:
            b.insert(k, f"added {k}\n")
    (d / "a.txt").write_text("".join(a))
    (d / "b.txt").write_text("".join(b))
    # A source-like tree: 40 folders of 50 files, each about 40 lines.
    for i in range(40):
        sub = d / "tree" / f"pkg{i:02d}"
        sub.mkdir(parents=True, exist_ok=True)
        for j in range(50):
            lines = []
            for k in range(40):
                w = " ".join(WORDS[r(len(WORDS))] for _ in range(5))
                tag = f"TODO({r(9)})" if r(40) == 0 else "ok"
                lines.append(f"    {w} = {i * 1000 + j * 40 + k}  # {tag}\n")
            (sub / f"mod{j:02d}.txt").write_text("".join(lines))
    # A YAML list of 2,000 records, block style, as config and fixtures are written.
    with open(d / "t.yaml", "w", newline="\n") as f:
        for i in range(2_000):
            f.write(f"- id: {i}\n  name: n{i}\n  owner: {WORDS[r(len(WORDS))]}\n")
            f.write(f"  tags:\n    - a\n    - b{i % 5}\n  size: {r(10000)}\n")
    # A template and the values to fill it with.
    (d / "tpl.txt").write_text(
        "Dear {{name}}, your order {{order}} of {{qty}} x {{item}} ships on {{date}} to {{city}}.\n"
    )


# Each program: the perl argv (paths under /data), and the Python equivalent where the comparison
# is fair (same algorithm class, stdlib only). Programs print a short answer.
JSON_SUM = 'my $d = decode_json($_); my $s = 0; $s += $_->{v} for @$d; print scalar(@$d), " $s\\n"'
BUILD = 'my @r = map { {id => $_, name => "n$_", tags => ["a", "b", $_ % 5], v => $_ * 0.5} } 0 .. 19999;'
PY_BUILD = 'r = [{"id": i, "name": f"n{i}", "tags": ["a", "b", i % 5], "v": i * 0.5} for i in range(20000)]\n'
TOP = 'END { my @k = sort { $c{$b} <=> $c{$a} || $a cmp $b } keys %c; print "$c{$_} $_\\n" for @k[0 .. 19] }'
GREP_C = r"""
for my $f (sort glob "/data/tree/*/*.txt") {
    open my $h, "<", $f or die "$f: $!"; my @l = <$h>; close $h; my $last = -1;
    for my $i (0 .. $#l) {
        next unless $l[$i] =~ /TODO\([0-3]\)/;
        my ($lo, $hi) = ($i - 2 < 0 ? 0 : $i - 2, $i + 2 > $#l ? $#l : $i + 2);
        print "--\n" if $last >= 0 && $lo > $last + 1;
        $lo = $last + 1 if $lo <= $last;
        print "$f", ($_ == $i ? ":" : "-"), $_ + 1, ($_ == $i ? ":" : "-"), $l[$_] for $lo .. $hi;
        $last = $hi;
    }
}
"""
PY_GREP_C = r"""
import glob, re
r = re.compile(r"TODO\([0-3]\)")
out = []
for f in sorted(glob.glob("/data/tree/*/*.txt")):
    l = open(f).readlines(); last = -1
    for i, s in enumerate(l):
        if not r.search(s): continue
        lo, hi = max(i - 2, 0), min(i + 2, len(l) - 1)
        if last >= 0 and lo > last + 1: out.append("--\n")
        lo = max(lo, last + 1)
        for k in range(lo, hi + 1):
            c = ":" if k == i else "-"
            out.append(f"{f}{c}{k + 1}{c}{l[k]}")
        last = hi
print("".join(out), end="")
"""
CSV_RE = r"""
while (<>) { next if $. == 1; chomp; my @f;
  while (/\G(?:"((?:[^"]|"")*)"|([^,]*))(,|$)/g) { my ($q, $p, $end) = ($1, $2, $3); push @f, defined $q ? $q =~ s/""/"/gr : $p; last if $end eq "" }
  $n++; $s += $f[3]; $len += length $f[1] }
print "$n ", sprintf("%.2f", $s), " $len\n";
"""
CSV_PW = r"""
use Text::ParseWords;
while (<>) { next if $. == 1; chomp; my @f = parse_line(",", 0, $_); $n++; $s += $f[3]; $len += length $f[1] }
print "$n ", sprintf("%.2f", $s), " $len\n";
"""
CSV_XS = r"""
use Text::CSV_XS;
my $c = Text::CSV_XS->new({binary => 1}); open my $h, "<", "/data/q.csv" or die; $c->getline($h);
while (my $f = $c->getline($h)) { $n++; $s += $f->[3]; $len += length $f->[1] }
print "$n ", sprintf("%.2f", $s), " $len\n";
"""
PY_CSV = """import csv
n = 0; s = 0.0; ln = 0
with open("/data/q.csv", newline="") as f:
    rd = csv.reader(f); next(rd)
    for row in rd:
        n += 1; s += float(row[3]); ln += len(row[1])
print(n, f"{s:.2f}", ln)
"""
REPORT = r"""
while (<>) { chomp; my @f = split /,/; $s{$f[1]} += $f[2]; $n{$f[1]}++; $y{$f[1]}++ if $f[3] eq "yes" }
printf "%-10s %8s %12s %10s %6s\n", "name", "rows", "sum", "mean", "yes%";
for my $k (sort { $s{$b} <=> $s{$a} || $a cmp $b } keys %s) {
  printf "%-10s %8d %12d %10.3f %5.1f%%\n", $k, $n{$k}, $s{$k}, $s{$k} / $n{$k}, 100 * ($y{$k} // 0) / $n{$k} }
"""
PY_REPORT = """s = {}; n = {}; y = {}
for line in open("/data/t.csv"):
    f = line.rstrip("\\n").split(",")
    s[f[1]] = s.get(f[1], 0) + int(f[2]); n[f[1]] = n.get(f[1], 0) + 1
    if f[3] == "yes": y[f[1]] = y.get(f[1], 0) + 1
out = ["%-10s %8s %12s %10s %6s" % ("name", "rows", "sum", "mean", "yes%")]
for k in sorted(s, key=lambda k: (-s[k], k)):
    out.append("%-10s %8d %12d %10.3f %5.1f%%" % (k, n[k], s[k], s[k] / n[k], 100 * y.get(k, 0) / n[k]))
print("\\n".join(out))
"""
DIFF_LCS = r"""
use Algorithm::Diff qw(diff);
open my $a, "<", "/data/a.txt" or die; open my $b, "<", "/data/b.txt" or die;
my @a = <$a>; my @b = <$b>; my ($h, $d, $i) = (0, 0, 0);
for my $hunk (diff(\@a, \@b)) { $h++; for (@$hunk) { $_->[0] eq "-" ? $d++ : $i++ } }
print "$h $d $i\n";
"""
DIFF_SET = r"""
open my $a, "<", "/data/a.txt" or die; open my $b, "<", "/data/b.txt" or die;
my %b; $b{$_}++ while <$b>; my (%a, $gone); while (<$a>) { $a{$_}++; $gone++ unless $b{$_} }
my $new = grep { !$a{$_} } keys %b; print "$gone $new\n";
"""
PY_DIFF = """import difflib
a = open("/data/a.txt").readlines(); b = open("/data/b.txt").readlines()
h = d = i = 0
for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
    if tag == "equal": continue
    h += 1; d += i2 - i1; i += j2 - j1
print(h, d, i)
"""
YAML_CORE = 'use CPAN::Meta::YAML; local $/; my $y = CPAN::Meta::YAML->read_string(<>)->[0]; my $s = 0; $s += $_->{size} for @$y; print scalar(@$y), " $s\\n"'
TPL = r"""
open my $t, "<", "/data/tpl.txt" or die; my $tpl = <$t>; my $out = "";
for my $i (1 .. 20000) {
  my %v = (name => "user$i", order => "A$i", qty => $i % 9 + 1, item => "widget", date => "2026-12-0" . ($i % 9 + 1), city => "c" . ($i % 50));
  (my $s = $tpl) =~ s/\{\{(\w+)\}\}/$v{$1}/g; $out .= $s }
print length($out), "\n";
"""
PY_TPL = """import re
tpl = open("/data/tpl.txt").read(); r = re.compile(r"\\{\\{(\\w+)\\}\\}"); out = []
for i in range(1, 20001):
    v = {"name": f"user{i}", "order": f"A{i}", "qty": str(i % 9 + 1), "item": "widget",
         "date": "2026-12-0" + str(i % 9 + 1), "city": "c" + str(i % 50)}
    out.append(r.sub(lambda m: v[m.group(1)], tpl))
print(len("".join(out)))
"""

P: dict[str, dict] = {
    # JSON
    "json_decode": {"perl": ["-MJSON::PP", "-0777", "-ne", JSON_SUM, "/data/t.json"],
                    "py": 'import json\nd = json.load(open("/data/t.json"))\nprint(len(d), sum(x["v"] for x in d))'},
    "json_decode+cpanel": {"perl": ["-MCpanel::JSON::XS", "-0777", "-ne", JSON_SUM, "/data/t.json"]},
    "json_decode+maybexs": {"perl": ["-MJSON::MaybeXS", "-0777", "-ne", JSON_SUM, "/data/t.json"]},
    "json_decode+json": {"perl": ["-MJSON", "-0777", "-ne", JSON_SUM, "/data/t.json"]},
    "json_decode+json_cpanel": {"perl": ["-MJSON", "-0777", "-ne", JSON_SUM, "/data/t.json"],
                                "env": {"PERL_JSON_BACKEND": "Cpanel::JSON::XS"}},
    "json_small": {"perl": ["-MJSON::PP", "-0777", "-ne", JSON_SUM, "/data/small.json"],
                   "py": 'import json\nd = json.load(open("/data/small.json"))\nprint(len(d), sum(x["v"] for x in d))'},
    "json_small+cpanel": {"perl": ["-MCpanel::JSON::XS", "-0777", "-ne", JSON_SUM, "/data/small.json"]},
    "load_jsonpp": {"perl": ["-MJSON::PP", "-e", "1"]},
    "load_cpanel+cpanel": {"perl": ["-MCpanel::JSON::XS", "-e", "1"]},
    "load_textcsv_xs+csv_xs": {"perl": ["-MText::CSV_XS", "-e", "1"]},
    "load_none": {"perl": ["-e", "1"]},
    "json_encode": {"perl": ["-MJSON::PP", "-e", BUILD + ' print length(JSON::PP->new->canonical->encode(\\@r)), "\\n"'],
                    "py": "import json\n" + PY_BUILD + "print(len(json.dumps(r, sort_keys=True, separators=(',', ':'))))"},
    "json_encode+cpanel": {"perl": ["-MCpanel::JSON::XS", "-e", BUILD + ' print length(Cpanel::JSON::XS->new->canonical->encode(\\@r)), "\\n"']},
    "jq_select": {"perl": ["-MJSON::PP", "-0777", "-ne", 'print "$_->{name}\\n" for grep { $_->{tags}[2] == 3 && $_->{v} > 5000 } @{decode_json($_)}', "/data/t.json"],
                  "py": 'import json\nprint("\\n".join(x["name"] for x in json.load(open("/data/t.json")) if x["tags"][2] == 3 and x["v"] > 5000))'},
    "jq_select+cpanel": {"perl": ["-MCpanel::JSON::XS", "-0777", "-ne", 'print "$_->{name}\\n" for grep { $_->{tags}[2] == 3 && $_->{v} > 5000 } @{decode_json($_)}', "/data/t.json"]},
    # CSV with quoted fields
    "csv_regex": {"perl": ["-e", CSV_RE, "/data/q.csv"], "py": PY_CSV},
    "csv_parsewords": {"perl": ["-e", CSV_PW, "/data/q.csv"]},
    "csv_xs+csv_xs": {"perl": ["-e", CSV_XS]},
    "csv_pp+textcsv": {"perl": ["-e", CSV_XS.replace("Text::CSV_XS", "Text::CSV")]},
    # Counting and sorting
    "sort_uniq": {"perl": ["-ane", '$c{"$F[1] $F[2]"}++; ' + TOP, "/data/t.log"],
                  "py": 'from collections import Counter\nc = Counter(" ".join(l.split()[1:3]) for l in open("/data/t.log"))\nprint("\\n".join(f"{n} {k}" for k, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:20]))'},
    "sort_lines": {"perl": ["-MDigest::MD5=md5_hex", "-e", 'my @l = sort <>; print scalar(@l), " ", md5_hex(@l), "\\n"', "/data/t.log"],
                   "py": 'import hashlib\nl = sorted(open("/data/t.log", "rb"))\nprint(len(l), hashlib.md5(b"".join(l)).hexdigest())'},
    "sort_numeric": {"perl": ["-MDigest::MD5=md5_hex", "-e", 'my @l = map { $_->[1] } sort { $b->[0] <=> $a->[0] || $a->[1] cmp $b->[1] } map { [(split /,/)[2], $_] } <>; print md5_hex(@l), "\\n"', "/data/t.csv"],
                     "py": 'import hashlib\nl = sorted(open("/data/t.csv", "rb"), key=lambda s: (-int(s.split(b",")[2]), s))\nprint(hashlib.md5(b"".join(l)).hexdigest())'},
    "sort_numeric+sortkey": {"perl": ["-MDigest::MD5=md5_hex", "-MSort::Key::Multi=rnskeysort", "-e", 'my @l = rnskeysort { ((split /,/)[2], $_) } <>; print md5_hex(@l), "\\n"', "/data/t.csv"]},
    "word_freq": {"perl": ["-0777", "-ne", '$c{lc $1}++ while /(\\w+)/g; ' + TOP, "/data/words.txt"],
                  "py": 'import re\nfrom collections import Counter\nc = Counter(w.lower() for w in re.findall(r"\\w+", open("/data/words.txt").read()))\nprint("\\n".join(f"{n} {k}" for k, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:20]))'},
    # Files: grep with context, diff, find, digest
    "grep_context": {"perl": ["-e", GREP_C], "py": PY_GREP_C},
    "diff_lcs+algdiff": {"perl": ["-e", DIFF_LCS], "py": PY_DIFF},
    "diff_set": {"perl": ["-e", DIFF_SET]},
    "file_find": {"perl": ["-MFile::Find", "-e", 'find(sub { if (-f) { $n++; $s += -s _ } }, "/data/tree"); print "$n $s\\n"'],
                  "py": 'import os\nn = s = 0\nfor dp, dn, fn in os.walk("/data/tree"):\n    for f in fn:\n        n += 1; s += os.stat(os.path.join(dp, f)).st_size\nprint(n, s)'},
    "sha256": {"perl": ["-MDigest::SHA", "-e", 'print Digest::SHA->new(256)->addfile(shift)->hexdigest, "\\n"', "/data/t.log"],
               "py": 'import hashlib\nprint(hashlib.sha256(open("/data/t.log", "rb").read()).hexdigest())'},
    # Formatting and serialising
    "sprintf_report": {"perl": ["-e", REPORT, "/data/t.csv"], "py": PY_REPORT},
    "template": {"perl": ["-e", TPL], "py": PY_TPL},
    "dumper": {"perl": ["-MData::Dumper", "-e", BUILD + ' $Data::Dumper::Sortkeys = 1; $Data::Dumper::Indent = 1; print length(Dumper(\\@r)), "\\n"'],
               "py": "import pprint\n" + PY_BUILD + "print(len(pprint.pformat(r)))"},
    "storable": {"perl": ["-MStorable=nfreeze,thaw,dclone", "-e", BUILD + ' my $f = nfreeze(\\@r); my $t = thaw($f); my $c = dclone($t); print length($f), " ", scalar(@$c), "\\n"'],
                 "py": "import pickle, copy\n" + PY_BUILD + "f = pickle.dumps(r); t = pickle.loads(f); c = copy.deepcopy(t)\nprint(len(f), len(c))"},
    # Unicode
    "unicode_fold": {"perl": ["-CSD", "-Mfeature=fc", "-ne", '$c{fc $1}++ while /(\\w+)/g; END { print scalar(keys %c), "\\n" }', "/data/uni.txt"],
                     "py": 'import re\nprint(len({w.casefold() for w in re.findall(r"\\w+", open("/data/uni.txt", encoding="utf-8").read())}))'},
    "utf8_encode_module": {"perl": ["-MEncode=decode,encode", "-0777", "-ne", 'my ($n, $t) = (0, $_); for (1 .. 20) { my $s = decode("UTF-8", $t); $n += length encode("UTF-8", $s) } print "$n\\n"', "/data/uni.txt"],
                           "py": 'b = open("/data/uni.txt", "rb").read()\nprint(sum(len(b.decode("utf-8").encode("utf-8")) for _ in range(20)))'},
    "utf8_builtin": {"perl": ["-0777", "-ne", 'my ($n, $t) = (0, $_); for (1 .. 20) { my $s = $t; utf8::decode($s) or die; utf8::encode($s); $n += length $s } print "$n\\n"', "/data/uni.txt"]},
    "utf8_xs+unicodeutf8": {"perl": ["-MUnicode::UTF8=decode_utf8,encode_utf8", "-0777", "-ne", 'my ($n, $t) = (0, $_); for (1 .. 20) { $n += length encode_utf8(decode_utf8($t)) } print "$n\\n"', "/data/uni.txt"]},
    # YAML
    "yaml_core": {"perl": ["-e", YAML_CORE, "/data/t.yaml"]},
    "yaml_pp+yamlpp": {"perl": ["-MYAML::PP=Load", "-e", 'local $/; my $y = Load(<>); my $s = 0; $s += $_->{size} for @$y; print scalar(@$y), " $s\\n"', "/data/t.yaml"]},
    "yaml_xs+yamlxs": {"perl": ["-MYAML::XS=Load", "-e", 'local $/; my $y = Load(<>); my $s = 0; $s += $_->{size} for @$y; print scalar(@$y), " $s\\n"', "/data/t.yaml"]},
}

# Idioms: the same answer two ways, for agent-facing docs.
P.update({
    "idiom_sum_lane": {"perl": ["-F,", "-lane", "$s += $F[2]; END { print $s }", "/data/t.csv"]},
    "idiom_sum_split": {"perl": ["-ne", "$s += (split /,/)[2]; END { print $s }", "/data/t.csv"]},
    "idiom_sum_regex": {"perl": ["-ne", "$s += $1 if /^\\d+,[^,]*,(\\d+)/; END { print $s }", "/data/t.csv"]},
    "idiom_sum_slurp": {"perl": ["-0777", "-ne", '$s += $1 while /^\\d+,[^,]*,(\\d+)/mg; print "$s\\n"', "/data/t.csv"]},
    "idiom_count_lines": {"perl": ["-ne", "$n++ if /ERROR/; END { print $n }", "/data/t.log"]},
    "idiom_count_slurp": {"perl": ["-0777", "-ne", '$n = () = /ERROR/g; print "$n\\n"', "/data/t.log"]},
    "idiom_count_tr": {"perl": ["-0777", "-ne", '$n++ while /ERROR/g; print "$n\\n"', "/data/t.log"]},
    "idiom_subst_lines": {"perl": ["-pe", "s/ERROR/WARN/g", "/data/t.log"], "digest": True},
    "idiom_subst_slurp": {"perl": ["-0777", "-pe", "s/ERROR/WARN/g", "/data/t.log"], "digest": True},
    "idiom_words_split": {"perl": ["-ne", '$c{lc $_}++ for split /\\W+/; ' + TOP.replace("keys %c", "grep { length } keys %c"), "/data/words.txt"]},
    "idiom_sort_naive": {"perl": ["-MDigest::MD5=md5_hex", "-e", 'my @l = sort { (split /,/, $b)[2] <=> (split /,/, $a)[2] || $a cmp $b } <>; print md5_hex(@l), "\\n"', "/data/t.csv"]},
    "idiom_uniq_hash": {"perl": ["-ne", "$n++ unless $s{(split)[1]}++; END { print $n }", "/data/t.log"]},
    "idiom_uniq_listutil": {"perl": ["-MList::Util=uniq", "-e", 'print scalar(uniq map { (split)[1] } <>), "\\n"', "/data/t.log"]},
    "idiom_print_each": {"perl": ["-ne", "print if /worker3 /", "/data/t.log"], "digest": True},
    "idiom_print_join": {"perl": ["-e", 'print grep { /worker3 / } <>', "/data/t.log"], "digest": True},
})


def cache_dir() -> Path:
    """A module cache that outlives one run, so a run does not time the first compile."""
    if os.environ.get("CORPUS_CACHE"):
        p = Path(os.environ["CORPUS_CACHE"])
        p.mkdir(mode=0o700, exist_ok=True)
        return p
    return Path(tempfile.mkdtemp(prefix="cache-"))


def needs(name: str) -> str | None:
    return name.split("+", 1)[1] if "+" in name else None


def perl_call(name: str, d: Path, ext: Path | None, fuel: int | None, cache: Path):
    import wasi_perl

    g = [wasi_perl.Grant(str(d), "/data")]
    env = dict(P[name].get("env", {}))
    if ext is not None:
        g.append(wasi_perl.Grant(str(ext), EXT))
        env["PERL5LIB"] = EXT
    t = time.perf_counter()
    r = wasi_perl.run(P[name]["perl"], grants=g, env=env, cwd="/data", fuel=fuel, cache_dir=cache, deadline_s=600)
    s = time.perf_counter() - t
    return s, r


def answer(name: str, out: bytes) -> str:
    if P[name].get("digest") or len(out) > 2000:
        return f"{len(out)} bytes sha256 {hashlib.sha256(out).hexdigest()[:16]}"
    return out.decode("utf-8", "replace").strip()


def selected(names: list[str]) -> list[str]:
    return names or list(P)


def guest_fuel(d: Path, out: Path, names: list[str]) -> None:
    cache = cache_dir()
    ext = Path(os.environ["CORPUS_EXT"]) if os.environ.get("CORPUS_EXT") else None
    res = json.loads(out.read_text()) if out.exists() else {}
    for name in selected(names):
        _, r = perl_call(name, d, ext, 10**13, cache)
        res[name] = {"fuel": r.fuel_used, "exit": r.exit_code, "answer": answer(name, r.stdout),
                     "stderr": r.stderr.decode("utf-8", "replace")[:300]}
        print(name, res[name]["fuel"], res[name]["exit"], res[name]["answer"][:60], flush=True)
        out.write_text(json.dumps(res, indent=1, ensure_ascii=False))


def reps_for(seconds: float) -> int:
    return 3 if seconds > 1.0 else 5 if seconds > 0.2 else 9


def guest_wall(d: Path, out: Path, names: list[str]) -> None:
    cache = cache_dir()
    ext = Path(os.environ["CORPUS_EXT"]) if os.environ.get("CORPUS_EXT") else None
    res = json.loads(out.read_text()) if out.exists() else {}
    for name in selected(names):
        s, r = perl_call(name, d, ext, None, cache)  # warm-up: compile, cache, tree check
        if r.exit_code != 0:
            print(name, "FAILED", r.stderr[:200], flush=True)
            continue
        xs = [perl_call(name, d, ext, None, cache)[0] for _ in range(reps_for(s))]
        res[name] = {"median_ms": round(statistics.median(xs) * 1000, 1), "n": len(xs)}
        print(name, res[name], flush=True)
        out.write_text(json.dumps(res, indent=1))


def native(d: Path, perl: str, extlib: str, out: Path, names: list[str]) -> None:
    res = json.loads(out.read_text()) if out.exists() else {}
    for name in selected(names):
        p = P[name]
        argv = [perl, *(a.replace("/data/", f"{d}/") for a in p["perl"])]
        env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "PERL5LIB": extlib, **p.get("env", {})}
        t = time.perf_counter()
        r = subprocess.run(argv, capture_output=True, env=env, cwd=d)
        first = time.perf_counter() - t
        if r.returncode != 0:
            res[name] = {"exit": r.returncode, "stderr": r.stderr.decode()[:300]}
            print(name, "FAILED", r.stderr[:200], flush=True)
            continue
        xs = []
        for _ in range(reps_for(first)):
            t = time.perf_counter()
            subprocess.run(argv, capture_output=True, env=env, cwd=d, check=True)
            xs.append(time.perf_counter() - t)
        res[name] = {"median_ms": round(statistics.median(xs) * 1000, 1), "n": len(xs),
                     "answer": answer(name, r.stdout.replace(str(d).encode() + b"/", b"/data/"))}
        print(name, res[name]["median_ms"], res[name]["answer"][:60], flush=True)
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False))


def py(d: Path, out: Path) -> None:
    import wasi_python

    cache = cache_dir()
    res = {}
    for name, p in P.items():
        if "py" not in p:
            continue
        g = [wasi_python.Grant(str(d), "/data")]

        def call(fuel=None):
            t = time.perf_counter()
            r = wasi_python.run(["-c", p["py"]], grants=g, fuel=fuel, cache_dir=cache, deadline_s=600)
            return time.perf_counter() - t, r

        s, r = call()
        if r.exit_code != 0:
            print(name, "FAILED", r.stderr[-300:], flush=True)
            continue
        xs = [call()[0] for _ in range(reps_for(s))]
        _, rf = call(10**13)
        res[name] = {"median_ms": round(statistics.median(xs) * 1000, 1), "n": len(xs),
                     "fuel": rf.fuel_used, "answer": answer(name, r.stdout)}
        print(name, res[name]["median_ms"], res[name]["fuel"], res[name]["answer"][:60], flush=True)
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    cmd, *a = sys.argv[1:]
    if cmd == "data":
        make_data(Path(a[0]))
    elif cmd == "fuel":
        guest_fuel(Path(a[0]), Path(a[1]), a[2:])
    elif cmd == "wall":
        guest_wall(Path(a[0]), Path(a[1]), a[2:])
    elif cmd == "native":
        native(Path(a[0]), a[1], a[2], Path(a[3]), a[4:])
    elif cmd == "py":
        py(Path(a[0]), Path(a[1]))
    else:
        raise SystemExit(__doc__)
