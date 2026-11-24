# wasi-perl

**A secure Perl 5 sandbox for agent-generated code.**

```
pip install wasi-perl
```

wasi-perl is Perl 5.42 compiled to WebAssembly (`wasm32-wasip1`) as one static binary, with the core XS modules linked in and the pure-Perl core library beside it. It is built to run the Perl that AI agents write, above all `perl -ne`, `-pi -e`, `-lane` and `-0777` one-liners over files, kept apart from the host process. By default the sandbox sees no host files, cannot write anything and has no network.

Run it from Python, from the command line, or load the same `perl.wasm` into any WebAssembly host that supports exception handling.

Every release is tested on Linux (x86-64 and arm64), macOS (arm64) and Windows (x86-64): Perl's own `t/` suite, a set of agent-style one-liners compared byte for byte with native perl 5.42.3 (two differ on Windows, as its filesystem does), and sandbox escape tests. [Modules](https://github.com/ClayGendron/wasi-perl/blob/main/docs/modules.md) lists what is linked in and what is left out. [The sandbox](https://github.com/ClayGendron/wasi-perl/blob/main/docs/sandbox.md) lists what a guest cannot do: `fork`, `exec`, `system`, backticks, pipe opens and signal handlers die with "… is not available in the wasi-perl sandbox".

# Installation and quick start

```
$ pip install wasi-perl
$ wasi-perl run -- -e 'print "hello, WebAssembly\n"'
{"exit_code": 0, "fuel_used": null, "stderr": "", "stdout": "hello, WebAssembly\n", "trapped": null, "truncated": []}
```

Requires Python 3.11 or later and installs `wasmtime` (`>=51,<52`). The first call compiles `perl.wasm` and keeps the compiled module in a cache folder of your own (`~/.cache/wasi_perl` on Linux, `~/Library/Caches/wasi_perl` on macOS, `%LOCALAPPDATA%\wasi_perl\cache` on Windows, or `$WASI_PERL_CACHE`). Never point `cache_dir` or `$WASI_PERL_CACHE` at a folder you grant to a guest, in any process: a compiled module is native code. Within one process the runner refuses to use a cache at, inside or above any folder it has granted writable.

Installed by uv, whose default on Linux and Windows links each file from its cache by hard link, wasmtime's native library has a second name in uv's cache. A writable grant that holds such a second name is refused, and a writable grant too large to search (100,000 entries) is refused while one exists. Install with `UV_LINK_MODE=copy` to have no second names.

# From Python

```python
from wasi_perl import run

done = run(["-e", 'print join(",", map { $_ * 2 } 1..5), "\n"'])
print(done.exit_code, done.stdout.decode())
```

```
0 2,4,6,8,10
```

