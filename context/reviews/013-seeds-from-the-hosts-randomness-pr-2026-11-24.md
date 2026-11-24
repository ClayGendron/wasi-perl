# Review: 013, seeds from the host's randomness (PR #21, 2026-11-24)

Reviewer: an agent (Claude Opus 5.5, `claude-opus-5-5`), a fresh sub-agent of https://claude.ai/code/session_01ThzDEUwftdmy8zWBJK1gWa; not the author of any file in this change.

- **Scope:** `git diff origin/dev...HEAD` on `spec/013-seeds-from-the-hosts-randomness` at `9f54811`: 7 files, +885 −62. I read the specification whole, with decision 7 and its landing notes, specs 009 and 010 for the table's rules, `recipes/wasi-fd-table.c` whole, `recipes/wasi-cc`, both test files, `docs/sandbox.md` and `CHANGELOG.md`. I checked decision 7 against `build/target/util.c` 4730, `doio.c` 577 and `perlio.c` 155–200, and against wasi-libc's `__header_fcntl.h` (READ).
- **Build:** `build/artifacts/perl.wasm` is newer than both recipes, the tree is clean, and its SHA-256 is `3403447480e6…ff8d`, the hash the landing notes give (EXECUTED: `ls --time-style=full-iso`, `git status --porcelain`, `sha256sum`). I did not rebuild.
- **Probes:** in `/tmp/claude-0/-home-user-wasi-perl/ecbe2564-8e44-50cd-9bc9-1c1b283f37e5/scratchpad/probes-013/`. `cmp.py` runs one program through `wasi_perl.run` (one writable grant at `/g`, `cwd=/g`, `-w`) and through native perl 5.42 (`WASI_PERL_ORACLE`) in a fresh folder of the same shape, and prints both. Programs that change names under `/dev` ran in the guest only: natively, as root, they would change the host's own `/dev`.

## Findings

### High

**H1. A `sysread` of more than 64 MiB from the device traps the guest.** `recipes/wasi-fd-table.c:316-333` (`random_read`, the call at `:326`).
- `random_read` hands each iovec to `__wasi_random_get` whole, up to 4 GiB.
- wasmtime refuses a `random_get` longer than 64 MiB with a trap, not an errno. The call ends `trapped`, and its output is lost.
- EXECUTED (`p9-limit.py`, `memory_bytes=2**31`):
  - `sysread($f, $b, 67108864)` prints `67108864`;
  - `sysread($f, $b, 67108865)` ends `trapped='1: requested len 67108865 exceeds limit 67108864'`, with empty stdout;
  - `sysread` of 100,000,000 bytes traps the same way;
  - buffered `read` of the same sizes works, since PerlIO reads in pieces;
  - native perl's `sysread` of 100,000,000 bytes from `/dev/urandom` returns 100000000.
- Decision 2 says reads fill every buffer, never short. `AGENTS.md` says refusals are clear, not crashes. This is the only new way the change gives Perl code to trap the guest, and a `sysread` from a regular file of the same size does not trap.
- A second reason to split the call: a host function runs where the runner's epoch deadline cannot reach (the reason for `sliced_poll`). One 64 MiB `random_get` is a short host call today, but its length is the guest's choice.
- **Fix:** in `random_read`, call `__wasi_random_get` in pieces of at most 64 KiB, in a loop in WebAssembly. Add a check that `sysread` of 64 MiB + 1 from `/dev/urandom` returns 67108865 and does not trap, with the memory limit raised for it.

### Medium

**M1. Perl types a handle opened with flags 0 as read-write, so decision 7's "reads as natively" holds only for the bytes read.** `recipes/wasi-fd-table.c:1063-1100` (`__wrap_open`, `__wrap_openat`), with Perl's `doio.c:577` and `perlio.c:155-175` (READ).
- The wrap fixes the open. Perl decides the handle's type before that, from the flags the program gave. `PerlIO_intmode2str` compares `rawmode & O_ACCMODE` with `O_RDONLY`. Under wasi-libc, `O_RDONLY` is `0x04000000`, so flags 0 fall to its `default:` case: `IoTYPE_RDWR`, mode `r+` (or `a+` with `O_APPEND`), and `writing` set.
- EXECUTED (`p3-accmode.pl`, guest against native):
  - `sysopen(F, "f.txt", 0); print F "x"` is true in the guest, and the later `close F` fails with "Bad file descriptor". Natively the print is false at once, with the warning "Filehandle F opened only for input", and the close succeeds. So `close F or die`, after a stray print, dies only in the guest.
  - `sysopen($f, "f.txt", O_APPEND)` reads 0 bytes in the guest and 3 natively. Perl's `a+` mode seeks to the end on open.
  - The same opens with `O_RDONLY` written out behave as natively (`p4-explicit.pl`).
