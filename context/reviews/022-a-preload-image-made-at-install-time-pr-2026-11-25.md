# Review: 022, a preload image the embedder makes at install time (PR #32)

Reviewer: a fresh agent (Claude Opus 5.5, `claude-opus-5-5`) that did not write the change. 2026-11-25.

- **Reviewed:** `git diff origin/dev...HEAD` on `spec/022-a-preload-image` at head `02e9994` (`02e99943f03a6abbf5327c0112d9753b5c050335`), 72 files. I read it against `AGENTS.md`, `context/constitution.md`, the specification with its amendments A1–A14 and landing notes, the prototype memo, the acceptance memo and `docs/snapshots.md`.
- **Sources:** wasi-python cloned read-only into my scratch folder. Its HEAD is `f9bf678`, the commit the spec cites. Perl 5.42.3's generated tree in `build/target` (`perl.c`, `locale.c`, `perlio.c`).
- **Where:** the builder's `build/` in this worktree. Probes and a mutated copy of `src/wasi_perl` are in `/tmp/claude-0/-home-user-wasi-perl/ecbe2564-8e44-50cd-9bc9-1c1b283f37e5/scratchpad/` (`p/` and `mut/`). The mutated copy ran first on `PYTHONPATH`, so the worktree's code was never edited. Logs are in `build/logs/review/`. Nothing here was timed, so no run needed the machine lock.
- **CI:** run 45522662926 is green on all seven jobs, at this head (EXECUTED: `gh api repos/ClayGendron/wasi-perl/actions/runs/45522662926/jobs`).

Labels: EXECUTED means I ran it and saw the result. READ means I read it in a file. INFERRED means my reasoning.

## Verdict, round 1

**Does not land, round 1.** The design is sound, and most of the build is careful. The writer, the cache key, the file checks, the fallback, the grant rules and the API all hold up. The sandbox holds on the image path. But a call from an image does not get its own locale, `PERL_UNICODE` or `PERLIO` (H1). It gets the making's, silently, with `image_sha256` set. Under `LC_ALL=C`, the one-liner oracle's own base environment, the image's output differs from perl.wasm's and from native perl's. The spec says this row agrees with perl.wasm, and the drift suite cannot see it. That breaks the owner's rule for this work: a call from an image behaves as a call from perl.wasm, apart from four listed drifts.

One round fixes it: a reset or a fallback in the reactor or the runner, drift cases for it, and a spec amendment. M1 should be fixed in the same round. Everything else can follow.

## Findings, round 1

### High

**H1. A call from an image keeps the making's locale, `PERL_UNICODE` and `PERLIO`.**

Perl reads these once, at start-up, and the reactor does not read them again:
- `LC_ALL`, `LC_*`, `LANG` and `PERL_UNICODE` are read by `Perl_init_i18nl10n`, which `perl_construct` calls (READ: `build/target/locale.c:9176-9177` and `:9518-9519`). `PL_unicode` and `PL_utf8locale` are set there. The second `perl_parse` reads only the `-C` switch (`perl.c:3731`).
- `PERLIO` is read when `PL_def_layerlist` is first made (`perlio.c:1483-1486`). That is at the first open, at the making.
- `run` in `recipes/wasi-reactor.c` reseeds, resets `%ENV`, the special variables and `@INC`, but calls neither again (READ).

So each call gets what the making's environment set, and its own is ignored. All EXECUTED, with `scratchpad/p/probe1.py` to `probe5.py`, comparing `run()` from perl.wasm and from an image of the same set:

| call | perl.wasm | from the image |
|---|---|---|
| `env={"PERL_UNICODE": "S"}`, `print chr(233)` | `c3 a9` | `e9` |
| `env={"PERL_UNICODE": "A"}`, `length($ARGV[0])` of `é` | `1` | `2` |
| `env={"PERLIO": ":crlf"}`, a file written with `"a\n"` | `61 0d 0a` | `61 0a` |
| `env={"LC_ALL": "C"}`, `${^UTF8LOCALE}` and `setlocale(LC_CTYPE)` | `0 C` | `1 C.UTF-8` |
| `env={"LC_ALL": "C"}`, `-CSL -e 'print qq{\x{e9}\n}'` | `e9 0a` | `c3 a9 0a` |
| `env={"LC_ALL": "C"}`, `-Mlocale -e 'print chr(0xC9) =~ /\w/ ? 1 : 0'` | `0` | `1` |
| `env={"LC_ALL": "C"}`, `-Mlocale -e 'print lc(chr(0xC9)) eq chr(0xE9) ? 1 : 0'` | `0` | `1` |
| `LANG=C`, `LC_CTYPE=C`, `LC_ALL=POSIX` or `LC_ALL=en_US.UTF-8` | that locale | `C.UTF-8` |

