"""Run Perl's own t/ suite under the runtime, one guest per test file, and keep the score.

    uv run --with <runner> tests/perl_t/run_suite.py [--only GLOB] [--jobs N] [--list-failures]

Perl's own harness cannot run inside the guest: it starts a process per file. So this host
program is the harness. It copies the pristine source tree of the pinned tarball into one fresh
folder per worker, lays the built library over its lib/ (adding only files the tarball does not
have, such as Config.pm), grants that folder writable at /perl, and runs each t/**/*.t with
cwd /perl/t and the switches t/TEST gives it. Tests write scratch files beside themselves, which
is why the copy is writable, fresh per run, and one per worker: the guest's $$ is the same in
every guest, so two tests sharing a folder could pick the same scratch name.

A file passes when its guest exits 0 and its TAP (parsed here, no CPAN module) has one plan, no
`not ok` outside a `# TODO`, and as many results as the plan says; or when it plans `1..0` with
a skip reason. Each failing file is matched against exclusions.toml. The run writes
build/perl_t/results.json and prints one scoreboard line.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import queue
import re
import shutil
import sys
import tarfile
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

import tomllib

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
BUILD = REPO / "build"
PERL_VERSION = "5.42.3"
TARBALL_NAME = f"perl-{PERL_VERSION}.tar.xz"
EXCLUSIONS = HERE / "exclusions.toml"
RESULTS = BUILD / "perl_t" / "results.json"
LOGS = BUILD / "perl_t" / "logs"  # each failing file's whole stdout and stderr
GUEST_ROOT = "/perl"
GUEST_TMP = "/tmp"
DEADLINE_S = 300.0
OUTPUT_BYTES = 8 << 20
MEMORY_BYTES = 2 << 30
TAIL_BYTES = 4000

REASONS = ("subprocess", "signals", "threads", "porting", "dynamic-loading", "platform", "bug")
NOTE_REQUIRED = ("platform", "bug")

# t/TEST runs these directories with no extra switch; the tests set @INC themselves.
BARE_DIRS = ("base", "comp", "run")
# The environment t/TEST adds to the one it inherits. A guest inherits nothing, so the few
# variables a native run reliably has are given here too.
GUEST_ENV = {
    "PERL_DESTRUCT_LEVEL": "2",
    "PERL_DL_NONLAZY": "1",
    "HOME": f"{GUEST_ROOT}/t",
    "TMPDIR": GUEST_TMP,
    "PATH": "/usr/bin:/bin",
    "LC_ALL": "C",
}


# ---------------------------------------------------------------------------------------------
# TAP


@dataclass
class Tap:
    """What one file's standard output said, as TAP."""

    planned: int | None = None
    skip_all: str | None = None  # the reason of a `1..0 # skip ...` plan
    plans: int = 0
    results: int = 0
    failed: list[int] = field(default_factory=list)
    todo: int = 0
    skipped: int = 0
    out_of_order: list[int] = field(default_factory=list)
    bailed_out: str | None = None

    def verdict(self) -> str | None:
        """None when the TAP alone is a pass; else why not, in a few words."""
        if self.bailed_out is not None:
            return f"bail out: {self.bailed_out}"
        if self.plans == 0:
            return "no plan"
        if self.plans > 1:
            return f"{self.plans} plans"
        if self.skip_all is not None:
            if self.results:
                return "skip-all plan with results"
            if not self.skip_all.strip():
                return "skip-all plan with no reason"
            return None
        if self.planned == 0:
            return "1..0 without a skip reason"
        if self.failed:
            shown = ",".join(str(n) for n in self.failed[:10])
            more = "..." if len(self.failed) > 10 else ""
            return f"not ok {shown}{more} ({len(self.failed)} of {self.planned})"
        if self.out_of_order:
            return f"results out of sequence at {self.out_of_order[0]}"
        if self.results != self.planned:
            return f"ran {self.results} of {self.planned}"
        return None


