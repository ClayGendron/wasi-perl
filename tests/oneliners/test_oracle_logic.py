"""The judge's own logic, checked without any perl: the recorded oracle, the choice of
where expected results come from, the CPAN modules the module cases need, the
known-difference verdicts, and the corpus loader."""

from __future__ import annotations

import json
from pathlib import Path

import oneliner_oracle as oo
import pytest

IDS = ["one", "two"]


def _result(stdout: bytes = b"out\n", files=None) -> oo.Result:
    return oo.Result(
        exit_code=0,
        stdout=stdout,
        stderr=b"",
        files={"a.txt": b"x\n", "sub/": None} if files is None else files,
    )


def _recording(**overrides) -> dict:
    data = {
        "format": oo.ORACLE_JSON_FORMAT,
        "perl": oo.ORACLE_VERSION.removeprefix("v"),
        "corpus_sha256": oo.corpus_sha256(),
        "cpan_versions": dict(oo.CPAN_VERSIONS),
        "cases": {cid: _result().to_json() for cid in IDS},
    }
    data.update(overrides)
    return data


def _write(tmp_path: Path, data) -> Path:
    path = tmp_path / "oracle.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The recorded oracle


def test_result_round_trips_bytes_and_folders():
    r = _result(stdout=b"\xff\x00caf\xc3\xa9", files={"bin": b"\x00\xfe", "d/": None, "e": b""})
    back = oo.Result.from_json(json.loads(json.dumps(r.to_json())))
    assert back == r
    assert r.to_json()["files"]["d/"] == ""


def test_a_fitting_recording_loads(tmp_path):
    got = oo.load_recorded(_write(tmp_path, _recording()), IDS)
    assert set(got) == set(IDS)
    assert got["one"] == _result()


@pytest.mark.parametrize(
    "overrides",
    [
        {"format": oo.ORACLE_JSON_FORMAT + 1},
        {"perl": "5.42.2"},
        {"corpus_sha256": "0" * 64},
        {"cases": {"one": _result().to_json()}},
    ],
    ids=["format", "perl", "corpus", "missing-case"],
)
def test_a_recording_that_does_not_fit_is_refused(tmp_path, overrides):
    with pytest.raises(oo.StaleOracle):
        oo.load_recorded(_write(tmp_path, _recording(**overrides)), IDS)


@pytest.mark.parametrize("content", ["[1, 2]", "not json", ""])
def test_a_recording_that_is_not_an_object_is_refused(tmp_path, content):
    path = tmp_path / "oracle.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(oo.StaleOracle):
        oo.load_recorded(path, IDS)


def test_a_missing_recording_file_is_refused(tmp_path):
    with pytest.raises(oo.StaleOracle):
        oo.load_recorded(tmp_path / "absent.json", IDS)


# ---------------------------------------------------------------------------
# Where expected results come from


def test_native_wins_over_a_recording(tmp_path):
    stale = _write(tmp_path, _recording(corpus_sha256="0" * 64))
    kind, source = oo.choose_expected(lambda: Path("/x/perl"), str(stale), IDS)
    assert (kind, source) == ("native", Path("/x/perl"))


def test_a_recording_is_used_without_native_perl(tmp_path):
    kind, source = oo.choose_expected(lambda: None, str(_write(tmp_path, _recording())), IDS)
    assert kind == "recorded" and set(source) == set(IDS)


def test_a_stale_recording_fails_rather_than_skips(tmp_path):
    stale = _write(tmp_path, _recording(perl="5.40.0"))
    with pytest.raises(oo.StaleOracle):
        oo.choose_expected(lambda: None, str(stale), IDS)


def test_with_neither_oracle_the_answer_is_none():
    kind, why = oo.choose_expected(lambda: None, None, IDS)
    assert kind == "none" and "WASI_PERL_ORACLE_JSON" in why


def test_a_refused_perl_is_not_skipped(tmp_path):
    def refuse():
        raise oo.OracleRefused("perl v5.38.2")

    with pytest.raises(oo.OracleRefused):
        oo.choose_expected(refuse, str(_write(tmp_path, _recording())), IDS)


def test_find_oracle_refuses_a_named_path_that_is_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("WASI_PERL_ORACLE", str(tmp_path / "nope"))
    with pytest.raises(oo.OracleRefused):
        oo.find_oracle()


# ---------------------------------------------------------------------------
# The module cases' CPAN distributions


def test_the_pinned_versions_are_the_recipes_five():
    assert oo.CPAN_VERSIONS == {
        "Cpanel::JSON::XS": "4.83",
        "Text::CSV_XS": "1.65",
        "JSON::MaybeXS": "1.004008",
        "Text::CSV": "2.06",
        "Algorithm::Diff": "1.201",
    }


def _oracle_with(versions: dict[str, str | None]):
    return lambda: Path("/x/perl"), lambda perl: versions


def test_an_oracle_with_all_five_at_the_pinned_versions_is_used():
    find, versions = _oracle_with(dict(oo.CPAN_VERSIONS))
    assert oo.choose_module_expected(find, versions, None, IDS) == ("native", Path("/x/perl"))


def test_an_oracle_missing_a_cpan_module_fails_rather_than_skips(tmp_path):
    find, versions = _oracle_with({**oo.CPAN_VERSIONS, "Text::CSV": None})
    with pytest.raises(oo.OracleRefused, match="Text::CSV"):
        oo.choose_module_expected(find, versions, str(_write(tmp_path, _recording())), IDS)


