# Review: research/monty-comparison (wasi-perl against Monty)

- **Reviewer:** an agent (Claude Opus 5.5), a fresh reviewer who did not write the change.
- **Branch:** `research/monty-comparison`, worktree `/home/user/wasi-perl-wt/monty`, `git diff origin/dev...HEAD` at `1f0fc96`. That covers two commits: `111702e` (the memo and its evidence) and `1f0fc96` (the roadmap line and `context/upstream/wasmtime-allow-blocking-current-thread.md`).
- **Read:** `AGENTS.md`; the memo; every script and raw results file in its evidence folder; `2026-11-24-perf-synthesis.md`, `2026-11-24-wasmtime-py-file-call-cost.md` and `2026-11-24-perf-runner.md`; specs 015, 016 and 018–022 as of `origin/dev`; Monty's source at `bdf4f6e` (`/home/user/wasi-perl/context/prior_art/monty`); and pydantic-monty 1.6.0's `_monty.pyi` in the scratch venv.
- **Labels:** as in the memo. EXECUTED means I ran it and saw the result. READ means I read it from a file, cited by path. INFERRED means my reasoning.

## How the numbers were checked

- **Tables §4.1–§4.5 and §4.7.** I regenerated them from `results/` (EXECUTED: `python3 tables.py`). Every cell matches.
- **Ratios in §1 and §5.** I recomputed them per session from the raw medians, with a short script over `perl-calls-*`, `monty-calls-*`, `perl-flag-on-*` and `native-times-*` (EXECUTED). All match except the cases in M4.
- **Output bytes.** `perl-stdout.json` equals `monty-stdout.json`. Both equal the `stdout` fields of `native-times-s1.json` and `-s2.json` for native perl and the Monty CLI, and the `stdout` of all four `perl-flag-*` files (EXECUTED: a Python comparison of the JSON files). The claim of the same bytes holds.

## Findings

### High

**H1. Monty's "like for like" mode is not 1.5–1.6 ms. The memo's conclusion that the fixed cost "cannot be closed without weakening the wall" rests on that number.**

- *Claim:* §1, §4.2 ("a fresh, empty process per session costs Monty 1.5–1.6 ms, the same as a fresh instance costs us"), §7 and §8 row 6.
- *What the source says (READ):*
  - With `max_checkouts_per_worker=1`, every session gets a fresh process, "usually spawned before it asked when `min_processes > 0`" (`crates/monty-pool/src/lib.rs:127-133`).
  - A retired worker frees its capacity, and `release_capacity` then calls `replenish`, which starts a replacement in the background (`crates/monty-pool/src/pool.rs:495-511`, `:562-567`, `:573-602`).
  - 1.6.0 behaves the same: `min_processes` defaults to 1, and "background refills restore it after a worker is recycled" (`pydantic_monty/_monty.pyi:714`, `:731-733`).
- *What that means:* `bench_monty.py calls fresh` makes its calls back to back. So its 1.5–1.6 ms measures the next call waiting on a spawn that started when the last call ended. It is a spawn race, not the cost an agent sees. An agent pauses between calls, and in that pause the next worker becomes ready (INFERRED from the source above).
- *Measured (EXECUTED, under the lock, `rev-monty/rm.py` in my scratch directory, hello with the data mounted, 15 calls each):*

  | mode | back to back | 50 ms between calls | 1 s between calls |
  |---|---:|---:|---:|
  | Monty `fresh`, a new process per session | 2.16 ms | **0.99 ms** (0.71–1.31) | **1.40 ms** (1.01–3.68) |
  | Monty `checkout`, worker reused | 0.148 ms | 0.87 ms (0.34–1.33) | 0.94 ms (0.59–1.33) |
  | wasi-perl, the memo's `perl-idle-s2.json` | 1.5–2.0 ms | | 2.9 ms (2.2–5.1) |

  So Monty gets a fresh process per session, the isolation the memo calls like for like, at about half our cost after a pause. The back-to-back figure is also unstable: I measured 2.16 ms against the memo's 1.5–1.6 ms. That is outside the 15% session spread, as a race would be.
