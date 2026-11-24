# Review: 015, the runner's checks kept across calls (pull request)

Reviewer: a fresh agent (Claude Opus 5.5, `claude-opus-5-5`), which did not write this change.

Pull request #25, branch `spec/015-the-runners-checks-kept-across-calls`, head `5b8f47a`, reviewed as `git diff origin/dev...HEAD` (`origin/dev` at `236dddc`). Read: `AGENTS.md`; `context/constitution.md`; specification 015 with its landing notes and the owner's answer (decision 3 dropped); specification 012 and its hard-link rule (M1); the review of 014; study A §4.1; the diff of `src/`, `tests/`, `docs/`, `CHANGELOG.md` and the memo's §8 with its raw results; the CI logs of the head commit. No code was edited.

Probes are in `/tmp/claude-0/-home-user-wasi-perl/ecbe2564-8e44-50cd-9bc9-1c1b283f37e5/scratchpad/probes-015/` (scratch, not kept): `probe_identity.py`, `probe_fs.py`, `probe_exchange.py`, `probe_threads.py`, `probe_rights.py`, and `mutate.py`, which writes a copy of `src/` with one change to `_grants.py`, for running the checks against it.

## Verdict

Does not land, as it stands. The argument of decision 1 holds against every ordinary way a runtime name can change (rename, a link over a file, a removed and remade folder with the same inode, an exchange, an overlay copy-up, one-second file times, threads). But three things must be fixed first:
- H1: no run of CI executes any of the new checks, on any system, and the specification says CI runs the Windows check.
- M1: a scan made while a runtime folder could not be listed is kept, and it misses that folder's files once the process can read it.
- M2: two parts of the argument have no check that would fail if they broke.

The rest is sound. Decision 2 walks the grants on every call. The three shortcuts read the runtime afresh. Decision 5 scans every call off Linux and macOS. Decision 6 keeps its rules. The memo's §8 numbers match its raw results. The text agrees with the owner's decision to drop decision 3.

## Findings, ranked

### H1. CI runs none of the new checks, on any system, so the Windows rule and the macOS keep are never checked there

- **Where.** `tests/runner/test_kept_checks.py`. `tests/conftest.py:60–65`: `standin_wasm` skips without wasi-sdk 34. `.github/workflows/ci.yml:61` and `:255`. The specification's check 6 ("CI runs it on Windows"). The landing notes ("CI's Windows wheel job runs `tests/runner`, so it runs them there. I did not see that run.").
- **What.**
  - Every check here but `test_scans_are_kept_on_linux_and_macos_only` needs the stand-in guest, either directly or through `standin_dir`.
  - The stand-in is compiled with wasi-sdk, and none of the jobs that run `tests/runner` has wasi-sdk.
  - So on CI these checks are skipped:
    - the patched Windows check (`test_on_windows_every_writable_call_scans`, both cases);
    - the `MAX_WALK`, unreadable-folder and read-afresh checks;
    - the copy-mode check;
    - the coverage check;
    - on macOS, every kept-scan check.
  - The job's gate "No guest check was skipped" counts only `tests/escapes` and the guest one-liners, so it does not see these skips.
  - Decision 1 is on for macOS (`SCANS_KEPT`), but no check has run on macOS. Decision 5's check has not run on Windows.
  - Spec 012's `test_hardening.py` has the same gap. It predates this change, but this change relies on it, and its text claims otherwise.
- **Evidence.** EXECUTED: `gh api repos/ClayGendron/wasi-perl/actions/jobs/<id>/logs` for the head `5b8f47a`, checking each job's `tests/runner/test_kept_checks.py` line:

  | job | `test_kept_checks.py` | skip reason |
  |---|---|---|
  | wheel (windows-2025) | `sssssssssssss.sss` | `:345 [2]` "no wasi-sdk 34 found … the stand-in guest"; the KEPT ones "a scan is not kept on this system" |
  | wheel (macos-15) | `sssssssssssss.sss` | every skip: "no wasi-sdk 34 found" |
  | wheel (ubuntu-24.04) | `sssssssssssss.sss` | the same |
  | lint (`pytest -m "not slow"`) | `sssssssssssss.sss` | the same |
