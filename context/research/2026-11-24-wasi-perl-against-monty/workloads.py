"""The same tasks written once in Perl (for wasi-perl and native perl) and once in the Python
subset Monty runs. Both sides must print the same stdout; `tables.py` compares them (its `check`).

Data lives under /data in both sandboxes: a read-only grant for wasi-perl, a read-only
`MountDir` for Monty. The inputs are the baseline memo's (`make_data`), plus 100 small files.
"""

from __future__ import annotations

import json
from pathlib import Path

N_LINES = 200_000
N_SMALL = 100

# name: (perl argv, python source, group)
W: dict[str, tuple[list[str], str, str]] = {
    "hello": (["-e", 'print "hello\\n"'], 'print("hello")', "b"),
    "fib25": (
        [
            "-e",
            'sub fib { my $n = shift; $n < 2 ? $n : fib($n - 1) + fib($n - 2) } print fib(25), "\\n"',
        ],
        "def fib(n):\n    return n if n < 2 else fib(n - 1) + fib(n - 2)\nprint(fib(25))",
        "c",
    ),
    "loop": (
        ["-e", 'my $s = 0; $s += $_ * $_ for 1 .. 1_000_000; print "$s\\n"'],
        "s = 0\nfor i in range(1, 1000001):\n    s += i * i\nprint(s)",
        "c",
    ),
    "strcat": (
        ["-e", 'my $s = ""; $s .= "$_," for 1 .. 200_000; print length($s), "\\n"'],
        "s = ''\nfor i in range(1, 200001):\n    s += str(i) + ','\nprint(len(s))",
        "c",
    ),
    "strjoin": (
        ["-e", 'print length(join ",", 1 .. 200_000), "\\n"'],
        "print(len(','.join([str(i) for i in range(1, 200001)])))",
        "c",
    ),
    "sum_csv": (
        ["-F,", "-lane", "$s += $F[2]; END { print $s }", "/data/t.csv"],
        "s = 0\nfor line in open('/data/t.csv'):\n    s += int(line.split(',')[2])\nprint(s)",
        "d",
    ),
    "grep_log": (
        ["-ne", '$n++ if /ERROR \\d+/; END { print "$n\\n" }', "/data/t.log"],
        "import re\nr = re.compile(r'ERROR \\d+')\nn = 0\n"
        "for line in open('/data/t.log'):\n    if r.search(line):\n        n += 1\nprint(n)",
        "d",
    ),
    "sub_log": (
        ["-0777", "-ne", 's/ERROR/WARN/g; my $n = () = /WARN/g; print "$n\\n"', "/data/t.log"],
        "import re\nt = open('/data/t.log').read()\nt = re.sub('ERROR', 'WARN', t)\n"
        "print(len(re.findall('WARN', t)))",
        "d",
    ),
    "split_join": (
        ["-0777", "-ne", 'print length(join " ", split " ", $_), "\\n"', "/data/t.log"],
        "t = open('/data/t.log').read()\nprint(len(' '.join(t.split())))",
        "d",
    ),
    "wordfreq": (
        [
            "-ne",
            "$c{$_}++ for split; END { my @k = sort { $c{$b} <=> $c{$a} || $a cmp $b } keys %c;"
            ' print scalar(@k), " $k[0] $c{$k[0]}\\n" }',
            "/data/t.log",
        ],
        "c = {}\nfor line in open('/data/t.log'):\n    for w in line.split():\n"
        "        c[w] = c.get(w, 0) + 1\nk = sorted(c, key=lambda w: (-c[w], w))\n"
        "print(len(k), k[0], c[k[0]])",
        "e",
    ),
    "sort_ints": (
        [
            "-e",
            "my @a = map { ($_ * 7919) % 10007 } 1 .. 200_000; my @s = sort { $a <=> $b } @a;"
            ' print "$s[0] $s[-1] $s[100000]\\n"',
        ],
        "a = [(i * 7919) % 10007 for i in range(1, 200001)]\ns = sorted(a)\nprint(s[0], s[-1], s[100000])",
        "e",
    ),
    "json_decode": (
        ["-MJSON::PP", "-0777", "-ne", 'print scalar(@{decode_json($_)}), "\\n"', "/data/t.json"],
        "import json\nprint(len(json.loads(open('/data/t.json').read())))",
        "e",
    ),
    "json_encode": (
        [
            "-MJSON::PP",
            "-e",
            'my @r = map { { id => $_, name => "n$_", tags => ["a", "b", $_ % 5] } } 0 .. 19_999;'
            ' print length(encode_json(\\@r)), "\\n"',
        ],
        "import json\nr = [{'id': i, 'name': f'n{i}', 'tags': ['a', 'b', i % 5]} for i in range(20000)]\n"
        "print(len(json.dumps(r, separators=(',', ':'))))",
        "e",
    ),
    # (f) host-provided data: one 10.2 MB file, and 100 files of 1 KiB
    "read_big": (
        ["-0777", "-ne", 'print length, "\\n"', "/data/t.log"],
        "print(len(open('/data/t.log').read()))",
        "f",
    ),
    "read_small": (
        [
            "-e",
            'my $n = 0; for my $i (0 .. 99) { open my $h, "<", "/data/small/f$i.txt" or die;'
            ' local $/; $n += length <$h> } print "$n\\n"',
        ],
        "n = 0\nfor i in range(100):\n    n += len(open(f'/data/small/f{i}.txt').read())\nprint(n)",
        "f",
    ),
}


def make_data(d: Path) -> None:
    """The baseline memo's three inputs, byte for byte, and 100 small files."""
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "t.csv", "w", newline="\n") as f:
        for i in range(N_LINES):
            f.write(f"{i},name{i % 977},{(i * 7919) % 10007},{'yes' if i % 3 else 'no'}\n")
    with open(d / "t.log", "w", newline="\n") as f:
        for i in range(N_LINES):
            level = f"ERROR {i % 500}" if i % 7 == 0 else "INFO ok"
            f.write(f"2026-11-24T10:{i % 60:02d}:{i % 59:02d} worker{i % 16} {level} request {i}\n")
    rows = [
        {"id": i, "name": f"n{i}", "tags": ["a", "b", i % 5], "v": i * 0.5} for i in range(20_000)
    ]
    (d / "t.json").write_text(json.dumps(rows))
    (d / "small").mkdir(exist_ok=True)
    for i in range(N_SMALL):
        (d / "small" / f"f{i}.txt").write_text(f"{i:04d} " + "x" * 1018 + "\n")


if __name__ == "__main__":
    import sys

    make_data(Path(sys.argv[1]))
