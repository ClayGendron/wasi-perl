"""The one-liner oracle: agent-style Perl under wasi-perl must match native perl 5.42.3.

Three kinds of check:

- the corpus is well formed (always runs);
- every case is deterministic natively: two runs, in different work directories and with
  different hash seeds, give the same result after path normalisation (needs the native
  oracle);
- every case gives the same result in the guest as natively (needs the guest runner, and
  either the native oracle or a recorded oracle named by WASI_PERL_ORACLE_JSON).

The cases of the cpan group need the CPAN modules. Natively they run with build/native-cpan
on PERL5LIB, and an oracle without the pinned versions there fails them rather than skipping.
"""

from __future__ import annotations

import os
from pathlib import Path

import oneliner_oracle as oo
import pytest

CASES = oo.load_corpus()
KNOWN = oo.load_known_differences()
IDS = [c.id for c in CASES]
PLAIN = [c for c in CASES if c.group is None]
MODULE_CASES = [c for c in CASES if c.group == "cpan"]

# The guest's hang guard counts operations, not seconds: the heaviest case (num-fib) uses
# about 1.5e8 units of fuel, this is some 700 times that, and a slow host changes nothing.
GUEST_FUEL = 10**11


# ---------------------------------------------------------------------------
# The corpus itself


def test_corpus_is_well_formed():
    assert len(IDS) == len(set(IDS)), "case ids must be unique"
    for case in CASES:
        assert case.category in oo.CATEGORIES, f"{case.id}: unknown category {case.category}"
        assert case.argv and all(isinstance(a, str) for a in case.argv), case.id
        for name in case.files:
            assert not name.startswith("/") and ".." not in Path(name).parts, f"{case.id}: {name}"
    for cid, entry in KNOWN.items():
        assert cid in IDS, f"known_differences.toml names {cid}, which is not a case"
        assert entry.get("reason"), f"{cid}: a known difference needs a reason"
        assert entry.get("kind") in ("accepted", "gap"), f"{cid}: kind is accepted or gap"
        fields = entry.get("fields")
        assert fields and set(fields) <= oo.FIELDS, (
            f"{cid}: fields is a non-empty list of {oo.FIELDS}"
        )
        hosts = entry.get("hosts", list(oo.HOSTS))
        assert hosts and set(hosts) <= oo.HOSTS, f"{cid}: hosts is a non-empty list of {oo.HOSTS}"


# ---------------------------------------------------------------------------
# The native oracle


@pytest.fixture(scope="session")
def oracle() -> Path:
    try:
        path = oo.find_oracle()
    except oo.OracleRefused as e:
        pytest.fail(str(e))
    if path is None:
        pytest.skip(
            "no native perl 5.42.3: build it at build/native-install (scripts/10_native.sh) "
            "or name one with WASI_PERL_ORACLE"
        )
    return path


@pytest.fixture(scope="session")
def module_oracle(oracle) -> Path:
    """The native oracle, for the module cases: with the pinned CPAN modules, or it fails."""
    try:
        oo.check_module_versions(oo.module_versions(oracle))
    except oo.OracleRefused as e:
        pytest.fail(str(e))
    return oracle


@pytest.mark.parametrize("case", PLAIN, ids=[c.id for c in PLAIN])
def test_native_is_deterministic(case, oracle, tmp_path):
    _check_deterministic(case, oracle, tmp_path)


@pytest.mark.parametrize("case", MODULE_CASES, ids=[c.id for c in MODULE_CASES])
def test_native_module_case_is_deterministic(case, module_oracle, tmp_path):
    _check_deterministic(case, module_oracle, tmp_path)


def _check_deterministic(case, oracle, tmp_path):
    first = oo.run_native(oracle, case, tmp_path / "w")
    second = oo.run_native(oracle, case, tmp_path / "a-longer-second-work-dir", oo.OTHER_SEED_ENV)
    diff = oo.differences(first, second)
    assert not diff, f"{case.id} is not deterministic natively:\n" + _render(diff)
    leaked = [str(oracle.parent.parent), str(oo.NATIVE_CPAN_LIB.parent.parent), str(tmp_path)]
    for blob in (first.stdout, first.stderr, *[v for v in first.files.values() if v]):
        for host_path in leaked:
            assert host_path.encode() not in blob, f"{case.id} prints a host path: {host_path}"


