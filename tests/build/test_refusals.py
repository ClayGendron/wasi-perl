"""Checks that processes and signals are refused clearly, and that what does not need them works.

WASI preview1 has no processes and no signals. Every operation that would need one dies with
"<what> is not available in the wasi-perl sandbox", which eval catches; uncaught, it ends the
program with status 255 and the message on stderr, and nothing traps.

wasmtime refuses an exit status of 126 or more and reports an error in its place, so an
uncaught refusal is read from the module's wasi_perl_exit_status export.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from _build_helpers import NATIVE_PERL, assert_exit_refused, default_grants

SUFFIX = b" is not available in the wasi-perl sandbox at -e line 1.\n"

# (what the message names, the Perl code that asks for it)
REFUSED = [
    ("fork", "fork"),
    ("fork", "my $pid = fork; print defined $pid ? 1 : 0"),
    ("exec", 'exec "true"'),
    ("exec", 'exec "true", "x"'),
    ("exec", 'exec { "true" } "true"'),
    ("system", 'system "true"'),
    ("system", 'system "true", "x"'),
    ("system", 'system { "true" } "true"'),
    ("backticks", "my $x = `true`"),
    ("backticks", "my $x = qx(true)"),
    ("backticks", 'my $x = readpipe("true")'),
    ("backticks", "my @x = `true`"),
    ("pipe open", 'open(my $fh, "-|", "ls")'),
    ("pipe open", 'open(my $fh, "|-", "cat")'),
    ("pipe open", 'open(F, "ls |")'),
    ("pipe open", 'open(F, "| cat")'),
    ("pipe open", 'open(my $fh, "-|", "ls", "-l")'),
    ("pipe open", 'open(my $fh, "-|")'),
    ("pipe open", 'open(my $fh, "|-")'),
    ("pipe", "pipe(my $r, my $w)"),
    ("wait", "wait"),
    ("waitpid", "waitpid(1, 0)"),
    ("waitpid", "waitpid(-1, 1)"),
    ("kill", "kill 0, $$"),
    ("kill", 'kill "TERM", 1'),
    ("alarm", "alarm 1"),
    ("alarm", "alarm 0"),
    ("signal handler for SIGINT", "$SIG{INT} = sub {}"),
    ("signal handler for SIGINT", "$SIG{INT} = 'IGNORE'"),
    ("signal handler for SIGTERM", "$SIG{TERM} = 'DEFAULT'"),
    ("signal handler for SIGHUP", '$SIG{"HUP"} = "main::h"'),
    ("signal handler for SIGCHLD", "$SIG{CHLD} = sub {}"),
    ("signal handler for SIGALRM", "local $SIG{ALRM} = sub { die }"),
    ("signal handler for SIGALRM", "local $SIG{ALRM}"),
    ("signal handler for SIGINT", "$SIG{INT} = undef"),
    ("signal handler for SIGINT", '@SIG{"INT", "TERM"} = ("IGNORE") x 2'),
    ("signal handler for SIGINT", '%SIG = (INT => "IGNORE")'),
    ("signal handler for SIGINT", "local %SIG; $SIG{INT} = sub {}"),
    (
        "backticks",
        "BEGIN { *CORE::GLOBAL::readpipe = sub { CORE::readpipe($_[0]) } } my $x = `true`",
    ),
    ("dump", "CORE::dump"),
    ("dump", 'CORE::dump; print "not reached\\n"'),
]


def _ids(cases):
    return [code for _, code in cases]


@pytest.mark.parametrize(("what", "code"), REFUSED, ids=_ids(REFUSED))
def test_refused_uncaught(guest, what, code):
    r = guest("-e", code)
    assert (r.stdout, r.stderr) == (b"", what.encode() + SUFFIX)
    assert_exit_refused(r, 255)


@pytest.mark.parametrize(("what", "code"), REFUSED, ids=_ids(REFUSED))
def test_refused_in_eval(guest, what, code):
    program = f'eval {{ {code}; 1 }} or print "caught: $@"; print "after\\n"'
    r = guest("-e", program)
    assert (r.code, r.stderr) == (0, b"")
    assert r.stdout == b"caught: " + what.encode() + SUFFIX + b"after\n"


def test_system_true_or_die(guest):
    # The refusal, not "died": system never returns to be compared with 0.
    r = guest("-e", 'system("true") == 0 or die')
    assert (r.stdout, r.stderr) == (b"", b"system" + SUFFIX)
    assert_exit_refused(r, 255)


def test_refusal_message_is_dollar_at(guest):
    r = guest(
        "-e",
        'eval { fork }; print ref(\\$@), "|", $@ =~ /^fork is not available in the wasi-perl '
        'sandbox at -e line 1\\.$/ ? "match" : "no", "\\n"',
    )
    assert (r.code, r.stdout, r.stderr) == (0, b"SCALAR|match\n", b"")


def test_refusal_line_numbers(guest, tmp_path: Path):
    r = guest("-e", 'print "one\\n";\n\nsystem "true";\n')
    assert r.stdout == b"one\n"
    assert (
        r.stderr == b"system is not available in the wasi-perl sandbox at -e line 3.\n"
    )


# What is a hook, not a signal, works as natively.
HOOKS = [
    'local $SIG{__WARN__} = sub { print "warned: $_[0]" }; warn "w\\n"; print "after\\n"',
    (
        '$SIG{__WARN__} = sub { print "warned: $_[0]" }; warn "w\\n"; delete $SIG{__WARN__}; '
        'warn "plain\\n"'
    ),
    '$SIG{__DIE__} = sub { print "dying: $_[0]" }; eval { die "d\\n" }; print "after: $@"',
    'local $SIG{__DIE__} = sub { die "wrapped: $_[0]" }; eval { die "d\\n" }; print $@',
    '$SIG{__DIE__} = sub { print "hook: $_[0]" }; die "uncaught\\n"',
    '$SIG{__WARN__} = "IGNORE"; warn "x\\n"; print "ok\\n"',
    'print defined $SIG{INT} ? "def" : "undef", " ", exists $SIG{ALRM} ? 1 : 0, "\\n"',
    'my @k = grep { /^(INT|TERM|ALRM|HUP)$/ } keys %SIG; print scalar(@k), "\\n"',
    "eval { $SIG{__FOO__} = sub {} }; print $@",
    '$SIG{NOSUCH} = sub {}; print "ok\\n"',
    'use warnings; $SIG{NOSUCH} = sub {}; print "ok\\n"',
    'delete $SIG{INT}; print "ok\\n"',
    'delete @SIG{qw(INT TERM ALRM)}; print defined $SIG{INT} ? "def" : "undef", "\\n"',
    'delete local $SIG{HUP}; print "ok\\n"',
]


@pytest.mark.parametrize("code", HOOKS, ids=range(len(HOOKS)))
def test_hooks_match_native(guest, native, code):
    g, n = guest("-e", code), native("-e", code)
    if n.code == 255:
        # An uncaught die: wasmtime refuses 255, the export keeps it.
        assert (g.status, g.stdout, g.stderr) == (255, n.stdout, n.stderr)
    else:
        assert (g.code, g.stdout, g.stderr) == (n.code, n.stdout, n.stderr)


def test_hooks_survive_a_refusal(guest):
    r = guest(
        "-e",
        '$SIG{__WARN__} = sub { print "warned: $_[0]" }; '
        'eval { $SIG{INT} = "IGNORE" }; print "caught: $@"; warn "still\\n"; '
        '$SIG{__DIE__} = sub { print "hook: $_[0]" }; eval { fork }; print "done\\n"',
    )
    assert r.code == 0 and r.stderr == b""
    assert r.stdout == (
        b"caught: signal handler for SIGINT" + SUFFIX + b"warned: still\n"
        b"hook: fork" + SUFFIX + b"done\n"
    )


def test_local_sig_restores_quietly(guest):
    # The refused store is undone when the scope ends; that restore is not refused again.
    r = guest(
        "-e",
        'for (1 .. 3) { eval { local $SIG{ALRM} = sub {}; 1 } or print "caught\\n" } '
        'print defined $SIG{ALRM} ? "def" : "undef", "\\n"',
    )
    assert (r.code, r.stdout, r.stderr) == (0, b"caught\ncaught\ncaught\nundef\n", b"")


# What still works.
WORKS = [
    ('print $$ > 0 ? "ok" : "bad", "\\n"', b"ok\n"),
    ('print getppid() >= 0 ? "ok" : "bad", "\\n"', b"ok\n"),
    ('print sleep(0), "\\n"', b"0\n"),
    (
        'my @t = times; print scalar(@t), " ", (grep { $_ >= 0 } @t) == 4 ? "ok" : "bad", "\\n"',
        b"4 ok\n",
    ),
    ('print scalar(select(undef, undef, undef, 0.01)), "\\n"', b"0\n"),
    ('END { print "end\\n" } print "main\\n"; exit 0', b"main\nend\n"),
]


@pytest.mark.parametrize(("code", "out"), WORKS, ids=range(len(WORKS)))
def test_still_works(guest, native, code, out):
    g, n = guest("-e", code), native("-e", code)
    assert (n.code, n.stdout, n.stderr) == (0, out, b"")
    assert (g.code, g.stdout, g.stderr) == (0, out, b"")


def test_exit_and_end(guest, native):
    code = 'END { print "end $?\\n" } exit 3'
    g, n = guest("-e", code), native("-e", code)
    assert (
        (g.code, g.stdout, g.stderr)
        == (n.code, n.stdout, n.stderr)
        == (3, b"end 3\n", b"")
    )


def test_still_imports_only_wasi(perl_wasm: Path):
    wasmtime = pytest.importorskip("wasmtime")
    config = wasmtime.Config()
    config.wasm_exceptions = True
    module = wasmtime.Module.from_file(wasmtime.Engine(config), str(perl_wasm))
    modules = {i.module for i in module.imports}
    assert modules == {"wasi_snapshot_preview1"}, modules


# The exit status a die takes from $! is the native (Linux) number, though $! itself is WASI's.
ERRNO_EXITS = [
    ('open my $f, "<", "nope" or die "x: $!\\n"', 2),
    ('open my $f, "<", "/dev/null/x" or die "x: $!\\n"', 20),
    ('opendir my $d, "/dev/null" or die "x: $!\\n"', 20),
]


@pytest.mark.parametrize(("code", "status"), ERRNO_EXITS, ids=range(len(ERRNO_EXITS)))
def test_die_status_from_errno_matches_native(guest, native, code, status):
    g, n = guest("-e", code), native("-e", code)
    assert (n.code, n.stdout) == (status, b"")
    assert (g.code, g.stdout, g.stderr) == (n.code, n.stdout, n.stderr)


def test_use_missing_module_status_matches_native(guest, native):
    g, n = guest("-e", "use Nonexistent"), native("-e", "use Nonexistent")
    assert n.code == 2
    assert g.code == 2
    assert g.stderr.startswith(b"Can't locate Nonexistent.pm in @INC")


def test_missing_script_status_matches_native(guest, native):
    g, n = guest("/no/such/script.pl"), native("/no/such/script.pl")
    assert n.code == 2
    assert (g.code, g.stderr) == (n.code, n.stderr)


def test_errno_number_stays_wasi(guest, native):
    code = 'open my $f, "<", "nope"; print $!+0, " $!\\n"'
    g, n = guest("-e", code), native("-e", code)
    assert n.stdout == b"2 No such file or directory\n"
    # Only the exit status is translated; $! keeps WASI's number and its usual text.
    assert (g.code, g.stdout, g.stderr) == (0, b"44 No such file or directory\n", b"")


def test_die_without_errno_is_255(guest, native):
    code = '$! = 0; die "plain\\n"'
    g, n = guest("-e", code), native("-e", code)
    assert n.code == 255
    assert (g.status, g.stderr) == (255, n.stderr)


def test_die_status_from_child_status(guest, native):
    # With $! zero, die takes the status from $? >> 8, as natively.
    code = '$! = 0; $? = 3 << 8; die "x\\n"'
    g, n = guest("-e", code), native("-e", code)
    assert n.code == 3
    assert (g.code, g.stderr) == (n.code, n.stderr)


# Reopening a standard handle onto a file: Perl dup2s the new descriptor onto 0, 1 or 2.
REOPEN = [
    'open STDOUT, ">", "f" or die "no: $!\\n"; print "x"; close STDOUT or die "close: $!\\n"',
    'open STDOUT, ">", "f" or die; print "one\\n"; open STDOUT, ">>", "f" or die; print "two\\n"',
    'open STDERR, ">", "f" or die; warn "w\\n"; print STDOUT "out\\n"',
    'open STDERR, ">", "f" or die; die "d\\n"',
    (
        'open STDOUT, ">", "f" or die; open my $g, ">", "g" or die; print $g "g\\n"; close $g; '
        'print "f\\n"; close STDOUT; open my $r, "<", "g" or die; print STDERR <$r>'
    ),
    (
        'open my $w, ">", "in" or die; print $w "a\\nb\\n"; close $w; open STDIN, "<", "in" or die; '
        "print scalar(<STDIN>)"
    ),
]


def _reopen(guest, code: str, tmp: Path):
    gdir, ndir = tmp / "guest", tmp / "native"
    gdir.mkdir()
    ndir.mkdir()
    g = guest(
        "-e",
        code,
        env={"PWD": "/work"},
        grants=[*default_grants(), (gdir, "/work", True)],
    )
    p = subprocess.run(
        [str(NATIVE_PERL), "-e", code],
        cwd=ndir,
        env={},
        capture_output=True,
        check=False,
    )

    def files(d: Path):
        return {f.name: f.read_bytes() for f in sorted(d.iterdir())}

    return g, p, files(gdir), files(ndir)


@pytest.mark.parametrize("code", REOPEN, ids=range(len(REOPEN)))
def test_reopen_standard_handle_matches_native(
    guest, native_perl, tmp_path: Path, code
):
    g, n, gfiles, nfiles = _reopen(guest, code, tmp_path)
    assert gfiles == nfiles and gfiles
    assert (g.stdout, g.stderr) == (n.stdout, n.stderr)
    if n.returncode == 255:
        assert_exit_refused(g, 255)
    else:
        assert g.code == n.returncode


# The fdopen forms share a descriptor with another handle, so it cannot be moved: the open fails
# with "Not supported" and both handles keep writing where they did. Natively each succeeds.
FDOPEN = [
    (
        'open(STDERR, ">&=STDOUT") or print "fail: $!\\n"; print STDOUT "out\\n"; '
        'print STDERR "err\\n"'
    ),
    (
        'open(STDERR, ">&=", \\*STDOUT) or print "fail: $!\\n"; print STDOUT "out\\n"; '
        'print STDERR "err\\n"'
    ),
    (
        'open my $h, ">", "h" or die; open(STDOUT, ">&=", $h) or print STDERR "fail: $!\\n"; '
        'print $h "via h\\n"; print "via stdout\\n"; close $h or die "close: $!\\n"'
    ),
    (
        'open my $h, ">", "h" or die; open(STDOUT, ">&=" . fileno($h)) or print STDERR "fail: $!\\n"; '
        'print $h "via h\\n"; print "via stdout\\n"; close $h or die "close: $!\\n"'
    ),
]


@pytest.mark.parametrize("code", FDOPEN, ids=range(len(FDOPEN)))
def test_fdopen_of_a_shared_descriptor_fails_cleanly(
    guest, native_perl, tmp_path: Path, code
):
    g, n, gfiles, _ = _reopen(guest, code, tmp_path)
    assert n.returncode == 0 and b"fail" not in n.stdout + n.stderr
    assert g.code == 0
    if "$h" in code:
        assert gfiles == {"h": b"via h\n"}
        assert (g.stdout, g.stderr) == (b"via stdout\n", b"fail: Not supported\n")
    else:
        assert (g.stdout, g.stderr) == (b"fail: Not supported\nout\n", b"err\n")


def test_dup_of_a_handle_fails_clearly(guest, native, tmp_path: Path):
    # Duplicating a descriptor needs dup(), which WASI preview1 does not have: the open fails
    # and says so in $!, natively it succeeds.
    code = 'open(STDERR, ">&", \\*STDOUT) or die "dup: $!\\n"; print STDERR "x\\n"'
    n = native("-e", code)
    assert (n.code, n.stdout, n.stderr) == (0, b"x\n", b"")
    g = guest("-e", code)
    assert (g.stdout, g.stderr) == (b"", b"dup: Not supported\n")
    assert g.code == 95  # ENOTSUP, in Linux's numbering
