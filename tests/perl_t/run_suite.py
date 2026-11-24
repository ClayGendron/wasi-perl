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
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

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

REASONS = (
    "subprocess",
    "signals",
    "refused",
    "threads",
    "porting",
    "dynamic-loading",
    "platform",
    "not-this-platform",
    "bug",
)
# An excluded file with one of these reasons must show the sandbox's refusal in its output.
REFUSAL_REASONS = ("subprocess", "signals", "refused")
REFUSAL = "is not available in the wasi-perl sandbox"
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
    oks: int = 0  # results that are "ok", not "not ok"
    unexpected: str | None = None  # the first line that is not TAP, where none is allowed

    def verdict(self) -> str | None:
        """None when the TAP alone is a pass; else why not, in a few words."""
        if self.bailed_out is not None:
            return f"bail out: {self.bailed_out}"
        if self.unexpected is not None:
            return f"unexpected output: {self.unexpected[:60]!r}"
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
_BAIL = re.compile(r"(?i)^Bail out!\s*(.*)$")


def parse_tap(text: str, strict: bool = False) -> Tap:
    """Parse top-level TAP. A blank line or a "#" comment is skipped. Any other line that is not
    a plan, a result or a bail-out is ignored, or, when `strict`, is unexpected output, as t/TEST
    has it for every core test outside lib/ (an indented subtest line included)."""
    tap = Tap()
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line.strip() or line.startswith("#"):
            continue
        m = _PLAN.match(line)
        if m:
            tap.plans += 1
            tap.planned = int(m.group(1))
            directive = (m.group(2) or "").strip()
            if tap.planned == 0 and re.match(r"(?i)skip", directive):
                tap.skip_all = re.sub(r"(?i)^skip\w*[:\s]*", "", directive)
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
            if not m.group(1):
                tap.oks += 1
            if is_todo:
                tap.todo += 1
            elif m.group(1):
                tap.failed.append(number)
            continue
        m = _BAIL.match(line)
        if m:
            if tap.bailed_out is None:
                tap.bailed_out = m.group(1).strip() or "(no reason)"
        elif strict and tap.unexpected is None:
            tap.unexpected = line
    return tap


def strict_tap(test: str) -> bool:
    """t/TEST allows output that is not TAP only in module tests; under t/ that is lib/."""
    return not test.startswith("lib/")


# ---------------------------------------------------------------------------------------------
# Exclusions


@dataclass(frozen=True)
class Exclusion:
    """One excluded file. `ok_at_least` is how many "ok" results it reached when it was
    classified: a run where it reaches fewer has regressed, as has a refusal-reason file whose
    output no longer shows the refusal."""

    reason: str
    note: str = ""
    ok_at_least: int = 0
    # For a refusal reason: a pattern its output shows where the file catches the refusal and
    # never prints it (the note says where it is refused); else the refusal message itself.
    shows: str | None = None

    def evidence(self) -> re.Pattern[str] | None:
        if self.reason not in REFUSAL_REASONS:
            return None
        return re.compile(self.shows if self.shows is not None else re.escape(REFUSAL), re.M)


def load_exclusions(path: Path = EXCLUSIONS) -> dict[str, Exclusion]:
    """The exclusion list: test file (relative to t/) -> its reason, note, ok_at_least and
    shows. Raises ValueError on an unknown reason, a missing note where one is required, a
    missing or negative ok_at_least, a shows that is not a refusal's or not a pattern, or an
    unknown key."""
    data = tomllib.loads(path.read_text())
    files = data.get("files", {})
    out: dict[str, Exclusion] = {}
    for name, entry in files.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{name}: an entry is a table with a reason")
        unknown = set(entry) - {"reason", "note", "ok_at_least", "shows"}
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
        oks = entry.get("ok_at_least")
        if isinstance(oks, bool) or not isinstance(oks, int) or oks < 0:
            raise ValueError(f"{name}: ok_at_least must be a whole number of results")
        shows = entry.get("shows")
        if shows is not None:
            if reason not in REFUSAL_REASONS or not isinstance(shows, str) or not note.strip():
                raise ValueError(f"{name}: shows is for a refusal reason, with a note")
            try:
                re.compile(shows)
            except re.error as e:
                raise ValueError(f"{name}: shows is not a pattern: {e}") from None
        out[name] = Exclusion(reason, note, oks, shows)
    return out


# ---------------------------------------------------------------------------------------------
# The tree


def _expected_sha256(name: str = TARBALL_NAME) -> str | None:
    sums = REPO / "downloads" / "SHA256SUMS"
    if not sums.is_file():
        return None
    for line in sums.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == name:
            return parts[0]
    return None


