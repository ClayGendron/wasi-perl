# Review: 011, a sleeping guest keeps its deadline (2026-11-24)

Reviewer: an agent (Claude), a fresh sub-agent of https://claude.ai/code/session_01ThzDEUwftdmy8zWBJK1gWa; not the author

- **Scope:** `git diff origin/spec/010-special-files-and-select...origin/spec/011-a-sleeping-guest-keeps-its-deadline` at 1da4e81 (8 files, +426/−30). I read the spec, the security review it answers (H1, M3, L3), and all of the `poll_oneoff` code in `recipes/wasi-fd-table.c` (`sliced_poll`, `:390-497`, and the wrapper, `:499-578`).
- **Build:** `WASI_PERL_DOWNLOAD_CACHE=/home/user/wasi-perl/downloads WASI_PERL_JOBS=2 scripts/build.sh`. The steps were `10_native: ok`, `20_target: ok`, `30_library: ok (1453 files)`. perl.wasm's sha256 is `5334d246…2b43b7`, from this build only (the module is not reproducible). EXECUTED.
- **Tests:**
  - `uv run --no-project --with pytest --with wasmtime==51.0.0 pytest tests/build -q` gave `393 passed`. EXECUTED.
  - `WASI_PERL_ARTIFACTS=$PWD/build/artifacts uv run pytest -q tests/escapes tests/runner` gave `180 passed, 1 skipped`. The two known conftest collisions did not show up in this run. EXECUTED.

## What I checked, and what I found

**Every route to a host clock wait goes through the slicer.** READ, then EXECUTED.
- In wasi-sdk 34's `libc.a`, only three objects call `__wasi_poll_oneoff`: `clock_nanosleep.c.obj`, `ppoll.c.obj` and `ioctl.c.obj` (`llvm-nm -A libc.a | grep poll_oneoff`). Only `__wasilibc_real.c.obj` reaches the import, and it is the object that defines the function. So `--wrap=__wasi_poll_oneoff` catches every call.
- No other archive in the sysroot names it.
- `sleep`, `nanosleep` and `usleep` call `clock_nanosleep(CLOCK_REALTIME, 0, …)`, according to the relocations `llvm-objdump -r` prints for `_CLOCK_REALTIME`. `select` and `IO::Poll` go through `ppoll`.
- Perl's bare `sleep` is `Pause()`. With `d_pause='undef'`, that becomes `sleep(2147450879)`, so it is sliced too.

**Probes under `run()` with `deadline_s=1`, no fuel (the unmetered engine).** Each call was timed with the host's clock (`probes011/sleep_probe.py`, in my scratchpad). EXECUTED.

| Guest code | Result |
|---|---|
| `sleep 2**31-1` | 1.06 s, `deadline` (1.96 s on the first run, which includes the compile) |
| `select(undef,undef,undef,1e9)` and `…,1e18` | 1.07 s, `deadline` |
| `Time::HiRes::nanosleep(1e18)`, `(2**63)`, `(2**64)` | 1.06 to 1.09 s, `deadline` |
| `Time::HiRes::usleep(1e18)`, `Time::HiRes::sleep(1e18)` | 1.07 s, `deadline` |
| bare `sleep;` | 1.06 s, `deadline` |
| `IO::Select->new(\*STDIN)->can_read(1e9)` | returns at once (0.06 s): stdin from bytes is always ready |
| `<STDIN>` then `sleep 30` | the read returns, then `deadline` at 1.07 s |
| `select` reading STDOUT's bit, timeout 1e9 | `deadline`. The table drops the subscription (STDOUT has no read right), so the call is a clock wait reached through the descriptor path |
| `select` reading STDIN's bit, timeout 1e9 | `n=1` at once |
| `Time::HiRes::clock_nanosleep($id, 3e9)` for ids 2, 4, 8, 16, 24, 32, 1024, 4096 | returns at once; ids 0 and 1 end at the deadline |

