"""Grants through the stand-in guest: read-only refuses writes, writable takes them, nothing is
writable without a grant, the runtime's folders are read-only, and `cwd` works."""

from __future__ import annotations

from wasi_perl import Grant, run


def test_a_read_only_grant_reads_and_refuses_a_write(tmp_path, standin):
    (tmp_path / "f").write_text("data\n")
    g = [Grant(tmp_path, "/d")]
    assert run(["cat", "/d/f"], grants=g, artifacts=standin).stdout == b"data\n"
    done = run(["write", "/d/f"], grants=g, artifacts=standin)
    assert done.stdout == b"open /d/f: Operation not permitted\n"
    done = run(["write", "/d/new"], grants=g, artifacts=standin)
    assert done.exit_code == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f"]
    assert (tmp_path / "f").read_text() == "data\n"


def test_a_writable_grant_takes_a_write(tmp_path, standin):
    done = run(
        ["write", "/w/out"], grants=[Grant(tmp_path, "/w", writable=True)], artifacts=standin
    )
    assert done.stdout == b"ok\n"
    assert (tmp_path / "out").read_text() == "written\n"


def test_nothing_is_writable_without_a_grant(standin):
    for path in ("/x", "x", "/tmp/x", "/usr/local/lib/perl5/x", "/dev/x", "/dev/null"):
        done = run(["write", path], artifacts=standin)
        assert done.exit_code == 1, path


def test_the_runtime_folders_are_read_only(standin, standin_dir):
    done = run(["cat", "/usr/local/lib/perl5/5.42/strict.pm"], artifacts=standin)
    assert done.stdout == b"package strict; 1;\n"
    assert run(["cat", "/dev/null"], artifacts=standin).stdout == b""
    done = run(["write", "/usr/local/lib/perl5/5.42/strict.pm"], artifacts=standin)
    assert done.stdout.endswith(b"Operation not permitted\n")
    assert run(["write", "/dev/null"], artifacts=standin).exit_code == 1
    assert (standin_dir / "dev" / "null").read_bytes() == b""


def test_a_folder_outside_every_grant_is_not_found(tmp_path, standin):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "secret").write_text("s")
    done = run(["cat", "/a/../b/secret"], grants=[Grant(tmp_path / "a", "/a")], artifacts=standin)
    assert done.exit_code == 1
    assert done.stdout.startswith(b"open /a/../b/secret: ")


def test_cwd_makes_relative_paths_land_in_its_grant(tmp_path, standin):
    (tmp_path / "sub").mkdir()
    g = [Grant(tmp_path, "/data", writable=True)]
    assert run(["cwd"], grants=g, cwd="/data/sub", artifacts=standin).stdout == b"/data/sub\n"
    assert run(["write", "rel"], grants=g, cwd="/data/sub", artifacts=standin).exit_code == 0
    assert (tmp_path / "sub" / "rel").read_text() == "written\n"
    env = run(["env"], grants=g, cwd="/data", env={"A": "1"}, artifacts=standin).stdout
    assert sorted(env.decode().splitlines()) == ["A=1", "PWD=/data"]


def test_without_cwd_a_relative_path_fails(tmp_path, standin):
    g = [Grant(tmp_path, "/data", writable=True)]
    assert run(["write", "rel"], grants=g, artifacts=standin).exit_code == 1
    assert list(tmp_path.iterdir()) == []


def test_grants_nest(tmp_path, standin):
    (tmp_path / "outer").mkdir()
    (tmp_path / "inner").mkdir()
    (tmp_path / "inner" / "f").write_text("inner\n")
    g = [Grant(tmp_path / "outer", "/o", writable=True), Grant(tmp_path / "inner", "/o/i")]
    assert run(["cat", "/o/i/f"], grants=g, artifacts=standin).stdout == b"inner\n"
    assert run(["write", "/o/i/g"], grants=g, artifacts=standin).exit_code == 1
    assert run(["write", "/o/g"], grants=g, artifacts=standin).exit_code == 0
