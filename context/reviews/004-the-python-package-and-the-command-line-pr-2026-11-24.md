# Review: 004, the Python package and the command line (pull request, 2026-11-24)

Reviewer: an agent (Claude), a fresh sub-agent of https://claude.ai/code/session_01ThzDEUwftdmy8zWBJK1gWa; not the author

- **Branch:** `spec/004-runner` at `c9f38bf`, against `origin/dev`.
- **Read:** AGENTS.md, `context/constitution.md` (§4.5 above all), the specification and its landing notes, `git diff origin/dev...HEAD`, and wasi-python's `_engines.py`, `_places.py`, `_platform_posix.py` and `cli.py` (READ).
- **Probe scripts:** they were kept in the reviewer's scratch folder and are not committed. Each finding below gives enough to repeat it.

## What was run

```
$ uv sync && uv run pytest -q
141 passed, 8 skipped, 4 warnings in 4.80s                                         EXECUTED
$ uv run ruff check
All checks passed!                                                                 EXECUTED
$ uv run ruff format --check
1 file would be reformatted, 20 files already formatted   (README.md:56)           EXECUTED
$ uv run ruff format --check src tests scripts
18 files already formatted                                                         EXECUTED
$ WASI_PERL_ARTIFACTS=/home/user/wasi-perl-wt/001-build/build/artifacts uv run pytest -q -rs
148 passed, 1 skipped (root can write every folder), 4 warnings in 7.47s          EXECUTED
```

That artifacts folder has no `MANIFEST` and no `SHA256SUMS`. The real-Perl checks pass the set as `artifacts=`, so it is not checked. `wasi-perl run` finds the set through the variable and refuses it with exit 1 and `{"error": ".../SHA256SUMS cannot be read ...", "kind": "ArtifactsMissing"}`, as Decision 9 says it should (EXECUTED). I copied the set to a scratch folder, wrote a stub `MANIFEST`, and sealed it with `uv run scripts/write_sums.py` (1625 lines). With that copy (EXECUTED):

| Command | Result line | Exit |
|---|---|---|
| `wasi-perl run -- -e 'print "hi\n"'` | `{"exit_code": 0, "fuel_used": null, "stderr": "", "stdout": "hi\n", "trapped": null, "truncated": []}` | 0 |
| `wasi-perl run -- -e 'die "boom\n"'` | `{"exit_code": 255, ..., "stderr": "boom\n", ..., "trapped": null, ...}` | 0 |
| `-e 'exit 130'` | `exit_code` 130, `trapped` null | 0 |
| `--fuel 100000 -- -e '1 while 1'` | `"fuel_used": 100000, "trapped": "out of fuel"` | 0 |
| `--deadline-s 0.3 -- -e '1 while 1'` | `"trapped": "deadline"`, `fuel_used` null | 0 |
| `-- -e 'binmode STDOUT; print "\xff"'` | `"stdout_base64": "/w=="` | 0 |
| `run -e 'print 1'` (no `--`) | argparse usage error, nothing on stdout | 2 |
| `--grant /d` | `--grant needs GUEST=HOST` | 2 |

The cache folder came out 0700 and both `.cwasm` files 0600 (EXECUTED, `ls -la`).

**Sandbox probes with the real perl.wasm (EXECUTED):**

- Through a read-only grant, all of these fail with `EPERM`: `>`, `>>`, `+<`, `unlink`, `rename`, `mkdir`, `rmdir`, `utime`, `symlink`, `link` and `truncate`. `chmod` fails with `ENOSYS`. The host folder is unchanged.
- The library and `/dev` refuse every write, `/dev/null` included.
- With no grant, `>/tmp/x` and `>x` fail with `ENOENT`, and `opendir "/"` fails.
- `..` climbs fail with `EPERM`. So do absolute and relative links that lead out of a grant.
- A writable grant takes writes. A guest can create a relative symlink that leads out of its grant, but it cannot follow it.
- An endless 64 KiB writer under a 4 KiB cap ends `deadline` with exactly 4096 bytes. The process's peak RSS was 52 MiB, and nothing new appeared in the temporary folder.
- Exit statuses through the export: 125, 126, 200 and 255 are exact, with and without fuel. So is `die` (255).
- Six threads ran at once (four deadlines, two fuel caps, one without a deadline). Each got its own outcome.
- `-pi -e` with `cwd` edits in place inside a writable grant.

