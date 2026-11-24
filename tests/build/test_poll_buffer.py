"""Checks that the host never writes past the caller's event buffer in poll_oneoff.

The descriptor table answers some subscriptions itself (a regular file, a closed number) and
sends the rest to the host, with a zero clock added so that the host does not wait. The host may
answer every subscription it is sent. poll_buffer_probe.c links the table against a fake host
that does exactly that, and puts a canary after the caller's two events; the table is compiled
from the recipe with only its call to the real host renamed. The shipped module is not built
this way.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from _build_helpers import ROOT

HERE = Path(__file__).resolve().parent


@pytest.fixture(scope="session")
def probe(tmp_path_factory, wasmtime_cli) -> Path:
    cc = ROOT / "recipes" / "wasi-cc"
    if not (ROOT / "build" / "stubs" / "libwasi-perl-fd-table.a").exists():
        pytest.skip("build/stubs is missing; run scripts/build.sh")
    out = tmp_path_factory.mktemp("poll-probe")
    table = out / "table.o"
    flags = ["-O2", "-Wall", "-Wextra", "-Werror"]
    rename = "-D__real___wasi_poll_oneoff=fake_host_poll"
    src = ROOT / "recipes" / "wasi-fd-table.c"
    subprocess.run([str(cc), *flags, rename, "-c", str(src), "-o", str(table)], check=True)
    wasm = out / "probe.wasm"
    subprocess.run(
        [str(cc), *flags, str(HERE / "poll_buffer_probe.c"), str(table), "-o", str(wasm)],
        check=True,
    )
    return wasm


def test_the_host_cannot_write_past_the_callers_events(probe, wasmtime_cli, tmp_path):
    (tmp_path / "file").write_text("x\n")
    p = subprocess.run(
        [str(wasmtime_cli), "run", "-W", "exceptions=y", "--dir", f"{tmp_path}::/g", str(probe)],
        capture_output=True,
        timeout=20,
        check=False,
    )
    # The file's event is the table's own (10); STDOUT's is the host's (11). The zero clock the
    # table added is dropped, and nothing lands after the two events.
    assert (p.returncode, p.stdout) == (0, b"error 0, 2 events: 10 11\ncanary whole\n"), p
