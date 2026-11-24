"""The one-liner oracle: agent-style Perl under wasi-perl must match native perl 5.42.3.

Three kinds of check:

- the corpus is well formed (always runs);
- every case is deterministic natively: two runs, in different work directories and with
  different hash seeds, give the same result after path normalisation (needs the native
  oracle);
- every case gives the same result in the guest as natively (needs the guest runner, and
  either the native oracle or a recorded oracle named by WASI_PERL_ORACLE_JSON).
"""

from __future__ import annotations

import os
from pathlib import Path

import oneliner_oracle as oo
import pytest

CASES = oo.load_corpus()
KNOWN = oo.load_known_differences()
IDS = [c.id for c in CASES]


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
        assert set(entry.get("fields", [])) <= {
            "exit_code",
            "stdout",
            "stderr",
            "files",
            "trapped",
        }, cid


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


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_native_is_deterministic(case, oracle, tmp_path):
    first = oo.run_native(oracle, case, tmp_path / "w")
    second = oo.run_native(oracle, case, tmp_path / "a-longer-second-work-dir", oo.OTHER_SEED_ENV)
    diff = oo.differences(first, second)
    assert not diff, f"{case.id} is not deterministic natively:\n" + _render(diff)
    leaked = [str(oracle.parent.parent), str(tmp_path)]
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
        path = oo.find_oracle()
    except oo.OracleRefused as e:
        pytest.fail(str(e))
    if path is not None:
        return lambda case, scratch: oo.run_native(path, case, scratch)
    named = os.environ.get("WASI_PERL_ORACLE_JSON")
    if named:
        try:
            recorded = oo.load_recorded(Path(named), IDS)
        except oo.StaleOracle as e:
            pytest.fail(str(e))
        return lambda case, scratch: recorded[case.id]
    pytest.skip(
        "no oracle: build native perl 5.42.3 at build/native-install, name one with "
        "WASI_PERL_ORACLE, or name a recorded oracle with WASI_PERL_ORACLE_JSON"
    )


@pytest.fixture(scope="session")
def guest():
    try:
        import wasi_perl
    except ImportError as e:
        pytest.skip(f"the wasi_perl runner is not importable ({e})")
    artifacts = os.environ.get("WASI_PERL_ARTIFACTS")
    if artifacts and not (Path(artifacts) / "perl.wasm").is_file():
        pytest.skip(f"WASI_PERL_ARTIFACTS={artifacts} holds no perl.wasm")
    return wasi_perl


def run_guest(wasi_perl, case: oo.Case, workdir: Path) -> oo.Result:
    oo.lay_out(case, workdir)
    env = {**oo.BASE_ENV, **case.env}
    done = wasi_perl.run(
        list(case.argv),
        grants=[wasi_perl.Grant(str(workdir), oo.GUEST_ROOT, writable=True)],
        env=env,
        stdin=case.stdin if case.stdin is not None else b"",
        cwd=oo.GUEST_ROOT,
    )
    notes = ["output truncated by the runner's limit"] if getattr(done, "truncated", False) else []
    return oo.Result(
        exit_code=done.exit_code,
        stdout=done.stdout,
        stderr=done.stderr,
        files=oo.snapshot(workdir),
        trapped=bool(getattr(done, "trapped", False)),
        notes=notes,
    )


@pytest.mark.slow
@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_guest_matches_native(case, expected_results, guest, tmp_path):
    expected = expected_results(case, tmp_path / "native")
    actual = run_guest(guest, case, tmp_path / "guest")
    diff = oo.differences(expected, actual)
    known = KNOWN.get(case.id)
    if not diff:
        assert known is None, f"{case.id} now matches; remove it from known_differences.toml"
        return
    if known and set(diff) <= set(known["fields"]):
        pytest.xfail(f"known {known['kind']}: {known['reason']}")
    pytest.fail(f"{case.id} differs from native perl:\n" + _render(diff) + "".join(actual.notes))


def _render(diff: dict[str, str]) -> str:
    return "\n".join(f"  {k}: {v}" for k, v in diff.items())