- `test_an_open_with_no_access_mode_reads` uses only `syswrite`. Perl gives the same `$!` there either way, so the check cannot see this.
- Perl's own seed is not affected: it uses `PerlLIO_open` and `PerlLIO_read`, not PerlIO.
- **Fix:** one of these two.
  - Preferred: extend the rule to Perl's own reading of the flags with a recipe-level wrap, as the refusals wrap Perl functions. `PerlIO_intmode2str` is defined in `perlio.c` and called from `doio.c`, another object, so `--wrap=PerlIO_intmode2str` reaches that call. The wrapper adds `O_RDONLY` when the access bits are zero, as `__wrap_open` does. Add check lines for the `print`/`close` pair and for an `O_APPEND`-only read, compared with native.
  - Or: amend decision 7 and `docs/sandbox.md` to name the difference, and add those two lines to the check as the guest's documented answers.

### Low

**L1. `fcntl(F_GETFL)` on the device does not read back `O_APPEND` or `O_NONBLOCK`.** `recipes/wasi-fd-table.c:415-440`.
- `fd_fdstat_get` answers `fs_flags = 0`, and `fd_fdstat_set_flags` drops what it is given. That is what decision 2 says ("succeeds and changes nothing"), but natively the flags read back.
- EXECUTED (`p10-flags.pl`): after `open(my $a, ">>", "/dev/urandom")`, `O_APPEND` reads 0 (native 1). After `$f->blocking(0)`, `$f->blocking` is 1 (native 0), and `O_NONBLOCK` reads 0 (native 1).
- **Fix:** keep the flags in the entry's `fdflags` field, which already exists, set them at open and at `fd_fdstat_set_flags`, and report them in `fs_flags`. Or list the difference in `docs/sandbox.md`.

**L2. Calls that name the device by a longer path, or change it by name, get the host's answer, and the docs say otherwise for `utime`.** `recipes/wasi-fd-table.c:259-285` (`device_at`); `docs/sandbox.md:85`.
- EXECUTED (`p1-paths.pl` against native; `p2-mutate-guest-only.pl` in the guest only):
  - `/dev/urandom/x` and `/dev/urandom/..`: `open`, `stat` and `-e` answer ENOENT. Natively they answer ENOTDIR. (`urandom/`, `urandom/.` and `urandom/` with `opendir` answer ENOTDIR, as decided.)
  - `readlink "/dev/urandom"` answers ENOENT. Natively it answers EINVAL.
  - `mkdir`, `rmdir`, `unlink`, `rename` (both ways), `link`, `symlink` and `utime` by name answer EPERM, from the read-only `/dev` grant. `/dev` still lists `.`, `..` and `null` afterwards.
  - `utime` on a handle dies with "The futimes function is unimplemented". So no Perl `utime` answers EINVAL, though `docs/sandbox.md:85` says it does.
- None of this reaches a host file or widens the sandbox. But the specification does not decide these answers, and the docs do not list them.
- **Fix:** correct the `utime` sentence in `docs/sandbox.md`, and add one line there for the rest: calls that name the device by path, other than open and stat, get the host's answer for a read-only folder with no such file. Optionally, make `device_at` answer ENOTDIR when a component follows the device's name, as Linux does.

**L3. Decision 2 and the code disagree on two points that the landing notes record as choices.** `013-seeds-from-the-hosts-randomness.md:44`; code at `recipes/wasi-fd-table.c:415-427` and `:213-237`.
- Decision 2 says the base rights include `FD_READ` and `FD_WRITE`. The code reports each only as the open asked, so `F_GETFL` reads back the open's mode and a read of a write-only open fails with EBADF. That is the better answer, and it matches native.
- Decision 2 says "everything else answers EINVAL". The code answers ENOTSOCK for the socket calls and EBADF for the preopen calls.
- `AGENTS.md` wants the specification to decide every choice. A landing note is not a decision.
- **Fix:** amend decision 2 with these answers, in the same commit as the fixes.