_PLAN = re.compile(r"^1\.\.(\d+)\s*(?:#\s*(.*))?$")
# A description may hold an escaped hash, \#; the first bare "#" starts the directive.
_RESULT = re.compile(r"^(not\s+)?ok\b\s*(\d+)?\s*((?:\\.|[^#])*?)\s*(?:#\s*(.*))?$")
_BAIL = re.compile(r"^Bail out!\s*(.*)$")


def parse_tap(text: str) -> Tap:
    """Parse top-level TAP. Indented lines (a subtest's own TAP) and anything that is not a
    plan, a result or a bail-out are ignored; a subtest is counted by its summary line."""
    tap = Tap()
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        m = _PLAN.match(line)
        if m:
            tap.plans += 1
            tap.planned = int(m.group(1))
            directive = (m.group(2) or "").strip()
            if tap.planned == 0 and re.match(r"(?i)skip", directive):
                tap.skip_all = directive[4:].strip(" :") or ""
            continue
        m = _RESULT.match(line)
        if m:
            tap.results += 1
            number = int(m.group(2)) if m.group(2) else tap.results
            if number != tap.results and not tap.out_of_order:
                tap.out_of_order.append(number)
            directive = (m.group(4) or "").strip()
            is_todo = bool(re.match(r"(?i)todo\b", directive))
            if re.match(r"(?i)skip", directive):
                tap.skipped += 1
            if is_todo:
                tap.todo += 1
            elif m.group(1):
                tap.failed.append(number)
            continue
        m = _BAIL.match(line)
        if m and tap.bailed_out is None:
            tap.bailed_out = m.group(1).strip() or "(no reason)"
    return tap


# ---------------------------------------------------------------------------------------------
# Exclusions


@dataclass(frozen=True)
class Exclusion:
    reason: str
    note: str = ""


def load_exclusions(path: Path = EXCLUSIONS) -> dict[str, Exclusion]:
    """The exclusion list: test file (relative to t/) -> its reason and note. Raises ValueError
    on an unknown reason, a missing note where one is required, or an unknown key."""
    data = tomllib.loads(path.read_text())
    files = data.get("files", {})
    out: dict[str, Exclusion] = {}
    for name, entry in files.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{name}: an entry is a table with a reason")
        unknown = set(entry) - {"reason", "note"}
        if unknown:
            raise ValueError(f"{name}: unknown keys {sorted(unknown)}")
        reason = entry.get("reason")
        if reason not in REASONS:
            raise ValueError(f"{name}: reason {reason!r} is not one of {', '.join(REASONS)}")
        note = entry.get("note", "")
        if not isinstance(note, str):
            raise ValueError(f"{name}: note must be a string")
        if reason in NOTE_REQUIRED and not note.strip():
            raise ValueError(f"{name}: a {reason} entry needs a note")
        out[name] = Exclusion(reason, note)
    return out


# ---------------------------------------------------------------------------------------------
# The tree


def _expected_sha256(tarball: Path) -> str | None:
    sums = REPO / "downloads" / "SHA256SUMS"
    if not sums.is_file():
        return None
    for line in sums.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == tarball.name:
            return parts[0]
    return None


def find_tarball() -> Path:
    """The pinned tarball: $WASI_PERL_TARBALL, else downloads/ of this checkout, else of the
    main checkout this worktree belongs to (git worktrees share downloads only by path)."""
    env = os.environ.get("WASI_PERL_TARBALL")
    if env:
        return Path(env)
    here = REPO / "downloads" / TARBALL_NAME
    if here.is_file():
        return here
    main = REPO.parent.parent / "wasi-perl" / "downloads" / TARBALL_NAME
    return main if main.is_file() else here


