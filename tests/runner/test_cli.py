"""`wasi-perl run`: the argv split, each option reaching `run()` as its argument (a fake `run`
records them), the exit codes, and the JSON line's shape; then real calls through `main()`."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from wasi_perl import Completed, Grant, cli


@pytest.fixture
def fake(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = list(argv)
        seen["kw"] = kw
        if "raise" in seen:
            raise seen["raise"]
        return seen.get("done", Completed(0, b"out\n", b"", None, None, ()))

    monkeypatch.setattr(cli, "_run", fake_run)
    return seen


def _main(args, capsysbinary):
    code = cli.main(args)
    out, err = capsysbinary.readouterr()
    return code, out, err


@pytest.mark.parametrize(
    ("args", "guest"),
    [
        (["run", "--", "-e", "print 1"], ["-e", "print 1"]),
        (["run", "--", "-ne", "--fuel"], ["-ne", "--fuel"]),
        (["run", "x.pl", "--fuel", "5"], ["x.pl", "--fuel", "5"]),
        (["run", "x.pl", "--", "y"], ["x.pl", "--", "y"]),
        (["run", "--fuel=5", "x.pl"], ["x.pl"]),
        (["run", "--fuel", "5", "--", "-v"], ["-v"]),
        (["run"], []),
        (["run", "-", "a"], ["-", "a"]),
    ],
)
def test_the_argv_split(args, guest, fake, capsysbinary):
    code, _, _ = _main(args, capsysbinary)
    assert code == 0
    assert fake["argv"] == guest


def test_each_option_reaches_run(fake, capsysbinary, tmp_path, monkeypatch):
    (tmp_path / "in").write_bytes(b"input")
    monkeypatch.setenv("SECRET", "s3")
    args = [
        "run",
        "--grant", "/a=/host/a",
        "--grant-writable", "/b=C:\\data=x",
        "--grant", "/c=relative",
        "--cwd", "/a",
        "--env", "K=V=W",
        "--env", "SECRET",
        "--stdin", str(tmp_path / "in"),
        "--deadline-s", "1.5",
        "--memory-bytes", "none",
        "--output-bytes", "10",
        "--fuel", "100",
        "--cache-dir", str(tmp_path / "c"),
        "--", "-e", "1",
    ]  # fmt: skip
    code, out, _ = _main(args, capsysbinary)
    assert code == 0
    kw = fake["kw"]
    assert kw["grants"] == [
        Grant("/host/a", "/a"),
        Grant("C:\\data=x", "/b", writable=True),
        Grant("relative", "/c"),
    ]
    assert kw["cwd"] == "/a"
    assert kw["env"] == {"K": "V=W", "SECRET": "s3"}
    assert kw["stdin"] == b"input"
    assert (kw["deadline_s"], kw["memory_bytes"], kw["output_bytes"], kw["fuel"]) == (
        1.5,
        None,
        10,
        100,
    )
    assert kw["cache_dir"] == str(tmp_path / "c")


def test_an_option_not_given_is_not_passed(fake, capsysbinary):
    _main(["run", "--", "-e", "1"], capsysbinary)
    assert fake["kw"] == {}
    _main(["run", "--no-cache", "--", "-e", "1"], capsysbinary)
    assert fake["kw"] == {"cache_dir": False}


def test_stdin_dash_reads_this_commands_input(fake, capsysbinary, monkeypatch):
    import io

    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"piped")))
    _main(["run", "--stdin", "-", "--", "-"], capsysbinary)
    assert fake["kw"]["stdin"] == b"piped"


@pytest.mark.parametrize(
    "args",
    [
        ["run", "-e", "1"],
        ["run", "--dead", "1", "--", "x"],
        ["run", "--fuel", "ten", "--", "x"],
        ["run", "--grant", "/d", "--", "x"],
        ["run", "--grant-writabl", "/d=x", "--", "x"],
        ["run", "--env", "NOT_IN_THIS_ENVIRONMENT_X", "--", "x"],
        ["run", "--cache-dir", "c", "--no-cache", "--", "x"],
        ["frobnicate"],
        [],
    ],
)
def test_a_usage_error_exits_2_with_nothing_on_stdout(args, fake, capsysbinary):
    code, out, err = _main(args, capsysbinary)
    assert (code, out) == (2, b"")
    assert err
    assert "argv" not in fake


def test_a_missing_env_name_is_never_echoed(fake, capsysbinary):
    code, _, err = _main(["run", "--env", "hunter2-not-a-name", "--", "x"], capsysbinary)
    assert code == 2 and b"hunter2" not in err


def test_help_exits_0(capsysbinary):
    code, out, _ = _main(["run", "--help"], capsysbinary)
    assert code == 0 and b"--grant" in out


def test_a_refusal_exits_1_with_the_error_line(fake, capsysbinary):
    fake["raise"] = ValueError("fuel must be from 0")
    code, out, _ = _main(["run", "--fuel", "-1", "--", "x"], capsysbinary)
    assert code == 1
    assert out.endswith(b"\n") and out.count(b"\n") == 1
    assert json.loads(out) == {"error": "fuel must be from 0", "kind": "ValueError"}


def test_an_unreadable_stdin_exits_1(fake, capsysbinary, tmp_path):
    code, out, _ = _main(["run", "--stdin", str(tmp_path / "gone"), "--", "x"], capsysbinary)
    assert code == 1 and json.loads(out)["kind"] == "FileNotFoundError"


def test_the_result_line(fake, capsysbinary):
    fake["done"] = Completed(3, "é\n".encode(), b"\xff", None, 12, ("stderr",))
    code, out, _ = _main(["run", "--", "x"], capsysbinary)
    assert code == 0
    assert out.endswith(b"\n") and out.count(b"\n") == 1
    out.decode("ascii")  # ensure_ascii
    line = json.loads(out)
    assert list(line) == sorted(line)
    assert line == {
        "exit_code": 3,
        "fuel_used": 12,
        "stderr_base64": "/w==",
        "stdout": "é\n",
        "trapped": None,
        "truncated": ["stderr"],
    }


def test_ctrl_c_exits_130(fake, capsysbinary):
    fake["raise"] = KeyboardInterrupt()
    assert _main(["run", "--", "x"], capsysbinary)[:2] == (130, b"")


def test_a_real_call(standin_dir, capsysbinary, monkeypatch):
    monkeypatch.setenv("WASI_PERL_ARTIFACTS", str(standin_dir))
    code, out, _ = _main(["run", "--", "hello"], capsysbinary)
    assert code == 0
    assert out == (
        b'{"exit_code": 0, "fuel_used": null, "stderr": "", "stdout": "hello\\n", '
        b'"trapped": null, "truncated": []}\n'
    )
    code, out, _ = _main(["run", "--fuel", "100000", "--", "spin"], capsysbinary)
    assert json.loads(out)["trapped"] == "out of fuel"


def test_python_dash_m(standin_dir):
    import os

    env = {**os.environ, "WASI_PERL_ARTIFACTS": str(standin_dir)}
    got = subprocess.run(
        [sys.executable, "-m", "wasi_perl", "run", "--", "exit", "4"],
        env=env, capture_output=True, check=True,
    )  # fmt: skip
    assert json.loads(got.stdout)["exit_code"] == 4