It also works the other way. A making with `env={"PERL_UNICODE": "SDA"}` gives every call a `:utf8` layer on STDOUT. A making with `env={"PERLIO": ":crlf"}` gives every call CRLF line ends. A making with `env={"LC_ALL": "C"}` gives every call `${^UTF8LOCALE}` 0, even a call with `LC_ALL=C.UTF-8`. `make_snapshot` accepts all three.

Why it matters:
- It is silent. Every row above has `Completed.image_sha256` set, so the embedder is told the call ran from the image, and nothing says its environment was not honoured.
- `LC_ALL=C` is `BASE_ENV` in `tests/oneliners/oneliner_oracle.py:99-103`. Native perl agrees with perl.wasm on these rows; the image does not.
- Decision 4's table says of locale: "under wasi-libc's C and C.UTF-8 it agrees with perl.wasm". That is not true for C. The drift case `locale` sets `LC_ALL=C.UTF-8`, which is what the image already has, and `lang_utf8` sets `LANG=C.UTF-8`. Neither can see the drift (READ: `tests/snapshot/test_drift.py:71,79`). The memo's corpus run kept `LC_ALL=C` and found 226 equal, because no case uses `-CL`, `use locale` or these variables (EXECUTED: I reran it, `build/logs/review/oneliners_pkg.txt`).
- The spec refuses `PERL_JSON_PP_USE_B` at the making because it "would be frozen into JSON::PP for every call". `PERL_UNICODE` and `PERLIO` are frozen the same way, and are not refused.

What would fix it, for the builder to choose and the spec to record:
- **Redo it per call.** In `run`, before the parse, set `PL_unicode` from `PERL_UNICODE` as `locale.c:9518-9521` does, and run the locale set-up again from this instance's environment. Then the image honours the call's values. `PERLIO` is harder, since the standard handles are open, so it could fall back.
- **Or fall back.** Add the names to `FROZEN_ENV`: the call runs perl.wasm and `image_sha256` is `None`, and the making refuses them. For the `LC_*` family that would send many calls to perl.wasm, so redoing the locale is the better fix there (INFERRED).
- Either way: drift cases for `LC_ALL=C`, `LANG=C`, `PERL_UNICODE=S`, `PERLIO=:crlf` and a making with each, which fail today; Decision 4's locale row corrected; and `docs/snapshots.md`'s "What is fresh" saying which of these a call gets.

### Medium

**M1. CI never runs the one-liner corpus or the escape suite from an image.**

- The oracle's tests set `PERL_HASH_SEED=0` and `PERL_PERTURB_KEYS=0`. Decision 6 sends those calls to perl.wasm. So in CI every corpus case falls back, and acceptance 2 is proved only by the memo's driver, `oneliners_on_image.py`, which is not a test (READ).
- The escape, mount and runner suites never pass `snapshot=`. Only the 47 drift cases and the image checks compare an image with perl.wasm in CI.
- I ran the escape, mount and runner suites with every call that could start from the package image sent there, by a pytest plugin that wraps `runner._run` (`scratchpad/p/imageplug.py`). 244 calls ran from the image. The only real failure was `test_every_call_has_its_own_seeds`, which is the accepted shared seed. The other seven failures were the plugin's own: stand-in sets with no reactor (EXECUTED: `build/logs/review/suites_on_image.txt`). So the wall holds on the image path today: deadline, fuel, output caps, read-only grants, `../` climbs, links, mounts, and the refusals of `system`, `fork` and `%SIG`.
- Today's state is good, but nothing keeps it. Make the memo's corpus run a check, the corpus minus the two seed names from the package image against perl.wasm, as the memo does. Run a sample of the escape tests from an image too. Both belong in the job that runs `tests/snapshot`.

### Low

**L1. The image hash check in `Snapshot.load` is never exercised.**
- I removed the `image_sha256` comparison in `_snapshot._read`. All four damaged-file cases still passed (EXECUTED: `scratchpad/p/mutate.sh nohashcheck`, 4 passed).
- The "flipped" case changes a byte of the zlib stream, and zlib's own checksum refuses it first. A file whose stream is valid but whose image is not the header's is never tried. Add one: recompress the image with one byte changed.
- The other important paths fail when removed (EXECUTED, each in the mutated copy): the fallback (2 drift checks fail), the set check in `run` (1 fails), `load`'s refusal of a file others can read (1 fails), the folder's publication on save (1 fails), the `memory_bytes` check (1 fails) and the open-file refusal (1 fails).

