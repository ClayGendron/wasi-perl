# Review: 005, the sandbox escape tests

Reviewer: an agent (Claude), a fresh sub-agent of https://claude.ai/code/session_01ThzDEUwftdmy8zWBJK1gWa; not the author

- **Branch:** `spec/005-escapes` at `51f654a`, reviewed as the diff `origin/spec/004-runner...origin/spec/005-escapes` (4 files: the spec, `tests/escapes/test_escapes.py`, `src/wasi_perl/_engines.py`, `README.md`).
- **Read:** `AGENTS.md`, `context/constitution.md` §4.5, the spec, and the guide's `test_v1_2_hostile_guest.py` and `test_v1_2_portable_and_sealed.py`.
- **Date:** 2026-11-24.

## The run

These ran against a scratch copy of spec 001's set. I copied `../wasi-perl-wt/001-build/build/artifacts` into this worktree's `build/artifacts` and sealed it with `uv run python scripts/write_sums.py build/artifacts`. That gave 1,624 lines, and `sha256sum -c` passes. `perl.wasm` has SHA-256 `c544325d…6d6116`, the same as in the landing notes. Every command below was EXECUTED.

```
$ WASI_PERL_ARTIFACTS=$PWD/build/artifacts uv run pytest -q -rsx tests
SKIPPED [1] tests/runner/test_arguments.py:207: root can write every folder
XFAIL tests/escapes/test_escapes.py::test_a_fifo_in_a_grant_is_refused - wasmtime opens ...
160 passed, 1 skipped, 1 xfailed, 4 warnings in 27.57s
$ WASI_PERL_ARTIFACTS=/nonexistent uv run pytest -q -rs tests/escapes
13 skipped   (each: "no perl.wasm: set $WASI_PERL_ARTIFACTS or build it with scripts/build.sh (spec 001)")
$ uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
All checks passed!  19 files already formatted
```

I also checked the FIFO xfail by hand (EXECUTED). I ran the test's own `FIFO_CHILD` against a real FIFO. It was still blocked when the 15 s timeout I set ran out. Against a regular file in the same place, it returned `0 None b'opened\n'`. So the harness works, and the expected failure is the documented block, not a broken child.

## Escape ideas of my own (real Perl, real perl.wasm)

I ran these in one guest with `ro`, `rw` and `outside` beside one another (scratch `probe.py`, EXECUTED). Afterwards, `ro`, `outside` and the set's `lib/` hashed the same as before. The hash covered names, modes, times, bytes and each folder's own stat. The temp folder held only its three folders, and `ro/f.txt` still read `a original`. **None escaped.**