**Edge cases against the real host.** I wrote a C probe, `probes011/edge.c`, linked it with the recipe's table, and ran it under `wasmtime run -W exceptions=y -W timeout=1s`. EXECUTED.
- A relative timeout of 2^63 ns or `UINT64_MAX`, and an absolute REALTIME time of `UINT64_MAX`, each stop at the 1 s epoch timeout. Due times saturate, and nothing overflows.
- An absolute MONOTONIC time in the past gives one event, userdata 7, in 0.05 ms.
- Two clocks at 15 ms and 25 ms give exactly one event, the 15 ms one's (userdata 1), at 16.4 ms. That is what a single poll gives: no early clock event, and userdata is kept.
- A 30 ms clock fires at 30.6 ms.
- Clock ids 2 and 3, and id 2 together with a monotonic clock, answer EINVAL in under 0.2 ms.
- Clock id 5 traps inside `__wasi_clock_time_get` (wasmtime refuses the out-of-range enum value). Perl cannot reach that id (see the table above).

**The loop calls the host on every pass.** READ.
- Each pass of the loop calls `__real___wasi_poll_oneoff` with a 10 ms clock, so it cannot spin without yielding.
- If a clock read fails partway through, `now` stays 0 and the loop keeps slicing. It still yields on every pass, so the deadline still ends it.

**Fuel.** EXECUTED.
- `sleep 2` with `fuel=10**12` and no deadline used 42,414 more fuel than `-e 1` (28,577,140). That is about 212 per slice.
- With `fuel=2_000_000` and `deadline_s=5`, `sleep 30` ends "out of fuel" at once.
- With `fuel=10**10` and `deadline_s=2`, `sleep 30` ends "deadline" at 2.07 s.
- Both answers are sensible.

**The 256 cap.** EXECUTED.
- With the two default grants, a guest opens 251 files. With 5 extra grants it opens 246, which is 256 less stdio, 2 and 5.
- With 260 grants, perl cannot start: `Can't open /dev/null: No file descriptors available`, exit 24.

**The build-host path check.** EXECUTED.
- `test_build_host_paths.py` scans every file under `build/artifacts`, `perl.wasm` included.
- Pointed at 009's set (`WASI_PERL_ARTIFACTS=…/009-fd/build/artifacts`), it fails and names `/root/.claude/rm-guard/…/refuse/rm` in `Config_heavy.pl`.
- On this set it passes.
- The toolchain paths in `Config_heavy.pl` are already the neutral `/wasi-perl-build/…`.

**The 10,000 × 1 ms loop.** This is a sanity measure only, not a test. EXECUTED.
- `Time::HiRes::sleep(0.001) for 1..10000` took 21.2 s in the guest and 10.9 s natively.
- 009's module, which does not slice, also took 21.2 s. The cost per call is wasmtime's, not the slicer's: a 1 ms sleep is a single host call either way.
- 250 sleeps of 20 ms took 5.32 s in the guest and 5.04 s natively.

## Findings