- *Why it matters:* §8 row 6 offers only "Monty's way" as keeping one live interpreter, and rejects it for weakening the wall. But Monty's isolated mode closes the gap in a different way: it does the start-up work before the call arrives. That is a separate lever. For wasi-perl it would mean, for example, making the next Store and instance ahead of time, and taking `Store.close` (about 0.25 ms, by the fixed-cost memo §3.1) off the caller's path. It does not obviously weaken the wall, and it needs no change to the API (INFERRED). The memo does not consider it. The headline "the same as ours" is true only back to back.
- *Fix:*
  1. Add a fresh-mode run with gaps between calls (the table above is enough) and report it next to `perl-idle`.
  2. Correct §1, §4.2 and §7 to say that Monty pre-spawns its fresh workers, citing the lines above.
  3. Add a row to §8 for "start-up work done before the call, with no state shared between calls", and rank it. If it is not measured, mark it INFERRED. Row 6's verdict on reusing an interpreter can stay.

### Medium

**M1. "With the flag on, wasi-perl reads host files 2–8× faster than Monty" compares us only with Monty's pool, whose mounts cost a pipe round trip. Monty's own in-process path reads as fast as we do with the flag, and its numbers are in the raw results but left out of the table.**

- *Claim:* §1 ("2.3–3.0 ms against Monty's 22.6–23.0 ms"), §4.6, §5's last row, and §8 row 1 ("the only lever that turns a row from a loss against Monty into a 2–8× win"). The roadmap line in `1f0fc96` repeats it.
- *Evidence (READ, `native-times-s{1,2}.json`, `monty_cli`):*
  - `monty -c` reads the 100 small files in 2.7–3.2 ms, and the 10.2 MB file in 9.5–11.1 ms.
  - Those figures include starting a process, about 1.8–2.1 ms by the same file's `hello`.
  - Our flag-on reads take 2.3–3.0 ms and 5.8–7.6 ms on a warm runner.
- *What that means:* Monty's slow reads come from serving a mount across the worker's pipe, as §4.6 itself says. They do not come from its interpreter or its file design. Against Monty with no hop, the flag brings us level on small files and about 1.5× ahead on one big file. It does not make us 2–8× faster (INFERRED from those medians). §4.6's table gives native perl but not the Monty CLI, which was measured in the same run.
- *Fix:* add the `monty CLI` column to §4.6. In §1, §5 and §8 row 1, say that the 2–8× is against Monty's pool with mounts, which pays a round trip for every OS call. The flag stays the largest lever measured here, because it turns our worst row against the pool into our best. But "2–8× faster than Monty" should not stand unqualified, and the roadmap line should not carry it as such.

**M2. The upstream draft (`context/upstream/wasmtime-allow-blocking-current-thread.md`) gives numbers that do not match their source. It will be posted to a public tracker as it stands.**

- *Line:* "a WASI `fd_read` of 4 bytes cost: 27–57 µs through the C API; 0.33–0.94 µs in the CLI; 0.4–1.5 µs … with the flag."
- *Evidence (READ):*
  - The probe reads **1 byte** (`2026-11-24-wasmtime-py-file-call-cost/probe.c:8`; memo §3.2, "`fd_read`, 1 byte").
  - The three ranges are not `fd_read`'s. Each spans several kinds of call, from `fd_read` to `path_filestat_get` or `path_open` + `fd_close` (memo §3.2 table). `fd_read` alone is 26.7 µs stock, 0.47 µs with the flag and 0.33 µs in the CLI.
  - In the wasmtime-py text, "about 40 µs to about 1 µs" is a figure no table gives. The measured ranges are 26.7–57.0 µs and 0.42–1.49 µs.
  - "the CLI's `run` command already sets it" leaves out a condition. It sets the flag only when no timeout is given (memo §2.2; `src/common.rs:318-327` as that memo cites). The draft's last paragraph assumes the reader knows this.
- *What holds:*
  - The futex counts (4.9–18.4 per call, at most 0.2 with the flag) and the thread counts (15–19 against 9) match memo §3.4 and §4.1.
  - "19 lines" matches the patch: 19 added lines (EXECUTED: `grep -c '^+[^+]'` gives 17, plus 2 blank added lines).
  - The ordering note matches memo §3.3 and §4.1.
  - 37 → 2.6 ms is a fair midpoint of `read_small`'s flag-off and flag-on medians (35.7–39.5 → 2.3–3.0).
  - The draft names nothing private. It contains no person, no path on this machine and no project but wasmtime and wasmtime-py. The owner's name does not appear.