def test_an_oracle_with_another_cpan_version_fails_rather_than_skips(tmp_path):
    find, versions = _oracle_with({**oo.CPAN_VERSIONS, "Cpanel::JSON::XS": "4.39"})
    with pytest.raises(oo.OracleRefused, match="4.39"):
        oo.choose_module_expected(find, versions, str(_write(tmp_path, _recording())), IDS)


@pytest.mark.parametrize(
    "versions",
    [
        {**oo.CPAN_VERSIONS, "Algorithm::Diff": "1.1903"},
        {k: v for k, v in oo.CPAN_VERSIONS.items() if k != "JSON::MaybeXS"},
        None,
    ],
    ids=["another-version", "one-missing", "none-recorded"],
)
def test_a_recording_with_other_cpan_versions_is_stale(tmp_path, versions):
    data = _recording(cpan_versions=versions)
    if versions is None:
        del data["cpan_versions"]
    with pytest.raises(oo.StaleOracle):
        oo.choose_expected(lambda: None, str(_write(tmp_path, data)), IDS)
    with pytest.raises(oo.StaleOracle):
        oo.choose_module_expected(lambda: None, lambda perl: {}, str(_write(tmp_path, data)), IDS)


def test_only_the_module_group_gets_the_cpan_library():
    plain = oo.Case("p", "grep", ("-e", "1"), {}, None, {})
    grouped = oo.Case("m", "modules", ("-e", "1"), {}, None, {}, group="cpan")
    assert oo.group_env(plain) == {}
    assert oo.group_env(grouped) == {"PERL5LIB": str(oo.NATIVE_CPAN_LIB)}


def test_an_unknown_group_is_refused():
    text = '[[case]]\nid = "x"\ncategory = "grep"\nargv = ["-e", "1"]\ngroup = "cpna"\n'
    with pytest.raises(ValueError, match="group"):
        oo.load_corpus(text)


# ---------------------------------------------------------------------------
# Verdicts and known differences

GAP = {"id": "one", "fields": ["exit_code"], "kind": "gap", "reason": "errno numbering"}


def test_a_match_passes():
    assert oo.verdict("one", {}, None) == ("pass", "")


def test_a_listed_case_that_matches_fails():
    outcome, message = oo.verdict("one", {}, GAP)
    assert outcome == "fail" and "remove it" in message


def test_a_listed_difference_is_an_expected_failure():
    outcome, message = oo.verdict("one", {"exit_code": "native 2, guest 44"}, GAP)
    assert outcome == "xfail" and "errno numbering" in message


def test_a_difference_outside_the_listed_fields_fails():
    diff = {"exit_code": "native 2, guest 44", "stdout": "x"}
    assert oo.verdict("one", diff, GAP)[0] == "fail"


def test_an_unlisted_difference_fails():
    outcome, message = oo.verdict("one", {"stdout": "native b'a', guest b'b'"}, None)
    assert outcome == "fail" and "native b'a'" in message


def test_a_trap_is_always_a_difference():
    expected = _result()
    actual = oo.Result(0, expected.stdout, b"", dict(expected.files), trapped=True)
    assert set(oo.differences(expected, actual)) == {"trapped"}


def test_differences_name_each_field():
    expected = _result()
    actual = oo.Result(3, b"other", b"warn", {"a.txt": b"y\n", "new": b""})
    assert set(oo.differences(expected, actual)) == {"exit_code", "stdout", "stderr", "files"}


# ---------------------------------------------------------------------------
# The corpus loader, the placeholder and normalisation


def test_an_unknown_case_key_is_refused():
    text = '[[case]]\nid = "x"\ncategory = "grep"\nargv = ["-e", "1"]\nfixture = ["log"]\n'
    with pytest.raises(ValueError, match="unknown keys"):
        oo.load_corpus(text)


def test_an_unknown_fixture_is_refused():
    text = '[[case]]\nid = "x"\ncategory = "grep"\nargv = ["-e", "1"]\nfixtures = ["nope"]\n'
    with pytest.raises(ValueError, match="no fixture"):
        oo.load_corpus(text)


def test_the_work_placeholder_expands():
    argv = ("-ne", "print", "{work}/a.txt", "b.txt")
    assert oo.expand_argv(argv, "/work") == ["-ne", "print", "/work/a.txt", "b.txt"]


def test_normalise_rewrites_the_work_directory(tmp_path):
    host = tmp_path / "w"
    host.mkdir()
    raw = oo.Result(0, f"{host}/a.txt\n".encode(), b"", {"log": f"at {host}\n".encode()})
    fixed = oo.normalise(raw, host)
    assert fixed.stdout == b"/work/a.txt\n" and fixed.files["log"] == b"at /work\n"


def test_snapshot_does_not_take_a_link_for_a_folder(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to("real")
    state = oo.snapshot(tmp_path)
    assert state["real/"] is None
    assert state["link"] == b"symlink -> real"


def test_a_host_entry_applies_on_its_hosts_alone():
    entry = {"id": "one", "fields": ["stdout"], "kind": "accepted", "hosts": ["win32"]}
    assert oo.known_here(entry, "win32") is entry
    assert oo.known_here(entry, "linux") is None
    everywhere = {"id": "one", "fields": ["stdout"], "kind": "accepted"}
    assert oo.known_here(everywhere, "darwin") is everywhere
    assert oo.known_here(None, "win32") is None