## Findings

| # | Rank | Where | Finding | Suggested fix |
|---|---|---|---|---|
| 1 | **High** | `src/wasi_perl/_grants.py:130`, `:162` | **On POSIX, a backslash in a host path or link target is read as a separator, so a grant can open a folder other than the one named, outside every grant.** `resolve` runs `rest.replace("\\", "/")` on every platform. On POSIX, `\` is an ordinary filename character. A folder literally named `..\secret` is resolved as the components `..` and `secret`. EXECUTED: a guest with a writable grant of `work/rw` ran `mkdir "/out/..\\secret"`. The host then granted that folder, `Grant(rw / "..\\secret", "/in", writable=True)`, which is an empty folder inside `rw`. The runner preopened `work/secret` instead. The next guest read `SECRET` from `/in/s.txt` and planted `/in/planted` in `work/secret`. This is the exact threat the link rule exists to stop: the host grants something a guest made, and the guest redirects it. A name does what a refused link would have done. The same split runs on readlink targets (`:162`). | Split on `os.sep` only, and also on `os.altsep` only when `WINDOWS`. Never rewrite `\` on POSIX. Add checks to `test_arguments.py`: a POSIX folder named `..\x` resolves to itself (or is refused), and a link target holding `\` is taken literally. |
| 2 | Medium | `src/wasi_perl/runner.py:196-203` | **Writable grants of the host Python's own code are allowed.** `_runtime_folders` lists the artifact set, `wasi_perl` and `wasmtime`, but not the interpreter. wasi-python's `_places._runtime_folders` also lists sysconfig's `stdlib`, `platstdlib`, `purelib`, `platlib` and `scripts`, plus the user site-packages ("The host Python's own code"). EXECUTED: `Grant("/usr/local/lib/python3.13", "/w", writable=True)` and `Grant(".venv/bin", "/w", writable=True)` both run, and the guest sees `-w "/w"`. A guest could then plant `sitecustomize.py`, or rewrite the `wasi-perl` entry script, and that code runs in the next host process. The specification's own reason ("change what the next call or the next host process runs") and constitution §4.5 cover it. AGENTS.md says to do what wasi-python does where this project is silent. | Add sysconfig's five paths and `site.getusersitepackages()` (when `ENABLE_USER_SITE`) to `_runtime_folders`, as wasi-python does. Extend `test_no_writable_grant_reaches_the_runtime`. Add the folders to the specification's list. |
| 3 | Medium | `tests/runner/test_perl.py:22-25`; `runner.py:183`; contract in Decision 7 | **The exit-status contract is not what the real perl.wasm exports, and no real-Perl check sees the gap.** Decision 7 says the export returns the status `& 0xff`. The stand-in masks (`standin.c:34`). spec 001's wrapper (`recipes/wasi-stubs.c` on `spec/001-*`) stores the raw `__wasi_exitcode_t`. EXECUTED with the real perl.wasm: `exit 256` and `exit -1` come back as `exit_code None, trapped "exit with invalid exit status outside of [0..126)"`. Native perl exits 0 and 255. `exit -1` is stored as `(int32_t)0xffffffff == -1`, which collides with the "never exited" sentinel, so the runner alone cannot recover it. | Have the build's wrapper store `rval & 0xff`, as the contract says, and record that against spec 001. In `test_perl.py`, check `exit 256` → 0 and `exit -1` → 255 against the real perl.wasm. The runner may also mask a non-negative status, as a second guard. |
| 4 | Medium | `src/wasi_perl/_grants.py:1-5`, `:236-245`; `README.md:121`; Decision 4's last bullet | **The race left out of the specification is reachable by a guest, through concurrent calls in one process.** The specification and README say only "another writer" can swap a folder between the walk and wasmtime's open, and that "the guest cannot do this". `run` is meant to be called from several threads at once (`test_concurrent_calls_share_nothing`). The rule "no read-only grant inside a writable one" is judged within one call only. Call A's guest, holding writable `W`, can replace `W/sub` with a relative symlink while call B, granting `W/sub`, is between `resolve` and `preopen_dir`. wasmtime then follows the link out of `W`. Without the race, A's guest can still rewrite B's "read-only" grant while B runs. (READ and reasoned. The swap itself was not timed, because tests may not use the clock.) wasi-python closes this with a process registry of live writable grants (`_hold_writable` and `live_writable`) and a post-preopen identity check. | Keep a process-wide registry of the live calls' writable host folders, under a lock. Refuse a grant whose chain passes through one, as the per-call rule does. Or port the identity check. At the least, correct the README's "Not protected" line and the specification so they say a concurrent call's guest is such a writer. |
| 5 | Low | `src/wasi_perl/runner.py:329-335` | The export is called with whatever fuel and epoch budget the guest left. Suppose a guest reaches the refused exit with no fuel left, or just past its deadline. The export then traps, `_exit_status` returns `None`, and the result is `trapped` = the refusal's words. That is neither the status nor "out of fuel" or "deadline". | After reading `fuel_used`, call `store.set_fuel(UNMETERED)` and `store.set_epoch_deadline(NO_DEADLINE)` before calling the export. |
| 6 | Low | `src/wasi_perl/_cache.py:28-29`, `:226-230` | The cache checks are close to wasi-python's, with two differences. `read` reopens the folder by name and does not check that descriptor again: wasi-python's `open_cached` runs `private_fd` on the folder descriptor it opens the file in. `_FILE_OPEN` also lacks `O_NONBLOCK`, so a FIFO swapped in after the `lstat` would block `open`. Only this user can do either, because `_way` holds the path to the folder. | `fstat` the reopened `dir_fd` and compare its owner, mode and (dev, ino) with the checked folder. Add `O_NONBLOCK` to `_FILE_OPEN`, as wasi-python's `_CACHE_FILE_OPEN` has. |
| 7 | Low | `tests/runner/test_perl.py:57-58` | The `cwd` check skips when it fails with "No such file". spec 001's build now carries the `chdir($PWD)` constructor, and the check passes with it (EXECUTED), so the skip can only hide a regression. | Drop the skip, or skip only when the module lacks a marker of the constructor. |
| 8 | Low | `tests/runner/test_perl.py:1`, `:58`; `tests/conftest.py:123` | Code under `tests/` names "spec 001" in a docstring and in skip messages. AGENTS.md forbids this ("names no specification or review number"). | Say "the build (`scripts/build.sh`)" in words. Reword Decision 12 to match. |
| 9 | Low | `README.md:56` | Bare `uv run ruff format --check` fails: ruff wants `r"""` for the README's code block. The landing notes ran it only on `src tests scripts`. | Use `r"""` in the README example, or exclude `*.md` in `[tool.ruff]`. |
| 10 | Low | `README.md:96-108` | The README's outputs are still "(not yet run)", and one of them is wrong. EXECUTED with the real perl.wasm: `$x = "x" x (512 * 1024 * 1024)` under `memory_bytes=128 << 20` gives exit 48 and `panic: fold_constants JMPENV_PUSH returned 2`, not 1. The other three limit lines, and the `map` example, match. | Run the examples and record the real outputs. Raise the panic against spec 001 if it differs from native Perl. |
| 11 | Low | `src/wasi_perl/_grants.py:220-226` | A read-only grant above a module cache is allowed (EXECUTED), so a guest can read the `.cwasm` files. This is harmless, since they hold no secret. But Decision 4's reason says "so the guest never reads ... a `.cwasm`". | Either refuse any grant above a cache, or reword the reason to "never plants". |
| 12 | Low | `tests/runner/test_perl.py:41-45` | The real-Perl write-refusal check asserts only `exit_code != 0`. It would also pass if the guest failed for an unrelated reason. | Assert on `Operation not permitted` in `stderr`. |
| 13 | Low | `src/wasi_perl/_artifacts.py:339-341` | `os.walk(root / "lib")` follows `lib` itself when it is a symlink. Links inside the tree are refused, but this one is not. | `lstat` `lib`, `lib/perl5` and `dev`, and refuse a link. |

## Checked and sound

- Nothing is writable by default. The library and `/dev` are always preopened with `fs_mutable=False`, before the grants. wasmtime-py 51 has no separate `DirPerms`/`FilePerms`. `fs_mutable=False` gives read-only directory and file permissions, and every write-type call was refused in the probes.
- Guest path checks: absolute, normalised, not `/`, no `\`, and not at, under or above either reserved folder. `/devx` and `/usr/local/lib/perl5x` are allowed, correctly. Duplicates are refused and nested paths allowed.
- A guest-made link in a writable folder is refused as a host path, both for a grant and for `cwd`. A Windows drive letter passes whole through the CLI's first-`=` split.
- Limits:
  - The deadline works by epoch, through one ticker per process.
  - Fuel runs on a second engine, with instantiation unmetered.
  - `set_limits` caps memory.
  - Output caps are exact per stream, with `truncated` in order, nothing on disk, and memory bounded by the cap.
  - A trap is never read as an exit: the export runs only after `ExitTrap` or the refusal's text.
  - `fuel_used` is read before the export.
- The module cache: the key is the digest, the wasmtime version and the flavor. The module is compiled from bytes hashed once and compared to the checked digest. A `.cwasm` is read through the checked descriptor and given to `deserialize` as bytes. It must be 0600 and have one name. The folder must be 0700, and every folder on the way must be this user's or root's and closed to others (or sticky). Writes go through `mkstemp` and `os.replace`.
- Artifacts: `perl.wasm` and `MANIFEST` are checked against `SHA256SUMS` on every call. The tree is checked once per `SHA256SUMS` stamp, unlisted files are refused, and `dev/` holds only an empty `null`.
- Thread safety: engines, modules and counts are under one lock, and one compile runs per key.
- The CLI's JSON shape and exit codes match the specification.
- No check measures wall-clock time. No check was weakened: the monkeypatches are spies, and the owner seam is used only where `chown` needs root.

## Verdict

Does not land: finding 1 is High. On POSIX, a host path with a backslash, which a guest can create inside a writable grant, resolves to a folder outside every grant. The read and the write through it were both EXECUTED. Fix 1. Fix or answer 2, 3 and 4 in writing. Then a fresh review.

## Round 2 (fix commit `571f50b`)

Reviewer: an agent (Claude), a fresh sub-agent of https://claude.ai/code/session_01ThzDEUwftdmy8zWBJK1gWa; not the author

I read `git show 571f50b` and the specification's "Author's answers", and ran the round-1 probes again against the same sealed copy of spec 001's perl.wasm. `sha256sum -c SHA256SUMS` still passes on that copy.

### What was run

```
$ uv run pytest -q
149 passed, 11 skipped, 4 warnings in 9.35s                                        EXECUTED
$ WASI_PERL_ARTIFACTS=<sealed copy> uv run pytest -q -rsx
157 passed, 1 skipped (root), 2 xfailed (exit 256, exit -1), 4 warnings in 11.89s  EXECUTED
$ uv run ruff check && uv run ruff format --check        # bare, README included
All checks passed! / 22 files already formatted                                    EXECUTED
```

### Each finding

| # | Round 1 | Round 2 |
|---|---|---|
| 1 | High | **Fixed.** `_parts` splits at `os.sep`, and at `os.altsep` only where one exists, for both host paths and link targets. EXECUTED with perl.wasm, the round-1 reproduction: a guest makes `/out/..\secret`. A later read-only grant of that folder lists only `.` and `..`. A later writable grant reads nothing of `secret` (`No such file or directory`), and `secret` still holds only `s.txt`. Three new checks cover this, one of them with the real perl.wasm. |
| 2 | Medium | **Fixed.** EXECUTED: a writable grant of `/usr/local/lib/python3.13` (stdlib), of the venv's `lib/python3.13`, of `site-packages` and of `.venv/bin` is each refused with "reaches the runtime's own files". This now matches wasi-python's list. |
| 3 | Medium | **Answered, and the answer is accepted.** The mask belongs in the guest, because −1 collides with the "never exited" sentinel, so the runner cannot fix it alone. spec 003's build supplies the mask. `test_exit_256_is_0` and `test_exit_minus_1_is_255` are strict xfails, so they will fail loudly once the masking build is merged. Whoever merges must not drop them. The runner deliberately does not mask a second time, which I accept: a broken contract stays visible. |
| 4 | Medium | **Fixed.** See "The live-call registry" below. The README and Decision 4 no longer say a guest cannot cause the race. The post-preopen identity check, which would stop writers outside the process, remains a stated later specification. |
| 5–13 | Low | **Fixed or answered as stated in the specification.** The export now gets unlimited fuel and no epoch deadline (`test_the_status_is_read_even_with_no_fuel_left`). The cache folder descriptor is checked again before the file is opened, and the open is `O_NONBLOCK`. The `cwd` skip is gone and the check passes. No specification numbers remain under `tests/`. The README now carries run outputs and passes bare ruff. A read-only grant above a cache is allowed on purpose, and the reason now reads "never plants". The write-refusal check now asserts the exact EPERM text. A linked `lib`, `lib/perl5` or `dev` is refused. |

### The live-call registry (`_grants.hold` and `let_go`, `runner.run`)

- **Lock.** `_live_lock` is a leaf lock. Inside it, `hold`, `let_go` and `live_count` touch only `_live` and `_tokens`, and none of them calls out or takes another lock, so it cannot deadlock. The check and the insert form one critical section, so of two calls starting at once, the second sees the first. A `ValueError` raised inside `with` releases the lock and leaves nothing registered.
- **Ordering closes the race.** `hold` runs after the walk (`judge`) and before any preopen, and a guest runs only after its own call's `hold`. Take call B's walk-to-preopen window and call A's guest run. If they overlap, then either A was registered when B's `hold` ran, so B is refused, or B was registered when A's `hold` ran, so A is refused. Either way the swap cannot land.
- **The rule.** A grant is refused when it is at, inside or above a live writable grant, and a writable grant is refused when it is at, inside or above any live grant. Both are compared by identity chain. Two read-only grants of one folder may run at once.
- **Release on every exit path.** `hold` is followed at once by `try: _call(...) finally: let_go(token)`, and `_call` closes the store in its own `finally`. So the registry is cleared after a normal end, an exit, a trap or a deadline, and after any exception, `BaseException` included. EXECUTED: with `runner._call` patched to raise `KeyboardInterrupt`, `live_count()` is back to 0 afterwards. Eight concurrent calls on separate writable folders all succeed and leave 0 live. `test_a_refused_call_holds_nothing` covers a refusal and a trap.
- **Scope.** The registry covers one process only. Another process, or a second interpreter, is still the "another writer" named in "Not protected". That is stated, and it is no wider than wasi-python's own guard of this kind.

### New findings

| # | Rank | Where | Finding | Suggested fix |
|---|---|---|---|---|
| 14 | Low | `tests/runner/test_live_grants.py:18-22` | Call A is held open only by `deadline_s=2`. If the body's calls take longer than 2 s on a slow or loaded runner, A ends first, and the "refused" assertions fail. That would be a flaky failure, not a false pass, so it does not weaken the check. The wait polls the registry, not a clock, which is allowed. | Give A a longer deadline, for example 30 s, and keep the body short, or add an assertion that A is still alive after the body. Do this when convenient. |

### Verdict

Lands: no High findings remain. The High is fixed and EXECUTED against the real perl.wasm. Mediums 2 and 4 are fixed. Medium 3 is answered, and its strict xfails must flip when spec 003's masking build merges. One new Low remains open (finding 14), along with the post-preopen identity check that the specification has deferred.
