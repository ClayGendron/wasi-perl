# 005 — The sandbox escape tests

- **Status:** pre-agreed by the owner 2026-11-24 with the roadmap; building.
- **Owner:** Clay Gendron
- **Kind:** checks only: a new `tests/escapes/test_escapes.py`; no change to the runner unless a check finds a hole, and then the fix is named here.
- **Depends on:** 004 (the runner), 001 (a `perl.wasm` to run them; they skip without one).
- **Research it stands on:**
  - wasi-python's escape checks (READ): `tests/checks/test_v1_2_hostile_guest.py` (`test_a_link_out_of_the_root_is_refused`, `test_a_read_only_grant_refuses_every_write`, `test_nothing_is_writable_by_default`, `test_planted_code_never_runs`, `test_a_trailing_slash_symlink_rename_cannot_escape`, `test_an_open_of_a_fifo_in_a_grant_returns_refused`) and `test_v1_2_portable_and_sealed.py`; its specs 006, 017 and 025.
  - A probe in this container (EXECUTED, 2026-11-24, scratch `probe.c` compiled with wasi-sdk 34 and run through `wasi_perl.run` under wasmtime-py 51.0.0, with a writable grant `/w`, a read-only grant `/r` and a host folder `outside` beside them): `symlink("../outside", "/w/up")` is made, but opening through it is `EPERM`; `rename("/w/up/", "/w/stolen")` (the trailing-slash rename wasi-python guards) is `ENOTDIR` and nothing named `stolen` appears; `symlink("/etc/passwd", "/w/abs")` is `EPERM` at creation; `open("/w/../outside/canary.txt")` is `EPERM`; `link("/r/f", "/w/h")` and `rename("/r/f", "/w/f2")` are `EPERM`; `mkdir("/dev/x")` is `EPERM`; `open("/etc/passwd")` is `ENOENT`; `chmod` is `ENOSYS` (WASI preview1 has no chmod); `outside` keeps its one file.
  - A second probe (EXECUTED): a FIFO the host made inside a read-only grant, opened for reading by the stand-in guest under `deadline_s=2`, was still blocked when the probe was killed at 20 s. wasmtime opens the path with a blocking `open(2)` in the host, and epoch interruption stops wasm code only. Decision 4.

## The problem

The constitution's goal 5: "wasi-python's sandbox escape tests, ported to Perl, pass: reads outside a grant, `../` climbs, links out of a grant, and writes to a read-only grant are all refused." Spec 004's checks prove the runner's rules with a C stand-in. What an agent will actually run is Perl, whose file operators (`open` modes, `-i`, `rename`, `link`, `symlink`, `utime`, `truncate`, `chmod`, `mkdir`, `opendir`, `socket`) take their own routes into wasi-libc. Each route must meet the same wall.

## Decisions

1. **Every check runs real Perl through `run()`**, with the `perl` fixture (spec 004: `$WASI_PERL_ARTIFACTS`, else the checkout's `build/artifacts/`; skipped with a message naming spec 001's build when absent), is marked `slow`, and counts outcomes: what the guest printed, its exit code, and the host files' bytes, modes, times and listings before and after. None measures time. A guest program prints one line per attempt, `NAME ok` or `NAME refused <errno text>`; a check fails on any `ok` it did not expect and on any change on the host. Errno texts are recorded in the landing notes as run; a check asserts the text only where WASI fixes the answer (`EPERM` for a write through a read-only grant, `ENOENT` outside every grant).