def pristine_tree(tarball: Path | None = None) -> Path:
    """build/perl-5.42.3/, unpacked from the pinned tarball after its hash is checked. An
    unpacked tree is reused; it is never written to (each run works on a copy)."""
    tree = BUILD / f"perl-{PERL_VERSION}"
    if (tree / "t" / "TEST").is_file():
        return tree
    tarball = tarball or find_tarball()
    want = _expected_sha256(tarball)
    if want is None:
        raise SystemExit(f"no pinned hash for {tarball.name} in downloads/SHA256SUMS")
    h = hashlib.sha256()
    with tarball.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    if h.hexdigest() != want:
        raise SystemExit(f"{tarball}: sha256 {h.hexdigest()} is not the pinned {want}")
    BUILD.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball) as tf:
        tf.extractall(BUILD, filter="data")
    return tree


def built_lib() -> Path:
    """The configured target build's lib/, which holds what the build generates (Config.pm,
    the extensions' Perl halves, lib/unicore's tables): $WASI_PERL_TARGET/lib, else
    build/target/lib."""
    env = os.environ.get("WASI_PERL_TARGET")
    target = Path(env) if env else BUILD / "target"
    return target / "lib"


def list_tests(tree: Path) -> list[str]:
    """Every t/**/*.t, relative to t/, sorted."""
    t = tree / "t"
    return sorted(p.relative_to(t).as_posix() for p in t.rglob("*.t") if p.is_file())


def stage(tree: Path, lib: Path, into: Path) -> None:
    """A fresh writable copy of the pristine tree at `into`, with the built library's files
    that the tarball does not have laid over its lib/. Files the tarball has are kept as
    shipped, so nothing a build step changed in place reaches the tests."""
    shutil.copytree(tree, into, symlinks=True)
    config_sh = lib.parent / "config.sh"  # t/TEST and a few tests read ../config.sh
    if config_sh.is_file() and not (into / "config.sh").exists():
        shutil.copy2(config_sh, into / "config.sh")
    dest = into / "lib"
    for src in lib.rglob("*"):
        rel = src.relative_to(lib)
        out = dest / rel
        if src.is_dir() and not src.is_symlink():
            out.mkdir(parents=True, exist_ok=True)
        elif not out.exists() and not out.is_symlink():
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out, follow_symlinks=True)
    for p in into.rglob("*"):  # the copy is the tests' to write, whatever the tarball's modes
        try:
            mode = p.lstat().st_mode
            if not p.is_symlink():
                p.chmod(mode | 0o200)
        except OSError:
            pass


# ---------------------------------------------------------------------------------------------
# One file


def argv_for(test: str, source: Path) -> list[str]:
    """The switches t/TEST gives `test` (relative to t/), then the test itself: none for
    base/, comp/ and run/; `-I.. -MTestInit` elsewhere (TestInit sets @INC to ../lib and
    PERL_CORE); and a -t or -T from the test's #! line."""
    args: list[str] = []
    if test.split("/", 1)[0] not in BARE_DIRS:
        args += ["-I..", "-MTestInit"]
    with source.open("rb") as f:
        first = f.readline().decode("latin-1")
    m = re.match(r"#!.*\bperl.*\s-\w*([tT])", first)
    if m:
        args.append(f"-{m.group(1)}")
    return [*args, test]


@dataclass
class Outcome:
    file: str
    status: str  # "pass" or "fail"
    why: str | None
    exit_code: int | None
    trapped: str | None
    planned: int | None
    results: int
    failed: list[int]
    todo: int
    skip_all: str | None
    seconds: float
    truncated: list[str]
    stdout_tail: str = ""
    stderr_tail: str = ""


def _tail(data: bytes) -> str:
    text = data[-TAIL_BYTES:].decode("utf-8", "replace")
    return text