**L4. Two of decision 6's controls have no check.** `tests/build/test_randomness.py:133-156`.
- The checks cover `PERL_HASH_SEED`, `PERL_HASH_SEED=0` and `srand(N)`. Nothing runs `PERL_PERTURB_KEYS` or `PERL_INTERNAL_RAND_SEED`.
- EXECUTED (`p5-seeds.py`, `p6-internal.py`, three calls each): both behave as natively. With `PERL_HASH_SEED` set, `PERL_PERTURB_KEYS=0` and `=2` pin the key order, and `=1` does not. `PERL_INTERNAL_RAND_SEED` changes nothing that is visible, guest or native. Without `PERL_HASH_SEED`, three calls give three seeds, three `rand` values and three key orders.
- **Fix:** add the `PERL_PERTURB_KEYS` lines to check 5. Or say in the specification that decision 6 is checked only for the controls named in check 5.

Aside, not part of this change: with `PERL_HASH_SEED=1`, the guest's key order differs from native's (`n,g,m,p,…` against `k,e,i,d,…`). The base build, perf-base, gives the same guest order, so this change did not cause it. The hash function and the seed bytes agree (`PERL_HASH_SEED_DEBUG`). With `PERL_HASH_SEED=0` and `=12345678` the orders agree. It may be worth a line in a memo.

## What holds

- **No call hands the sentinel to the host.** I read every use of `.real` and every `__real___wasi_*` call (`grep -n '\.real\|__real___wasi\|TO_REAL'`):
  - each `PASS_FD` wrapper, and each path call with the device as its folder, returns through `TO_REAL_OR` first, with ENOTDIR for a folder;
  - `poll_oneoff` decides `device` before it stats `.real` or copies it into the host's subscriptions;
  - `find_held` and the held-file rename touch only `reopenable` entries, which the device never is;
  - `random_stat` stats only the `/dev` grant, which is never freed;
  - `release()` skips the host for the device.
- **The table holds** (EXECUTED, `p7-table.pl`, the same as native line for line, except the lines noted):
  - `POSIX::dup`, then close of the original: the copy still reads random bytes, and the next open takes the freed number and reads the file;
  - `POSIX::dup2` of the device onto a file's number: reads are random, and `-c` and size 0 follow it;
  - `F_DUPFD` to 40 and up, `F_GETFD` and `F_SETFD`, `sysseek` to the end, `eof`, `readline`;
  - `open STDIN, '<', '/dev/urandom'` and back, and `open STDOUT, '>', '/dev/urandom'` and back. The print is dropped.
  - `IO::Select`, four-argument `select` and `IO::Poll` with the device beside a regular file and a write-only file. The only differences are `/dev/random`'s write readiness, which the docs list.
  - A closed device number in a select set gives EBADF, as natively.
  - `close` makes no host call (`test_a_close_of_the_device_makes_no_host_call`, READ and run).
- **The device cannot be reached by any other path** (EXECUTED):
  - `/dev/../dev/urandom` answers EPERM (the host refuses the climb);
  - `/dev/URANDOM` and `/dev/urandomx` answer ENOENT;
  - `chdir "/dev"`, then `urandom` and `./random`, open the device, as natively;
  - a grant's symlinks `abs -> /dev/urandom`, `rel -> ../dev/urandom`, and `dev/urandom -> /dev/urandom` inside a grant are refused by the host (EPERM);
  - the runner refuses a grant at `/dev`, `/dev/x` or `/dev/urandom`, and `cwd="/dev"` (ValueError, by `_grants._reserved` and `check_cwd`). So exactly one preopen is named `/dev`, and `is_dev` and wasi-libc agree on it.
  - From Perl there is no `openat` with a folder descriptor: `chdir($fh)` dies, since `fchdir` is unimplemented. A folder descriptor opened on `/dev` other than the grant's own sends `urandom` to the host, which has no such file (READ: `device_at` compares the virtual number with `devdir`).