- *Fix:* state the numbers per call kind ("`fd_read` of 1 byte: 26.7 µs → 0.47 µs, CLI 0.33 µs; `path_filestat_get`: 57.0 → 1.24 µs, CLI 0.94 µs"), use the measured ranges in the wasmtime-py text, and write "sets it unless a timeout is given".

**M3. The security comparison in §7 is uneven in two places.**

- *Limits row.* "allocator-counted memory; time at checkpoints; the wall-clock backstop is off by default" reads as if Monty's memory and time limits were on by default. They are not:
  - "Omit `max_memory`, either duration key … to disable that limit", and every key is optional (`docs/resource-limits.md:59-61`, READ);
  - `request_timeout` defaults to no deadline in every local pool (`docs/security.md:466-469`, READ).

  Monty's only limits on by default are recursion depth and `max_suspensions`. wasi-perl's 1 GiB, 60 s and 16 MiB limits are on by default (`src/wasi_perl/runner.py:57`, READ). This is a difference in our favour that the memo understates. It also means Monty's timings ran with no limit set, while ours ran with the epoch deadline armed. That is a small cost, but it should be named in §9.
- *Escape row.* Monty's side gets a line count (147,970 lines in `crates/monty/src`, EXECUTED: `wc -l`) and an `unsafe` count (39, EXECUTED: `grep -rn "unsafe {" crates/monty/src | wc -l`; both reproduce). Our side gets "a bug in wasmtime, Cranelift or wasmtime-wasi", with no size, and it leaves out wasmtime-py's C API and our own runner (grants, the hard-link rule, the fd-table), which hold host-side decisions. Either give both sides a measure of size or neither, and list the runner in our trusted base.
- *Fix:* rewrite the two cells, and add the "no limits were set on Monty" line to §9.

**M4. Some ranges are built differently from what the text says, and `tables.py` does not produce §4.6 or §5.**

- *§4.6, the "wasi-perl, grant" column.* It is the range of four medians: `perl-calls-s{1,2}` and `perl-flag-off-s{1,2}`. So `read_big` is 47.3–53.9, where `calls` alone gives 47.3–50.2. `sum_csv` is 134–150 here and 138–150 in §4.4. `read_small` is 35.7–39.5 here and 36.0–38.1 by `tables.py`. The column does not say it mixes two harnesses.
- *§5, `sub_log`.* "0.008–0.013×" pairs medians across sessions, though the heading says "in each of the two sessions". The per-session ratios are 0.0087 and 0.0111 (EXECUTED).
- *§1, Text.* "5.8–24.6 s" for `strcat` mixes the pool's `checkout` (5.8–7.0 s) with the CLI (22.7–24.6 s). "5.8–9.4 s" for `sub_log` mixes `checkout` (5.8–8.5 s) with `session` (9.4 s) and the CLI (9.2 s). The ratios beside them, 90–450×, are against `checkout` alone, and they are right: 90.2–114.6× and 345–451× (EXECUTED). `strcat` also sits under "Text" in §1 but under CPU in §4.3.
- *Fix:* say which medians each range spans, or use `calls` alone in §4.6. Give §5 per-session ratios. Use `checkout` ranges in §1 to match its ratios. Extend `tables.py` to print §4.6 and §5, so that every table in the memo has the command that made it.

### Low

**L1. "Natively the two interpreters are level" is supported only as "within about 1.5× either way".**
- *What was run (EXECUTED, `native-instr.json`):* perl's instruction count over the Monty CLI's, on the nine tasks counted that are not library-bound, runs from 0.52 (`grep_log`) to 1.49 (`split_join`): `loop` 0.57, `sum_csv` 0.76, `wordfreq` 0.87, `strjoin` 1.19, `sort_ints` 1.25 and `fib25` 1.35.
- *What the run supports:* on `fib25` and `loop`, native perl ties or beats Monty in wall time, and wasm costs us 2.4–3.2× (EXECUTED: native-times against perl-calls). For those two tasks the inference that "Monty's lead is the wall's price" holds. On `strjoin`, `sort_ints` and `split_join`, Monty is ahead natively. There, part of its lead is its interpreter.
- *What is not counted:* Monty's two cliffs (`strcat`, `sub_log`). The Monty CLI is the same 32.4 MB binary as the worker (EXECUTED: `find_monty_binary()` returns `venvs/monty-venv/bin/monty`), so its counts are a fair stand-in for the worker. But the CLI runs `strcat` 4× slower than the pool does, so on that task it is not.
- *Fix:* say "within 1.5× either way on nine tasks", and narrow "the wall's price" to the tasks where it holds.