2. **The checks**, each ported from the wasi-python check named, with the host laid out by the check under its own `tmp_path` (folders `ro`, `rw`, `outside` holding `secret.txt`):
   1. `test_a_read_outside_every_grant_is_not_found` (wasi-python's sealed checks): `/etc/passwd`, `/outside/secret.txt`, the host's absolute path of `secret.txt`, `/tmp/x`, a relative `secret.txt` without `cwd`, and `opendir("/")` all fail; nothing of `secret.txt` is printed.
   2. `test_dot_dot_cannot_climb_out_of_a_grant`: `/ro/../outside/secret.txt`, `/ro/sub/../../outside/secret.txt`, and with `cwd="/ro"` the relative `../outside/secret.txt` and `../../../../etc/passwd` are refused; nothing of `secret.txt` is printed.
   3. `test_an_absolute_climb_is_refused`: `/usr/local/lib/perl5/../../../../etc/passwd` and `/dev/../etc/passwd` are refused.
   4. `test_a_host_made_link_out_of_a_grant_is_refused` (`test_a_link_out_of_the_root_is_refused`, POSIX): links the host made inside `ro` and `rw`, to `outside` and to `secret.txt`, absolute and relative, are refused for reading, and for writing in `rw`; `outside` is unchanged.
   5. `test_a_guest_made_link_out_of_a_grant_is_refused` (the same, and `test_a_trailing_slash_symlink_rename_cannot_escape`): in `rw` the guest makes `symlink("../outside", ...)`, `symlink("<host path of outside>", ...)` and `symlink("/etc", ...)`; whichever are made, reading or writing through them is refused, and `rename("/rw/up/", "/rw/stolen")` is refused; `outside` is unchanged and nothing named `stolen` exists.
   6. `test_a_read_only_grant_refuses_every_write` (its namesake): open for `>`, `>>`, `+<`, `+>`; `sysopen` with `O_CREAT`; `unlink`; `rename` inside, out to `rw` and in from `rw`; `mkdir`; `rmdir`; `symlink`; `link` in place, out and in; `chmod`; `utime`; `truncate` by name and by handle; and a write through a handle opened for reading. All refused; the tree under `ro` (names, modes, bytes, modification times) is unchanged; a write to `rw` in the same program succeeds, as the control.
   7. `test_an_in_place_edit_of_a_read_only_grant_changes_nothing`: `-pi -e 's/a/b/' /ro/f.txt` leaves `f.txt`'s bytes and time unchanged, adds no file to `ro`, and says why on stderr (Perl's "Can't" message). Its exit code is recorded, not asserted: Perl skips a file it cannot edit with a warning and goes on, natively too. The same edit in `rw` changes the file, as the control.
   8. `test_nothing_is_writable_without_a_grant` (`test_nothing_is_writable_by_default`): with no grant, writes to `/x`, `x`, `/tmp/x`, `/usr/local/lib/perl5/x`, `/usr/local/lib/perl5/5.42/strict.pm` (append) and `mkdir("/tmp")` are refused, and `opendir("/")` fails; Perl still computes (`print 6*7`).
   9. `test_the_library_and_dev_null_are_not_writable`: with a writable grant present, appending to `strict.pm`, creating a file in the library, `unlink` of a library file, and writing `/dev/null` (`>` and `>>`) are refused; reading `/dev/null` gives end of file; the artifact set's `lib/` and `dev/` trees are byte-for-byte as before.
   10. `test_there_is_no_network`: `socket(...)` with the core numbers for `AF_INET`, `SOCK_STREAM` fails or dies (WASI preview1 cannot create a socket), and, if `IO::Socket::INET` loads, a connect to `127.0.0.1:9` fails. No guest line says `connected`.
   11. `test_the_module_cache_is_never_reachable`: with the call's cache a folder `cache` beside `rw`, the guest's climbs (`/rw/../cache`, a link to it) cannot list, read or write it, its listing and bytes are unchanged after the call, and `run()` refuses a grant at `cache`, inside it, or a writable one above it, before any work (spec 004's rule, now with Perl).
   12. `test_a_writable_grant_cannot_plant_code_in_the_library_path` (`test_planted_code_never_runs`): `run()` refuses a writable grant at `/usr/local/lib/perl5/site_perl` and of the set's own `lib/` folder; a guest cannot create `/usr/local/lib/perl5/5.42/Evil.pm` or `site_perl/Evil.pm`; a second call's `require Evil` fails; and `@INC` holds only paths under `/usr/local/lib/perl5` (no `.`), so a module the guest left in a writable grant runs only if the caller puts that grant on `PERL5LIB` itself (spec 004, Decision 6).

3. **What is not ported, and why.** wasi-python's checks of its root (`root=True`), its door, mounts, tools, sessions, the zip and its import path test parts this runner does not have. Process and signal refusals are spec 003's.

4. **A FIFO in a grant blocks past the deadline: recorded, not fixed here.** wasmtime opens a guest's path with a blocking `open(2)` on the host thread, so a guest that opens a FIFO no one writes waits in the host, and epoch interruption, which stops only wasm code, never ends the call (the second probe). The guest cannot make a FIFO (WASI preview1 has no `mknod`); one exists only if the host's own files hold one, or another process makes one in a granted folder during the call. wasi-python refuses such an open in its own path layer (`test_an_open_of_a_fifo_in_a_grant_returns_refused`); this runner has no such layer, and building one (a `path_open` that checks the file's kind before wasmtime opens it, through a linker that shadows wasmtime's own) is a specification of its own. Until then `test_a_fifo_in_a_grant_is_refused` states the wanted behaviour (the open is refused, the call ends by itself, the deadline holds) and is marked `xfail(strict=True)` with this reason, running the call in a child process with a fail-safe timeout so the suite never hangs; the README's "Not protected" names it.

## Acceptance

1. `uv run pytest tests/escapes` with spec 001's `perl.wasm` (`WASI_PERL_ARTIFACTS=…/001-build/build/artifacts`): every check passes, the FIFO check is an expected failure, EXECUTED, output and each guest's lines in the landing notes. Without `perl.wasm`, every check skips with its message.
2. `ruff check`, `ruff format --check` and the whole suite stay green.
3. Any hole found is fixed in the runner in this branch, with a check seen failing before the fix, or recorded as Decision 4 is.

## Landing notes

Built 2026-11-24 by the implementer (Claude Opus 5.5) on `spec/005-escapes`, branched from `spec/004-runner` at `c9f38bf`, Linux x86-64, CPython 3.13, wasmtime-py 51.0.0, as root. **Not yet reviewed.** Every claim below was run (EXECUTED) unless it says otherwise.

### The perl.wasm they ran against

Spec 001's build in its own worktree, `../wasi-perl-wt/001-build/build/artifacts/` (read only; never written by these checks): `perl.wasm` of 4,029,132 bytes, SHA-256 `c544325d3989c8456b19d42248e2630c6d6531f03c2eb9c555e6ca69be9d6116`, a library of 1,622 files, `dev/null`. That set has no `MANIFEST` or `SHA256SUMS` yet, so a found set is refused by spec 004's check (`ArtifactsMissing: …/SHA256SUMS cannot be read`, EXECUTED). The checks ran against a scratch copy sealed by `scripts/write_sums.py` (1,625 lines; `sha256sum -c` passes), which also runs spec 004's tree check over the real library on every found-set call.

It has the start-up `chdir($PWD)` (spec 004's `test_cwd` passes, not skips) and the `wasi_perl_exit_status` export (`die "x\n"` gives `exit_code` 255, `trapped` null, through `wasi-perl run`). It has no XS modules yet (spec 002): `Fcntl`, `Socket` and `IO::Socket::INET` are absent, so the `sysopen` row says "absent Fcntl" and is not counted, and the network check sees only the core `socket` operator.

### The checks

```
$ WASI_PERL_ARTIFACTS=<sealed copy> uv run pytest -q -rsx
SKIPPED [1] tests/runner/test_arguments.py:207: root can write every folder
XFAIL tests/escapes/test_escapes.py::test_a_fifo_in_a_grant_is_refused - wasmtime opens a guest's path with a blocking open(2) on the host thread, which epoch interruption cannot stop: a FIFO in a grant holds the call past its deadline (spec 005, Decision 4)
160 passed, 1 skipped, 1 xfailed, 4 warnings in 29.45s
$ uv run pytest -q          # no perl.wasm: every real-Perl check skips with its message
141 passed, 21 skipped, 4 warnings in 4.81s
$ uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
All checks passed!
19 files already formatted
```

The 160 include spec 004's seven real-Perl checks (`tests/runner/test_perl.py`), all passing with this perl.wasm.

### What the guests printed (errno texts as Perl's `$!` gives them)

- Outside every grant: `/etc/passwd` "No such file or directory"; `/outside/…`, the host's absolute path, `/tmp/x`, a relative path without `cwd` and `opendir("/")` all refused.
- `..`: `/ro/../outside/secret.txt`, `/ro/sub/../../…`, and with `cwd` `../outside/secret.txt` and `../../../../etc/passwd`, from `/ro` and from `/rw`: all refused; nothing outside was created.
- Host-made links in `ro` and `rw` to `outside` and to `secret.txt`, absolute and relative: every read, write and `opendir` through them refused.
- Guest-made links in `rw`: `symlink("../outside")` and `symlink("../outside/secret.txt")` are **made** (`ok`), `symlink("<host path>")` and `symlink("/etc")` are refused "Operation not permitted"; reading or writing through the made ones is "Operation not permitted"; `rename("/rw/up/", "/rw/stolen")` is "Not a directory"; nothing named `stolen` exists. A made link stays in the host folder after the call: the guest cannot follow it, but the embedder's own code can (the README's "Not protected" says so).
- A read-only grant: `open` for `>`, `>>`, `+<`, `+>`, a new file, `unlink`, `rename` inside, out and in, `mkdir`, `rmdir`, `symlink`, `link` in place, out and in, `utime` of a file and of the grant, `truncate` by name and by handle: each "Operation not permitted"; `chmod` "Function not implemented" (WASI preview1 has none); `syswrite` on a read handle "Bad file descriptor". The tree's names, modes, times and bytes are unchanged; the control write to `rw` succeeded.
- `-pi -e 's/a/b/' /ro/f.txt`: exit 0, stderr `Can't do inplace edit on /ro/f.txt: Cannot make temp name: Operation not permitted.`, the file unchanged; the same edit in `rw` changed it.
- No grant: `/x`, `x`, `/tmp/x`, `/usr/local/lib/perl5/x`, appending to `strict.pm`, `mkdir("/tmp")`, `opendir("/")`: all refused; `print 6*7` printed 42.
- The library and `/dev`: appending to `strict.pm`, a new `Evil.pm`, `unlink` and `rename` of `strict.pm`, `/dev/null` for `>` and `>>`, a new `/dev/tty`: all refused; `/dev/null` reads end of file; the set's `lib/` and `dev/` hash as before.
- Network: `socket(my $s, 2, 1, 0)` dies `Unsupported socket function "socket" called`; `IO::Socket::INET` is absent in this build.
- The module cache: climbs and a link from `rw` to the cache cannot list, read or write it; the cache's listing and bytes are unchanged; `run()` refuses a grant at it (both modes) and a writable one above it.
- Planted code: writes into `/usr/local/lib/perl5/5.42` and `site_perl` refused; `require Evil` fails in a second call even with `cwd` at the writable grant holding `Evil.pm`; `@INC` is `/usr/local/lib/perl5/5.42/site_perl/wasm32-wasi`, `…/5.42/site_perl`, `…/5.42/wasm32-wasi`, `…/5.42` (no `.`). `run()` refuses writable grants at `/usr/local/lib/perl5/site_perl`, `/usr/local/lib/perl5/5.42`, `/usr/local` and of the set's `lib/` folder.

### Seen failing first

Against stand-ins of the runner, each one change made by text replacement and restored, run with a freshly sealed copy (`build/logs/005-mutations.txt`): grants always writable (2 checks fail: the read-only rows and the in-place edit); the library preopened writable (5: the library check, the planted-code check, and three that then found `strict.pm` gone, since a guest under that stand-in really did unlink it from the scratch copy; the copy was rebuilt afterwards); `/dev` writable (1); the cache rule off (1); the reserved guest paths off (1). The first run of the last three, without a fresh copy, failed 11 each, from that damaged copy; the rerun is the record.

### Found while building

- **A FIFO in a grant blocks past the deadline** (Decision 4; the second probe, EXECUTED). Not fixed: a follow-up specification.
- **A guest can leave a link that climbs out** in a writable grant (`symlink("../outside", …)` is made). wasmtime refuses to follow it, so the guest gains nothing; a host reading the folder later would follow it. Recorded in the README; not a hole in the wall.
- **wasmtime-py objects finalized at interpreter exit** printed `Exception ignored in: <function Managed.__del__ …> TypeError: 'NoneType' object is not callable` after `wasi-perl run`. Fixed in this branch: `_engines` frees its modules and engines in an `atexit` hook, as wasi-python's `release_at_exit` does (EXECUTED: the message is gone).
- **The README's outputs** were run with this perl.wasm and replaced; one example changed: allocating `"x" x (512 * 1024 * 1024)` as a constant is folded at compile time and panics (`Out of memory in perl:util:safesysrealloc` then `panic: fold_constants JMPENV_PUSH returned 2`, exit 48); a run-time size gives exit 1 with Perl's own `Out of memory` line, which the README now shows. Whether the fold-time panic is right is spec 002's or 007's question.

### Left over

- The FIFO refusal (a `path_open` that checks the file's kind first), its own specification.
- Re-run with spec 002's build, when `Fcntl`, `Socket` and `IO::Socket::INET` exist: the `sysopen` row then counts, and the network check reaches `IO::Socket::INET`'s connect.
- Spec 001's assembly should write `MANIFEST` and `SHA256SUMS` (with `scripts/write_sums.py`) so the found-set path runs without a scratch copy.