def _main_checkout() -> Path | None:
    """The checkout whose .git this worktree shares, where scripts/00_download.sh may have
    fetched the tarball once for every worktree."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(out).parent if out else None


def find_tarball() -> Path:
    """The pinned tarball: $WASI_PERL_TARBALL; else downloads/ of this checkout; else
    $WASI_PERL_DOWNLOAD_CACHE, the folder the build copies downloads from; else downloads/ of
    the main checkout of this worktree."""
    env = os.environ.get("WASI_PERL_TARBALL")
    if env:
        return Path(env)
    here = REPO / "downloads" / TARBALL_NAME
    places = [here]
    if os.environ.get("WASI_PERL_DOWNLOAD_CACHE"):
        places.append(Path(os.environ["WASI_PERL_DOWNLOAD_CACHE"]) / TARBALL_NAME)
    main = _main_checkout()
    if main is not None:
        places.append(main / "downloads" / TARBALL_NAME)
    return next((p for p in places if p.is_file()), here)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def pristine_tree(tarball: Path | None = None) -> Path:
    """build/perl-5.42.3/, unpacked from the pinned tarball after its hash is checked. The
    unpacked tree is reused only while its stamp holds the pinned hash; otherwise it is
    unpacked again. It is never written to: each run works on a copy."""
    want = _expected_sha256()
    if want is None:
        raise SystemExit(f"no pinned hash for {TARBALL_NAME} in downloads/SHA256SUMS")
    tree = BUILD / f"perl-{PERL_VERSION}"
    stamp = BUILD / f"perl-{PERL_VERSION}.sha256"
    if (tree / "t" / "TEST").is_file() and stamp.is_file() and stamp.read_text().strip() == want:
        return tree
    tarball = tarball or find_tarball()
    if not tarball.is_file():
        raise SystemExit(f"no {TARBALL_NAME}: run scripts/00_download.sh or set WASI_PERL_TARBALL")
    got = _sha256(tarball)
    if got != want:
        raise SystemExit(f"{tarball}: sha256 {got} is not the pinned {want}")
    shutil.rmtree(tree, ignore_errors=True)
    stamp.unlink(missing_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball) as tf:
        tf.extractall(BUILD, filter="data")
    stamp.write_text(want + "\n")
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


def timed(test: str) -> bool:
    """t/benchmark/ asserts ratios of wall-clock speeds, which t/TEST runs only when
    PERL_BENCHMARK is set; so does this harness, since its checks count operations, not time."""
    return test.startswith("benchmark/") and not os.environ.get("PERL_BENCHMARK")


def stage(tree: Path, lib: Path, into: Path) -> None:
    """A fresh writable copy of the pristine tree at `into`, with the built library's files
    that the tarball does not have laid over its lib/. Files the tarball has are kept as
    shipped, so nothing a build step changed in place reaches the tests."""
    shutil.copytree(tree, into, symlinks=True)
    # make test runs with t/perl, the built perl; the guest's $^X is "perl", so test.pl's
    # which_perl looks for ./perl. An empty stand-in is found and never runs: a child perl is
    # refused before it would start.
    (into / "t" / "perl").touch()
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
    oks: int = 0
    refusal_seen: bool = False
    stdout_tail: str = ""
    stderr_tail: str = ""


def _tail(data: bytes) -> str:
    text = data[-TAIL_BYTES:].decode("utf-8", "replace")
    return text


def run_one(
    test: str,
    slot: Path,
    tmp: Path,
    artifacts: object,
    deadline_s: float,
    logs: Path | None = None,
    evidence: re.Pattern[str] | None = None,
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
    tap = parse_tap(stdout, strict=strict_tap(test))
    stderr = done.stderr.decode("utf-8", "replace")
    pattern = evidence or re.compile(re.escape(REFUSAL))
    refusal_seen = bool(pattern.search(stdout) or pattern.search(stderr))
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
        oks=tap.oks,
        refusal_seen=refusal_seen,
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
    passed), and regressed (excluded, still failing, but reaching fewer "ok" results than its
    entry records, or, for a refusal reason, no longer showing the refusal)."""
    passed, failed, stale = [], [], []
    regressed: dict[str, str] = {}
    excluded: dict[str, list[str]] = {r: [] for r in REASONS}
    guarded = 0
    for o in outcomes:
        ex = exclusions.get(o.file)
        if ex is None:
            (passed if o.status == "pass" else failed).append(o.file)
        elif o.status == "pass":
            stale.append(o.file)
        else:
            excluded[ex.reason].append(o.file)
            guarded += ex.ok_at_least
            if o.oks < ex.ok_at_least:
                regressed[o.file] = f"{o.oks} ok, fewer than the {ex.ok_at_least} recorded"
            elif ex.reason in REFUSAL_REASONS and not o.refusal_seen:
                shown = "what its entry shows" if ex.shows is not None else "a refusal"
                regressed[o.file] = f"a {ex.reason} exclusion, but no {shown} in its output"
    return {
        "run": len(outcomes),
        "passed": sorted(passed),
        "excluded": {r: sorted(v) for r, v in excluded.items() if v},
        "failed": sorted(failed),
        "stale_exclusions": sorted(stale),
        "regressed": dict(sorted(regressed.items())),
        "guarded_oks": guarded,
    }


def score_line(board: dict) -> str:
    excl = board["excluded"]
    total_excl = sum(len(v) for v in excl.values())
    by = ", ".join(f"{r} {len(v)}" for r, v in excl.items())
    return (
        f"perl t/: {board['run']} run, {len(board['passed'])} passed, "
        f"{total_excl} excluded ({by}), {len(board['failed'])} failed, "
        f"{len(board['stale_exclusions'])} stale exclusions, "
        f"{len(board['regressed'])} regressed ({board['guarded_oks']} guarded oks)"
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
    tests = [t for t in list_tests(tree) if not timed(t)]
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
                    ex = exclusions.get(test)
                    evidence = ex.evidence() if ex is not None else None
                    return run_one(test, slot, tmp, artifacts, deadline_s, logs, evidence)
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
        for f, why in board["regressed"].items():
            print(f"REGRESSED {f}: {why}")
    print(board["line"])
    clean = not board["failed"] and not board["stale_exclusions"] and not board["regressed"]
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