**L2. The 1,000-suspension cap is shown by a REPL piling up feeds, not by one call.**
- The cap is per checkout (`docs/security.md`, "`max_suspensions` bounds suspension events per checkout"; `docs/resource-limits.md:56`). The memo's evidence is a `session` run in which `read_small` was fed 12 times.
- I probed one default checkout (EXECUTED, under the lock, `rev-monty/cap.py`). One call that opens and reads 400 small files succeeds. One that reads 600 fails with "RuntimeError: suspension limit 1000 exceeded", so the limit falls at about 500 files per call.
- *Fix:* add this per-call probe to §6. It is stronger evidence for the claim.

**L3. §1 and §4.1 disagree on the first call after spec 015.** §1 gap 3 says spec 015 "draws level with Monty's 35–51 ms". §4.1 says our import-to-answer would be "about 55–60 ms, near Monty's", which is 1.1–1.7× Monty's. *Fix:* use §4.1's wording in §1.

**L4. The status of specs is not stated.**
- 015 and 016 are agreed. 018 and 019 are "proposed, not yet agreed by the owner" (their Status lines on `origin/dev`). §1 says "Spec 018 closes most of it" without that caveat.
- Row 4 cites `grep_log` 111 → 68 ms, which is study C without `isatty`. The mechanism the row names includes `isatty`, and spec 016 says "about 66 ms".
- The gap list orders by gain on this comparison, which differs from the synthesis's build order (015 is the synthesis's third item, 018 its sixth). That is fine, but say so.
- *Fix:* name each spec's status, cite 66 ms, and add one line on what the order means.

**L5. Reproducibility and the pin rule.**
- `measure.sh` and `counts.sh` hard-code `PERL_PY` to this worktree's `.venv`, but §3 says s1's warm calls ran from the 014 worktree at `72738d2`. The script as committed does not reproduce s1 as it was run. The `src/` difference is one comment, so the numbers are not affected.
- `workloads.py`'s docstring names a `check.py` that does not exist.
- pydantic-monty 1.6.0 and its runtime were installed unpinned (`uv pip install pydantic-monty`), and the 32 MB native worker ran on the host and under valgrind. `AGENTS.md` asks that downloads be pinned by URL and SHA-256 and run only as build tools or under wasmtime. A research tool in a scratch venv is a grey area, but the memo should record the wheels' SHA-256 and say where the binary ran.
- *Fix:* note the `PERL_PY` swap in `measure.sh`, drop or add `check.py`, and record the hashes of the three wheels.

## What holds

- **Every number in §4.1–§4.5 and §4.7 matches the raw results.** Recomputed by `tables.py`, and every ratio in §1 and §5 except those in M4 recomputed per session (EXECUTED).
- **The same bytes on all four runtimes, and in both flag arms** (EXECUTED, above).
- **The workloads do the same work on both sides.** Each pair prints the same answer from the same input. The one idiom difference, the keyed sort in `wordfreq`, is named in §4.5 and goes against us.
- **The flag arms have a proper control.** The same patched library with the flag off matches the stock runner.
- **The claims about Monty's design check out against its source:**
  - a bytecode VM (`crates/monty/src/bytecode/mod.rs:1-13`);
  - `enum Value` (`value.rs:50`);
  - the gray-marking cycle collector (`heap/mod.rs:71-90`, `:832-835`);
  - "MIT AND Python-2.0.1" (`crates/monty/Cargo.toml:5`);
  - a recycle count of 1,000 by default (`crates/monty-pool/src/lib.rs:72-74`, `:206`; 1.6.0 `_monty.pyi:719`);
  - the warning that "A compromised worker could see later sessions until it is replaced … Set the count to 1 when callers do not trust each other" (`docs/security.md:435-437`);
  - "no container, no seccomp filter and no virtual machine" (`docs/security.md:13`);
  - workers start with an empty environment (`docs/security.md:427`);
  - `max_suspensions` defaults to 1,000 and cannot be disabled (`docs/resource-limits.md:56`, `:61`);
  - `request_timeout` defaults to none (`docs/security.md:466-469`);
  - each `open` and each read is a one-shot OS call, and reads share one whole-file buffer (`docs/limitations/open.md:12-27`, `:126-132`);
  - the Python API has no in-process mode (`docs/security.md:507-511`).
