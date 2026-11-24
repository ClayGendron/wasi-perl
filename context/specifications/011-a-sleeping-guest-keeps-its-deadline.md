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

(Written when built.)
