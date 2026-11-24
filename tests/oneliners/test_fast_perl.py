"""docs/fast-perl.md's claims, each checked.

- Every example on the page is a case of cases.toml, word for word, so the oracle checks it
  against native perl like any other case (always runs).
- Every ratio the page quotes is guest instructions (fuel) of its slow example over its fast
  one, run on large inputs. The measured ratio must lie within 20% of the page's figure, either
  way, so the page cannot keep a number the guest no longer earns.
- The POSIX import's cost, the number of names it imports, and what File::Find does with and
  without a working folder are checked as the page states them.

The guest checks need the runner and perl.wasm, and count instructions, never time.
"""

from __future__ import annotations

import ast
import json
import re
import shlex
import shutil
from pathlib import Path

import oneliner_oracle as oo
import pytest

PAGE = oo.REPO / "docs" / "fast-perl.md"
CASES = {c.id: c for c in oo.load_corpus()}

# Each ratio the page quotes: the slow and the fast example, by case id, the figure as the page
# writes it beside "×", and the start of the heading of the page's section that quotes it.
RATIOS = {
    "count matches in a whole file": (
        "fast-count-lines",
        "fast-count-whole-file",
        "9",
        "Whole-file regex work",
    ),
    "substitute in a whole file": (
        "fast-substitute-lines",
        "fast-substitute-whole-file",
        "7.5",
        "Whole-file regex work",
    ),
    "keyed sort": ("fast-sort-split-in-block", "fast-sort-schwartzian", "5", "Keyed sorts"),
    "JSON through JSON::MaybeXS": ("fast-json-pp", "fast-json-maybexs", "29", "JSON"),
    "JSON through Cpanel::JSON::XS": ("fast-json-pp", "fast-json-cpanel", "29", "JSON"),
    "CSV": ("fast-csv-parsewords", "fast-csv-text-csv", "13", "CSV"),
}
MARGIN = 0.20

# What the page says of `use POSIX`: how many names it imports, and what they cost, in
# millions of instructions, against `use POSIX ()`.
POSIX_NAMES = 587
POSIX_IMPORT_M = 16
POSIX_PAIR = ("fast-posix-default-import", "fast-posix-no-import")
POSIX_SECTION = "Imports"

# The guest's hang guard: the heaviest run, the keyed sort with split in its block, takes
# about 3e10 instructions.
FUEL = 10**12


# ---------------------------------------------------------------------------
# The page and the corpus


def page_examples(text: str) -> list[list[str]]:
    """The argv after `perl` of every example: each `perl …` line of a ```sh block, and the
    first argument of each `run(…)` call of a ```python block."""
    examples = []
    for lang, body in re.findall(r"^```(\w+)\n(.*?)^```", text, flags=re.M | re.S):
        if lang == "sh":
            for line in body.splitlines():
                if line.startswith("perl "):
                    examples.append(shlex.split(line)[1:])
        elif lang == "python":
            for node in ast.walk(ast.parse(body)):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "run":
                    examples.append([ast.literal_eval(a) for a in node.args[0].elts])
    return examples


def test_every_example_on_the_page_is_a_case():
    examples = page_examples(PAGE.read_text(encoding="utf-8"))
    assert len(examples) >= len(RATIOS) + 1, examples
    by_argv = {c.argv: c.id for c in CASES.values()}
    missing = [e for e in examples if tuple(e) not in by_argv]
    assert not missing, f"examples on the page that are not cases word for word: {missing}"


def test_every_measured_example_is_on_the_page():
    examples = {tuple(e) for e in page_examples(PAGE.read_text(encoding="utf-8"))}
    ids = {cid for slow, fast, _, _ in RATIOS.values() for cid in (slow, fast)} | set(POSIX_PAIR)
    for cid in sorted(ids):
        assert CASES[cid].argv in examples, f"{cid} is measured but not shown on the page"