- **The 1,000-suspension cap is real, and it binds within one call** (L2, EXECUTED).
- **Monty's published 0.77 ms after-idle number matches what we measured:** 0.76 ms (memo) and 0.94 ms (mine).
- **Gaps 2, 3, 4, 5, 7 and 8 match the synthesis and the specs.** 018's decode to about 46 ms. 015's first call to about 34–35 ms. Study A's L6 check of 10–15 ms (`perf-runner.md:84`). 019's index. 021's −2.7%. 022 limited to images made at install time. Gap 9's verdict on snapshots taken mid-run follows from the constitution's rule against Asyncify.
- **The prior-art row records the licence and commit,** as `AGENTS.md` asks.
- **The roadmap line in `1f0fc96` records the owner's words exactly,** apart from the 2–8× qualification of M1.

### Re-run (EXECUTED, under `flock /home/user/wasi-perl-wt/machine.lock`)

Scripts: `rev-monty/rp.py` and `rev-monty/rm.py` in my scratch directory, on the memo's data, artifacts and venvs. They take 100 calls for hello and 10 for the rest, after 2 warm-ups.

| task | wasi-perl, mine | memo | Monty `checkout`, mine | memo | Monty `fresh`, mine | memo |
|---|---:|---:|---:|---:|---:|---:|
| hello | 1.63 | 1.5–2.0 | 0.148 | 0.13–0.14 | 2.16 | 1.5–1.6 |
| fib25 | 55.0 | 56.7–57.5 | 19.7 | 19.1–20.4 | 21.2 | 21.8–22.4 |
| read_small | 38.5 | 36.0–38.1 | 24.4 | 22.6–23.0 | 27.5 | 25.1–27.2 |
| grep_log | 112.4 | 104–124 | 77.2 | 74.6–78.6 | 79.3 | 73.1–74.4 |

Every cell is within 7% of the memo's range, inside the 15% session spread, except Monty `fresh` hello at +35%. That is the spawn race of H1.

## Verdict

**Does not land as it stands.**

The measurements are sound and they reproduce. The bytes match. Most numbers trace exactly to their files. But the memo's headline judgement on the fixed cost of a call rests on Monty's fresh-process mode costing what ours costs, and that holds only back to back. Monty's source pre-spawns those workers, and with a pause between calls they cost 1.0–1.4 ms against our 2.9 ms (H1). The host-file headline also needs its qualification (M1). And the upstream draft should not go to a public tracker with mislabelled numbers (M2).

What would make it land:
- fix H1, which needs one short run and the text;
- fix M1–M4, which are text and a `tables.py` extension;
- answer the Lows or fix them.

## Round 2

- **Reviewer:** the same agent (Claude Opus 5.5), who did not write the change.
- **Reviewed:** `git diff 1f0fc96..26a6ec7`, the author's answer to every finding of round 1.
- **Read:**
  - the amended memo, every new script and results file, the upstream draft and the roadmap line;
  - spec 015's memo §8 (`2026-11-24-the-runners-fixed-cost-measured.md` on `spec/015-the-runners-checks-kept-across-calls`) and spec 016's memo §1 (on `spec/016-the-guests-reads-and-writes`);
  - wasmtime-py 51.0.0's `_store.py` and `_instance_pre.py` in the worktree's `.venv`, and wasmtime v51's `crates/c-api/src/linker.rs` and `store.rs` (`/home/user/wasi-perl-downloads/src/wasmtime-v51.0.0`).

### Round 1, answered