# ---------------------------------------------------------------------------
# The guest against the oracle


@pytest.fixture(scope="session")
def expected_results():
    """A function (case, scratch dir) -> the expected Result.

    A native perl, when one is found, wins over a recorded oracle; a recorded oracle that
    does not fit this corpus fails the run rather than skipping it.
    """
    try:
        kind, source = oo.choose_expected(
            recorded_path=os.environ.get("WASI_PERL_ORACLE_JSON"), case_ids=IDS
        )
    except (oo.OracleRefused, oo.StaleOracle) as e:
        pytest.fail(str(e))
    if kind == "native":
        return lambda case, scratch: oo.run_native(source, case, scratch)
    if kind == "recorded":
        return lambda case, scratch: source[case.id]
    pytest.skip(source)


@pytest.fixture(scope="session")
def module_expected_results():
    """`expected_results`, for the module cases: a native oracle without the pinned CPAN
    modules fails them, as a recording made with other versions does."""
    try:
        kind, source = oo.choose_module_expected(
            recorded_path=os.environ.get("WASI_PERL_ORACLE_JSON"), case_ids=IDS
        )
    except (oo.OracleRefused, oo.StaleOracle) as e:
        pytest.fail(str(e))
    if kind == "native":
        return lambda case, scratch: oo.run_native(source, case, scratch)
    if kind == "recorded":
        return lambda case, scratch: source[case.id]
    pytest.skip(source)


@pytest.fixture(scope="session")
def guest():
    try:
        import wasi_perl
    except ImportError as e:
        pytest.skip(f"the wasi_perl runner is not importable ({e})")
    try:
        found = wasi_perl.artifacts()
    except wasi_perl.ArtifactsMissing as e:
        pytest.skip(f"no perl.wasm for the runner: {e}")
    if not Path(found.perl).is_file():
        pytest.skip(f"{found.perl} does not exist")
    return wasi_perl


def run_guest(wasi_perl, case: oo.Case, workdir: Path) -> oo.Result:
    oo.lay_out(case, workdir)
    env = {**oo.BASE_ENV, **case.env}
    done = wasi_perl.run(
        oo.expand_argv(case.argv, oo.GUEST_ROOT),
        grants=[wasi_perl.Grant(str(workdir), oo.GUEST_ROOT, writable=True)],
        env=env,
        stdin=case.stdin if case.stdin is not None else b"",
        cwd=oo.GUEST_ROOT,
        fuel=GUEST_FUEL,
        deadline_s=None,
    )
    notes = [f"output truncated by the runner's limit: {done.truncated}"] if done.truncated else []
    return oo.Result(
        exit_code=done.exit_code,
        stdout=done.stdout,
        stderr=done.stderr,
        files=oo.snapshot(workdir),
        trapped=done.trapped is not None or done.exit_code is None,
        notes=notes,
    )


@pytest.mark.slow
@pytest.mark.parametrize("case", PLAIN, ids=[c.id for c in PLAIN])
def test_guest_matches_native(case, expected_results, guest, tmp_path):
    _check_guest(case, expected_results, guest, tmp_path)


@pytest.mark.slow
@pytest.mark.parametrize("case", MODULE_CASES, ids=[c.id for c in MODULE_CASES])
def test_guest_module_case_matches_native(case, module_expected_results, guest, tmp_path):
    _check_guest(case, module_expected_results, guest, tmp_path)


def _check_guest(case, expected_results, guest, tmp_path):
    expected = expected_results(case, tmp_path / "native")
    actual = run_guest(guest, case, tmp_path / "guest")
    diff = oo.differences(expected, actual)
    outcome, message = oo.verdict(case.id, diff, oo.known_here(KNOWN.get(case.id)))
    if outcome == "xfail":
        pytest.xfail(message)
    if outcome == "fail":
        pytest.fail("\n  ".join([message, *actual.notes]))


def _render(diff: dict[str, str]) -> str:
    return "\n".join(f"  {k}: {v}" for k, v in diff.items())