**L2. The rebuild that is not byte-reproducible has a known cause, and it is not this change.**
- Both binaries hold `Compiled at Nov 25 2026 00:21:50` (EXECUTED: `grep -a -o "Compiled at [A-Za-z0-9 :]*"` on `build/artifacts/perl.wasm` and `perl-reactor.wasm`). Another worktree's `perl.wasm` holds `Nov 24 2026 11:17:09`. That is `perl -V`'s line, from `__DATE__` and `__TIME__` in `perl.c`. So every recompile of `perl.c` changes `perl.wasm`, on dev too (INFERRED).
- It does not block. But the memo says "the cause was not looked into". It should name this cause. Setting `SOURCE_DATE_EPOCH`, or a recipe define, is a follow-up for the build.

**L3. `test_perturb_keys_and_the_internal_seed_work_as_natively` is a rare flake that predates this change.**
- The file is unchanged here (EXECUTED: `git diff origin/dev...HEAD --stat -- tests/build/test_randomness.py` is empty).
- With `PERL_PERTURB_KEYS=1`, its program has about 64 orders of near-equal chance: 300 native runs gave a top count of 11, and 300 perl.wasm runs gave 64 orders (EXECUTED). Three runs all equal is then about 1 in 4,000 per side (INFERRED). The check asks for more than one order in three runs.
- It does not block. Record it, and take more runs in a follow-up.

**L4. `release.yml`'s six assets have not run.** It agrees with `ci.yml`, which now uploads `perl-reactor.wasm` among the release files (READ). It can only run on a tag. It does not block. Check it on the first release candidate.

**L5. `lib/deprecate.t` from the package image is an instance of `inc_keys`.** It fails in both places, after the same 10 oks, with another message, because Carp is already loaded (READ: the memo, §5). Since it fails from perl.wasm too, `exclusions-image.toml` does not apply. The memo's explanation is enough. It does not block.

### Nit

**N1.** `docs/snapshots.md`'s "What is fresh in every call" lists the environment as fresh. After H1 it should say exactly which start-up settings come from the call.

**N2.** The changelog gives "about 0.7 ms more" for `-e 1`. `docs/snapshots.md`'s table gives 1.1 → 1.8 ms. The memo's two runs give 0.63 and 0.73 ms. These agree, but one figure in all three places would be easier to keep true.

## What I checked and found right

- **Lint and the fast suite.** `ruff check` and `ruff format --check` on `src` and `tests` are clean. `uv run pytest -m "not slow"`: 661 passed, 2 skipped (EXECUTED: `build/logs/review/notslow.txt`).
- **The suites.** `WASI_PERL_ORACLE=build/native-install/bin/perl uv run pytest tests/snapshot tests/runner tests/escapes tests/mounts tests/oneliners`: 1058 passed, 2 skipped (EXECUTED: `build/logs/review/full_guest.txt`). So every check passes. H1 is a gap the checks do not cover, not a failure among them. The build checks the change touches, `tests/build/test_reactor.py`, `test_reads_and_writes.py`, `test_wasm_opt.py`, `test_perl_wasm.py`, `test_lib_index.py` and `test_modules.py`: 261 passed (EXECUTED).
- **The API, row by row.** The fixture `tests/fixtures/wasi_python_public_api.txt` equals wasi-python's `tests/public_api.txt` at `f9bf678`, its HEAD (EXECUTED: `diff`). I printed both listings' blocks for `make_snapshot`, `make_package_snapshot`, `Snapshot`, `SnapshotOrigin`, `run`, `Completed` and `Artifacts`. Every difference is a row of the spec's table: rows 6 and 13 for the keywords, 8 for the three names, 14 for `Artifacts`, and the order of `snapshot` and `image_sha256`. Each row says how wasi-python could adopt it (EXECUTED: `scratchpad/p/api.py`).
- **Isolation between calls.** Each call is a fresh instance of the image module, so nothing a call writes reaches the next one. The only non-memory state, the stack pointer, is checked at `prepare`'s end. I probed `$0`, `@ARGV`, `@INC`, `%ENV`, `$$`, `umask`, `${^GLOBAL_PHASE}` in `BEGIN`, `$^H`, `-T`, `-W`, `-x`, `-i.bak`, `srand`'s return, `times`, `Time::HiRes`, `readdir("/")`, `stat _`, the refusals and a `select` left by the prelude. All equal perl.wasm (EXECUTED: `probe1.py`, `probe2.py`). `TZ` is equal too (`probe5.py`).
- **The image file.** `save` writes 0600 through `mkstemp`. `load` opens with `O_NOFOLLOW` beneath its folder's descriptor. It refuses a link, a second name, another owner, and a mode others may read or write. It checks the header's fields by type, the size, the stream's end and bytes past it (READ; the mode check's mutation fails, L1). The header's digests are not signed. That is right: a file only you can write is as trusted as you, and the binding is a check of fit, not of origin (INFERRED).
- **The cache.** The `.cwasm` name is `SHA-256("wasi-perl image module 1\0" + reactor digest + "\0" + image_sha256)`, truncated as perl.wasm's is, under the cache's own checks. The image's hash is the computed one for a making and the checked one for a load, so a name always names its bytes (READ: `_engines.image_module`, `_snapshot.start`).
- **The writer.** `_image_module` copies the code section byte for byte and drops `_initialize`, `prepare`, the data count and the custom sections. It refuses an image below the reactor's own pages or past a maximum. The coalescer closes exactly `runs − cap` gaps, shortest and lowest first (READ).
- **Grants beside saved images.** `publish_snapshot_folder` and `hold` compare folder identities under one lock, with links followed by `chain`, so a link cannot dodge either rule (READ).
- **The changes to existing checks** are not weakenings. The cache checks now compare the cache's three counts by name. Before, they compared the whole dict, which now has two more keys. The 017 listing check moves `files` one place up for `image_sha256`, in wasi-python's order. The link-step check counts two links. `test_no_wasm_opt` asserts instead of skipping. The SIMD check pins the exact set (READ).
- **The folded follow-ups.** 457873c, db03931 and 4a1f354 tighten checks, and their build checks pass (EXECUTED). af55f00 and 989e2ed correct prose. 3c7772f's arithmetic is right: 1,557,752 / 1,600,585 − 1 = −2.676% (INFERRED).
- **Code comments** name no specification or review number (EXECUTED: a grep of the added lines).

