"""The shared machinery of the one-liner oracle.

A case from ``cases.toml`` is laid out in a fresh work directory, run, and summed up as a
``Result``: exit code, stdout bytes, stderr bytes and the final state of every file and
folder under the work directory. Native perl 5.42.3 gives the expected ``Result``; the
guest gives the actual one. Nothing in this module stores an expected output.

The native run sees its work directory at a host path; the guest sees the same layout at
``/work``. ``normalise`` rewrites the host path to ``/work`` in every byte the native run
produced, and it is the only rewriting anywhere: the live tests and ``record_oracle.py``
both get their native results from ``run_native``, which always applies it.

The cases of the ``cpan`` group use the five CPAN distributions the guest has built in. Native
perl runs them with ``PERL5LIB`` naming ``build/native-cpan``, where ``scripts/10_native.sh``
installs the same five, apart from the oracle's own library; every other case runs with no
``PERL5LIB``. An oracle whose ``build/native-cpan`` lacks one of the five, or has another
version, is refused for that group, and so is a recording made with one.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CASES_PATH = HERE / "cases.toml"
KNOWN_DIFFERENCES_PATH = HERE / "known_differences.toml"

ORACLE_VERSION = "v5.42.3"
GUEST_ROOT = "/work"
# A recording is refused when cases.toml changes. Any change to how a case is run or summed
# up (BASE_ENV, GUEST_ROOT, the placeholder, `normalise`, `snapshot`, the JSON layout) must
# bump this number, so recordings made the old way are refused too.
ORACLE_JSON_FORMAT = 2

# In argv, this stands for the work directory: the host path natively, /work in the guest.
# Agents pass absolute paths, and the guest resolves those through its preopens rather than
# its working directory, so both spellings are exercised.
WORK_PLACEHOLDER = "{work}"

# The compared fields, as `differences` names them and known_differences.toml lists them.
FIELDS = {"exit_code", "stdout", "stderr", "files", "trapped"}

CASE_KEYS = {"id", "category", "argv", "fixtures", "files", "stdin", "env", "group"}

# A case with `group = "cpan"` needs the CPAN distributions; no other group exists.
GROUPS = ("cpan",)

# Where scripts/10_native.sh installs the CPAN distributions for native perl.
NATIVE_CPAN_LIB = REPO / "build" / "native-cpan" / "lib" / "perl5"


def _pinned_cpan_versions() -> dict[str, str]:
    """Module -> version, for each distribution recipes/cpan-xs and recipes/cpan-pure pin: the
    tarball `Text-CSV_XS-1.65.tgz` is Text::CSV_XS 1.65."""
    versions = {}
    for recipe in ("cpan-xs", "cpan-pure"):
        for line in (REPO / "recipes" / recipe).read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            tarball = line.split()[0]
            for ext in (".tar.gz", ".tgz"):
                tarball = tarball.removesuffix(ext)
            dist, _, version = tarball.rpartition("-")
            versions[dist.replace("-", "::")] = version
    return versions


CPAN_VERSIONS = _pinned_cpan_versions()

CATEGORIES = (
    "grep",
    "multifile",
    "fields",
    "inplace",
    "records",
    "counting",
    "text",
    "numbers",
    "modules",
    "unicode",
    "files",
    "errors",
    "switches",
    "paths",
)

# Every run, native or guest, gets exactly this environment plus the case's own `env`.
# Fixed hash seeds make one build repeatable; they cannot make two builds agree, which is
# why the corpus never prints in hash order. LC_ALL=C pins the locale on both sides.
BASE_ENV = {
    "PERL_HASH_SEED": "0",
    "PERL_PERTURB_KEYS": "0",
    "LC_ALL": "C",
}

# A second native run uses a different seed and key order: a case whose output changes
# here depends on hash order, and would compare unfairly against another build.
OTHER_SEED_ENV = {
    "PERL_HASH_SEED": "12345",
    "PERL_PERTURB_KEYS": "2",
}

# A hang guard only. No test asserts on time.
RUN_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    argv: tuple[str, ...]
    files: dict[str, bytes | None]
    stdin: bytes | None
    env: dict[str, str]
    group: str | None = None


@dataclass
class Result:
    exit_code: int
    stdout: bytes
    stderr: bytes
    # Relative POSIX path -> content; None marks a folder.
    files: dict[str, bytes | None]
    trapped: bool = False
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        # A folder is its path with a trailing "/" and an empty value, so every value in
        # "files" is a base64 string.
        return {
            "exit_code": self.exit_code,
            "stdout": _b64(self.stdout),
            "stderr": _b64(self.stderr),
            "files": {k: _b64(v or b"") for k, v in sorted(self.files.items())},
        }

    @classmethod
    def from_json(cls, data: dict) -> Result:
        return cls(
            exit_code=data["exit_code"],
            stdout=base64.b64decode(data["stdout"], validate=True),
            stderr=base64.b64decode(data["stderr"], validate=True),
            files={
                k: (None if k.endswith("/") else base64.b64decode(v, validate=True))
                for k, v in data["files"].items()
            },
        )


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# ---------------------------------------------------------------------------
# The corpus


def corpus_sha256() -> str:
    return hashlib.sha256(CASES_PATH.read_bytes()).hexdigest()


def _content(value) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, dict) and set(value) == {"base64"}:
        return base64.b64decode(value["base64"], validate=True)
    raise ValueError(f"a file's content is a string or {{ base64 = ... }}, not {value!r}")


def load_corpus(text: str | None = None) -> list[Case]:
    """The cases of cases.toml (or of `text`). An unknown key or fixture name is an error, so
    a typo cannot turn a case into one that runs with no files and covers nothing."""
    if text is None:
        text = CASES_PATH.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    fixtures = data.get("fixtures", {})
    cases = []
    for raw in data["case"]:
        unknown = set(raw) - CASE_KEYS
        if unknown:
            raise ValueError(f"case {raw.get('id')!r}: unknown keys {sorted(unknown)}")
        if raw.get("group", GROUPS[0]) not in GROUPS:
            raise ValueError(f"case {raw.get('id')!r}: group is one of {GROUPS}")
        missing = [n for n in raw.get("fixtures", []) if n not in fixtures]
        if missing:
            raise ValueError(f"case {raw.get('id')!r}: no fixture named {missing}")
        files: dict[str, bytes | None] = {}
        for name in raw.get("fixtures", []):
            for path, value in fixtures[name].items():
                files[path] = None if path.endswith("/") else _content(value)
        for path, value in raw.get("files", {}).items():
            files[path] = None if path.endswith("/") else _content(value)
        stdin = raw.get("stdin")
        cases.append(
            Case(
                id=raw["id"],
                category=raw["category"],
                argv=tuple(raw["argv"]),
                files=files,
                stdin=None if stdin is None else stdin.encode("utf-8"),
                env=dict(raw.get("env", {})),
                group=raw.get("group"),
            )
        )
    return cases


# The systems an entry's `hosts` may name, as Python's sys.platform spells them.
HOSTS = {"linux", "darwin", "win32"}


def known_here(entry: dict | None, platform: str = sys.platform) -> dict | None:
    """The entry if it applies on this host: an entry with `hosts` is a difference of those
    host systems alone (what their file systems or wasmtime's port to them report), and on any
    other host the case must match."""
    if entry is not None and "hosts" in entry and platform not in entry["hosts"]:
        return None
    return entry


def load_known_differences() -> dict[str, dict]:
    if not KNOWN_DIFFERENCES_PATH.exists():
        return {}
    data = tomllib.loads(KNOWN_DIFFERENCES_PATH.read_text(encoding="utf-8"))
    return {entry["id"]: entry for entry in data.get("difference", [])}


# ---------------------------------------------------------------------------
# Work directories


def lay_out(case: Case, workdir: Path) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    for name, content in case.files.items():
        target = workdir / name
        if content is None:
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)


def expand_argv(argv: tuple[str, ...], root: str) -> list[str]:
    return [a.replace(WORK_PLACEHOLDER, root) for a in argv]


def snapshot(workdir: Path) -> dict[str, bytes | None]:
    state: dict[str, bytes | None] = {}
    for dirpath, dirnames, filenames in os.walk(workdir):
        base = Path(dirpath)
        for d in dirnames:
            p = base / d
            rel = p.relative_to(workdir).as_posix()
            if p.is_symlink():  # os.walk lists a link to a folder as a folder; it is not one
                state[rel] = b"symlink -> " + os.readlink(p).encode()
            else:
                state[rel + "/"] = None
        for f in filenames:
            p = base / f
            rel = p.relative_to(workdir).as_posix()
            state[rel] = (
                p.read_bytes() if not p.is_symlink() else b"symlink -> " + os.readlink(p).encode()
            )
    return dict(sorted(state.items()))


def normalise(result: Result, host_root: Path) -> Result:
    """Rewrite the host path of the work directory to the guest's `/work`, everywhere."""
    forms = {str(host_root), os.path.realpath(host_root)}
    needles = sorted((f.encode() for f in forms), key=len, reverse=True)

    def fix(data: bytes | None) -> bytes | None:
        if data is None:
            return None
        for n in needles:
            data = data.replace(n, GUEST_ROOT.encode())
        return data

    return Result(
        exit_code=result.exit_code,
        stdout=fix(result.stdout),
        stderr=fix(result.stderr),
        files={k: fix(v) for k, v in result.files.items()},
        trapped=result.trapped,
        notes=list(result.notes),
    )


# ---------------------------------------------------------------------------
# The native oracle


class OracleRefused(RuntimeError):
    """The perl found is not native perl 5.42.3, so it cannot be the oracle."""


def find_oracle(explicit: Path | str | None = None) -> Path | None:
    """The native oracle: `explicit`, else WASI_PERL_ORACLE, else build/native-install/bin/perl.

    Never the system perl: whatever is found must say it is v5.42.3, or it is refused.
    """
    named = explicit or os.environ.get("WASI_PERL_ORACLE")
    path = Path(named).absolute() if named else REPO / "build" / "native-install" / "bin" / "perl"
    if not path.is_file():
        if named:
            raise OracleRefused(f"{named} is not a file, so it cannot be the oracle")
        return None
    out = subprocess.run(
        [str(path), "-e", "print $^V"],
        capture_output=True,
        env={},
        timeout=RUN_TIMEOUT_SECONDS,
        check=False,
    )
    version = out.stdout.decode(errors="replace")
    if version != ORACLE_VERSION:
        raise OracleRefused(f"{path} is perl {version or '?'}, the oracle must be {ORACLE_VERSION}")
    return path


def group_env(case: Case) -> dict[str, str]:
    """What a native run of the case adds to BASE_ENV for its group."""
    return {"PERL5LIB": str(NATIVE_CPAN_LIB)} if case.group == "cpan" else {}


def module_versions(oracle: Path) -> dict[str, str | None]:
    """The version of each pinned CPAN module the oracle loads with the group's PERL5LIB, or
    None for one it cannot load."""
    prog = 'for (@ARGV) { print "$_\t", (eval "require $_; 1" ? $_->VERSION : ""), "\n" }'
    out = subprocess.run(
        [str(oracle), "-e", prog, *CPAN_VERSIONS],
        capture_output=True,
        env={"PERL5LIB": str(NATIVE_CPAN_LIB)},
        timeout=RUN_TIMEOUT_SECONDS,
        check=False,
    )
    found: dict[str, str | None] = dict.fromkeys(CPAN_VERSIONS)
    for line in out.stdout.decode(errors="replace").splitlines():
        name, _, version = line.partition("\t")
        if name in found:
            found[name] = version or None
    return found


def check_module_versions(found: dict[str, str | None]) -> None:
    """Refuse an oracle that lacks one of the pinned CPAN modules or has another version."""
    wrong = [
        f"{name} {found.get(name) or 'missing'} (pinned {want})"
        for name, want in CPAN_VERSIONS.items()
        if found.get(name) != want
    ]
    if wrong:
        raise OracleRefused(
            f"the oracle's {NATIVE_CPAN_LIB} does not hold the pinned CPAN modules: "
            + ", ".join(wrong)
            + "; run scripts/10_native.sh"
        )


def run_native(
    oracle: Path, case: Case, workdir: Path, extra_env: dict[str, str] | None = None
) -> Result:
    lay_out(case, workdir)
    env = {**BASE_ENV, **group_env(case), **(extra_env or {}), **case.env, "PWD": str(workdir)}
    proc = subprocess.run(
        [str(oracle), *expand_argv(case.argv, str(workdir))],
        input=case.stdin if case.stdin is not None else b"",
        capture_output=True,
        cwd=workdir,
        env=env,
        timeout=RUN_TIMEOUT_SECONDS,
        check=False,
    )
    if proc.returncode < 0:
        raise RuntimeError(f"native perl died by signal {-proc.returncode} on case {case.id}")
    raw = Result(
        exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr, files=snapshot(workdir)
    )
    return normalise(raw, workdir)


# ---------------------------------------------------------------------------
# The recorded oracle


def record(oracle: Path, cases: list[Case], scratch: Path) -> dict:
    """Every case's native result, with the CPAN versions the module cases ran with. An oracle
    without the pinned versions is refused: OracleRefused."""
    versions = module_versions(oracle)
    check_module_versions(versions)
    return {
        "format": ORACLE_JSON_FORMAT,
        "perl": ORACLE_VERSION.removeprefix("v"),
        "corpus_sha256": corpus_sha256(),
        "cpan_versions": versions,
        "cases": {case.id: run_native(oracle, case, scratch / case.id).to_json() for case in cases},
    }


class StaleOracle(RuntimeError):
    """A recorded oracle that does not belong to this corpus, this perl or this format."""


def load_recorded(path: Path, case_ids: list[str]) -> dict[str, Result]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise StaleOracle(f"{path}: cannot be read as a recorded oracle ({e})") from e
    if not isinstance(data, dict):
        raise StaleOracle(f"{path}: a recorded oracle is a JSON object")
    if data.get("format") != ORACLE_JSON_FORMAT:
        raise StaleOracle(f"{path}: format {data.get('format')!r}, expected {ORACLE_JSON_FORMAT}")
    want_perl = ORACLE_VERSION.removeprefix("v")
    if data.get("perl") != want_perl:
        raise StaleOracle(f"{path}: recorded with perl {data.get('perl')!r}, expected {want_perl}")
    if data.get("corpus_sha256") != corpus_sha256():
        raise StaleOracle(
            f"{path} was recorded from another cases.toml "
            f"(sha256 {data.get('corpus_sha256')}); record it again with record_oracle.py"
        )
    if data.get("cpan_versions") != CPAN_VERSIONS:
        raise StaleOracle(
            f"{path}: recorded with the CPAN modules {data.get('cpan_versions')!r}, "
            f"expected {CPAN_VERSIONS}"
        )
    cases = data.get("cases", {})
    missing = [cid for cid in case_ids if cid not in cases]
    if missing:
        raise StaleOracle(f"{path} has no result for {len(missing)} cases: {missing[:5]}")
    return {cid: Result.from_json(cases[cid]) for cid in case_ids}


def choose_expected(
    find=lambda: find_oracle(), recorded_path: str | None = None, case_ids: list[str] = ()
):
    """Where expected results come from: ("native", perl path), ("recorded", {id: Result}), or
    ("none", why). A native perl, when one is found, wins. A refused perl raises
    OracleRefused and a recording that does not fit raises StaleOracle: both fail the run."""
    path = find()
    if path is not None:
        return "native", path
    if recorded_path:
        return "recorded", load_recorded(Path(recorded_path), list(case_ids))
    return "none", (
        "no oracle: build native perl 5.42.3 at build/native-install, name one with "
        "WASI_PERL_ORACLE, or name a recorded oracle with WASI_PERL_ORACLE_JSON"
    )


def choose_module_expected(
    find=lambda: find_oracle(),
    versions=module_versions,
    recorded_path: str | None = None,
    case_ids: list[str] = (),
):
    """`choose_expected`, for the cases of the cpan group: a native oracle must also load the
    five pinned CPAN modules at their versions with the group's PERL5LIB, or OracleRefused
    fails the group. `versions(perl)` asks it."""
    kind, source = choose_expected(find, recorded_path, case_ids)
    if kind == "native":
        check_module_versions(versions(source))
    return kind, source


def verdict(case_id: str, diff: dict[str, str], known: dict | None) -> tuple[str, str]:
    """("pass" | "xfail" | "fail", message) for a compared case and its known-difference
    entry, if any. A listed case that matches fails, so the list cannot go stale; a
    difference outside the listed fields fails."""
    if not diff:
        if known is not None:
            return "fail", f"{case_id} now matches; remove it from known_differences.toml"
        return "pass", ""
    rendered = "\n".join(f"  {k}: {v}" for k, v in diff.items())
    if known is not None and set(diff) <= set(known["fields"]):
        return "xfail", f"known {known['kind']}: {known['reason']}"
    return "fail", f"{case_id} differs from native perl:\n{rendered}"


# ---------------------------------------------------------------------------
# Comparing


def differences(expected: Result, actual: Result) -> dict[str, str]:
    """Field name -> a short description, for every field that differs."""
    out: dict[str, str] = {}
    if actual.trapped:
        out["trapped"] = "the guest trapped"
    if expected.exit_code != actual.exit_code:
        out["exit_code"] = f"native {expected.exit_code}, guest {actual.exit_code}"
    for name in ("stdout", "stderr"):
        e, a = getattr(expected, name), getattr(actual, name)
        if e != a:
            out[name] = f"native {e[:400]!r}\n      guest  {a[:400]!r}"
    if expected.files != actual.files:
        lines = []
        for path in sorted(set(expected.files) | set(actual.files)):
            e, a = expected.files.get(path, "<absent>"), actual.files.get(path, "<absent>")
            if e != a:
                lines.append(f"{path}: native {_short(e)}, guest {_short(a)}")
        out["files"] = "\n      ".join(lines)
    return out


def _short(v) -> str:
    if v is None:
        return "<folder>"
    if isinstance(v, str):
        return v
    return repr(v[:120])
