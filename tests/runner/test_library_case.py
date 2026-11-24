"""A library name in another case is the host's to answer, on every system.

perl.wasm holds an index of its library, and answers "no such file" itself only for a name no
host could find. A name the library holds in another case, such as 5.42/Strict.pm for
strict.pm, is found by a host whose file names ignore case (macOS and Windows, as usually set
up) and not by one whose names keep it (Linux). So the guest must see what this host's own open
of the same file sees, whatever this system is. CI runs these on all four systems.
"""

from __future__ import annotations

import pytest

from wasi_perl import run

pytestmark = pytest.mark.slow

GUEST = "/usr/local/lib/perl5/5.42"


def _host_finds(perl, name: str) -> bool:
    """Whether a plain open of the library's file, by this name, succeeds on this host."""
    try:
        with open(perl.lib / "5.42" / name, "rb"):
            return True
    except OSError:
        return False


def test_a_name_in_another_case_is_found_as_the_host_finds_it(perl):
    assert _host_finds(perl, "strict.pm")
    found = _host_finds(perl, "Strict.pm")
    code = f'print -e "{GUEST}/Strict.pm" ? 1 : 0, open(my $f, "<", "{GUEST}/Strict.pm") ? 1 : 0'
    done = run(["-e", code], artifacts=perl)
    assert (done.exit_code, done.stderr) == (0, b""), done
    assert done.stdout == (b"11" if found else b"00"), (found, done)


def test_require_in_another_case_loads_as_the_host_finds_it(perl):
    found = _host_finds(perl, "Strict.pm")
    done = run(["-e", 'require Strict; print "loaded\\n"'], artifacts=perl)
    if found:
        assert (done.exit_code, done.stdout, done.stderr) == (0, b"loaded\n", b""), done
    else:
        assert done.exit_code == 2, done
        assert done.stderr.startswith(b"Can't locate Strict.pm in @INC"), done