def run_one(
    test: str, slot: Path, tmp: Path, artifacts: object, deadline_s: float, logs: Path | None = None
) -> Outcome:
    from wasi_perl import Grant, run

    source = slot / "t" / test
    argv = argv_for(test, source)
    started = time.monotonic()
    done = run(
        argv,
        grants=[Grant(slot, GUEST_ROOT, writable=True), Grant(tmp, GUEST_TMP, writable=True)],
        cwd=f"{GUEST_ROOT}/t",
        env=GUEST_ENV,
        deadline_s=deadline_s,
        fuel=None,
        memory_bytes=MEMORY_BYTES,
        output_bytes=OUTPUT_BYTES,
        artifacts=artifacts,  # type: ignore[arg-type]
    )
    seconds = time.monotonic() - started
    stdout = done.stdout.decode("utf-8", "replace")
    tap = parse_tap(stdout)
    why: str | None
    if done.trapped is not None:
        why = f"trapped: {done.trapped}"
    elif done.truncated:
        why = f"output over {OUTPUT_BYTES} bytes on {','.join(done.truncated)}"
    else:
        why = tap.verdict()
        if why is None and done.exit_code != 0:
            why = f"exit {done.exit_code}"
        elif why is not None and done.exit_code != 0:
            why = f"{why}; exit {done.exit_code}"
    status = "pass" if why is None else "fail"
    if logs is not None:
        for stream, data in (("stdout", done.stdout), ("stderr", done.stderr)):
            log = logs / f"{test}.{stream}"
            if status == "fail":
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_bytes(data)
            else:
                log.unlink(missing_ok=True)
    return Outcome(
        file=test,
        status=status,
        why=why,
        exit_code=done.exit_code,
        trapped=done.trapped,
        planned=tap.planned,
        results=tap.results,
        failed=tap.failed[:200],
        todo=tap.todo,
        skip_all=tap.skip_all,
        seconds=round(seconds, 2),
        truncated=list(done.truncated),
        stdout_tail="" if status == "pass" else _tail(done.stdout),
        stderr_tail="" if status == "pass" else _tail(done.stderr),
    )


# ---------------------------------------------------------------------------------------------
# The run


def _artifacts() -> object:
    from wasi_perl import Artifacts

    env = os.environ.get("WASI_PERL_ARTIFACTS")
    folder = Path(env) if env else BUILD / "artifacts"
    found = Artifacts.at(folder)
    if not found.perl.is_file():
        raise SystemExit(f"no perl.wasm at {found.perl}: set WASI_PERL_ARTIFACTS")
    return found


def scoreboard(outcomes: list[Outcome], exclusions: dict[str, Exclusion]) -> dict:
    """Counts and lists: passed, excluded by reason, failed (not excluded), stale (excluded but
    passed)."""
    passed, failed, stale = [], [], []
    excluded: dict[str, list[str]] = {r: [] for r in REASONS}
    for o in outcomes:
        ex = exclusions.get(o.file)
        if ex is None:
            (passed if o.status == "pass" else failed).append(o.file)
        elif o.status == "pass":
            stale.append(o.file)
        else:
            excluded[ex.reason].append(o.file)
    return {
        "run": len(outcomes),
        "passed": sorted(passed),
        "excluded": {r: sorted(v) for r, v in excluded.items() if v},
        "failed": sorted(failed),
        "stale_exclusions": sorted(stale),
    }


def score_line(board: dict) -> str:
    excl = board["excluded"]
    total_excl = sum(len(v) for v in excl.values())
    by = ", ".join(f"{r} {len(v)}" for r, v in excl.items())
    return (
        f"perl t/: {board['run']} run, {len(board['passed'])} passed, "
        f"{total_excl} excluded ({by}), {len(board['failed'])} failed, "
        f"{len(board['stale_exclusions'])} stale exclusions"
    )


