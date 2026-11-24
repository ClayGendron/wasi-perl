"""Every argument of `run` is checked before any work: a wrong type is a TypeError, a bad value
a ValueError, and no module is compiled or loaded for a refused call."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import wasi_perl
from wasi_perl import Defaults, Grant, _engines, run


@pytest.fixture
def no_work(monkeypatch: pytest.MonkeyPatch):
    """Fails the check if a refused call reaches the module."""
    calls = []
    monkeypatch.setattr(_engines, "module", lambda *a, **k: calls.append(a) or 1 / 0)
    yield
    assert calls == []


def test_the_public_api():
    assert wasi_perl.__version__ == "0.1.0"
    assert sorted(wasi_perl.__all__) == sorted(
        [
            "Artifacts",
            "ArtifactsMissing",
            "Completed",
            "Defaults",
            "Errno",
            "FileFault",
            "FileProvider",
            "FileRecord",
            "Grant",
            "Mount",
            "RefusedWrite",
            "Stat",
            "TooManyCalls",
            "WritableFileProvider",
            "__version__",
            "artifacts",
            "defaults",
            "run",
            "set_defaults",
        ]
    )


@pytest.mark.parametrize(
    ("kwargs", "kind", "words"),
    [
        ({"argv": "-e 1"}, TypeError, "argv"),
        ({"argv": [b"-e"]}, TypeError, "argv[0]"),
        ({"argv": ["a\0b"]}, ValueError, "argv[0]"),
        ({"argv": ["\ud800"]}, ValueError, "argv[0]"),
        ({"env": ["A=1"]}, TypeError, "env"),
        ({"env": {"": "1"}}, ValueError, "env key"),
        ({"env": {"A=B": "1"}}, ValueError, "env key"),
        ({"env": {"A": 1}}, TypeError, "env value"),
        ({"env": {"A": "x\0"}}, ValueError, "env value"),
        ({"env": {"PWD": "/x"}}, ValueError, "cwd"),
        ({"stdin": "text"}, TypeError, "stdin"),
        ({"fuel": True}, TypeError, "fuel"),
        ({"fuel": -1}, ValueError, "fuel"),
        ({"fuel": 2**63}, ValueError, "fuel"),
        ({"fuel": 1.5}, TypeError, "fuel"),
        ({"memory_bytes": -1}, ValueError, "memory_bytes"),
        ({"output_bytes": "1"}, TypeError, "output_bytes"),
        ({"deadline_s": 0}, ValueError, "deadline_s"),
        ({"deadline_s": float("inf")}, ValueError, "deadline_s"),
        ({"deadline_s": float("nan")}, ValueError, "deadline_s"),
        ({"deadline_s": True}, TypeError, "deadline_s"),
        ({"deadline_s": "1"}, TypeError, "deadline_s"),
        ({"grants": "/x"}, TypeError, "grants"),
        ({"grants": ["/x"]}, TypeError, "grants[0]"),
        ({"cache_dir": ""}, ValueError, "cache_dir"),
        ({"cache_dir": 3}, TypeError, "cache_dir"),
        ({"artifacts": "/x"}, TypeError, "artifacts"),
        ({"cwd": "/data"}, ValueError, "cwd"),
        ({"cwd": 5}, TypeError, "cwd"),
    ],
)
def test_a_bad_argument_is_refused_before_any_work(kwargs, kind, words, no_work, standin):
    argv = kwargs.pop("argv", ["hello"])
    with pytest.raises(kind, match=words.replace("[", r"\[").replace("]", r"\]")):
        run(argv, artifacts=standin, **kwargs)


def test_an_env_value_is_never_quoted(no_work, standin):
    with pytest.raises(ValueError) as e:
        run(["hello"], env={"TOKEN": "secret\0value"}, artifacts=standin)
    assert "secret" not in str(e.value)


@pytest.mark.parametrize(
    ("guest", "words"),
    [
        ("data", "absolute"),
        ("/", "not /"),
        ("/data/", "normalized"),
        ("/data//x", "normalized"),
        ("/data/./x", "normalized"),
        ("/data/../x", "normalized"),
        ("/da\\ta", "normalized"),
        ("/da\0ta", "NUL"),
        ("/usr/local/lib/perl5", "runtime"),
        ("/usr/local/lib/perl5/5.42", "runtime"),
        ("/usr/local", "runtime"),
        ("/usr", "runtime"),
        ("/dev", "runtime"),
        ("/dev/x", "runtime"),
    ],
)
def test_a_bad_guest_path_is_refused(guest, words, tmp_path, no_work, standin):
    with pytest.raises(ValueError, match=words):
        run(["hello"], grants=[Grant(tmp_path, guest)], artifacts=standin)


def test_a_grant_is_typed(tmp_path, no_work, standin):
    with pytest.raises(TypeError, match="writable"):
        Grant(tmp_path, "/d", writable="no")
    with pytest.raises(TypeError, match="host"):
        Grant(b"/tmp", "/d")
    with pytest.raises(TypeError, match="guest"):
        Grant(tmp_path, Path("/d"))


def test_a_host_path_must_be_an_existing_folder(tmp_path, no_work, standin):
    with pytest.raises(ValueError, match="does not exist"):
        run(["hello"], grants=[Grant(tmp_path / "gone", "/d")], artifacts=standin)
    (tmp_path / "file").write_bytes(b"x")
    with pytest.raises(ValueError, match="not a folder"):
        run(["hello"], grants=[Grant(tmp_path / "file", "/d")], artifacts=standin)


def test_two_grants_may_not_share_a_guest_path(tmp_path, no_work, standin):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    grants = [Grant(tmp_path / "a", "/d"), Grant(tmp_path / "b", "/d", writable=True)]
    with pytest.raises(ValueError, match=r"grants\[1\].*/d"):
        run(["hello"], grants=grants, artifacts=standin)


def test_a_read_only_grant_inside_a_writable_one_is_refused(tmp_path, no_work, standin):
    (tmp_path / "in").mkdir()
    grants = [Grant(tmp_path, "/w", writable=True), Grant(tmp_path / "in", "/r")]
    with pytest.raises(ValueError, match="inside the writable"):
        run(["hello"], grants=grants, artifacts=standin)
    # A writable grant inside a read-only one is allowed: it only adds a way to write there.


def test_no_grant_reaches_the_module_cache(tmp_path, no_work, standin):
    cache = tmp_path / "cache"
    cache.mkdir(mode=0o700)
    for grant in (Grant(cache, "/c"), Grant(cache, "/c", writable=True)):
        with pytest.raises(ValueError, match="module cache"):
            run(["hello"], grants=[grant], cache_dir=cache, artifacts=standin)
    with pytest.raises(ValueError, match="module cache"):
        run(
            ["hello"],
            grants=[Grant(tmp_path, "/c", writable=True)],
            cache_dir=cache,
            artifacts=standin,
        )


def test_the_cache_the_variable_names_is_kept_out_even_when_unused(
    tmp_path, no_work, standin, private_cache
):
    private_cache.mkdir(mode=0o700, exist_ok=True)
    with pytest.raises(ValueError, match="module cache"):
        run(["hello"], grants=[Grant(private_cache, "/c")], cache_dir=False, artifacts=standin)


def test_a_missing_cache_is_judged_by_its_existing_ancestor(tmp_path, no_work, standin):
    with pytest.raises(ValueError, match="module cache"):
        run(
            ["hello"],
            grants=[Grant(tmp_path, "/c", writable=True)],
            cache_dir=tmp_path / "not" / "yet",
            artifacts=standin,
        )


def test_no_writable_grant_reaches_the_runtime(tmp_path, standin_dir, no_work, standin):
    import wasmtime

    for folder in (
        standin_dir,
        standin_dir / "lib",
        standin_dir / "lib" / "perl5" / "5.42",
        standin_dir / "dev",
        standin_dir.parent,
        Path(wasi_perl.__file__).parent,
        Path(wasmtime.__file__).parent,
    ):
        with pytest.raises(ValueError, match="runtime"):
            run(["hello"], grants=[Grant(folder, "/w", writable=True)], artifacts=standin)
    run_ro = Grant(standin_dir / "lib", "/r")  # read-only is allowed
    assert run_ro.writable is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX links")
def test_a_link_in_a_writable_folder_is_refused(tmp_path, no_work, standin):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    with pytest.raises(ValueError, match="link"):
        run(["hello"], grants=[Grant(tmp_path / "link", "/d")], artifacts=standin)


@pytest.mark.skipif(os.name == "nt", reason="POSIX links")
def test_a_link_in_a_folder_this_user_cannot_write_is_followed(tmp_path, standin):
    if os.geteuid() == 0:
        pytest.skip("root can write every folder")
    # A folder this user cannot write, with a link in it, made by the check itself.
    locked = tmp_path / "locked"
    locked.mkdir()
    (tmp_path / "real").mkdir()
    (locked / "link").symlink_to(tmp_path / "real")
    locked.chmod(0o555)
    try:
        done = run(["hello"], grants=[Grant(locked / "link", "/d")], artifacts=standin)
        assert done.exit_code == 0
    finally:
        locked.chmod(0o755)


def test_cwd_must_be_inside_a_grant(tmp_path, no_work, standin):
    (tmp_path / "sub").mkdir()
    g = [Grant(tmp_path, "/data")]
    for bad, words in (
        ("/other", "inside a grant"),
        ("data", "absolute"),
        ("/data/../x", "normalized"),
        ("/data/missing", "does not exist"),
        ("/usr/local/lib/perl5", "inside a grant"),
    ):
        with pytest.raises(ValueError, match=words):
            run(["hello"], grants=g, cwd=bad, artifacts=standin)


def test_defaults_are_checked_by_runs_rules():
    with pytest.raises(ValueError, match="deadline_s"):
        Defaults(memory_bytes=1, deadline_s=-1, output_bytes=1)
    with pytest.raises(TypeError, match="memory_bytes"):
        Defaults(memory_bytes="1", deadline_s=1, output_bytes=1)
    with pytest.raises(TypeError):
        wasi_perl.set_defaults({"deadline_s": 1})


def test_the_defaults_are_wasi_pythons():
    d = wasi_perl.defaults()
    assert (d.memory_bytes, d.deadline_s, d.output_bytes) == (1 << 30, 60.0, 16 << 20)


@pytest.mark.skipif(os.name == "nt", reason="a backslash is a separator on Windows")
def test_a_backslash_in_a_posix_host_path_is_a_name(tmp_path, standin):
    # A guest with a writable grant can make a folder named `..\secret`; a later grant of it
    # must open that folder, never its sibling `secret`.
    rw = tmp_path / "rw"
    rw.mkdir()
    (rw / "..\\secret").mkdir()
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "s.txt").write_bytes(b"SECRET")
    done = run(["cat", "/in/s.txt"], grants=[Grant(rw / "..\\secret", "/in")], artifacts=standin)
    assert done.exit_code == 1 and b"SECRET" not in done.stdout
    done = run(
        ["write", "/in/planted"],
        grants=[Grant(rw / "..\\secret", "/in", writable=True)],
        artifacts=standin,
    )
    assert done.exit_code == 0
    assert (rw / "..\\secret" / "planted").is_file()
    assert sorted(p.name for p in (tmp_path / "secret").iterdir()) == ["s.txt"]


@pytest.mark.skipif(os.name == "nt", reason="a backslash is a separator on Windows")
def test_a_backslash_is_a_name_in_every_part_of_a_walk():
    from wasi_perl import _grants

    assert _grants._parts("a\\..\\b/c") == ["a\\..\\b", "c"]
    assert _grants._parts("/x//y/") == ["x", "y"]


def test_no_writable_grant_reaches_the_host_pythons_own_code(no_work, standin):
    import site
    import sysconfig

    paths = sysconfig.get_paths()
    folders = [Path(paths[k]) for k in ("stdlib", "platstdlib", "purelib", "platlib", "scripts")]
    if site.ENABLE_USER_SITE and Path(site.getusersitepackages()).is_dir():
        folders.append(Path(site.getusersitepackages()))
    for folder in folders:
        if folder.is_dir():
            with pytest.raises(ValueError, match="runtime"):
                run(["hello"], grants=[Grant(folder, "/w", writable=True)], artifacts=standin)
