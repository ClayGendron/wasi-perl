# 015 — The runner's checks, kept across calls

- **Status:** agreed by the owner 2026-11-24 ("Agree all four (Recommended)"). Written by the orchestrator; amended the same day for the review `../reviews/perf-research-and-specs-013-016-pr-2026-11-24.md` (H2, M3, M4, L10). Decision 3 was dropped while it was built, and the owner agreed on 2026-11-24 ("Drop decision 3 (Recommended)": every new process hashes the library, as today; a first call is about 73 ms).
- **Owner:** Clay Gendron
- **Kind:** `src/wasi_perl/` (`_grants.py`, `_artifacts.py`, `_cache.py`, `runner.py`), checks in `tests/runner/` and `tests/escapes/`.
- **Depends on:** 014.
- **Research it stands on:** `../research/2026-11-24-perf-runner.md` (study A), its levers L5 and L6, its §4.1 and §4.2, and the patches in its evidence folder.

## Intent

Two checks of specification 012 redo the same work on every call. Each was right to do so until there was a way to know the work's answer had not changed.

- **The hard-link rule.** No writable grant may hold a second name of a runtime file. It lists and `lstat`s about 3,000 runtime files on every call with a writable grant. That is 19 ms, which takes a writable call to 21–24 ms (EXECUTED in study A, `results/calls.jsonl`, the wheel's layout).
- **The artifact check.** It hashes all 1,455 library files in every new process, about 70 ms of a first call (EXECUTED in study A, `results/first_call.jsonl`).

After this specification, both keep exactly the guarantee they give today:
- a writable call costs about 3 ms (EXECUTED in study A: 2.8–3.3 ms with L1–L8);
- a fresh process's first call costs about 73 ms, against about 104 ms (EXECUTED: `../research/2026-11-24-the-runners-fixed-cost-measured.md` §8.2, session s4). Every library file is still hashed in every new process; only the walk is cheaper (decision 6). Study A's 34 ms needed a record of what was hashed; decision 3 says why it was dropped.

These levers keep a rule by a new argument. That is why they are a specification of their own, with checks for each way the argument could fail.

## Decisions

1. **The runtime files' identities are scanned once and reused while every folder stamp holds (L5).**
   - **What is kept, per process:** the set of `(st_dev, st_ino)` of every regular file under wasmtime-py, `wasi_perl` and the library, and the files `perl.wasm`, `MANIFEST`, `SHA256SUMS` and `dev/null`. Beside it, the stamp `(st_dev, st_ino, st_mtime_ns, st_ctime_ns)` of every folder walked. Each folder is stamped before it is listed.
   - **When it is reused:** only if every folder stamp still holds. Any change rescans.
   - **The argument** (study A §4.1):
     - A file is added, removed or replaced only through an entry of its folder, which moves that folder's mtime and ctime.
     - A write into an existing file keeps its inode, so its identity is still right.
     - Making a second name of a runtime file *inside a writable grant* changes no runtime folder. That is why decision 2 exists.
2. **The writable grants are still walked on every call.**
   - Every file there with more than one name is looked up among the identities.
   - `MAX_WALK`, the refusal of an unreadable folder, and the shortcut when no runtime file has a second name all keep their meaning.
   - Those three decisions need to know whether any runtime file has a second name *now*. A link made inside a grant raises that count without changing any runtime folder, so the cached scan cannot answer it. So each of them `lstat`s every runtime file afresh, at the point it is needed, as study A's prototype does (`RuntimeFiles.linked_now`):
     - when the walk passes the runtime's own size;
     - on an unreadable folder;
     - past `MAX_WALK`.
   - A file that a grant's file matched is read afresh too.
3. **Dropped: a record of what a first call hashed (L6).** Study A kept, in the module cache, each library file's stamp `(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)` under which its hash matched, so a later process would hash only the files whose stamp moved. Its argument was that a write moves a file's mtime and ctime, and no user can set a ctime. A write through a shared, writable mapping does not always move them: Linux stamps a mapped file when a write faults a clean page, and later writes to that page, while it stays dirty, stamp nothing. The probe `../research/2026-11-24-the-runners-fixed-cost-measured/mmap_stamp.py` changed a file's bytes after its stamp had settled, and the stamp did not move after the write, the unmap or a sync, in three runs (EXECUTED, memo §8.4). So whoever can write a library file could change it after a record was made, and every later process would run it unhashed, where today every new process refuses it. The owner dropped the record on 2026-11-24 ("Drop decision 3 (Recommended)"). Every new process hashes every library file, as today.
4. **Coarse file times are allowed for in folder stamps**, as git does for its index: a folder stamp whose mtime or ctime lies within 2 s of the start of the scan that read it is not trusted, and the folder is scanned again on the next call.
5. **Decision 1's reuse of a scan is also Linux and macOS only.** On Windows every writable call scans, as today. There `st_ctime` is the creation time, which the file's owner can set with `SetFileTime`, so a folder's stamp does not show every change of its entries.
6. **Plain string paths in the tree walk.** This takes a full check from 86 ms to 64 ms with no change of rule (study A §4.2). It stands on its own.

## Checks (written first, seen failing; they count operations, never time)

1. **Scans.** Ten writable calls in a row scan the runtime trees once, counted by a patch on the walk. Touching a file in the library (a new file, a removal, a rename over it) makes the next call scan again.
2. **The rule, after a scan.**
   - (a) A hard link to wasmtime's shared library made inside a writable grant *after* several calls is refused on the next call.
   - (b) The same with a link to a library `.pm` file.
   - (c) A runtime file replaced by rename, then linked into a grant, is refused.
   - (d) A grant past `MAX_WALK` is refused exactly when a runtime file has a second name, as today.
3. **Dropped: records.** The checks of decision 3's record (a warm second process hashing nothing; a file changed in place with its mtime restored; a record edited by hand; `cache_dir=False`; a record of another `SHA256SUMS`) go with it, for the reason decision 3 gives.
4. **The shortcut in a copy-mode install.** The check 2(a) is run again in a layout where no runtime file has a second name when the scan is made, such as an install with `UV_LINK_MODE=copy`. The link made later is still refused.
5. **Coarse times.** A folder changed within 2 s of its stamp is scanned again. (The check of a file rewritten within the second of its recorded stamp went with decision 3.)
6. **Windows.** On Windows every writable call scans. A patched platform check verifies it on any system, and CI runs it on Windows.
7. Every check of spec 012 and every escape test passes unchanged.

## Acceptance

1. The checks pass, and every existing suite stays green (EXECUTED).
2. The memo of spec 014 is extended with the writable-call and first-call timings.
3. `docs/sandbox.md` says that every new process hashes the library, as today, and why a kept record of hashed files was refused.
4. `CHANGELOG.md` under "Unreleased".
5. A fresh agent's review, which checks the arguments of decisions 1 and 3 against the code, lands.

## Landing notes

Built 2026-11-24 by the implementer (Claude Opus 5.5) on `spec/015-the-runners-checks-kept-across-calls`, from `dev` at `8b81f84`. Linux x86-64, ext4, Linux 6.18, CPython 3.13.18, wasmtime-py 51.0.0, as root, with perf-base's set (`/home/user/wasi-perl-wt/perf-base/build/artifacts`, perl 5.42.3). **Not yet reviewed.** Each claim carries its label.

### Decision 3 is unsound as argued, so it was not built (the owner dropped it)

**What breaks.** Decision 3 trusts a library file whose stamp matches the one recorded when it was last hashed. Its argument is that a write moves a file's mtime and ctime, and no user can set a ctime. But a write through a shared, writable mapping does not always move them.
- Linux stamps a mapped file when a write faults a clean page. Further writes to that page, while it stays dirty, do not fault, so they stamp nothing (INFERRED, and consistent with the probe below).
- The probe `../research/2026-11-24-the-runners-fixed-cost-measured/mmap_stamp.py` maps a file and writes one byte. It waits 3 s, past decision 4's window, and then reads the bytes and the stamp as a check would. Then it writes the same page again.
- In three runs, the bytes changed each time. The stamp did not move after the second write, nor after the unmap, nor after `os.sync()` (EXECUTED: `uv run python …/mmap_stamp.py build/mmap-probe-$i.pm 3`, memo §8.4).

**What it would allow.**
- Anyone who can write a library file could change it after a check recorded its stamp, and keep the stamp.
- Every later process would trust the record and run the changed file. Today, every new process hashes the file and refuses it.
- The writer needs no access to the record, so "whoever could forge it could already write a `.cwasm`" does not cover this case.
- `inotify` would tell the writer when a check has read the file (INFERRED; not probed).

So the record does not keep "exactly the guarantee they give today", as the intent requires. As instructed, I stopped on decision 3 and did not design a replacement. Decision 4's file stamps, checks 3 and 5(a), and the record half of check 6 go with it.

**The context.**
- Within one process the tree is checked once, so a library writer can already change a file unseen after a process's first call (READ: `_artifacts.checked`). The record would carry that to every later process, with no race to win (INFERRED).
- **A follow-up for the owner, not fixed here.** The same weakness exists within one process. 0.1.0's per-stamp `digest()` of `perl.wasm` and `MANIFEST`, and 014's per-stamp parse of `SHA256SUMS`, are used again while the file's stamp is unchanged (READ: `_artifacts.digest`, `_read_sums`). So a write through a shared mapping, made after a process's first check, would not be seen by that process's later calls (INFERRED from the probe). It predates this change. Within a process the library tree is checked only once anyway (READ: `checked`).

**The owner's answer.** On 2026-11-24 the owner chose option 1 below ("Drop decision 3 (Recommended)"), as relayed by the orchestrator. The Status, Intent, decision 3, checks 3, 5 and 6 and acceptance 3 above are amended in place to say so, and `docs/sandbox.md` says that every new process hashes the library.

**Options put to the owner**, my recommendation first:
1. **Drop decision 3.** Keep decisions 1, 2, 5 and 6 as built. A first call is about 73 ms here, against 104 ms before (EXECUTED, memo §8.2), not the original intent's 34 ms. This is the only option I found that keeps today's guarantee exactly.
2. **Trust a record entry only for a file that no one but this user and root can write.** The test would be: owned by this user or root, with no group or other write bit. Then whoever can use the mapping could also write a `.cwasm`, which is the specification's own trust argument.
   - Linux: the mode's group bits are the ACL mask, so no ACL grants a write past them (INFERRED).
   - macOS: an ACL can grant a write beyond the mode bits, so this test is not enough there (INFERRED).
   - Its limit: a writer that opened or mapped the file while it was still writable keeps that access after a `chmod` (INFERRED).
3. **Keep decision 3 as written**, and state the mapping limit in `docs/sandbox.md`. I do not recommend it.

### What was built

- **Decision 1** (`_grants.RuntimeFiles`, `_grants.runtime_scan`, `runner._runtime_files`): one scan kept per process, keyed by its tops and single files, made again when `fresh()` fails. `fresh()` holds when every folder stamp `(st_dev, st_ino, st_mtime_ns, st_ctime_ns)` is unchanged, and every stamp was settled when the scan took it. Each folder is stamped before it is listed (READ: the code).
- **Decision 2** (`_grants._refuse_second_names`): every writable grant is walked on every call. Each file with more than one name is looked up among the scan's identities, and a match is read afresh. The three shortcuts each `lstat` every runtime file afresh (`linked_now`), only when reached: past the runtime's own size, on an unreadable folder, and past `MAX_WALK` (READ: the code; EXECUTED: the checks below).
- **Decision 4, for folders:** a stamp whose mtime or ctime lies within 2 s of the scan's start is not settled, so the next call scans again.
- **Decision 5:** `_grants.SCANS_KEPT` is `sys.platform in ("linux", "darwin")`. Elsewhere every writable call scans, and no scan is kept.
- **Decision 6** (`_artifacts._check_tree`): names are joined as strings, `rel` is cut from the path, and the hash takes the string. The rules are unchanged.

### Choices the specification left to me

- **Tops are stamped through their links; folders below are stamped without.** A top, or a single file's folder, is stamped by `os.stat`, so the stamp is that of the folder the path names now. If the path comes to name another folder, its identity differs and the scan is made again. Folders below a top are stamped by `lstat`. Study A's prototype used `lstat` everywhere, which stamps a link rather than the folder a linked top leads to (READ: its L5 patch).
- **A folder reached twice is stamped and listed once.** It is keyed by path. In the wheel's layout the library sits inside `wasi_perl`, so the scan reaches it from two tops. 272 folders and 1,543 identities in that layout (EXECUTED).
- **One unsettled stamp means the whole scan is made again,** not only that folder. This is simpler. A scan is about 14 ms, and a stamp settles after 2 s.
- **"Within 2 s of the start"** is read as an mtime or ctime at or after the start minus 2 s. That includes any stamp in the future.
- **"A file that a grant's file matched is read afresh."** On a match, the runtime file's path is `lstat`ed.
  - If the path no longer holds that identity, the runtime is scanned again once in that call, and the identity is looked up in the new scan.
  - So a file that only reuses an inode number is not refused, while a runtime file renamed within its own tree still is.
  - Check: `test_a_match_is_read_afresh_and_a_stale_one_scans_again`.
- **Past `MAX_WALK` with no second name anywhere, the walk stops and the grant is accepted.** Study A's prototype would have raised `StopIteration` (`next(iter({}))`) there, once a runtime holds more files than `MAX_WALK` (READ: its patch). Check: `test_past_max_walk_a_grant_is_refused_exactly_when_a_runtime_file_has_two_names`.
- **Other POSIX systems scan every call.** Decision 5 names only Linux and macOS.
- **How scans are counted.** The checks count the `os.scandir` audit events that name the library folder. `Path.rglob` binds `scandir` when it is imported, so patching `os.scandir` saw none of the old code's listings (EXECUTED: a probe of `rglob` with `os.scandir` patched recorded nothing).
- **The copy-mode layout** (check 4) is a site folder that holds copies of wasmtime-py, its dist-info and `wasi_perl`, used by a child process. It needs no network and no `uv` install. The child asserts that no runtime file has a second name when the scan is made.
- **The clock in the checks.** The checks that need a kept scan move `_grants._now_ns` 10 s forward, since a scratch set made by the check is not yet settled. The coarse-time check pins the clock 1 s, then 3 s, after the folder's change.

### The checks

`tests/runner/test_kept_checks.py`, 17 checks. All 17 failed before the change (EXECUTED: commit `924e045`, `uv run pytest tests/runner/test_kept_checks.py`, "17 failed"; run again for these notes with `924e045`'s `src/` first on `PYTHONPATH`, the same result).
- Five failed with an `AttributeError`, because `RuntimeFiles`, `SCANS_KEPT` or a scan's `files` did not exist yet.
- The copy-mode check failed in its child process.
- The rest failed on their counts (for example "20 == 1").
- One failure is weaker than it looks. The Windows case of check 6 failed only because the old code listed the library twice per call ("20 == 10"). Its rule, a scan on every call, held before the change.

| spec check | test | seen failing |
|---|---|---|
| 1, scans | `test_ten_writable_calls_scan_the_runtime_once`; `test_a_change_in_the_library_makes_the_next_call_scan_again` (a new file, a removal, a rename over it) | yes (ten calls listed the library 20 times; three calls 6 times, against 1) |
| 2(a) | `test_a_link_to_wasmtimes_library_made_after_several_calls_is_refused` | yes, on the scan count; the refusal held before |
| 2(b) | `test_a_link_to_a_library_file_made_after_several_calls_is_refused` | yes, on the scan count |
| 2(c) | `test_a_runtime_file_replaced_by_rename_then_linked_is_refused` | yes, on the scan count |
| 2(d) | `test_a_grant_past_max_walk_after_a_kept_scan_is_refused_while_a_file_has_two_names`; `test_past_max_walk_a_grant_is_refused_exactly_when_a_runtime_file_has_two_names`; `test_an_unreadable_folder_is_refused_exactly_when_a_runtime_file_has_two_names` | yes |
| decision 2, read afresh | `test_a_match_is_read_afresh_and_a_stale_one_scans_again` | yes |
| 4, copy mode | `test_in_a_copy_mode_install_a_link_made_after_the_scan_is_still_refused` | yes |
| 5, coarse folder times | `test_a_folder_changed_within_two_seconds_of_its_stamp_is_scanned_again` | yes |
| 6, Windows scans | `test_scans_are_kept_on_linux_and_macos_only`; `test_on_windows_every_writable_call_scans` (patched both ways) | yes; the Windows case only on the count unit, as above |
| the scan's coverage | `test_the_scan_holds_every_file_of_the_three_trees_and_the_set` | yes |
| 3(a)–(e), 5(a), 6 records | dropped with decision 3 | — |
| 7 | every check of 012 and every escape test, unchanged | — |

On a system that does not keep scans, the kept-scan checks skip, and the patched checks run. CI's Windows wheel job runs `tests/runner`, so it runs them there. I did not see that run.

```
$ WASI_PERL_ARTIFACTS=/home/user/wasi-perl-wt/perf-base/build/artifacts \
  WASI_PERL_ORACLE=/home/user/wasi-perl-wt/perf-base/build/native-install/bin/perl \
  uv run pytest -m "not slow" -q
489 passed, 3 skipped, 242 deselected, 4 warnings
$ (same variables) uv run pytest -m slow -q tests/runner tests/escapes
32 passed, 214 deselected
$ uv run ruff check && uv run ruff format --check
All checks passed!  40 files already formatted
```

All EXECUTED, under the machine lock. Before the change the fast suite gave 472 passed, 3 skipped (EXECUTED). No existing check was changed. `tests/runner/test_kept_checks.py` passed five runs in a row (EXECUTED).

### The specification's numbers, against what was found

The before figures are `dev` at `8b81f84`; see memo §8, session s4.

| the specification says | found | label and command |
|---|---|---|
| a writable call 21–24 ms before | 22.30 ms | EXECUTED, `calls.py` in `measure.sh … s4` |
| a writable call about 3 ms after (study A 2.8–3.3) | **2.67 ms**; `run()` 22,155 → 2,857 µs in `phases.py rw 100` | EXECUTED |
| the scan, 19 ms of a writable call | `_runtime_files` 14,257 → 386 µs and `judge` 5,003 → 524 µs | EXECUTED, `phases.py rw 100` |
| a first call about 73 ms (amended; study A's 34 ms needed decision 3) | **73.2 ms** (median of five), against 104.0 ms | EXECUTED, `first_call.py` |
| string paths: a full check 86 → 64 ms | 78.8 → 45.8 ms (medians) | EXECUTED, `first_call.py` |
| a first call as today without a cache | unchanged: every file is hashed in every process | READ: the code; the record is not built |

### Left open

- Decision 3 is dropped by the owner (above). The in-process per-stamp reuse is a follow-up for the owner.
- The P1 of 014's review (`_cache.folder` and `_grants.hold` as separate steps) bears only on the record, which would be read through `_cache.folder`'s check. With no record, this change does not touch it. Had the record been built, P1 would leave it no weaker than a `.cwasm` (INFERRED): a concurrent guest could rewrite either in place, and a record without a tampered library gains nothing.
- The 2 s window assumes the file system's clock is this machine's. A network file system whose server's clock lags by more than 2 s, with coarse times, could show a change made within one tick of a stamp as settled (INFERRED). That holds for decision 1's folder stamps. There, forging a folder's stamp needs write access to the runtime's own folders, so it grants no new reach.