`run` returns a `Completed` with `exit_code`, `stdout`, `stderr` (bytes), `trapped` (`None`, `"deadline"`, `"out of fuel"`, or wasmtime's words for another trap), `fuel_used` and `truncated`. An uncaught `die` is exit code 255, as in native Perl. Nothing the program does makes `run` raise.

# Grants

The program inside the sandbox is the guest; your program is the host. The guest sees a host folder only through a `Grant`, read-only unless it says `writable=True`.

```python
from pathlib import Path

from wasi_perl import Grant, run

Path("shared").mkdir(exist_ok=True)
Path("shared/hello.txt").write_text("hello from shared\n")
Path("private").mkdir(exist_ok=True)
Path("private/secret.txt").write_text("secret\n")

code = r"""
for my $path ("/shared/hello.txt", "/private/secret.txt", "/shared/../private/secret.txt") {
    if (open(my $f, "<", $path)) { print "$path -> ", scalar <$f> }
    else { print "$path -> $!\n" }
}
open(my $out, ">", "/shared/new.txt") or print "write to /shared -> $!\n";
"""

done = run(["-e", code], grants=[Grant("shared", "/shared")])
print(done.stdout.decode())
```

```
/shared/hello.txt -> hello from shared
/private/secret.txt -> No such file or directory
/shared/../private/secret.txt -> Operation not permitted
write to /shared -> Operation not permitted
```

A one-liner edits files in place only in a writable grant, and `cwd` sets the working folder:

```python
run(
    ["-pi", "-e", "s/colour/color/g", "notes.txt"],
    grants=[Grant("docs", "/docs", writable=True)],
    cwd="/docs",
)
```

What the guest always sees, read-only: the Perl library at `/usr/local/lib/perl5` and an empty `/dev/null`. There is no `/`, no `/tmp` and no `HOME` unless you grant them. The guest's environment is exactly `env=` (plus `PWD` from `cwd=`); nothing of your own environment reaches it.

A grant is refused before the guest runs when its guest path is not absolute and normalized, is `/`, or is at, under or above `/usr/local/lib/perl5` or `/dev`; when two grants share a guest path; when a read-only grant lies inside a writable one; when any grant reaches a module cache; when a writable grant reaches the artifact set, this package or wasmtime; and when a symbolic link on the way lies in a folder you can write.

# Limits

Every call runs under limits; a call that names none has the defaults: 1 GiB of memory, 60 s, and 16 MiB of output per stream. `defaults()` and `set_defaults()` read and replace them.

```python
from wasi_perl import run

spin = ["-e", "1 while 1"]
print(run(spin, deadline_s=1).trapped)
print(run(spin, fuel=10_000_000).trapped)
print(run(["-e", 'print "x" x 10_000_000'], output_bytes=1_000_000).truncated)
done = run(["-e", 'my $n = 512 << 20; my $x = "x" x $n'], memory_bytes=128 << 20)
print(done.exit_code, done.stderr)
```

```
deadline
out of fuel
('stdout',)
1 b'Out of memory in perl:util:safesysrealloc\n'
```

# From any language

`wasi-perl run [OPTIONS] [--] ARG...` (also `python -m wasi_perl run`) prints one JSON line with sorted keys `exit_code`, `fuel_used`, `stderr`, `stdout`, `trapped` and `truncated`. Output that is not valid UTF-8 comes as `stdout_base64` or `stderr_base64` instead. Each option is one of `run`'s arguments: `--grant GUEST=HOST`, `--grant-writable GUEST=HOST`, `--cwd GUEST`, `--env KEY=VALUE` (or `--env KEY` to pass your own value), `--stdin PATH|-`, `--deadline-s`, `--memory-bytes`, `--output-bytes`, `--fuel` (each takes `none`), `--cache-dir PATH` and `--no-cache`.

```
$ wasi-perl run --grant /logs=./logs -- -ne 'print if /ERROR/' /logs/app.log
```

The command exits 0 when the call returned (the JSON says how the program ended), 1 when `run` refused it (one JSON line with `error` and `kind`), 2 for a usage error and 130 for Ctrl-C, which stops a computing guest at once. From Python, Ctrl-C during `run` on the main thread stops the guest and raises `KeyboardInterrupt`.

# Not protected

- Symbolic links a guest leaves in a writable grant. The guest cannot follow one out of the grant, but your own program, reading the folder afterwards, can.
- A host folder swapped by another writer between the runner's check and wasmtime's own open of it. Within one process the runner keeps a registry of the grants of the calls running now, and refuses a grant at, inside or above another live call's writable grant (and a writable grant over another live call's grant), so no guest of this process can do it; another process, or another guest of another process, with write access to a folder on the way still could.
- Host descriptors. The runner raises the process's soft open-file limit toward its hard limit once, then counts each running call as 256 descriptors, the most the guest's own descriptor table holds. A call alone is never refused; a call beside others past half the soft limit (4,096 on Windows) raises `TooManyCalls` before it starts.
- On Windows, the module cache is trusted by its kind (a folder, a file with one name, no link) and the folder's access list, not by its owner: keep `cache_dir` in a folder only you can write, as `%LOCALAPPDATA%` is.
- `SHA256SUMS` proves the set is whole, not who made it, and the library is hashed once per process: a library file changed while a process runs is seen by the next process.
- Disk use inside a writable grant, and anything a writable grant holds that a later call reads.
- Timing, the real clock and entropy.

# Licence

The project's code is Apache-2.0. Perl is under the Artistic License or GPL-1.0-or-later; its notice ships inside the wheel.
