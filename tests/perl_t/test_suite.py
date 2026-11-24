"""Perl's own t/ suite under the runtime (slow), the TAP parser and the exclusion list (fast)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_suite  # noqa: E402
from run_suite import load_exclusions, parse_tap  # noqa: E402

# ---------------------------------------------------------------------------------------------
# The TAP parser


def verdict(text: str) -> str | None:
    return parse_tap(text).verdict()


def test_plan_first_all_ok_passes():
    assert verdict("1..3\nok 1\nok 2 - two\nok 3 # skip no reason needed\n") is None


def test_plan_last_passes():
    assert verdict("ok 1\nok 2\n1..2\n") is None


def test_numbers_may_be_left_out():
    assert verdict("1..2\nok\nok\n") is None


def test_not_ok_fails():
    assert verdict("1..2\nok 1\nnot ok 2 - broken\n") == "not ok 2 (1 of 2)"


def test_todo_is_not_a_failure():
    tap = parse_tap("1..2\nok 1\nnot ok 2 - later # TODO not yet\n")
    assert tap.verdict() is None and tap.todo == 1


def test_todo_is_case_insensitive():
    assert verdict("1..1\nnot ok 1 # todo someday\n") is None


def test_hash_in_description_is_not_a_directive():
    assert verdict("1..1\nnot ok 1 - matches foo\n# TODO this is a comment line\n") is not None


def test_escaped_hash_in_description_then_todo():
    tap = parse_tap("1..1\nnot ok 1 - RT \\#2166: spurious # TODO RT #2166: later\n")
    assert tap.verdict() is None and tap.todo == 1


def test_escaped_hash_alone_is_no_directive():
    assert verdict("1..1\nnot ok 1 - see \\# TODO here\n") == "not ok 1 (1 of 1)"


def test_description_may_end_in_a_backslash():
    assert verdict("1..2\nok 1 - \\c\\\nok 2 - \\c]\n") is None


def test_bail_out_is_case_insensitive():
    assert verdict("1..2\nok 1\nbail out! gone\n") == "bail out: gone"


def test_strict_tap_refuses_unexpected_output():
    text = "1..1\nNot ok 1\nok 1\n"
    assert parse_tap(text).verdict() is None
    assert parse_tap(text, strict=True).verdict() == "unexpected output: 'Not ok 1'"
    assert parse_tap("1..1\n\n# note\nok 1\n", strict=True).verdict() is None


def test_strict_tap_is_for_core_tests_outside_lib():
    assert run_suite.strict_tap("op/x.t") and not run_suite.strict_tap("lib/x.t")


def test_skip_all_reason_after_skipped():
    assert parse_tap("1..0 # Skipped: no fork\n").skip_all == "no fork"


def test_too_few_results_fail():
    assert verdict("1..3\nok 1\nok 2\n") == "ran 2 of 3"


def test_too_many_results_fail():
    assert verdict("1..1\nok 1\nok 2\n") == "ran 2 of 1"


def test_no_plan_fails():
    assert verdict("ok 1\nok 2\n") == "no plan"


def test_two_plans_fail():
    assert verdict("1..1\nok 1\n1..1\n") == "2 plans"


def test_skip_all_with_reason_passes():
    tap = parse_tap("1..0 # skip no fork here\n")
    assert tap.verdict() is None and tap.skip_all == "no fork here"


def test_skip_all_spellings():
    assert parse_tap("1..0 # Skip: no threads\n").skip_all == "no threads"
    assert parse_tap("1..0 #SKIP no threads\n").skip_all == "no threads"


def test_skip_all_needs_a_reason():
    assert verdict("1..0 # skip\n") == "skip-all plan with no reason"
    assert verdict("1..0\n") == "1..0 without a skip reason"


def test_bail_out_fails():
    assert verdict("1..2\nok 1\nBail out! no perl\n") == "bail out: no perl"


def test_out_of_sequence_fails():
    assert verdict("1..2\nok 2\nok 1\n").startswith("results out of sequence")


def test_subtest_lines_are_not_counted():
    text = "1..1\n    1..2\n    ok 1\n    not ok 2 # TODO inner\nok 1 - subtest\n"
    assert verdict(text) is None


def test_comments_and_noise_are_ignored():
    assert verdict("# hello\n1..1\nnoise ok 7\nok 1\n# done\n") is None


def test_ok_must_be_a_word():
    assert verdict("1..1\nokay\nok 1\n") is None


def test_crlf_lines():
    assert verdict("1..1\r\nok 1\r\n") is None


# ---------------------------------------------------------------------------------------------
# The exclusion list


def test_exclusions_are_well_formed():
    exclusions = load_exclusions()
    for name, ex in exclusions.items():
        assert name.endswith(".t") and not name.startswith(("/", "t/")), name
        assert ex.reason in run_suite.REASONS


def test_exclusions_name_real_files():
    tree = run_suite.BUILD / f"perl-{run_suite.PERL_VERSION}"
    if not (tree / "t").is_dir():
        pytest.skip("the pristine tree is not unpacked: run tests/perl_t/run_suite.py once")
    known = set(run_suite.list_tests(tree))
    assert sorted(set(load_exclusions()) - known) == []


def test_bad_reason_is_refused(tmp_path):
    bad = tmp_path / "x.toml"
    bad.write_text('[files]\n"op/x.t" = { reason = "flaky" }\n')
    with pytest.raises(ValueError, match="not one of"):
        load_exclusions(bad)


def test_bug_needs_a_note(tmp_path):
    bad = tmp_path / "x.toml"
    bad.write_text('[files]\n"op/x.t" = { reason = "bug" }\n')
    with pytest.raises(ValueError, match="needs a note"):
        load_exclusions(bad)


def test_ok_at_least_is_required(tmp_path):
    bad = tmp_path / "x.toml"
    bad.write_text('[files]\n"op/x.t" = { reason = "porting", note = "n" }\n')
    with pytest.raises(ValueError, match="ok_at_least"):
        load_exclusions(bad)
    bad.write_text('[files]\n"op/x.t" = { reason = "porting", ok_at_least = -1 }\n')
    with pytest.raises(ValueError, match="ok_at_least"):
        load_exclusions(bad)


def outcome(name: str, status: str, oks: int = 1, signature: bool = True) -> run_suite.Outcome:
    return run_suite.Outcome(
        name, status, None, 0, None, 1, 1, [], 0, None, 0.0, [], oks=oks, signature_seen=signature
    )


def test_scoreboard_sorts_every_file():
    exclusions = {
        "op/x.t": run_suite.Exclusion("subprocess", ok_at_least=3),
        "op/y.t": run_suite.Exclusion("bug", "broken"),
    }
    files = [
        outcome("op/a.t", "pass"),
        outcome("op/b.t", "fail"),
        outcome("op/x.t", "fail", oks=3),
        outcome("op/y.t", "pass"),
    ]
    board = run_suite.scoreboard(files, exclusions)
    assert board["passed"] == ["op/a.t"]
    assert board["failed"] == ["op/b.t"]
    assert board["excluded"] == {"subprocess": ["op/x.t"]}
    assert board["stale_exclusions"] == ["op/y.t"]
    assert board["regressed"] == {}
    assert run_suite.score_line(board) == (
        "perl t/: 4 run, 1 passed, 1 excluded (subprocess 1), 1 failed, 1 stale exclusions, "
        "0 regressed (3 guarded oks)"
    )


def test_an_excluded_file_that_reaches_fewer_oks_has_regressed():
    exclusions = {"op/x.t": run_suite.Exclusion("bug", "broken", ok_at_least=10)}
    board = run_suite.scoreboard([outcome("op/x.t", "fail", oks=9)], exclusions)
    assert board["regressed"] == {"op/x.t": "9 ok, fewer than the 10 recorded"}
    board = run_suite.scoreboard([outcome("op/x.t", "fail", oks=11)], exclusions)
    assert board["regressed"] == {}


def test_shows_guards_any_reason():
    exclusions = {"op/x.t": run_suite.Exclusion("bug", "traps", ok_at_least=0, shows="trapped: x")}
    board = run_suite.scoreboard([outcome("op/x.t", "fail", signature=False)], exclusions)
    assert board["regressed"] == {"op/x.t": "its output lacks the entry's shows pattern"}
    board = run_suite.scoreboard([outcome("op/x.t", "fail", signature=True)], exclusions)
    assert board["regressed"] == {}


def test_an_edited_tree_has_another_digest(tmp_path):
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "a.t").write_text("print 1")
    before = run_suite.tree_digest(tmp_path)
    (tmp_path / "t" / "a.t").write_text("print 2")
    assert run_suite.tree_digest(tmp_path) != before


@pytest.mark.parametrize("reason", run_suite.REFUSAL_REASONS)
def test_a_refusal_exclusion_must_show_the_refusal(reason):
    exclusions = {"op/x.t": run_suite.Exclusion(reason, ok_at_least=1)}
    board = run_suite.scoreboard([outcome("op/x.t", "fail", signature=False)], exclusions)
    assert list(board["regressed"]) == ["op/x.t"]
    board = run_suite.scoreboard([outcome("op/x.t", "fail", signature=True)], exclusions)
    assert board["regressed"] == {}


# ---------------------------------------------------------------------------------------------
# The whole suite


@pytest.mark.slow
def test_perl_t_suite():
    """Every file not excluded passes, and every excluded file still fails."""
    pytest.importorskip("wasi_perl")
    if not (run_suite.built_lib() / "Config.pm").is_file():
        message = "no built library: run scripts/build.sh or set WASI_PERL_TARGET"
        if os.environ.get("CI"):
            pytest.fail(message)  # in CI a missing build is an error, not a skip
        pytest.skip(message)
    jobs = int(os.environ.get("PERL_T_JOBS", os.cpu_count() or 4))
    board, outcomes = run_suite.run_suite(jobs=jobs, progress=False)
    why = {o.file: o.why for o in outcomes}
    failed = [f"{f}: {why[f]}" for f in board["failed"]]
    stale = board["stale_exclusions"]
    regressed = [f"{f}: {w}" for f, w in board["regressed"].items()]
    assert not failed and not stale and not regressed, "\n  ".join(
        [board["line"], "failed:", *failed, "stale:", *stale, "regressed:", *regressed]
    )