Every finding of round 1 is fixed or answered.
- **H1:** a paced series on both sides, §1, §4.2 and §7 corrected, and a new §8 row 2.
- **M1:** a `monty -c` column in §4.6 and §5, and the 2–10× now names Monty's pool. The roadmap line is qualified.
- **M2:** the draft's table is the file-call memo's §3.2, copied cell for cell from its three columns (READ, both files). The draft says "1 byte" and "unless a timeout is given". It still names nothing private: no person, no path on this machine, and no project but wasmtime and wasmtime-py.
- **M3:** both sides' limits are given at their defaults, with a run of Monty with limits set. Line and `unsafe` counts are given for both sides. My spot checks match, all EXECUTED with `wc -l` and `grep -rn "unsafe {"` outside `tests/`:
  - `crates/wasmtime`: 125,064 lines and 974 `unsafe` blocks;
  - `crates/c-api`: 9,829 and 67;
  - `crates/wasi`: 29,394 and 20;
  - `src/wasi_perl`: 2,488 lines.
- **M4:** `python3 tables.py` now prints every table, including §4.2's paced table, §4.6 and §5 (EXECUTED). Each prints as the memo shows it, and the byte check prints "True".
- **L1–L5:** all done.
  - §4.3 now says "within about 1.5× either way".
  - The cap probe gives 400–500 files ok and 550 and 600 failing (`monty-cap-s1.json`).
  - The wheels' hashes are recorded.
  - `measure.sh` records where `PERL_PY` ran.

### The new numbers, checked

- **The paced series matches its raw files** (EXECUTED: the six `*-paced-*.json` files):
  - wasi-perl: 1.913/2.077, 2.656/2.878 and 3.173/3.184 ms;
  - Monty `fresh`: 2.108/2.12, 0.942/0.787 and 0.965/1.178 ms;
  - Monty `checkout`: 0.146/0.198, 0.451/0.904 and 0.622/0.783 ms.

  Each pair is s1/s2 at no pause, 50 ms and 1 s. They give the memo's ranges.