- **Fix.** Pick one; the first is best.
  - Make the checks run where the wheel is tested. Most need no stand-in. The scan, Windows, `MAX_WALK`, unreadable-folder, read-afresh and coverage checks can use `_grants.runtime_scan` or `runner._runtime_files` on the set `WASI_PERL_ARTIFACTS` names, which the wheel job sets. Those that need `run()` can run the wheel's own `perl.wasm` with `-e 'print "hello\n"'`.
  - Then add `tests/runner/test_kept_checks.py` to the "no check skipped" gate, allowing only its own reason ("a scan is not kept on this system").
  - If that is not done here, restrict `SCANS_KEPT` to Linux until a macOS run is seen. Correct check 6 and the landing notes to say that CI does not run these checks.

### M1. A scan that could not list a runtime folder is kept, and misses that folder's files once the process can read it

- **Where.** `src/wasi_perl/_grants.py`, `RuntimeFiles.__init__`: `except OSError: continue  # an unreadable folder`. Also `note()`'s `lstat` failure, and `_folder_stamp` returning `None`.
- **What.**
  - Before, an unreadable runtime folder was skipped on that call only. Each later call listed it again.
  - Now the scan that skipped it is kept. Nothing about the folder changes when the process's own rights grow, so no stamp moves. Rights can grow by `setuid`, `setgroups`, capabilities, an LSM policy reload or a server-side ACL.
  - A second name of a file in that folder, in a writable grant, is then accepted. The old rule refused it.
  - This departs from the intent's "exactly the guarantee they give today". It is rare, since installed runtime folders are readable. The fix costs nothing in the normal case.
- **Evidence.** EXECUTED, `probe_rights.py`:
  - A runtime subfolder of mode 000 is scanned with `CAP_DAC_OVERRIDE` and `CAP_DAC_READ_SEARCH` dropped. The scan saw only `open.pm`.
  - The capabilities are raised again. A file in the folder is linked into the grant.
  - Result: `{"kept_scan_reused_as_root": true, "refused_with_kept_scan": false, "refused_with_a_new_scan": true}`.
- **Fix.**
  - Treat a scan that met any `OSError` other than `ENOENT` (on a `scandir`, a `lstat` or a stamp) as not settled. It is then used for that call only, as before.
  - Check it with a patched `os.scandir`, as `test_an_unreadable_folder_…` already does for the grant: one call lists the folder and fails, the next call succeeds and must scan again.

### M2. Two parts of decision 1's argument have no check: the ctime in a folder stamp, and the folders of the single files

- **Where.** `_grants._folder_stamp` and the `singles` loop in `RuntimeFiles.__init__`. `tests/runner/test_kept_checks.py`.
- **What.**
  - **The ctime.** The argument rests on the ctime, since a folder's owner can set its mtime back with `utime`. No check would fail if the ctime were dropped from the stamp.
  - **The singles.** `perl.wasm`, `MANIFEST`, `SHA256SUMS` and `dev/null` are covered only by their folders' stamps. In the stand-in and `WASI_PERL_ARTIFACTS` layouts those folders are not inside any top. No check replaces one of these files and then links it into a grant.
  - The code is right in both cases (probes below). The specification asks for "checks for each way the argument could fail".
- **Evidence.** EXECUTED. Each mutation was made with `mutate.py` and run as `PYTHONPATH=<copy>/src uv run pytest tests/runner/test_kept_checks.py tests/runner/test_hardening.py tests/runner/test_fixed_cost.py`. The unchanged code gives "63 passed, 1 skipped".

  | mutation | result |
  |---|---|
  | ctime dropped from the folder stamp (`no_ctime`) | 63 passed: **survives** |
  | singles' parent folders not stamped (`no_single_parent`) | 63 passed: **survives** |
  | mtime dropped (`no_mtime`) | survives (ctime alone suffices; not a gap) |
  | only tops stamped (`tops_only`) | 11 failed |
  | settle window ignored (`no_settle`) | 1 failed |
  | match not read afresh (`no_reread`) | 1 failed |
  | size shortcut ignores second names (`shortcut_blind`) | 1 failed |
  | unreadable grant folder never refused (`unsearchable_lenient`) | 2 failed |

  The real code (EXECUTED, `probe_identity.py`):
  - A file renamed over a runtime file, with the folder's mtime then set back by `utime`, gives `mtime_same: true, ctime_same: false, reused: false`, and it is refused.
  - `perl.wasm` replaced by rename, then linked into a grant, gives `reused: false`, and it is refused.