## Round 2

- **Reviewed:** `git diff 02e9994 b2e4aee`, the fix commit `b2e4aee` (`b2e4aeee2097c74d464fa5c83607879ad7464e5e`), 14 files. It changes no recipe or build script, so the round 1 build in `build/` is the one it runs on (READ: the diff's file list).
- **CI:** run 45527490121 is green on all seven jobs, at `b2e4aee` (EXECUTED: `gh api .../actions/runs/45527490121`). From the four wheel legs' logs: `235 checks from the image (at least 119), 0 skipped, 218 calls started from it`, with 215 on Windows, and `234 passed, 1 xfailed` (231 passed and 3 skipped on Windows). Guest checks went from 581 in round 1's run to 843 (EXECUTED: the job logs).
- **Here:** `ruff check` and `ruff format --check` are clean. `-m "not slow"`: 661 passed, 2 skipped. `WASI_PERL_ORACLE=build/native-install/bin/perl uv run pytest tests/snapshot tests/runner tests/escapes tests/mounts tests/oneliners`: 1320 passed, 2 skipped. That is round 1's 1058 plus 262 new. `WASI_PERL_TESTS_FROM_IMAGE=1 uv run pytest tests/escapes tests/mounts`: 234 passed, 1 xfailed, 218 calls from the image (EXECUTED: `build/logs/review/r2-*.txt`).

### The round 1 findings, checked

- **H1: fixed, by a visible fallback.** I reran every row of H1's table on `b2e4aee` (EXECUTED: `scratchpad/p/r2probe.py`). Each gives perl.wasm's bytes, with `image_sha256` `None`: `PERL_UNICODE=S` and `=A`, `PERLIO=:crlf`, and `LC_ALL=C` with `${^UTF8LOCALE}`, `-CSL`, `-Mlocale` `\w` and `lc`. So do `LANG=C`, `LC_CTYPE=C`, `LC_ALL=POSIX` and `LC_ALL=en_US.UTF-8`. A making refuses `PERL_UNICODE`, `PERLIO` and `LC_ALL`. A call with no locale variable still starts from the image and equals perl.wasm (`1C.UTF-8`). So does `PERL5OPT=-CS`, since the parse reads `PERL5OPT`'s `-C` per call.
- **The audit (A15).** I checked its rows against the sources. `PERL_DESTRUCT_LEVEL` is read in `perl_destruct` (`perl.c:648`). `debug_hash_seed` runs from `perl_parse` (`perl.c:1792`). `PERL5OPT` is read in `S_parse_body` (`perl.c:2405`). `S_init_perllib` is in the parse. So the "per call" rows are per call (READ). The other C reads I found outside the files it names are `PERL_DL_NONLAZY` (`ext/DynaLoader/dlutils.c:118`), which this build without dynamic loading does not use, and `HOME`/`USERPROFILE` in `File::Glob`, read at each glob (READ). The C audit is complete for what a call can see. It misses one read in Perl code, R2-L1.
- **Coverage, narrowed or not.** The ported `env` case had `LANG=C`, so it now falls back. The new `env_plain` keeps `%ENV` checked from the image (READ). `locale` and `lang_utf8` now fall back, so no "same" case checks the image's own locale with no locale variable (R2-N2). The corpus from the image leaves out `LC_ALL` but compares against native answers made under `LC_ALL=C`. So a case that read the locale would fail, not pass falsely (INFERRED). No check got weaker in a way that hides a fault.
- **M1 (a): fixed.** `test_guest_from_the_package_image_matches_native` runs the 226 cases from the package image and asserts that each call started there. It ran in CI: 581 + 226 + 36 new image checks = 843 (EXECUTED: the logs). Locally the whole oneliners run passed (above).
- **M1 (b): fixed.** The routing in `tests/conftest.py` wraps `runner._run`, which `run` and the making both go through (READ). It cannot run nothing unseen. The shared-seed check is a strict xfail, so it fails if the routing stops. The gate also requires a nonzero count of calls from the image. Leaving `tests/runner` out is right: its stand-in sets have no reactor, and its cache counts would change by design (READ; round 1's plugin run agrees).
- **L1: fixed.** "another image" is a sound stream with one image byte changed. With the hash check removed it fails (EXECUTED: `mutate.sh r2-nohashcheck`, 1 failed). With `LC_ALL` removed from `FROZEN_ENV`, the per-name checks fail (EXECUTED: `mutate.sh r2-nolcall`, 2 failed).
- **L2–L5, N1, N2.** The memo names the cause of the rebuild that is not reproducible, and the roadmap carries `SOURCE_DATE_EPOCH`. L3 and L4 are recorded for follow-ups. L5 is answered. The "What is fresh" list names the per-call variables and the ones that fall back. The memo gives the `-e 1` figure once, 0.64–0.76 ms, and the changelog and docs quote it. Those are the four medians of §1 (READ).

### New findings

None blocks.

**R2-L1. A module the package image loaded keeps the making's `PERL_ENCODE_DEBUG`.**
- `Encode.pm:7`, `Encode/Alias.pm:5` and `Encode/Encoding.pm:14` read it in `use constant DEBUG`, at compile time, as JSON::PP reads `PERL_JSON_PP_USE_B` (READ). I grepped the 26 files the package image loads; no other reads `%ENV` as it loads.
- EXECUTED (`r2probe.py`): with `env={"PERL_ENCODE_DEBUG": "1"}`, `-MEncode -e 'print Encode::DEBUG() ? 1 : 0'` prints `1` and seven `delete $Alias{…}` lines on stderr from perl.wasm. From the package image it prints `0`, with no stderr and `image_sha256` set.
- It is a debug switch, so the harm is small. But it is the A15 class, found in Perl code, which the audit (C sources only) did not search. The same holds for any module a prelude loads. Follow-up: add `PERL_ENCODE_DEBUG` to `FROZEN_ENV`. Say in `docs/snapshots.md`'s "What is frozen" that a module the image loaded keeps whatever it read from the environment as it loaded.

**R2-L2. The wheel legs' guest gate does not count the 226 cases from the image.** Its floor went from 432 to 433 while the count ran went from 581 to 843 (EXECUTED: the logs). A skip or an error among them is caught. A change that stopped collecting them is not. Follow-up: add the corpus count once more to the floor.

**R2-N1.** `docs/snapshots.md` keeps two blank lines where the table's `-e 1` row was.

**R2-N2.** Add a "same" drift case for the image's own locale with no locale variable, such as `setlocale(LC_CTYPE)` and `${^UTF8LOCALE}`. It equals perl.wasm today (EXECUTED: `1C.UTF-8` from both), but no case now holds it.

The fallback costs calls that set `LANG` or `LC_ALL` the image's speed. Embedders often set those. The docs say so plainly, and the roadmap holds the re-reading that would win it back. That is a cost, not a fault.

## Verdict, round 2

**Lands.** H1 is fixed as the orchestrator decided. Every case I found gives perl.wasm's bytes, by a fallback that `Completed.image_sha256` shows. The audit behind it is right for Perl's C. M1 is fixed. The corpus and the escape and mount walls now run from an image on every wheel leg, and gates fail if they do not. L1's check is real. Nothing in the fix commit broke a check, here or in CI. R2-L1 and R2-L2 are follow-ups, not a third round.
