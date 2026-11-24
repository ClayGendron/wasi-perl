"""Grants across calls running at once in one process: a grant at, inside or above a live
call's writable grant is refused, and so is a writable grant at, inside or above a live call's
read-only one, so one call's guest can neither swap a folder on another call's way nor write
another call's read-only grant. Each check holds call A running (spinning until its deadline)
and waits on the registry, not on a clock, before it makes call B."""

from __future__ import annotations

import threading

import pytest

from wasi_perl import Grant, _grants, run


def _while_running(standin, grants, body):
    before = _grants.live_count()
    done = {}
    a = threading.Thread(
        target=lambda: done.update(a=run(["spin"], grants=grants, deadline_s=2, artifacts=standin))
    )
    a.start()
    try:
        while _grants.live_count() == before and a.is_alive():
            threading.Event().wait(0.001)
        assert a.is_alive(), "call A ended before it was seen running"
        body()
    finally:
        a.join()
    assert done["a"].trapped == "deadline"
    assert _grants.live_count() == before  # let go when it ended


@pytest.fixture
def folders(tmp_path):
    for d in ("w", "w/sub", "r", "r/sub", "apart"):
        (tmp_path / d).mkdir()
    return tmp_path


def test_a_grant_at_inside_or_above_a_live_writable_grant_is_refused(folders, standin):
    def body():
        for grant in (
            Grant(folders / "w" / "sub", "/b"),
            Grant(folders / "w", "/b"),
            Grant(folders / "w", "/b", writable=True),
            Grant(folders, "/b"),
        ):
            with pytest.raises(ValueError, match="running now"):
                run(["hello"], grants=[grant], artifacts=standin)
        assert (
            run(
                ["hello"], grants=[Grant(folders / "apart", "/b", True)], artifacts=standin
            ).exit_code
            == 0
        )

    _while_running(standin, [Grant(folders / "w", "/a", writable=True)], body)
    # Once A has ended, the same grant is fine.
    assert (
        run(["hello"], grants=[Grant(folders / "w" / "sub", "/b")], artifacts=standin).exit_code
        == 0
    )


def test_a_writable_grant_over_a_live_read_only_grant_is_refused(folders, standin):
    def body():
        for grant in (
            Grant(folders / "r", "/b", writable=True),
            Grant(folders / "r" / "sub", "/b", writable=True),
            Grant(folders, "/b", writable=True),
        ):
            with pytest.raises(ValueError, match="running now"):
                run(["hello"], grants=[grant], artifacts=standin)
        # Two calls may read one folder at once.
        assert run(["hello"], grants=[Grant(folders / "r", "/b")], artifacts=standin).exit_code == 0

    _while_running(standin, [Grant(folders / "r", "/a")], body)


def test_a_refused_call_holds_nothing(folders, standin):
    before = _grants.live_count()
    with pytest.raises(ValueError):
        run(
            ["hello"],
            grants=[Grant(folders / "w", "/a", True), Grant(folders / "w" / "sub", "/b")],
            artifacts=standin,
        )
    assert _grants.live_count() == before
    run(["trap"], grants=[Grant(folders / "w", "/a", True)], artifacts=standin)
    assert _grants.live_count() == before
