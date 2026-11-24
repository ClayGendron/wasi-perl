# 011 — A sleeping guest keeps its deadline

- **Status:** written by the orchestrator 2026-11-24, under the owner's pre-agreement of the work toward 0.1.0; it closes the blocker the 0.1.0 security review found (`../reviews/0.1.0-security-and-structural-review-2026-11-24.md`, H1).
- **Owner:** Clay Gendron
- **Kind:** the build recipe (`recipes/wasi-fd-table.c`'s `poll_oneoff` wrapper, `recipes/config.sh`), checks in `tests/build/` and `tests/escapes/`.
- **Depends on:** 010.
- **Research it stands on:**
  - The security review, H1 (EXECUTED): with `deadline_s=1`, `perl -e 'sleep 5'` returned only after 5.0 s, then as "deadline". `sleep`, four-argument `select`, `Time::HiRes::sleep` and `nanosleep` all wait in wasmtime's host `poll_oneoff`, where neither the epoch deadline nor fuel can stop them. `sleep 2**31` holds the caller's thread for good.
  - wasi-python's specification 010, "A sleeping guest keeps its deadline" (READ): its answer shadows `poll_oneoff` on the host.
  - The same review (M3, EXECUTED): a guest can hold up to the table's 1,024 host descriptors per call, so a few concurrent calls exhaust a process's default limit.
  - The same review (Low): `recipes/config.sh` carries a path from the builder's own machine (`rm_try`), which ships in `Config_heavy.pl`.

## Decisions

1. **Every clock wait is cut into slices of at most 10 ms.** The guest's `__wasi_poll_oneoff` wrapper finds the earliest clock subscription. If it is more than 10 ms away, it polls the host with that clock shortened to 10 ms (relative, keeping its userdata), and loops until the real time is reached or another subscription fires. Wasm code runs between slices, so the runner's epoch deadline and fuel stop a sleeping guest within one slice. Absolute clocks are turned into relative ones against the same clock. The events returned are exactly those a single poll would have returned: a slice's clock event is not reported until the real deadline passes. A poll with no clock subscription is unchanged.
2. **The guest holds at most 256 host descriptors at once.** The descriptor table's capacity drops from 1,024 to 256 numbers (preopens and stdio included), so `open` past it answers `EMFILE`. The runner's own per-process accounting is specification 012's.
3. **No build-host path in the artifacts.** `rm_try` and any other `config.sh` value naming a build machine path are replaced with plain values. A check scans the shipped library for the build host's home and checkout paths and for `/root/`.
4. **Checks.**
   - `sleep 30` under the runner with `deadline_s=1` ends "deadline", counted by the guest's own clock, never by a wall-clock assertion: the check passes `fuel` too and asserts the trap, plus a bounded `Time::HiRes::time` delta printed by an END block that must not run.
   - `select(undef, undef, undef, 0.25)` and `Time::HiRes::sleep(0.05)` still sleep at least their time, compared with the guest's clock.
   - Sleeps of 0 and 0.001 behave as natively.
   - 257 opens answer `EMFILE` at the 254th or so, as the table's count says.
   - The new escape check in `tests/escapes/`: a guest that sleeps forever returns from `run()` with "deadline".

## Acceptance

1. The checks pass, EXECUTED; 001–010's checks still pass.
2. A fresh agent's review lands.

## Landing notes

Built 2026-11-24 on branch `spec/011-a-sleeping-guest-keeps-its-deadline`, from 010's head plus the spec commit. It was then merged with origin/spec/010-special-files-and-select at 7bcaf73, 010's review round. Nothing was rebased.

### Commands and the artifact

    WASI_PERL_DOWNLOAD_CACHE=/home/user/wasi-perl/downloads WASI_PERL_JOBS=3 scripts/build.sh
    # 20_target: ok / 30_library: ok (1453 files)                                              EXECUTED
    sha256sum build/artifacts/perl.wasm; stat -c %s build/artifacts/perl.wasm
    # 534b28b1847aff4a6085e7535c74c6c3f2ab0a6b0d45fbe07e4a201f3056ce98, 9259764 bytes            EXECUTED

    uv run --no-project --with pytest --with wasmtime==51.0.0 pytest tests/build -q             EXECUTED
    393 passed in 16.91s
    WASI_PERL_ARTIFACTS=$PWD/build/artifacts WASI_PERL_ORACLE=$PWD/build/native-install/bin/perl \
        uv run pytest -q tests/escapes tests/runner tests/oneliners                              EXECUTED
    2 failed, 625 passed, 1 skipped

The two failures are the `from conftest import` collisions in `tests/runner` that are already on dev. The module is not byte-reproducible (`__DATE__`/`__TIME__`, 001), so the hash is of this build only. 007's t/ harness, run as in 009's notes, gives the same as 010: `617 run, 291 passed, … 9 failed`. The only difference is `win32/seekdir.t`, whose count varies between runs. EXECUTED.

### Decision 1: every clock wait is sliced

`sliced_poll()` in `recipes/wasi-fd-table.c` is the only place the table calls the host's `poll_oneoff`. Both the clock-only path and the path with descriptors go through it.

- **Due times.** It reads each clock subscription's due time once, on that subscription's own clock (`__wasi_clock_time_get`). Absolute ones keep their time. Relative ones become now plus the timeout, saturating at the top.
- **The slices.** While the soonest is more than 10 ms away, it sends the host the descriptor subscriptions and one 10 ms monotonic clock of its own, and drops that clock's event. It returns as soon as a descriptor answers.
- **The last wait.** Within 10 ms it sends the subscriptions as given, with each clock made relative to what remains. So the events returned are the host's own for exactly those subscriptions.
- **Unreadable clocks.** A clock the host cannot read sends the call to the host unchanged, as the host's own answer.
- **What the loop costs.** WebAssembly runs between slices, so the epoch deadline and fuel stop a sleeping guest within one slice. One slice costs about 550 fuel: `select(undef,undef,undef,0.5)` used 27,605 more than `-e 1`'s 1,582,930, over about 50 slices. EXECUTED.

**Checks.** `tests/escapes/test_escapes.py`, each in a child with a 30 s fail-safe:
- `test_a_sleeping_guest_keeps_its_deadline` (6): `sleep 30`, `sleep 2**31 - 1` (the largest `sleep` takes, since it reads an I32; `2**31` warns "negative argument" and returns at once, natively too, EXECUTED), `select(undef,undef,undef,1e9)`, `Time::HiRes::sleep(1e6)`, `Time::HiRes::nanosleep(1e15)`, and `clock_nanosleep` to an absolute `CLOCK_REALTIME` time an hour ahead. Each runs under `run()` with `deadline_s=1` and `fuel=10**12`. Each ends `exit_code=None, trapped="deadline"`, with empty stdout: the END block that would print the guest's measured wait never runs. No wall-clock assertion.
- `test_a_sleeping_guest_runs_out_of_fuel` (2): `sleep 30` and the `select`, with no deadline and a fuel cap of start-up plus 100,000. Each ends "out of fuel".

All EXECUTED. On 010's module all 8 failed: each child outlived its 30 s fail-safe (`8 failed in 242.00s`, EXECUTED). The security review's `probes/sleep.py` gives `1.01 None deadline` for each of its four forms on this module, where it gave 5.0 s before. EXECUTED (its first form, 2.44 s, includes the module's compile).

`tests/build/test_sleep.py` (8), against native:
- Each program measures its own wait with the guest's clock and prints only whether it lasted at least the time asked. The cases are `select(…, 0.25)`, `Time::HiRes::sleep(0.05)`, `sleep 1`, `nanosleep(3e7)`, `usleep(20_000)`, `clock_nanosleep` absolute (0.1 s ahead) and relative (`CLOCK_MONOTONIC`, 40 ms). Each prints `long enough`, as natively.
- `sleep(0)`, `select` with 0 and 0.001, `Time::HiRes::sleep` with 0 and 0.001, and `usleep(0)` print what native perl prints. The elapsed values that `Time::HiRes` returns vary from run to run, so only their sign is printed.

All EXECUTED.

### Decision 2: 256 host descriptors

`FD_LIMIT` is 256, and the comment says why (macOS's default limit, so that a few calls cannot exhaust the embedder's descriptors). `test_257_opens_meet_the_256_limit`: 257 opens of `/dev/null` stop at 251 with EMFILE, which is 256 less stdio and the two default grants. Native perl under `ulimit -n 256` stops at 253. 009's EMFILE check now runs native under `ulimit -n 256`. `docs/sandbox.md` says 256. All EXECUTED. The runner's per-process accounting is specification 012's.

### Decision 3: no build-host path

`rm_try` in `recipes/config.sh` was `/root/.claude/rm-guard/…/refuse/rm -f try …`, the agent harness's `rm` that Configure found on PATH when the file was first written. It is now plain `rm -f try …`. The shipped `Config_heavy.pl` line 1228 now reads that. EXECUTED. (`build/native/config.sh` has the same harness path, because the native build ran Configure under the harness. The native perl is the oracle and never ships.)

`tests/build/test_build_host_paths.py` (2):
- The checkout's path appears in no file of the set.
- Every `/root/…`, `/home/…` and `/Users/…` string, and every token holding `.claude`, found anywhere in the set (perl.wasm included) must also appear in the pinned tarball. That passes the library's own examples (CPAN's `/root/.cpan/…`, MakeMaker's `/home/foo`, IO::Compress's `/home/*.txt`).

Against 010's set it fails, naming `/root/.claude/rm-guard/…/refuse/rm` in `wasm32-wasi/Config_heavy.pl`; against this set both pass. EXECUTED. A first version matched `.claude` with a leading `[^…]*` and took 114 s on the 9 MB module. It now finds `.claude` with a byte search and takes about 1 s.

### The host's event buffer (009's R2-M1, 010's M1)

It is fixed in 010's review round (7bcaf73), with its canary probe `tests/build/test_poll_buffer.py`. In this branch, `sliced_poll` has the same property for every host call it makes: the host writes into a buffer of `sliced_poll`'s own, one event per subscription sent, and at most `cap` events, the table's own clocks dropped, reach the caller's buffer. The probe passes here. EXECUTED.

### Not changed here

Specification 004's text says no runner call can block. With this change a guest sleep no longer blocks past the deadline, but the text belongs to 004 and is left for its owner. `docs/sandbox.md` now says that a sleep ends at the deadline or fuel limit.

### The review round (2026-11-24)

These are the findings of `context/reviews/011-a-sleeping-guest-keeps-its-deadline-pr-2026-11-24.md`, fixed in one commit after a merge of dev at 961791f. dev holds 009 and 010 as landed, and every add/add conflict kept both sides.

- **M1, a relative wait measured on the wall clock.** Fixed. A relative subscription's due time is kept on `CLOCK_MONOTONIC`, whatever clock it names. An absolute one stays on its own clock. The last call sends the remaining time as a relative timeout on the subscription's own clock, as before. Stepping the host's clock was not tried: there is no way to step it here without root on the host. The checks of `test_sleep.py`, which sleep on `CLOCK_REALTIME` (`sleep`, `nanosleep`, `usleep`) and `CLOCK_MONOTONIC`, still last their time. EXECUTED.
- **L1, a caller's clock with userdata `UINT64_MAX` dropped.** Fixed. In a slice call the table's slice is the only clock, so every clock event there is the slice's. In the last call, clock events are dropped only when the wrapper added the zero clock (`own_clock`), and then the caller's clocks were taken out already. `test_poll_buffer.py`'s probe has a second case: a 20 ms clock with that userdata. With the table before this fix it gave `0 events`, and it now gives `1 events: the caller's clock`. EXECUTED.
- **L2, an unreadable clock sent unsliced.** Fixed. An absolute wait on a clock the guest cannot read answers EINVAL and is never sent to the host. A relative wait reads only the monotonic clock. Its last slice, at most 10 ms, still names its own clock, so the host answers for a clock it does not know.
- **L3, a wait on descriptors with no clock.** Fixed. It is sliced too, as a wait whose clock never comes due. So the deadline reaches a guest blocked on an inherited stdin or a socket. The cost is one pass through the loop every 10 ms, and only while nothing is ready.
- **L4, fuel overstated.** Fixed in `docs/sandbox.md`. The deadline bounds a sleep. A sleeping guest spends about 20,000 fuel a second (the review's 212 per slice; these notes' 550 for `select`), so a fuel cap alone bounds a sleep only loosely.
- **L5, the unmetered engine.** Fixed. `test_a_sleeping_guest_keeps_its_deadline_unmetered` runs `sleep 2**31 - 1` with `deadline_s=1` and no fuel, on the default engine, and it ends "deadline". EXECUTED.
- **L6, about 250 grants stop perl at start-up.** Answered. It is a clear EMFILE exit from the guest, never a crash. Refusing that many grants in `run()` belongs to 012's runner work.
- **Imports.** The new test files import from `tests/build/_build_helpers.py`, never from `conftest`, as 008 does. That file is 008's, byte for byte, so 008's merge meets the same file. It is not reformatted here.

    uv run --no-project --with pytest --with wasmtime==51.0.0 pytest tests/build -q             EXECUTED
    394 passed in 18.24s
    WASI_PERL_ARTIFACTS=$PWD/build/artifacts uv run pytest -q tests/escapes                      EXECUTED
    22 passed in 29.56s