| Attempt | Perl's answer |
|---|---|
| `opendir("/..")`, `/ro/..`, `/rw/..`, `/usr/local/lib/perl5/..` | ENOENT, then EPERM for the rest |
| `symlink` to the library by absolute target | EPERM at creation |
| `symlink("../../usr/local/lib/perl5/5.42/strict.pm", "/rw/s2")` | made (a dangling host link, as the README's "Not protected" says) |
| `symlink("../ro/f.txt", "/rw/rolink")`, then `>>` through it, then `-i` through it | made; the write is EPERM; `-i` gives "Can't open /rw/rolink: Operation not permitted" |
| `rename` a `rw` file over `strict.pm`, over `/ro/f.txt`; `rename` a `rw` folder over `/ro/sub` | EPERM |
| `-pi -e` on `/usr/local/lib/perl5/5.42/strict.pm`; `-pi.bak` on `/ro/f.txt`; `-pi` on `../ro/f.txt` with `cwd=/rw` | "Can't do inplace edit … Cannot make temp name: Operation not permitted" or "Can't open … Operation not permitted"; exit 0; nothing changed |
| `open "+<"` on `/ro/f.txt`, then print and close; `open "+<"` on `strict.pm` | EPERM |
| `truncate`, `utime` on `strict.pm`; `utime(undef, undef, …)`; `utime` on `/ro/`; `utime` on a read handle | EPERM; the handle form says "futimes function is unimplemented" |
| `link` from the library into `rw`; `link("/rw/../outside/secret.txt", …)` | EPERM |
| Other spellings: `/usr/./local/lib/perl5/5.42/Evil.pm`, `//usr/…`, `…/5.42/../5.42/Evil.pm`, `/./ro/new.txt`, `/rw/../ro/new.txt`, `//ro//new.txt` | ENOENT or EPERM |
| `chdir("/rw"); chdir("..")`, then relative `outside/secret.txt`, `ro/new3.txt`, `rw/new4.txt`; `chdir("/")`, then `>x` | `chdir("..")` EPERM; every relative open fails; no file was made |
| `stat("/rw/../outside/secret.txt")` | EPERM |

## Findings

| # | Rank | Where | Finding | Suggested fix |
|---|---|---|---|---|
| 1 | Medium | `tests/escapes/test_escapes.py:69-81`, used at `:256` and `:267` | `tree(folder)` walks `folder.rglob("*")`, which leaves out `folder` itself. A change to the grant root's own mode or time would not be caught. That includes `utime(1, 1, "/ro")` (the `utime-dir` row, `:234`). That row rests only on the guest's word, not on the host's state. | Add `folder.lstat()`'s mode and `st_mtime_ns` as a first row `(".", …)` in `tree()`. |
| 2 | Medium | `:114-115`, `:254-255` | The errno texts are checked more weakly than Decision 1 says and than the guide checks. Its read-only check wants EPERM (63) on every row. Here, check 1 tests ENOENT only for `passwd`. But every row there (`/outside/…`, the host path, `/tmp/x`, relative, `opendir("/")`) gives "No such file or directory" (EXECUTED above). Check 6 tests EPERM on 8 of its 21 rows, yet the landing notes record EPERM on every row except `chmod` (ENOSYS) and `write-read-handle` (EBADF). | Assert "No such file or directory" on every row of check 1. Assert "Operation not permitted" on every row of check 6, except the two named ones, each with its own text. |
| 3 | Medium | `:277-294`, `:383-405`, `:119-135` | Some host-side "unchanged" checks are missing. `test_nothing_is_writable_without_a_grant` checks no host state at all. It neither hashes the library before and after the `strict.pm` append and `lib-new`, nor checks for a stray `x`. `test_a_writable_grant_cannot_plant_code_in_the_library_path` never hashes `perl_set.lib`. The `..` check looks at `outside`'s names, not its bytes, and not at `tmp_path`'s top-level listing. | Wrap each of these in `_digest_tree(perl_set.lib)` and `tree(outside)` before and after. Assert that `sorted(host.iterdir())` still lists only the folders the fixture made. |
| 4 | Medium | `:55-61`, `:326`, `:349`, `:405`, `:253` | A guest that stops early can pass. `lines()` asserts `trapped is None` but never `exit_code == 0`. `refused(seen, *seen)` in the library and planted-code checks has no row count, so a guest that dies after its first row still passes. `len(seen) in (21, 22)` lets the Fcntl build lose a row unnoticed. | Assert `done.exit_code == 0` in `lines()`. Give each check an exact expected set of row names (`assert set(seen) == {...}`), with the `sysopen` row in the set only when Fcntl is present. |
| 5 | Low | `:435-455` | The FIFO xfail is honest, since I saw the child really block. But `strict=True` without `raises=` reads any failure as the expected one: an import error in the child, a non-zero exit, a changed `Artifacts` API. A broken harness would stay "XFAIL" forever. | Raise a named exception (for example `class BlockedInHost(Exception)`) in the `TimeoutExpired` branch, and mark it `xfail(raises=BlockedInHost, strict=True)`. Optionally add a regular-file control row that must return `opened`. |
| 6 | Low | `:383-405`, `:99-116` | Two of the guide's checks are not ported. Its planted-code check writes through other spellings (`/usr/./local/lib`), and its sealed check lists `/..`. Neither is here. Both are refused today (EXECUTED above). | Add `/usr/./local/lib/perl5/5.42/Evil.pm`, `//usr/local/…` and `…/5.42/../5.42/Evil.pm` to the plant rows, and `opendir("/..")` to check 1. |
| 7 | Low | `:263-274` | The in-place check covers only `/ro`. `-i` on the library, and `-i` through a guest-made link from `rw` into `ro`, are not checked. Both are refused today (EXECUTED above). | Add both as rows, with a library hash and `tree(ro)` before and after. |
| 8 | Low | `:441-455`; spec Decision 4 | The guide checks a FIFO open both for reading on a read-only grant and for writing on a writable one. The port checks only the first. | Parametrize over `writable` (open `>` on the writable grant), both under the same xfail. |
| 9 | Low | spec, "The perl.wasm they ran against" | The landing notes say the sealed copy's `SHA256SUMS` had 1,625 lines. The same `write_sums.py` over the same set gives 1,624 here. | Re-run it and record the count, or name the extra file. |

## On the owner's bar

All three are tested with real Perl under the real `perl.wasm`, and they pass: reads outside a grant, `../` climbs, and writes to a read-only grant. Links out are tested too, both host-made and guest-made, including the trailing-slash rename. Each named check also compares host state. The exceptions are the gaps in findings 1 and 3.

Nothing is skipped silently. Without a set, every check skips with spec 001's message. The Windows skips name their reason. The one row that drops out (`sysopen` without Fcntl) is recorded in the landing notes. The FIFO xfail states the wanted behaviour, and I confirmed the block it records. The README's "Not protected" names both the FIFO block and the guest-made link left behind. The `_engines` `atexit` change is small and matches wasi-python's `release_at_exit`.

Verdict: Lands. No escape was found by the branch's checks or by mine. The four Medium findings strengthen assertions that already pass. Fix them, or answer them in writing, in one round before the merge.

## Round 2

I re-reviewed fix commit `6fbe4e8` on `spec/005-escapes`, by the diff `git diff a1f679c..6fbe4e8 -- tests/escapes context/specifications/005*`. I left out the 004 merge (`bc64277`), which belongs to 004's own reviewer. Fresh worktree, 2026-11-24.

### The run (EXECUTED)

I sealed a fresh scratch copy of 001's set with `scripts/write_sums.py`. It gave 1,624 lines, with no `MANIFEST`, and `sha256sum -c` passes.

```
$ WASI_PERL_ARTIFACTS=$PWD/build/artifacts uv run pytest -q -rsx tests
XFAIL test_escapes.py::test_a_fifo_in_a_grant_is_refused
XFAIL test_perl.py::test_exit_256_is_0, test_exit_minus_1_is_255   (004's, not this review's)
169 passed, 1 skipped, 3 xfailed, 4 warnings in 33.60s
$ WASI_PERL_ARTIFACTS=/nonexistent uv run pytest -q tests
149 passed, 24 skipped, 4 warnings in 9.60s
$ uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
All checks passed!  20 files already formatted
```

These counts match the author's revised landing notes. My ruff run reports 20 files, theirs 23, because they ran over more folders.

### Each finding

| # | Was | Now |
|---|---|---|
| 1 | Medium | **Fixed.** `tree()` now starts with a `"."` row holding the folder's own mode and mtime. `_digest_tree()` is built on `tree()`, so the library and `dev/` digests also cover modes and times. |
| 2 | Medium | **Fixed.** `expect()` checks the text of every row. Check 1 wants "No such file or directory" on all 7 rows, the new `/..` row included. Check 6 wants "Operation not permitted" on every row except `chmod` (ENOSYS) and `write-read-handle` (EBADF). The guest-made link rows each want the text this run gives. The six plant rows accept either ENOENT or EPERM. That is reasonable: `/usr/./local` stops at ENOENT, as my round-1 probe saw, and both answers are the wall's refusal. |
| 3 | Medium | **Fixed.** The no-grant check compares the library and `dev/` digests and `tmp_path`'s tree. The plant check compares the library digest and `ro`'s tree, and wants `rw` to hold exactly `Evil.pm` and `w.txt`. The `..` check compares the trees of `outside` and `ro`, and the top-level listing. |
| 4 | Medium | **Fixed.** `lines()` asserts `(exit_code, trapped) == (0, None)`. `expect()` asserts the exact set of row names. The read-only check counts 22 rows, or 23 with Fcntl. The row names come from `READ_ONLY` itself, and the `sysopen` row is added only when Fcntl is present. |
| 5 | Low | **Fixed.** The check is now `xfail(raises=BlockedInHost, strict=True)`, and only the child's timeout raises it. A control child on a regular file must print `opened` first. Anything else is a real failure: an import error, a non-zero exit, or a failed control. A future fix that refuses the open would XPASS, which `strict=True` turns into a failure, so the marker gets removed. |
| 6 | Low | **Fixed.** The three other spellings are plant rows, and `opendir("/..")` is a row of check 1. |
| 7 | Low | **Fixed.** The in-place check now covers `-pi` and `-pi.bak` on `/ro`, `-pi` on `strict.pm`, `-pi` on `../ro/f.txt` from `/rw`, and `-pi` through a link the guest made. Each must say "Operation not permitted" on stderr. Afterwards `ro`'s tree and the library's digest must be unchanged. |
| 8 | Low | **Answered, accepted.** The author did not add a FIFO open for writing. They argue it is the same blocking host `open(2)`, and the spec labels that INFERRED. The one xfail names the gap, and the planned `path_open` fix covers both directions. |
| 9 | Low | **Answered, accepted.** Their sealed copy also held a `MANIFEST`. Mine had none, which accounts for the difference of one line. |

New code I checked in passing:
- `want["inet"] = ""` requires only a refusal, which is right for a row that this build never reaches.
- `null-read` is `!defined <$n>`, so the row is `ok` exactly at end of file.
- The `dataclasses.replace()` that drops the `absent` lines works on `Completed`, since the suite passes.

I have no new findings.

Verdict: Lands. All four Medium findings and Lows 5, 6, 7 and 9 are fixed and pass against the real `perl.wasm`. Low 8 is answered in the specification.