- **Fix.** Add two checks:
  - a library file replaced by rename, the folder's `(atime, mtime)` restored with `os.utime(ns=…)`, then a link of the new file in the grant, refused;
  - `perl.wasm` (or `dev/null`) replaced by rename after a kept scan, then linked into the grant, refused.

### L1. A bind mount over a runtime file after the scan is not seen; the old rule saw it when the bound file had a second name

- **What.**
  - Decision 1 says "A file is added, removed or replaced only through an entry of its folder." A mount is the exception: it changes what a name leads to without touching the folder.
  - A mount over a runtime folder changes that folder's identity, so it is seen. A mount over a file is not.
  - It needs `CAP_SYS_ADMIN` in the runner's mount namespace. Whoever has that can already replace any runtime file.
  - The old rule caught only the narrow case where the bound file also had a second name. A grant's file with one name, bound over a runtime file, passed both rules. So the loss is small.
- **Evidence.** EXECUTED, `probe_identity.py`:
  - `bind_mount_over_file` gives `{"reused": true, "refused": null, "old_rule_refuses": true}`.
  - `bind_mount_over_subfolder` gives `{"reused": false, "refused": …}`.
- **Fix.**
  - Say in decision 1's argument, and in the README's "Not protected", that the rule assumes no mount changes under the runtime while a process runs.
  - Optionally, on Linux, keep a hash of `/proc/self/mountinfo` with the scan and scan again when it changes. That is a small read per call.

### L2. The copy-mode check never reaches the shortcut it is named for

- **What.**
  - Check 4 is "the shortcut in a copy-mode install". But the child's grant holds two entries. That is fewer than the runtime's file count, so `seen > len(runtime.files)` is never true, and `linked_now` is never called in the child.
  - The check refuses through the plain lookup.
  - The mutation that makes the shortcut ignore second names (`shortcut_blind`) passes this check. It is caught only by the unit check `test_past_max_walk_…`, and only because its stand-in scan holds fewer files than its grant.
  - The real code is right: `probe_identity.py`'s `nlink_one_then_two_in_big_grant` (7 runtime files, a grant of 1,000 files, a link made after the scan in the folder walked last) is refused.
- **Fix.** In the child, put more entries than `len(runtime.files)` before the link (for example 2,000 files in a folder walked first), so the call must read the runtime afresh at the shortcut.

### L3. Two of the implementer's choices have no check

- **The rescan of a stale match.** `test_a_match_is_read_afresh_and_a_stale_one_scans_again` passes with the rescan removed (`no_rescan` survives, EXECUTED). Its stale identity is the grant's own file, which is accepted with or without a rescan.
  - A check that needs the rescan: give the scan's entry for `strict.pm`'s identity another path, then link `strict.pm` into the grant. It must be refused.
  - The rescan does matter. On the one-second file system below, a scan made stale within one tick was rescued by it (`refused_by_stale_scan: true`).
- **Tops stamped through their links.** Stamping the tops by `lstat` (`tops_lstat`) survives (EXECUTED). A check: a top that is a link, retargeted to a copy of the tree after a kept scan, makes the next call scan again.

### L4. `docs/sandbox.md` says more than the code does

- "This happens in every new process, on every system." That is so for the set the runner finds itself. A set passed as `run(artifacts=…)` "is used as you give it" (runner docstring) and is not hashed.
- **Fix.** "Before a process's first call with the set it finds…".
- The specification's **Kind** line still names `_cache.py`. With decision 3 dropped, it is not touched.

### L5. The macOS premise is not shown by any run

- On macOS the folder stamps rest on "no user can set a ctime". The specification gives this as READ/INFERRED from study A.
- `setattrlist(2)` lists `ATTR_CMN_CHGTIME` among its attributes. Whether APFS honours a set of it was not probed (INFERRED; no macOS here).
- Forging it needs write rights on the runtime's own folders, so it would grant no new reach. But it is one more reason for H1.
- **Fix.** On macOS CI, a probe that sets a folder's change time with `setattrlist` and shows whether `st_ctime` moves to the value asked for, recorded in the memo.

### L6. The recorded slow-test run predates the merge of `dev`