def sections(text: str) -> dict[str, str]:
    """The page's `## ` sections: heading -> the text up to the next heading."""
    parts = re.split(r"^## (.*)$", text, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def section(text: str, start: str) -> str:
    found = [body for heading, body in sections(text).items() if heading.startswith(start)]
    assert len(found) == 1, f"the page has {len(found)} sections headed {start!r}"
    return found[0]


def quotes(body: str, number: str, unit: str) -> bool:
    """Whether `body` has `number` followed by `unit` as a whole number: not the end of a
    longer one, so 9× is not found in 29× nor 5× in 7.5×."""
    return re.search(rf"(?<![\d.]){re.escape(number)}{re.escape(unit)}", body) is not None


def test_the_page_quotes_each_checked_figure():
    text = PAGE.read_text(encoding="utf-8")
    for name, (_, _, figure, start) in RATIOS.items():
        assert quotes(section(text, start), figure, "×"), (
            f"the section {start!r} does not quote {figure}× ({name})"
        )
    imports = section(text, POSIX_SECTION)
    assert quotes(imports, str(POSIX_NAMES), " names"), POSIX_NAMES
    assert quotes(imports, str(POSIX_IMPORT_M), " M instructions"), POSIX_IMPORT_M
    assert "Can't cd to /" in section(text, "`File::Find`")


def test_every_ratio_on_the_page_is_a_checked_one():
    text = PAGE.read_text(encoding="utf-8")
    on_page = set(re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)×", text))
    checked = {figure for _, _, figure, _ in RATIOS.values()}
    assert on_page == checked, f"ratios on the page with no check: {sorted(on_page - checked)}"


def test_the_page_is_linked_from_the_readme_and_the_modules_page():
    assert "docs/fast-perl.md" in (oo.REPO / "README.md").read_text(encoding="utf-8")
    assert "(fast-perl.md)" in (oo.REPO / "docs" / "modules.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The large inputs, laid out under the names the examples use


def write_inputs(folder: Path) -> None:
    """Deterministic inputs, as the study that found the ratios made them: a 9.8 MB log of
    200,000 lines, a CSV of 200,000 rows, a JSON array of 20,000 objects and a CSV of 50,000
    rows whose names are quoted and hold commas."""
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "app.log", "w", newline="\n") as f:
        for i in range(200_000):
            level = f"ERROR {i % 500}" if i % 7 == 0 else "INFO ok"
            f.write(f"2026-11-24T10:{i % 60:02d}:{i % 59:02d} worker{i % 16} {level} request {i}\n")
    with open(folder / "data.csv", "w", newline="\n") as f:
        for i in range(200_000):
            f.write(f"{i},name{i % 977},{(i * 7919) % 10007},{'yes' if i % 3 else 'no'}\n")
    rows = [
        {"id": i, "name": f"n{i}", "active": i % 3 == 0, "tags": ["a", "b", i % 5], "v": i * 0.5}
        for i in range(20_000)
    ]
    (folder / "users.json").write_text(json.dumps(rows), newline="\n")
    words = "alpha beta gamma delta file error request worker value table record agent".split()
    with open(folder / "people.csv", "w", newline="\n") as f:
        f.write("id,name,note,amount\n")
        for i in range(50_000):
            name = f'"{words[i % 12]}, {words[(i * 7) % 12]} {i % 97}"'
            note = f'"said ""{words[(i * 5) % 12]}"" twice"' if i % 4 == 0 else words[i % 11]
            f.write(f"{i},{name},{note},{(i * 37) % 100000 / 100:.2f}\n")


@pytest.fixture(scope="session")
def guest():
    try:
        import wasi_perl
    except ImportError as e:
        pytest.skip(f"the wasi_perl runner is not importable ({e})")
    try:
        wasi_perl.artifacts()
    except wasi_perl.ArtifactsMissing as e:
        pytest.skip(f"no perl.wasm for the runner: {e}")
    return wasi_perl


@pytest.fixture(scope="session")
def inputs(tmp_path_factory) -> Path:
    folder = tmp_path_factory.mktemp("fast-perl-inputs")
    write_inputs(folder)
    return folder


def fuel_of(wasi_perl, argv: tuple[str, ...], inputs: Path, scratch: Path):
    """(instructions, stdout, the files after) for one guest run over a fresh copy of the
    inputs at /work, which is also the working folder."""
    work = scratch / "work"
    shutil.copytree(inputs, work)
    done = wasi_perl.run(
        list(argv),
        grants=[wasi_perl.Grant(str(work), oo.GUEST_ROOT, writable=True)],
        env=dict(oo.BASE_ENV),
        cwd=oo.GUEST_ROOT,
        fuel=FUEL,
        deadline_s=None,
        output_bytes=64 * 2**20,
    )
    assert (done.exit_code, done.stderr, done.trapped) == (0, b"", None), (argv, done.stderr)
    assert not done.truncated, argv
    files = {p.name: p.read_bytes() for p in work.iterdir()}
    shutil.rmtree(work)
    return done.fuel_used, done.stdout, files


def measure(wasi_perl, inputs: Path, scratch: Path, slow: str, fast: str):
    """(slow fuel, fast fuel) for two cases, which must give the same answer."""
    a = fuel_of(wasi_perl, CASES[slow].argv, inputs, scratch / "slow")
    b = fuel_of(wasi_perl, CASES[fast].argv, inputs, scratch / "fast")
    assert a[1:] == b[1:], f"{slow} and {fast} answer differently on the large inputs"
    return a[0], b[0]


# ---------------------------------------------------------------------------
# The guest checks


@pytest.mark.slow
@pytest.mark.parametrize("name", RATIOS)
def test_guest_ratio_is_the_pages(name, guest, inputs, tmp_path):
    slow, fast, figure, _ = RATIOS[name]
    used_slow, used_fast = measure(guest, inputs, tmp_path, slow, fast)
    ratio = used_slow / used_fast
    claim = float(figure)
    assert claim * (1 - MARGIN) <= ratio <= claim * (1 + MARGIN), (
        f"{name}: {used_slow:,} / {used_fast:,} instructions is {ratio:.2f}×; "
        f"the page says {figure}×"
    )


@pytest.mark.slow
def test_guest_posix_default_import_costs_what_the_page_says(guest, inputs, tmp_path):
    used_all, used_none = measure(guest, inputs, tmp_path, *POSIX_PAIR)
    cost = (used_all - used_none) / 1e6
    assert POSIX_IMPORT_M * (1 - MARGIN) <= cost <= POSIX_IMPORT_M * (1 + MARGIN), (
        f"use POSIX imports for {cost:.1f} M instructions; the page says {POSIX_IMPORT_M} M"
    )
    done = guest.run(["-MPOSIX", "-e", "print scalar @POSIX::EXPORT"])
    assert (done.exit_code, done.stdout) == (0, str(POSIX_NAMES).encode()), done


FIND = CASES["fast-file-find"].argv


@pytest.mark.slow
def test_guest_file_find_needs_a_working_folder(guest, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.c").write_text("int a;\n")
    grant = [guest.Grant(str(tmp_path), oo.GUEST_ROOT)]
    absolute = (*FIND[:-1], FIND[-1].replace('"."', f'"{oo.GUEST_ROOT}"'))
    without = guest.run(list(absolute), grants=grant)
    assert (without.exit_code, without.stdout) == (2, b""), without
    assert without.stderr == b"Can't cd to /: No such file or directory\n", without
    relative = guest.run(list(FIND), grants=grant)
    assert (relative.exit_code, relative.stdout, relative.stderr) == (0, b"", b""), relative
    within = guest.run(list(FIND), grants=grant, cwd=oo.GUEST_ROOT)
    assert (within.exit_code, within.stdout, within.stderr) == (0, b"./src/a.c\n", b""), within