def run_suite(
    only: list[str] | None = None,
    jobs: int = 4,
    deadline_s: float = DEADLINE_S,
    results_path: Path | None = RESULTS,
    keep: bool = False,
    progress: bool = True,
    logs: Path | None = LOGS,
) -> tuple[dict, list[Outcome]]:
    tree = pristine_tree()
    lib = built_lib()
    if not (lib / "Config.pm").is_file():
        raise SystemExit(f"no built library at {lib}: set WASI_PERL_TARGET to the target tree")
    artifacts = _artifacts()
    exclusions = load_exclusions()
    tests = list_tests(tree)
    if only:
        tests = [t for t in tests if any(fnmatch.fnmatch(t, g) for g in only)]
    if not tests:
        raise SystemExit("no test matches")
    missing = sorted(set(exclusions) - set(list_tests(tree)))
    if missing:
        raise SystemExit(f"exclusions.toml names files t/ does not have: {', '.join(missing)}")
    # The slowest files first, as the last run timed them, so no long file starts last.
    previous: dict[str, float] = {}
    if results_path is not None and results_path.is_file():
        try:
            for f in json.loads(results_path.read_text()).get("files", []):
                previous[f["file"]] = f.get("seconds", 0.0)
        except (ValueError, KeyError, TypeError):
            previous = {}
    tests.sort(key=lambda t: -previous.get(t, 0.0))

    jobs = max(1, min(jobs, len(tests)))
    work = Path(tempfile.mkdtemp(prefix="perl_t-", dir=_work_parent()))
    slots: queue.Queue[tuple[Path, Path]] = queue.Queue()
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(jobs) as pool:
            staged = []
            for i in range(jobs):
                slot, tmp = work / f"slot{i}", work / f"tmp{i}"
                tmp.mkdir()
                staged.append(pool.submit(stage, tree, lib, slot))
                slots.put((slot, tmp))
            for s in staged:
                s.result()
            lock = threading.Lock()
            outcomes: list[Outcome] = []

            def task(test: str) -> Outcome:
                slot, tmp = slots.get()
                try:
                    return run_one(test, slot, tmp, artifacts, deadline_s, logs)
                finally:
                    slots.put((slot, tmp))

            futures = {pool.submit(task, t): t for t in tests}
            for fut in as_completed(futures):
                o = fut.result()
                with lock:
                    outcomes.append(o)
                    if progress:
                        mark = "ok  " if o.status == "pass" else "FAIL"
                        n = len(outcomes)
                        print(f"[{n}/{len(tests)}] {mark} {o.file} ({o.seconds}s)", flush=True)
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    outcomes.sort(key=lambda o: o.file)
    board = scoreboard(outcomes, exclusions)
    board["seconds"] = round(time.monotonic() - started, 1)
    board["line"] = score_line(board)
    if results_path is not None:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "scoreboard": board,
            "exclusions": {k: asdict(v) for k, v in sorted(exclusions.items())},
            "files": [asdict(o) for o in outcomes],
        }
        results_path.write_text(json.dumps(doc, indent=1) + "\n")
    return board, outcomes


def _work_parent() -> Path:
    parent = BUILD / "perl_t" / "work"
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run Perl's own t/ suite under perl.wasm.")
    ap.add_argument("--only", action="append", metavar="GLOB", help="files to run (rel. to t/)")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--deadline", type=float, default=DEADLINE_S, help="seconds per file")
    ap.add_argument("--list-failures", action="store_true", help="print each failing file")
    ap.add_argument("--keep", action="store_true", help="keep the work folders")
    ap.add_argument("--quiet", action="store_true", help="no per-file lines")
    ap.add_argument("--results", type=Path, default=RESULTS)
    a = ap.parse_args(argv)
    results = a.results
    if a.only and a.results == RESULTS:
        results = RESULTS.with_name("results-only.json")  # a partial run keeps the full one
    board, outcomes = run_suite(a.only, a.jobs, a.deadline, results, a.keep, not a.quiet)
    if a.list_failures:
        by_file = {o.file: o for o in outcomes}
        for f in board["failed"]:
            print(f"FAIL  {f}: {by_file[f].why}")
        for f in board["stale_exclusions"]:
            print(f"STALE {f}: excluded but passed")
    print(board["line"])
    return 0 if not board["failed"] and not board["stale_exclusions"] else 1


if __name__ == "__main__":
    sys.exit(main())