- The landing notes report `-m slow tests/runner tests/escapes`: 32 passed. The head now holds 013's device checks, merged from `dev` in `5b8f47a`.
- With perf-base's set, two of 013's escape tests fail. They fail the same way on `origin/dev` with the same set, so the cause is the set, which predates 013, not this change. With a set that has 013's device, all 35 pass. All three runs are under "Tests run".
- **Fix.** Record the run against a set built from the merged head.

## What holds

- **Rename, a link over a file, an exchange.** A runtime file replaced by rename, or by `ln` through a temporary name, moves its folder's stamp. So does `renameat2(RENAME_EXCHANGE)` of a runtime file with a grant's file that has a second name. The next call scans again and refuses. This was EXECUTED on ext4, on tmpfs, and on an ext4 with one-second times under the real clock (`probe_identity.py`, `probe_exchange.py`).
- **Inodes reused.** The lib top removed and made again got the same inode number (`same_ino: true`), as did a removed subfolder. Neither scan was reused, because the new folder's ctime is new (EXECUTED).
- **Overlay.** On an overlay, a link to a lower file, an open for writing (copy-up), and a `chmod` each moved the merged folder's stamp. The identity stayed constant across copy-up. Every scan was made again, and every second name was refused (EXECUTED, `probe_fs.py overlay …`, with the folder fresh and with it already in the upper layer).
- **One-second times.** On a loop ext4 made with `mkfs.ext4 -I 128`, a change within the second of the stamp left it identical (`folder_stamp_identical_after_change: true`). The 2 s window kept the scan from being trusted (`settled_first_scan: false`, `reused: false`). Three runs, EXECUTED, `probe_fs.py coarse`.
- **A mapped write** moves neither a name nor an identity. The kept scan was reused, and a second name already in the grant was still refused (EXECUTED, `probe_identity.py` `mapped_write`). Decision 3 is the only place a mapped write matters. Decision 1 needs only identities, and a folder cannot be mapped. The memo's last paragraph of §8.4 is right.
- **A link count of 1 at the scan and 2 later** is caught in two ways. The walk looks up every grant file with two or more names among the identities. The shortcuts call `linked_now`, which reads the runtime afresh (EXECUTED: `nlink_one_then_two_in_big_grant` refused; the copy-mode check passes).
- **Folder stamped before it is listed.** A change between the stamp and the listing makes the next call scan again. A subfolder swapped for a link between its listing and its stamp makes the scan stamp the link and list its target. But the parent's stamp was taken earlier, so the next call scans again (READ). The old `rglob` had the same race within a call.
- **Order of `os.stat` and `lstat`.** Tops are stamped by `os.stat`, and so a top that comes to name another folder is seen. Folders below are stamped by `lstat`. A top reached first as a subfolder is a real folder, never a link, since links are not pushed as folders, so following it would not differ (READ).
- **Threads.** A scan is never changed once made. `_scan` is read and replaced under `_scan_lock`. An older scan kept after a newer one only fails `fresh()`.
  - EXECUTED, `probe_threads.py`: eight threads judged for 8 s while a ninth churned the runtime folders. There were 168 judgements, all as expected (the four threads on a grant with a second name always refused, the four on a grant without one always accepted), and no exception. Once the churn stopped, the scan settled and was reused.
- **`MAX_WALK` and an unreadable folder in a grant** keep their meaning. Each asks `linked_now` when reached. The `StopIteration` of study A's prototype is gone (READ, and the checks).
- **Decision 5.** `SCANS_KEPT` is checked both in `runtime_scan` and in `_keep`, so either alone keeps Windows scanning on every call. The patched check passes both ways on Linux (EXECUTED; see H1 for Windows itself).
- **Decision 6.** `rel = path[len(base):].replace(os.sep, "/")` equals `relative_to(root).as_posix()` for the paths `os.walk` gives from `root / top`. Links are found with `os.path.islink` as before (READ). The 012 and 014 checks of the tree pass.
- **No test was weakened.** `git diff origin/dev...HEAD -- tests/` adds `tests/runner/test_kept_checks.py` only. `tests/perl_t/exclusions.toml` is untouched. Each new check fails under at least one mutation of the behaviour it names, except as M2, L2 and L3 say.
- **The memo's §8** matches `results/*.jsonl` and `phases-*-rw-s4.json`. This was READ and recomputed. First call: 104.0 (103.3–108.2) → 73.2 (67.5–76.5). Check: 78.8 → 45.8. Writable call: 22.298 → 2.672 ms. `run()`: 22,154.9 → 2,857.3 µs. `_runtime_files`: 14,256.6 → 386.4. `judge`: 5,003.3 → 523.7. Peak RSS: 38.9–39.1 → 38.6–38.9 MiB. The wheel layout's counts were 271 folders and 1,530 identities with no `__pycache__` (EXECUTED); the memo's 272 and 1,543 include one `__pycache__` of 13 files (INFERRED).
- **The text agrees with the owner's decision.** The Status line, Intent, decision 3, checks 3, 5 and 6, acceptance 3, the CHANGELOG line and `docs/sandbox.md` all say that every new process hashes the library and why the record was refused. The in-process per-stamp reuse is recorded as a follow-up for the owner, not fixed here.