- **The access-mode rule changes only opens that failed.** wasi-libc's `O_ACCMODE` is `O_EXEC | O_RDWR | O_SEARCH`, and it refuses an open whose access bits are zero with EINVAL (READ: `__header_fcntl.h:18-37`). The wrap touches only that case. There is no `O_PATH`, and `O_EXEC` and `O_SEARCH` are access bits, so they pass unchanged. EXECUTED (`p3-accmode.pl`):
  - on perf-base, every open with zero access bits failed with EINVAL: flags 0, `O_CREAT`, `O_CREAT|O_EXCL`, `O_DIRECTORY`, `O_APPEND`, `O_NONBLOCK`, and a missing file;
  - `O_RDONLY`, `O_WRONLY` and `O_RDWR` give the same answers on the branch, on perf-base and natively;
  - with `O_RDONLY` written out, `O_CREAT` (opened read-write), `O_DIRECTORY` (a read answers EBADF, natively EISDIR) and `O_APPEND` answer the same on the branch as on perf-base (`p4-explicit.pl`). So those differences from native come from wasmtime, not from the wrap.
  - The mode argument is read only with `O_CREAT`. That is safe, since wasi-libc ignores the mode.
- **Perl's seeds come from `random_get`.**
  - `test_the_seeds_come_from_random_get` and `test_perls_seed_reaches_the_device` trace them through a linker whose `random_get` is fixed. The landing notes record both failing on the device-only build.
  - Under the real runner, three calls give three `rand` values, three hash seeds and three key orders. `PERL_HASH_SEED=0` and `=12345678` pin the seed and the order, as natively (EXECUTED, `p5-seeds.py`).
- **Decisions 1–7 against the code.** Each is in the code, apart from the points in H1, M1, L1 and L3:
  - 1: the device, with no wrap of `Perl_seed`;
  - 2: the paths, `.` and empty parts dropped; the sentinel; the open's ENOTDIR and EEXIST; reads, writes, seeks and stat; waits; the EINVAL and ENOTDIR answers; `fd_renumber` onto a host descriptor answers EINVAL;
  - 3: the FIFO named `urandom` answers ENXIO;
  - 4: `dev/` still holds one empty `null`;
  - 5: one import, `random_get`, and the header comment corrected;
  - 6: Perl's controls;
  - 7: the wrap.
- **No test was weakened.**
  - The test diff is +427 −0 lines (`git diff --numstat`).
  - `tests/perl_t/exclusions.toml` is untouched.
  - No code comment names a specification or review.
  - Every commit carries the model's trailer.
- **The checks would fail on regression.**
  - The `random_get` probe fails with clock seeds.
  - The close probe compares host closes with the device and without it.
  - The import check compares the exact list.
  - The 20-seed and `PERL_HASH_SEED` checks are controls, and the landing notes say so.
  - The gap: no check reads more than 1 MiB at once (H1), and none writes to a flags-0 handle (M1).
- `CHANGELOG.md` names the fix under "Unreleased", Security. `docs/sandbox.md` has the Randomness section and the two differences acceptance 3 asks for.

## Tests run

All EXECUTED in the worktree, with `WASI_PERL_ARTIFACTS=$PWD/build/artifacts` and `WASI_PERL_ORACLE=/home/user/wasi-perl-wt/perf-base/build/native-install/bin/perl`:

```
$ uv run pytest -m "not slow" -q
445 passed, 1 skipped, 244 deselected, 4 warnings in 20.76s
$ uv run pytest tests/build tests/escapes -q
442 passed in 49.33s
$ uv run ruff check src tests && uv run ruff format --check src tests
All checks passed!
30 files already formatted
$ uv run pytest tests/runner tests/escapes tests/oneliners -q -ra
649 passed, 1 skipped, 4 warnings in 58.55s   # the skip: test_arguments.py:208, root can write every folder
$ flock /home/user/wasi-perl-wt/machine.lock uv run python tests/perl_t/run_suite.py --quiet --list-failures --jobs 4
perl t/: 616 run, 439 passed, 177 excluded (subprocess 143, signals 11, refused 2, porting 6,
platform 6, not-this-platform 6, bug 3), 0 failed, 0 stale exclusions, 0 regressed (19052 guarded oks)
```

The probes are listed under each finding. The one timed probe, `p8-bigread.py`, ran under the machine lock and served only to find H1's trap. It reports no speed number.

## Verdict

Does not land, for H1 alone: a `sysread` larger than 64 MiB from the new device traps the guest, where decision 2 promises a full read. The fix is a loop of a few lines in `random_read` and one check.