| # | Rank | Where | Finding | Suggested fix |
|---|---|---|---|---|
| M1 | Medium | `recipes/wasi-fd-table.c:435` (`due[i] = now + timeout` on the subscription's own clock), `:450-452` | **A relative wait is measured against the wall clock.** wasi-libc's `sleep`, `nanosleep` and `usleep` send a *relative* `CLOCK_REALTIME` subscription (READ, from the relocations in `libc.a`). The slicer turns it into an absolute REALTIME due time and re-reads REALTIME on every slice. A host clock step changes the length of the sleep. A step forward ends it early, which breaks the spec's "no early clock event" (Decision 1). A step back makes it longer, up to the deadline. wasmtime treats a relative REALTIME timeout as a duration, and so does Linux's `nanosleep`. So this is a divergence from a single poll. The guest cannot step the clock, and the deadline bounds the effect. INFERRED from the code: I did not step the host's clock. | Track every *relative* subscription's due time on `__WASI_CLOCKID_MONOTONIC`, whatever its clock id. Keep absolute subscriptions on their own clock. In the last call, send the remaining time as a relative timeout on the subscription's own id, as now. One flag per subscription is enough. |
| L1 | Low | `recipes/wasi-fd-table.c:487` | **A caller's clock whose userdata is `UINT64_MAX` is dropped.** The filter now runs on every result. 010 ran it only when the table had added its own zero clock. The edge probe's case 1 (a 20 ms clock with userdata `~0`) returned success with 0 events after 20.7 ms, where a single poll returns 1 event. Perl cannot reach this: wasi-libc's callers use small userdata. A C caller would get nothing back, and `clock_nanosleep` would then read an event that was never written. EXECUTED. | Drop the table's own clock only in calls that added one. Either pass `sliced_poll` a flag from the wrapper's `nlocal > 0` branch, or drop by position: the slice's clock is always `work[m-1]`. |
| L2 | Low | `recipes/wasi-fd-table.c:428-430`, `:437-440` | **A clock the guest cannot read sends the whole call to the host unsliced.** This fails open: a host that can wait on a clock it cannot read would wait past every limit. On wasmtime 51 the host refuses such calls at once (EINVAL for ids 2 and 3), and an id of 4 or more traps in `clock_time_get` before the host is asked. Perl's `Time::HiRes` cannot reach either case. EXECUTED. | Answer `__WASI_ERRNO_INVAL` (or NOTSUP) for an unreadable clock, and never send it unsliced. That makes the "no unbounded host wait" property independent of the host. |
| L3 | Low | `recipes/wasi-fd-table.c:518-519` with `:437` (`clocks == 0`) | **A poll with descriptors and no clock still waits in the host without limit.** The spec chose this ("unchanged"). Under `run()` nothing can block this way: stdin is bytes, stdout and stderr are custom writers, regular files are answered locally, and handles without the right are dropped. `IO::Poll` on STDOUT for reading, with no usable timeout, returned 0 at once (EXECUTED). An embedder that inherits stdin or grants a socket would block. | When a call sends host descriptors and has no clock, add an infinite clock (due `UINT64_MAX`) so the call is sliced too. That costs one pass through the loop every 10 ms. Or record in `docs/sandbox.md` that only runner-shaped stdio is safe. |
| L4 | Low | `docs/sandbox.md:47` | **"A sleep still ends at the runner's … fuel limit" overstates fuel.** A sleeping guest spends about 212 fuel per 10 ms slice (EXECUTED, above), roughly 21,000 a second. So a fuel cap of 10^9 with no deadline lets a guest sleep for about 13 hours (INFERRED from that rate). The landing notes give about 550 per slice for `select`, the same order. | Say that the deadline bounds a sleep, and that fuel spent while asleep is small, about 20,000 a second. |
| L5 | Low | `tests/escapes/test_escapes.py:593` | **The deadline checks run only on the metered engine.** Each one passes `fuel=10**12`, so the default `run()` (no fuel: the unmetered engine, whose compiled code differs) is not checked for sleeps. My probes show that it works (1.06 s, `deadline`, EXECUTED). | Add one case, `sleep 2**31 - 1` with `deadline_s=1` and no `fuel`, to `test_a_sleeping_guest_keeps_its_deadline`. |
| L6 | Low | `src/wasi_perl/_grants.py` (no count check); `recipes/wasi-fd-table.c:43`, `:105` | **About 250 grants or more stop perl at start-up.** The grants fill the table, and perl exits 24 with `Can't open /dev/null: No file descriptors available` (EXECUTED, 260 grants). That is a clear error, not a crash, but it comes from the guest, not from `run()`. With 1,024 numbers the limit was out of reach. | Have `run()` refuse more than, say, 240 grants with a `ValueError` that names the limit. This can ride with 012's runner work. |

Not findings, but noted:
- `select(undef,undef,undef,undef)` answers −1 "Not supported" at once, where natively it blocks forever. That comes from wasi-libc rejecting an empty poll, and it is older than this branch. It cannot hang.
- `sleep undef` returns at once, which this branch does not change.
- Spec 004's text that "no call can block" is left to its owner, as the landing notes say.

## Verdict

Lands. Every route a Perl guest has to a host clock wait is now sliced:
- `sleep`, bare `sleep`;
- `select`, and `select` through a dropped descriptor;
- `Time::HiRes`'s `sleep`, `usleep`, `nanosleep` and `clock_nanosleep`, absolute and relative;
- `IO::Select` and `IO::Poll`.

Each of them stops within one slice of the deadline, on both engines. Huge timeouts saturate. The events returned match a single poll's. The 256 cap counts stdio and the grants. The build-host path check catches the regression it was written for.

M1 (relative waits measured on the wall clock) should be fixed in the next round, or answered in writing. The Lows can ride.

## Round 2 (2026-11-24)

The same reviewer checked 340c93b, a merge of dev, and the fix commit 99b6172. I made a fresh worktree and rebuilt (`10_native`, `20_target` and `30_library: ok`). EXECUTED.

**Tests:**
- `uv run --no-project --with pytest --with wasmtime==51.0.0 pytest tests/build -q` gave `394 passed`. EXECUTED.
- `WASI_PERL_ARTIFACTS=$PWD/build/artifacts uv run pytest -q tests/escapes` gave `22 passed`. EXECUTED.

**M1 is fixed.**
- Reading the code: `recipes/wasi-fd-table.c:428-442` reads a relative subscription's due time on `CLOCK_MONOTONIC` and an absolute one's on its own clock. `:447-455` measures what remains the same way. The last call still sends a relative timeout on the subscription's own id. READ.
- I wrote a fake-step probe, `probes011/step.c` in my scratchpad. It compiles the table with `-D__wasi_clock_time_get=fake_clock` against the real host's `poll_oneoff`. After the table's first REALTIME read, REALTIME jumps by ±1 h. It sends one 300 ms wait, timed on the real monotonic clock, under `wasmtime run -W timeout=5s`. EXECUTED.

| Wait | Step | This round's table | 1da4e81's table |
|---|---|---|---|
| relative | +1 h | 301 ms, 1 event, userdata 9 | 0 ms (early) |
| relative | −1 h | 300 ms | still waiting at the 5 s timeout, which traps |
| relative | none | 301 ms | 301 ms |
| absolute, 300 ms ahead | +1 h | 0 ms (correct: that time has passed) | 0 ms |
| absolute, 300 ms ahead | none | 300 ms | 301 ms |

**L1 to L5 are fixed.** EXECUTED.
- L1: the edge probe's case 1 now gives 1 event with userdata `UINT64_MAX`, at 21 ms. `test_poll_buffer.py` checks it.
- L2: an absolute wait on a clock the guest cannot read now answers EINVAL.
- L3: `select(undef,undef,undef,undef)` now waits until the deadline, which ends it at 1.08 s, as natively it blocks. Before, it answered −1 at once.
- L4: `docs/sandbox.md` now says that the deadline bounds a sleep.
- L5: an unmetered deadline check was added.

**L6** is answered and left to 012. I accept that.

**The round-1 probes still pass** under `deadline_s=1`, no fuel. `sleep 2**31-1`, `select(…,1e9)` and `nanosleep(1e18)` each end `deadline` at about 1.07 s (2.06 s for the first, which includes the compile). `IO::Select` on STDIN returns at once. EXECUTED.

### Findings of round 2

| # | Rank | Where | Finding | Suggested fix |
|---|---|---|---|---|
| R2-L1 | Low | `recipes/wasi-fd-table.c:434-435`, `:458-460` | **A relative wait on a clock the host does not know now lasts its full time and then answers EINVAL.** Before, it answered at once. In the edge probe, ids 2, 3 and 5, each with a 3 s relative timeout, answered errno 28 after about 3.0 s. Id 5 no longer traps. The time is still bounded by the timeout and by the deadline, and Perl cannot reach it: `Time::HiRes::clock_nanosleep(2, 3e9)` returns in 0.08 s. EXECUTED. | Optionally, check the id up front with `__wasi_clock_res_get(c->id, …)` and answer EINVAL at once when it fails. Otherwise, write the behaviour down in the comment. |
| R2-L2 | Low | `recipes/wasi-fd-table.c:444-489` (n = 0) | **A poll with no subscriptions now waits in 10 ms slices until the deadline.** The host answers EINVAL for it at once. The edge probe's case 12 was interrupted at 4 s. This is what makes `select` with nothing and no timeout block, as it does natively. It is harmless, but it differs from the host. EXECUTED. | Keep it, and name it in the comment above `sliced_poll`. |

### Round 2 verdict

Lands. M1 is fixed: the fake-step probe shows relative waits unaffected by a wall-clock step in either direction, and absolute waits still following their own clock. L1 to L5 are fixed. L6 is fairly left to 012. The two new Lows are bounded by the deadline, Perl cannot reach the first, and neither blocks the merge.