## Tests run

All under `flock /home/user/wasi-perl-wt/machine.lock`, from the worktree, with `WASI_PERL_ARTIFACTS=/home/user/wasi-perl-wt/perf-base/build/artifacts` and `WASI_PERL_ORACLE=/home/user/wasi-perl-wt/perf-base/build/native-install/bin/perl` unless said otherwise.

| command | result |
|---|---|
| `uv run pytest -m "not slow" -q -p no:cacheprovider` | 489 passed, 3 skipped, 245 deselected (EXECUTED) |
| `uv run pytest -m slow -q -p no:cacheprovider tests/runner tests/escapes` | 33 passed, 2 failed: `test_a_big_read_of_the_device_does_not_trap`, `test_a_write_to_the_random_device_reaches_no_host_file` (EXECUTED) |
| the same two, on `git archive origin/dev` with the same set | 2 failed, the same way (EXECUTED): the set predates 013 |
| the slow command with `WASI_PERL_ARTIFACTS=/home/user/wasi-perl-wt/016-the-guests-reads-and-writes/build/before/artifacts` | 35 passed (EXECUTED; that set has 013's device, INFERRED from the result) |
| `uv run ruff check`; `uv run ruff format --check` | All checks passed; 40 files already formatted (EXECUTED) |
| `uv run pytest tests/runner/test_kept_checks.py tests/runner/test_hardening.py tests/runner/test_fixed_cost.py` | 63 passed, 1 skipped (macOS only) (EXECUTED); then once per mutation, as in M2 |
| the probes listed above, as root | as reported in the findings (EXECUTED) |
| CI logs of `5b8f47a` through `gh api` | all jobs green; the new checks skipped, as H1 says (EXECUTED) |

## Round 2

Reviewer: the same fresh agent (Claude Opus 5.5, `claude-opus-5-5`).

Reviewed as `git diff 5b8f47a..4a82a75`, setting aside what the merge of `dev` at `d8fd00c` (016) brought in (`b5ecfa2`). Read: the fix commit `4a82a75`, which changes `_grants.py`, `tests/runner/test_kept_checks.py`, `.github/workflows/ci.yml`, `README.md`, `docs/sandbox.md`, the specification's landing notes and `context/roadmap.md`; the CI logs of `4a82a75`. The probes and `mutate.py` are those of round 1, updated for the new code. No code was edited.

### Verdict, round 2

Lands. H1 is fixed, and CI shows it. On ubuntu (x86-64 and arm) and macOS, all 22 cases of `test_kept_checks.py` run and pass. On Windows, the ten checks that do not need a kept scan run and pass, the copy-mode check among them; the twelve kept-scan cases skip with the one reason the gate allows there. The gate counts them on all four. M1 is fixed and checked. M2, L2 and L3 each have a check that fails under its mutation. The text fixes for L1, L4, L5 and L6 are right. Two small things are left (below). Neither needs another round.

### H1 in CI

EXECUTED: `gh api repos/ClayGendron/wasi-perl/actions/jobs/<id>/logs` for each job of `4a82a75`. Every job is green, "Perl's own suite" included.

| job | `tests/runner/test_kept_checks.py` | the gate's last line |
|---|---|---|
| wheel (ubuntu-24.04) | `......................` (22 passed) | "256 guest checks (at least 247: 209 cases, 19 escape tests, 19 kept-scan checks), 0 skipped" |
| wheel (ubuntu-24.04-arm) | `......................` | the same |
| wheel (macos-15) | `......................` | the same |
| wheel (windows-2025) | `ssssssssssss....s.....` | the same, after 13 `allowed: tests.runner.test_kept_checks::…: this host lacks a scan is not kept on this system`, and the three escape tests it already allowed |
| lint | `ssssssssssss.....s.ss.` | no gate; guest checks skip without a set, as the notes say |

- **The Windows risk the implementer named did not happen.** The copy-mode check is the first `.` after the `s` in the Windows row, and it passed. So the child hard-linked its loaded copy of wasmtime's DLL into the grant, and that link was refused. Had the link failed, the check would have skipped with "no hard link to wasmtime's library here". The gate does not allow that reason, so the job would have failed.
- **Windows' skips are all of one kind.** Every one is a `KEPT` check: 12 cases at lines 160–298, and line 382, `test_a_top_that_is_a_link_…`. The patched Windows check ran both ways, as did the `MAX_WALK`, unreadable-folder, read-afresh, stale-match and coverage checks.
- **On macOS the ctime check ran.** There, a rename moved the folder's ctime after its mtime had been set back. That shows the folder stamp on APFS does what decision 1 needs for an ordinary change. It does not probe a forged ctime (`setattrlist`), which the notes leave as INFERRED. The notes say why that grants no new reach. That is enough for L5.

### Mutations, round 2

Each was run as `PYTHONPATH=<copy>/src uv run pytest -q tests/runner/test_kept_checks.py tests/runner/test_hardening.py tests/runner/test_fixed_cost.py`, with 016's set (EXECUTED). Unchanged, the code gives 68 passed, 1 skipped (a macOS-only check).

| mutation | the checks that fail |
|---|---|
| `no_ctime` | `test_a_rename_with_the_folders_mtime_set_back_is_seen_by_its_ctime` |
| `no_single_parent` | `test_perl_wasm_replaced_by_rename_after_a_kept_scan_then_linked_is_refused` |
| `no_rescan` | `test_a_stale_match_scans_again_and_is_refused` |
| `tops_lstat` | `test_a_top_that_is_a_link_is_stamped_by_the_folder_it_names` |
| `shortcut_blind` | the copy-mode check and `test_past_max_walk_…` (L2 fixed) |
| `no_reread` | `test_a_match_is_read_afresh` |
| `no_settle` (now `complete or all(…)`) | the 2 s check and the M1 check |
| `keep_incomplete` (M1 undone: `complete` ignored) | the M1 check |
| `scandir_error_ignored` (a failed listing not counted) | the M1 check |
| `unsearchable_lenient` | the unreadable-folder check, and 012's |
| `tops_only` | 12 checks |
| `no_mtime` | none; ctime alone suffices, so this is not a gap |
| `reuse_any_platform`, `keep_any_platform` | none; each guard alone keeps Windows scanning every call, so behaviour is unchanged, as in round 1 |
| `note_error_ignored` (a file's failed `lstat` not counted) | **none**; see R2-L1 |

The new checks were also run against round 1's `src/` (`git archive 5b8f47a src`). Only the M1 check failed (EXECUTED: "1 failed, 21 passed"), as it should. The others check behaviour that round 1's code already had right.

`probe_rights.py`, against the new code, gives `{"kept_scan_reused_as_root": false, "refused_with_kept_scan": true, "refused_with_a_new_scan": true}` (EXECUTED). M1 is fixed.

### Findings, round 2

#### R2-L1. The M1 fix covers a file whose `lstat` fails, but no check does

- **What.**
  - `note()` now counts a failed `lstat` as an error, so the scan is not kept. The landing notes say the fix covers "a listing, a folder's stamp, and a file's `lstat`". That is true of the code.
  - But the only M1 check refuses a listing. So the mutation that drops `failed(e)` from `note()` passes all 68 checks.
  - The case is real. A folder of mode `0444` can be listed but not searched, so every `lstat` in it fails.
- **Evidence.** EXECUTED, `probe_rights_rx.py` (round 1's probe with the folder `0444` instead of `0000`):
  - The code gives `kept_scan_reused_as_root: false, refused_with_kept_scan: true`.
  - With `note_error_ignored`, it gives `kept_scan_reused_as_root: true, refused_with_kept_scan: false`.
- **Fix.** A second case of the M1 check that patches `os.lstat` to raise `EACCES` for `strict.pm` on the first call. The next call must scan again and refuse the link.

#### R2-L2. The gate's floor is one sum, so a few dropped checks would go unseen

- **What.** `floor = cases + escapes + kept` is compared with the count of every guest case. On the three POSIX jobs, the count is 256 against a floor of 247. So up to 9 cases could drop out of collection unseen, in any group. A skip is still caught, so a skipped check cannot hide.
- **Context.** The gate had this shape before the change; the change adds a third group to it.
- **Fix, optional.** Compare each group with its own floor.

### What holds, round 2

- **`_grants.py`.**
  - `_folder_stamp` returns `None` only for `ENOENT` and `ENOTDIR`, and raises any other error.
  - The scan's `stamp`, `note` and listing each record any other error as incomplete, and `settled` requires a complete scan.
  - `fresh()` answers no when a stamp cannot be read.
  - Errors in an installed runtime would stop scans from being kept. That only costs time, and CI's `test_ten_writable_calls_scan_the_runtime_once` would show it. It passed on the real wheel trees of ubuntu and macOS.
- **The guest checks.**
  - Each copies the built `perl.wasm` into a scratch set of its own, so it never changes the set under test.
  - The stand-in remains the fallback. Without `$WASI_PERL_ARTIFACTS`, all 22 cases passed here (EXECUTED).
- **`same_disk`** makes the wasmtime-link grant under `sys.prefix`. In a venv that is beside `lib/` and `bin/`, not at, inside or above any runtime folder. So the grant is not refused for its place, and the check sees the refusal of the link itself (READ, and the passing CI run).
- **No test was weakened.**
  - The fix commit touches only `tests/runner/test_kept_checks.py`.
  - The old checks there keep their assertions. Each now runs on a scratch set around the built `perl.wasm`. `test_a_match_is_read_afresh_and_a_stale_one_scans_again` was split in two, and the new half fails on `no_rescan`.
  - The exclusions are untouched.
- **The text agrees with the code and the owner's decision.**
  - The README's "Not protected" names a mount over a runtime file.
  - `docs/sandbox.md` says a set passed as `artifacts=` is not hashed.
  - The spec's Kind line and acceptance 3 are corrected.
  - The roadmap records 014's skipped checks and the in-process stamp reuse as follow-ups.

### Tests run, round 2

All under `flock /home/user/wasi-perl-wt/machine.lock`, with `WASI_PERL_ARTIFACTS=/home/user/wasi-perl-wt/016-the-guests-reads-and-writes/build/artifacts` and `WASI_PERL_ORACLE=/home/user/wasi-perl-wt/perf-base/build/native-install/bin/perl`.

| command | result |
|---|---|
| `uv run pytest -m "not slow" -q -p no:cacheprovider` | 494 passed, 3 skipped, 245 deselected (EXECUTED) |
| `uv run pytest -m slow -q -p no:cacheprovider tests/runner tests/escapes` | 35 passed, 219 deselected (EXECUTED) |
| `uv run ruff check`; `uv run ruff format --check` | All checks passed; 40 files already formatted (EXECUTED) |
| `env -u WASI_PERL_ARTIFACTS uv run pytest -q tests/runner/test_kept_checks.py` | 22 passed (EXECUTED) |
| the new checks against `git archive 5b8f47a src` | 1 failed (the M1 check), 21 passed (EXECUTED) |
| the 15 mutations above | as the table says (EXECUTED) |
| `probe_rights.py`, `probe_rights_rx.py` | as R2-L1 and M1 say (EXECUTED) |
| CI logs of `4a82a75` through `gh api` | as the H1 table says (EXECUTED) |

## After round 2 (the orchestrator)

- **R2-L1, fixed** in `6c3e147`. The M1 check gains a case in which `os.lstat` of one runtime file fails with `EACCES`. It fails with `failed(e)` removed from `note()` (1 failed, 22 passed) and passes on the real code (23 passed).
- **R2-L2, left as optional**, as the review allows. It is recorded as a follow-up in the landing notes.

The verdict stands: **Lands.**