The rest is sound. The sentinel never reaches the host. dup, dup2, close and reuse, poll and select, and every path trick I tried behave as natively or are refused by the host. The access-mode wrap changes only opens that failed before, and Perl's seeds are traced to `random_get`.

M1 should be fixed in the same round, by the `PerlIO_intmode2str` wrap or by a written difference. The Lows can be fixed or answered in writing. A second round that sees H1's check pass should land.

## Round 2

- **Scope:** `git diff 9f54811..e317084`, one commit: 8 files, +309 −52 lines. I read every line of it. `build/artifacts/perl.wasm` is newer than all three changed recipes. Its SHA-256 is `daa7cf80…c6bbe7`, the hash the revised landing notes give (EXECUTED: `ls --time-style=full-iso`, `sha256sum`). I reran my round-1 probes on that build, with the same harness and folder as before.

### The findings, checked

- **H1, fixed.** `random_read` now asks `random_get` for at most 64 KiB at a time (`RANDOM_PIECE`), in a loop in WebAssembly. The pointer arithmetic on `iovs[i].buf + done` and the `fits` cap on the total are right (READ).
  - EXECUTED (`p9-limit.py`): `sysread` of 67108864, 67108865 and 100000000 bytes each returns its length, with nothing trapped. So does buffered `read`.
  - The new checks would fail on the old code. `test_a_big_read_is_filled_whole` compares with native. `test_a_big_read_of_the_device_does_not_trap` runs under `run()`'s default limits.
- **M1, fixed by the preferred route.** `--wrap=PerlIO_intmode2str`, with its wrapper in `recipes/sandbox-refusals.c`, adds `O_RDONLY` when the access bits are zero. There is no Perl patch.
  - I read the other two callers in `perlio.c`, which the wrap does not reach. At 246, `PerlIO_fdupopen`'s non-layers branch passes `fcntl(F_GETFL)`, whose access bits wasi-libc always sets. At 5491, mkostemp passes explicit modes. Neither needs the wrap.
  - EXECUTED (`p3-accmode.pl`): the guest now equals native on every line, warning and file, except two lines that were already there. Those two are `O_CREAT` opening read-write and a read of a folder answering EBADF; round 1 showed both come from wasmtime and are the same on perf-base.
  - `print F` after `sysopen(F, $p, 0)` is false, with "Filehandle F opened only for input", and `close` is true. `O_APPEND` alone reads 3 bytes.
- **L1, fixed.** The entry keeps its `fdflags` from the open and from `fd_fdstat_set_flags`, and `fd_fdstat_get` reports them. A dup shares them, as an open file's status flags are shared natively.
  - EXECUTED (`p10-flags.pl`): equal to native, `append flag 1`, `blocking was 1 now 0`, `nonblock flag 1`.
- **L2, fixed and documented.**
  - `device_at` now flags any component after the device's name, so `urandom/x` and `urandom/..` answer ENOTDIR to open and stat.
  - A new `path_readlink` wrapper answers EINVAL for the device and ENOTDIR through it. It still answers ENOTDIR when the device descriptor is itself the folder (READ).
  - EXECUTED (`p1-paths.pl`): every line equals native, except `/dev/../dev/urandom`, which the host refuses (decided, unchanged).
  - EXECUTED (`p2-mutate-guest-only.pl`, the guest only): the changes by name answer EPERM, `utime` on a handle dies, and `/dev` still lists `.,..,null`.
  - `docs/sandbox.md` now says so, and its wrong `utime` sentence is corrected.
- **L3, fixed.** Decision 2 now states the rights as the open asked, EBADF for the wrong direction, ENOTSOCK, EBADF for the preopen calls, EINVAL for `fd_fdstat_set_rights`, and the path calls. I checked each against the code (READ).
- **L4, fixed.** `test_perturb_keys_and_the_internal_seed_work_as_natively` runs `PERL_PERTURB_KEYS=0`, `=1` and `=2`, and `PERL_INTERNAL_RAND_SEED=7` and `=8`, three calls each, in the guest and natively.
- **The table, again.** EXECUTED (`p7-table.pl`): dup, dup2, reuse after close, `F_DUPFD`, the STDIN and STDOUT reopens, select and poll give the same lines as in round 1. Every difference from native is one round 1 explained.
- **No test was weakened.** The 21 lines removed from `test_randomness.py` are the old body of `test_an_open_with_no_access_mode_reads`. It moved into `_against_native`, and it is stronger now: it runs with `-w`, compares stderr too, and adds three lines. `tests/perl_t/` is untouched.