- **Re-run under the lock** (EXECUTED: the author's `bench_perl.py paced` and `bench_monty.py paced fresh`, outputs in my scratch directory, `rev-monty/*-paced-*-r.json`):

  | side | back to back | 50 ms | 1 s |
  |---|---:|---:|---:|
  | wasi-perl | 1.60 | 2.20 | 3.13 |
  | Monty `fresh` | 2.37 | 0.85 | 1.16 |

  - Monty `fresh` reproduces inside the memo's ranges after both pauses.
  - Ours reproduces at 1 s.
  - At 50 ms ours ran 19% under the memo's 2.7–2.9 ms, so the ratio there is 2.6×. R2-L1 covers this.
- **The phase profile:** `_start` takes 783–804 µs bare and 914–967 µs with a read-only grant. `Store.close` takes 205–242 µs. `_set_up` takes 40–56 µs. All three match `perl-phases-*.json` (READ).
- **The limits run:** −6.9% to +10.7% (`monty-limits-s1.json`, EXECUTED by a short script).
- **Other new numbers that match:**
  - spec 015's 73.2 ms first call and 42.7–47.0 ms check (READ: that memo §8.2);
  - spec 016's 113.6 → 61.3 ms (READ: that memo §1);
  - row 1's "4.5–13× behind" the CLI;
  - "1.3–1.9× ahead" on `read_big`;
  - row 8's 1.65–1.82× fuel over native perl's instructions.
- **POD:** my own scan of `=…`/`=cut` blocks and `.pod` files gives 4.41 MB of the library's 22.61 MB (EXECUTED: a short script over the library folder). So the memo's 4.4 MB holds. R2-L2 covers the missing command.

### The premise of §8 row 2: it holds

The question is whether an instance can be made before the call's WASI context is set.
- **wasmtime-py** (READ):
  - `InstancePre.instantiate(store)` needs only a store (`wasmtime/_instance_pre.py`, `instantiate`);
  - `Store.set_wasi` only hands the config to the C API, with no check of order (`wasmtime/_store.py:92-98`).
- **The C API** (READ):
  - `wasmtime_context_set_wasi` stores the context in the store's data (`crates/c-api/src/store.rs:236-243`);
  - the WASI imports look it up only when the guest calls one: `ctx.wasi.as_mut().expect("wasi context must be populated")` (`crates/c-api/src/linker.rs:116-117`).
- **perl.wasm has no start section** (EXECUTED: a scan of its section ids gives 0–7, 9–11 and 13, with no 8). So nothing runs at instantiation.
- **A functional probe** (EXECUTED: `rev-monty/order.py` in my scratch directory) patched the runner to instantiate first, then build and set the `WasiConfig`, then call `_start`. `hello`, `read_small` and `grep_log` exited 0 and printed the memo's bytes (`hello\n`, `102400\n`, `28572\n`). That checks behaviour only, not time.

The memo may move this premise from "not checked" to READ and EXECUTED, citing the lines above.

### Findings, round 2

#### Medium

**R2-M1. §8 row 6, the library as one archive: the probe does not show what the row says it shows.**

- *Claim:* about half of 015's 43–47 ms check is "per-file work", and one archive would take the check to about 22 ms and the first call from about 73 to about 50 ms. It is labelled INFERRED, which is fair, and its inputs agree with 015's memo §8.2.
- *What the probe shows:* reading and hashing the 1,452 files one by one costs 22.52 ms, against 21.94 ms as one file (`hashprobe.json`). So the number of files adds about 0.6 ms to the reading and hashing.
- *What the probe leaves out:* it lists the folder (`sorted(... rglob ...)`) before the timer starts. So the walk is not in either figure (READ: `hashprobe.py`).
- *What that means:* the 21–25 ms of 015's check beyond hashing is the walk plus 015's own work per file. It is not measured. Spec 015's memo §8.2 already cut 33 ms of that kind of work with string paths alone (decision 6). So some of the rest may go without changing the library's layout, `%INC` or `__FILE__` (INFERRED).
- *What the row also omits:*
  - its cost on every call: each module load would go through a pure-Perl `@INC` hook reading the archive through WASI, at about 27 µs per file call today;
  - its overlap with spec 019's guest-side index (proposed).
- *Fix:*
  1. Time 015's check by phase: the walk, the stats, the hash and the comparison.
  2. List "the same layout, a leaner check" as the first option.
  3. Name the cost the archive adds to every call, and cite 019.

  Until then, §1's item 5 should not present 73 → 50 ms as the archive's gain.

**R2-M2. §8 row 2: the gain's upper end and the gap left afterwards are stated more favourably than the phase files support.**

- *The "rest of `_call`, which holds the unwrapped `InstancePre.instantiate`: 100–140 µs" cannot be derived from `perl-phases-*.json`:*
  - Instantiation runs inside `_drive` (`runner.py:401`), not in the rest of `_call`.
  - `_drive` minus `_start` and `exports` is 56–78 µs across the four files. `_call` minus its timed children is 59–101 µs (EXECUTED, by subtraction).
- *What the phase files support:* the parts that can move are `Store.close` (205–242 µs), `Store.__init__` and `set_limits` (17–21 µs), instantiation (56–78 µs), and the part of `_set_up` that does not depend on the call (the config and the two runtime preopens, about 20–25 µs). That is about 0.30–0.37 ms, before taking off the profile's own timers, which add about 0.1 ms (INFERRED). The upper end of 0.45 ms is not supported.
- *The gap left afterwards is wider than stated.* Taking 0.30–0.37 ms off each paced wasi-perl median, and dividing by Monty `fresh` in the same session and pause, leaves us 2.4–3.3× behind. The memo says "about 2–2.5×" (EXECUTED, by arithmetic on the paced files).
- *A risk the row does not list:* `memory_bytes` reaches the store through `set_limits` before instantiation. wasmtime-py's docs say a lower limit "will cause instantiation to fail if a module needs more memory" (`_store.py`, `set_limits`). So a store prepared with the default limit fits only calls that use the default. Any other call needs the present path, or a check against the instance's starting memory.
- *Fix:* give 0.30–0.37 ms with its subtraction, give 2.4–3.3× for what remains, and add the `memory_bytes` case to the risk cell.

#### Low

- **R2-L1.** "About 3×" after a pause is 2.7–3.7× in the memo's sessions. It is 2.6× at 50 ms in my re-run, where our call ran 19% faster than the memo's. Give the range.
- **R2-L2.** The 4.4 MB of POD is labelled EXECUTED, but no command or script for it is in the evidence folder. `AGENTS.md` asks for the command beside the number. My scan agrees, so only the command is missing.
- **R2-L3.** §1 item 1's "7–16×" is 7.1–15.3× per session, flag off over flag on (`perl-flag-*.json`). It should read "7–15×".
- **R2-L4.** "Level with the CLI" includes one session in which we are 12% slower on `read_small`: the ratio is 0.524–1.12 (§5). Write "0.52–1.12×" where §1 says "level to 1.9× ahead".

### What holds, round 2

- The H1 correction is right, and it is measured on both sides. It reproduces.
- The memo now states plainly that Monty's isolated mode is about 3× faster after a pause, and why.
- Row 2's premise holds, by the source and a functional probe.
- Row 2 is correctly marked a prototype candidate, not a specification.
- The host-file headline is now qualified wherever it appears, the roadmap line included.
- The upstream draft is accurate to its source and safe to file.
- The security table is now even-handed. It gives both sides' defaults, both sides' code size, and a note that size is not risk.
- Every table comes from one command, and every range says how it was built.

### Verdict, round 2

**Lands once R2-M1 and R2-M2 are fixed in text.** Neither needs a new timed run. The phase split that R2-M1 asks for can be a follow-up, if row 6 says that it is not yet measured and §1 stops quoting 73 → 50 ms as the archive's gain. The Lows can be fixed in the same commit or answered in writing.

## Round 3

- **Reviewer:** the same agent (Claude Opus 5.5), who did not write the change.
- **Reviewed:** `git diff 26a6ec7..dc15126`, the author's answer to round 2.

### Round 2 findings, checked

- **R2-M1: fixed.** §8 row 6 is now "a leaner library check".
  - It lists option (a) first: the same layout, a leaner check. Option (b), the archive, comes second.
  - Its gain is "unknown until measured". It is bounded at "at most the 21–25 ms beyond hashing" (INFERRED), and its next step is a phase timing of 015's check.
  - It names the archive's cost on every call: a pure-Perl `@INC` hook reading over WASI, at about 27 µs per file call.
  - It names the overlap with spec 019.
  - §4.1 now says the walk is outside the probe's timer.
  - §1 item 5 no longer quotes 73 → 50 ms. A grep of the memo and the roadmap finds no stale figure: no "73 → 50", no "0.3–0.45", no "2–2.5×", no "about 3×", no "7–16" and no "level to 1.9" remain, except where §10 quotes them as fixed (EXECUTED: `grep`).
- **R2-M2: fixed.** §8 row 2 and §1 item 2 now use 0.30–0.37 ms and 2.4–3.3×.
  - §4.2 places instantiation inside `_drive`, at 56–78 µs, and gives the rest of `_call` as 59–101 µs.
  - The risk cell names `memory_bytes`: a store made ahead fits only calls with the default memory limit, and any other call takes today's path.
  - The premise is now stated as holding, with the round-2 citations and probe.
- **R2-L1 to L4: fixed.**
  - The pause ratio is 2.6–3.7×, with the source of each end given.
  - The file-call lever is 7–15×.
  - The comparison with the CLI is 0.52–1.12×, in §1, §4.6, §8 row 1 and the roadmap line.
  - `podscan.py` is added with its command beside the number. Re-run, it gives the committed `podscan.json` exactly: 4,406,479 of 22,612,689 bytes, a share of 0.195 (EXECUTED: `python3 podscan.py <artifacts>/lib <scratch>/podscan.json`). That agrees with my own scan's 4.41 MB.
- **Nothing else moved.** `python3 tables.py` still prints "same stdout on every runtime and arm: True" and the same tables (EXECUTED). The diff touches only the memo, `podscan.py`, its result and the roadmap line.

### Verdict, round 3

**Lands.**
- Every finding of rounds 1 and 2 is fixed.
- The numbers trace to their raw files and reproduce under the lock.
- The comparisons say what is like for like.
- The two new levers are labelled as estimates: row 2 is inferred from the phase profile, and row 6 is not yet measured.
- The upstream draft is accurate and names nothing private.