### The changed L4 check

The question: the implementer first compared the pinned key order with native's, then compared only that the order is pinned. Is that fair, or does it hide something?

It is fair. It hides nothing this change caused. EXECUTED (`p11-order.py`, guest against native, full key lists, two programs, seeds `0`, `1` and `12345678`, with `PERL_PERTURB_KEYS` unset, `0` and `2`):
- With `PERL_PERTURB_KEYS=0`, and with `PERL_HASH_SEED=0` (which forces that mode), the guest's order equals native's for every seed.
- With `PERL_PERTURB_KEYS=2`, and with a nonzero seed and the mode unset (which defaults to 2), it differs from native's for every seed.
- perf-base, built before the device, gives exactly the same SAME and DIFF table. So the difference is in Perl's DETERMINISTIC key-order mode on this target, not in where the seed comes from.

The check, as changed, still catches what this change could break:
- seeds leaking into a pinned mode, because the order would vary across calls;
- `=1` no longer randomising, because one order would repeat;
- the internal seed changing the order.

And it asserts native's own pinning alongside the guest's. Comparing the order in mode 2 with native would test an old difference that this specification does not touch. The specification's revised notes record that difference with its evidence.

Two small things remain, neither blocking:
- **R2-L1.** The check could keep the native comparison where the orders agree: mode 0 and `PERL_HASH_SEED=0`. They agree on both builds, so that line would guard what does match without testing the known difference. **Fix:** compare the order with native's for `PERL_PERTURB_KEYS=0`.
- **R2-L2.** The known difference is written only in the specification. `docs/sandbox.md` still says `PERL_HASH_SEED` and `PERL_PERTURB_KEYS` "work as natively", but a user who pins the seed to match native perl's key order gets another order in the default mode. The specification describes it as "for some seeds". It is really every nonzero seed in mode 2. **Fix:** add a line under "Differences from native that remain": with `PERL_HASH_SEED` fixed, the order is pinned on every call, but it equals native's only with `PERL_PERTURB_KEYS=0` or `PERL_HASH_SEED=0`. Correct the specification's wording to match.

### Tests run, round 2

All EXECUTED in the worktree at `e317084`, with `WASI_PERL_ARTIFACTS=$PWD/build/artifacts` and `WASI_PERL_ORACLE=/home/user/wasi-perl-wt/perf-base/build/native-install/bin/perl`:

```
$ uv run pytest -m "not slow" -q
445 passed, 1 skipped, 245 deselected, 4 warnings in 20.49s
$ uv run pytest tests/build tests/escapes -q
448 passed in 53.15s
$ uv run ruff check src tests && uv run ruff format --check src tests
All checks passed!
30 files already formatted
$ uv run pytest tests/runner tests/escapes tests/oneliners -q
650 passed, 1 skipped, 4 warnings in 57.72s
$ flock /home/user/wasi-perl-wt/machine.lock uv run python tests/perl_t/run_suite.py --quiet --list-failures --jobs 4
perl t/: 616 run, 439 passed, 177 excluded (subprocess 143, signals 11, refused 2, porting 6,
platform 6, not-this-platform 6, bug 3), 0 failed, 0 stale exclusions, 0 regressed (19052 guarded oks)
```

### Verdict, round 2

Lands. H1, M1 and L1–L4 are fixed, each with a check that would fail on the round-1 code. Every probe from round 1 now gives native's answer, or the decided and documented one. The suites are green. Changing the L4 check was fair: the native comparison it dropped tests a key-order difference in Perl's DETERMINISTIC mode that perf-base shows too. R2-L1 and R2-L2 can follow in a later change, or be fixed before the merge; neither blocks.

## After round 2 (the orchestrator)

- **R2-L2, fixed** in the commit before this one, text only. `docs/sandbox.md` now says a fixed hash seed pins the order. The order equals native's only with `PERL_HASH_SEED=0` or `PERL_PERTURB_KEYS=0`, and differs in the default deterministic mode with any other seed. The specification's aside says the same.
- **R2-L1, deferred.** Keeping the comparison with native in mode 0 adds a check. It is recorded here for the next change to `tests/build/test_randomness.py`. It does not block, as the reviewer says.
