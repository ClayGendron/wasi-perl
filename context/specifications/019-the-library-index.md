# 019 — The library index: module searches that ask the host only for files that exist

- **Status:** agreed by the owner 2026-11-24, through AskUserQuestion. Asked to agree 019 and 021 together, the owner answered "Agree both (Recommended)". That agrees this text's new choices: an index rather than a new layout, the build order of Decision 5 with its new `scripts/40_link.sh`, and the rule for a set passed with `artifacts=`. Written by the orchestrator, 2026-11-24, for synthesis item 7, a leaner `@INC`. The owner agreed to build the ten items that day: "You can do all 10 actually, but do not change the API around perl to be different than wasi python, unless wasi python could adopt the same changes." Amended the same day for the review `../reviews/specs-017-022-pr-2026-11-24.md` (H2, H3, M5, M6; round 2: N2, N4).
- **Owner:** Clay Gendron
- **Kind:**
  - the build recipe: `recipes/wasi-fd-table.c`, and a new `recipes/wasi-lib-index-empty.c`;
  - the scripts: `scripts/20_target.sh`, `scripts/30_library.sh`, `scripts/lib.sh` and `scripts/build.sh`; new `scripts/lib_index.sh` and `scripts/40_link.sh`;
  - checks in `tests/build/`;
  - the `Artifacts` docstring in `src/wasi_perl/_artifacts.py`, and `docs/modules.md`.
- **Depends on:** 013, 016 and 018. It meets their changes to the descriptor table and the library. 013 finds the `/dev` preopen by its name, and this finds the library's preopen the same way.
- **Research it stands on:** `../research/2026-11-24-perf-startup.md` (study D) §1.3 and §8 rank 2; `../research/2026-11-24-perf-interp.md` §4.4 (`PERL_DISABLE_PMC`).

Labels: EXECUTED means run and seen. READ means read from a file. INFERRED means reasoned.

## Intent

A program that loads five common modules makes 155 stats, and 118 of them fail. The reasons:
- `@INC` names two `site_perl` folders that the library does not have;
- every `require` tries `X.pmc` before `X.pm` in each folder.

A failed stat costs 28.7 µs on the host, so this is about 3.4 ms of a 25 ms call (D §1.3, INFERRED). Under vfs, a library served by a provider would make each failed stat a provider call.

After this specification, the guest itself answers "no such file" for a library path the library does not hold, wherever the host would answer the same. `@INC`, `.pmc` handling, every errno and every other behaviour stay as they are.

## Terms

- **The index:** a sorted list of every file and folder under the library, relative to `/usr/local/lib/perl5`, compiled into `perl.wasm`.
- **The library's preopen:** the preopened folder whose name, from `fd_prestat_dir_name`, is `/usr/local/lib/perl5`. The descriptor table finds it by that name at first use, as spec 013 finds `/dev`.
- **A plain path:** a path relative to a preopen with no empty component, no `..` component and no trailing `/`. `.` components are dropped first, as spec 013 drops them.

## Decisions

### 1. An index rather than a new layout

Study D measured three ways to remove the failed stats:
- dropping the absent folders from `@INC`: 72 stats;
- merging the arch folder into `5.42`: 100;
- adding `-DPERL_DISABLE_PMC` as well: 118.

An index removes all 118 and changes nothing the program can see. `@INC` prints as natively configured, and a `.pmc` would still be preferred. So the index is chosen, and the layout and `PERL_DISABLE_PMC` are not changed.

### 2. The index is compiled into `perl.wasm`

- **The source.** `scripts/lib_index.sh <library folder> <out.c>` writes the index as one C source. It defines one `const` structure with three members:
  - the count;
  - the sorted array of names;
  - a marker string, `wasi-perl-lib-index-sha256:<hex>`. The hex is the SHA-256 of the names, one per line, in the array's order.
- **The order.** The names are sorted with `LC_ALL=C sort`, which is byte order, the order `strcmp` gives. The table's binary search uses `strcmp`.
- **No escaping.** `30_library.sh` already refuses a library file name that holds a blank, a quote or a backslash (`scripts/30_library.sh:57-61`, READ). So every name is a plain C string literal, and the generator escapes nothing.
- **The marker.** The structure holds the marker, so the linker keeps the marker whenever it keeps the index. Check 5 finds it in the shipped module.
- **The empty index.** `recipes/wasi-lib-index-empty.c` defines the same structure with a count of 0. A count of 0 means "no index": every lookup goes to the host, as today.
  - `20_target.sh` compiles it and adds it to `build/stubs/libwasi-perl-fd-table.a`, as a member beside the descriptor table.
  - `wasi-cc` links that archive into every program it links (`recipes/wasi-cc`, READ). So the first link of `perl` gets the empty index, and so does every test probe that `wasi-cc` links. Each behaves exactly as today.
  - A link that names an index object before the archive gets that index instead. The linker pulls an archive member only for a symbol still undefined, so the empty member is then never pulled.

### 3. Where the index answers, and where it does not

The index answers only where the host's answer is already known. Every other call goes to the host unchanged, so the host's errno stays the answer.

- **What it judges.** Three calls, and only when the descriptor is the library's preopen and the path is plain:
  - the descriptor table's `path_filestat_get`;
  - the look's own stat before an open (spec 010, `look()` in `recipes/wasi-fd-table.c`);
  - `path_open`.
- **`path_filestat_get`, and the look's stat.**
  - A path in the index goes to the host, as today.
  - A path below a name the index holds as a file, such as `5.42/strict.pm/x`, answers `ENOTDIR` with no host call. That is the host's answer today (EXECUTED, the table below).
  - Any other path answers `ENOENT` with no host call.
  - For the look, either answer means "the name is not there". The look then lets the open go on, as it does today when the host answers so.
- **`path_open`.** The index answers it only when the open cannot be refused for what it asks. That is when all three hold:
  - its `oflags` hold none of `CREAT`, `TRUNC` and `EXCL`;
  - its base rights hold none of `FD_WRITE`, `FD_DATASYNC`, `FD_ALLOCATE` and `FD_FILESTAT_SET_SIZE`;
  - its `fdflags` hold none of `APPEND`, `DSYNC`, `RSYNC` and `SYNC`.

  Such an open of a path not in the index answers `ENOENT`, or `ENOTDIR` below a file, with no host call. Every other open goes to the host. So a create, a truncation, an append and a read-write open such as `+<` keep the host's `EPERM` for the read-only library.
- **Why the open is judged by what it asks.**
  - wasmtime refuses a write-shaped open on a read-only preopen with `EPERM` before it looks the name up. The built `perl.wasm` shows it today: `open "+<"` of a missing library file, and `open ">"` under a missing library folder, are both "Operation not permitted" (EXECUTED: the table below).
  - An index that answered `ENOENT` for such an open would change two escape tests. `lib-new` (`tests/escapes/test_escapes.py:365`) expects `PERM` at :376, and `create-in-lib` (:386) expects `PERM` at :394 (READ). Both would become "No such file or directory".
- **What is not judged.** These go to the host unchanged:
  - a path that is not plain, such as `..` above the preopen (`lib-climb`, :173) or a trailing `/`;
  - a path on any other descriptor;
  - every other call: `path_create_directory`, `path_unlink_file`, `path_rename`, `fd_readdir` and the rest.
- **Today's answers, which the index keeps** (EXECUTED: the built perl.wasm of `/home/user/wasi-perl-wt/perf-base/build/artifacts`, through `run(["-e", …])`, 2026-11-24):

  | Perl, under `/usr/local/lib/perl5` | today | with the index |
  |---|---|---|
  | `open "<"`, `5.42/strict.pm/x` | `ENOTDIR` | `ENOTDIR`, from the index |
  | `stat`, `5.42/strict.pm/x` | `ENOTDIR` | `ENOTDIR`, from the index |
  | `open "<"`, `5.42/nope.pm` | `ENOENT` | `ENOENT`, from the index |
  | `opendir`, `5.42/Nope` | `ENOENT` | `ENOENT`, from the index |
  | `open "<"`, `5.42/strict.pm/` | `ENOTDIR` | `ENOTDIR`, from the host: not plain |
  | `open ">"`, `x` (`lib-new`) | `EPERM` | `EPERM`, from the host |
  | `open ">"`, `5.42/Evil.pm` (`create-in-lib`) | `EPERM` | `EPERM`, from the host |
  | `open ">>"`, `5.42/Evil.pm` | `EPERM` | `EPERM`, from the host |
  | `open "+<"`, `5.42/Evil.pm` | `EPERM` | `EPERM`, from the host |
  | `open ">"`, `nope/x` | `EPERM` | `EPERM`, from the host |

- **Amended 2026-11-24, after round 1 of the review** (`../reviews/019-the-library-index-pr-2026-11-24.md`, H1, M1, L3). The owner's choice, answered 2026-11-24: keep host behaviour, the index answers only where every host agrees. Every host includes one whose names ignore case (macOS and Windows) and one whose names keep it (Linux). So:
  - the names keep their case and are sorted by their ASCII-lowercased bytes (`LC_ALL=C`), and the search compares with A to Z as a to z; `lib_index.sh` refuses two names that differ only in case;
  - the index answers ENOENT only when no name of the path, in any case, is in the index and no prefix of it is a file in any case; it answers ENOTDIR only below a file reached by its exact names; everything else goes to the host, a name the index holds in another case among it;
  - a path goes to the host whole if a component ends in `.` or a blank, or holds `~`, `\` or `:` (Windows rewrites them), has a stem (the part before its first `.`) that is a Windows device name in any case (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`, `CONIN$`, `CONOUT$`; the coordinator's ruling under the owner's rule, 2026-11-24), is longer than 255 bytes (the host answers ENAMETOOLONG), or holds a byte of 0x80 or above (wasmtime answers EILSEQ for one that is not UTF-8);
  - the look's own stat is no longer judged. `path_open` already judges a read before the look, so the counts do not change, and a write-shaped open keeps the look's refusal of a special file.

### 4. Only the library is indexed

Grants, mounts and `/dev` are untouched. `/dev/urandom` is spec 013's.

### 5. The build's order

`20_target.sh` links `perl` before `30_library.sh` can lay out the library, and the index is made from that library. So `perl.wasm` is linked a second time, after the library. That relink is the last link. Specs 021 and 022 add their steps to the same script, at the places named here.

| step | script | stamp | what it does |
|---|---|---|---|
| 1 | `20_target.sh` | `.target.stamp` | compiles the stubs, the descriptor table and the empty index (Decision 2), and links `build/target/perl` with the empty index. It no longer copies `perl` into `build/artifacts/`, so only shipped bytes are ever there. Its up-to-date test (`20_target.sh:26`) looks for `build/target/perl` instead of `$ART/perl.wasm`. Spec 022 compiles the reactor's object here, after the final `make`, which writes the `perlmain.c` it includes |
| 2 | `30_library.sh` | `.library.stamp` | runs `installperl`, which installs the tree's linked `perl` and so needs it; lays out `lib/` and `dev/`, with spec 018's pure-Perl copies; writes `MANIFEST` in its one format, `<sha256>  <path>` for every file under `lib/` and `dev/` (`scripts/30_library.sh:62-69`, READ). It no longer writes `SHA256SUMS` |
| 3 | `40_link.sh` | `.link.stamp` | the index: `lib_index.sh build/artifacts/lib/perl5 build/index/lib-index.c`, compiled by `wasi-cc -O2 -c` to `build/index/lib-index.o` |
| 4 | `40_link.sh` | `.link.stamp` | spec 022's reactor link, once 022 is built (its Decision 2) |
| 5 | `40_link.sh` | `.link.stamp` | the relink: removes `build/target/perl` and runs `make perl` in the tree, with `libs` set to the index object followed by the configured `libs` (`recipes/config.sh:935`, READ). Every other input of the rule is up to date, so make runs only the link. The result is copied to `build/link/perl.wasm` |
| 6 | `40_link.sh` | `.link.stamp` | spec 021's `wasm-opt`, once 021 is built, from `build/link/` into `build/artifacts/`. Until then, a copy |
| 7 | `40_link.sh` | `.link.stamp` | `SHA256SUMS`, last, after every link and every `wasm-opt`: every file of the set but itself |

- **Why the reactor link comes before the relink.** Both use the tree's `perl` rule. Linking the command last leaves the tree's `perl` the command, which a later `installperl` copies.
- **The stamps.**
  - `.link.stamp` is `stamp_of` over `40_link.sh`, `lib_index.sh`, `lib.sh` and `.library.stamp`, and over spec 021's binaryen pin once 021 is built.
  - `.library.stamp` already covers `.target.stamp` (`30_library.sh:24`, READ). So a change anywhere before step 3 relinks.
  - `40_link.sh` is up to date only when its stamp matches and `build/artifacts/SHA256SUMS` exists.
  - `30_library.sh`'s own up-to-date test looks for `MANIFEST` instead of `SHA256SUMS`, which is no longer its output. Its precondition (`30_library.sh:23`) looks for `.target.stamp` and `build/target/perl` instead of `$ART/perl.wasm`, which step 1 no longer writes.
- **The shared lister.** The `listed` function, which writes the sorted `<sha256>  <path>` lines, moves from `30_library.sh` to `lib.sh`. `30_library.sh` uses it for `MANIFEST` and `40_link.sh` for `SHA256SUMS`. Both keep today's bytes and order.
- **`build.sh`** runs `40_link.sh` after `30_library.sh`.

### 6. A set the runner finds, and a set the embedder passes

- **A set the runner finds** (`artifacts()`) is checked against its `SHA256SUMS`: `perl.wasm` and `MANIFEST` on every call, and the library tree once per stamp. No library file may be unlisted or missing (`_artifacts.checked`, `_check_tree`, READ). `SHA256SUMS` pins `perl.wasm` and the library together. So in such a set the index and the files cannot disagree.
- **A set passed as `run(artifacts=...)`** is used as it is given, unchecked (`_artifacts.py:44-47`; `runner.py:290-293`, READ). The embedder vouches for it, and its `perl.wasm`'s index must list its library. A file added to such a library is not found: a stat or a read-only open of it answers `ENOENT`.
- **The rule, documented.** The `Artifacts` docstring and `docs/modules.md` say it: a set's `perl.wasm` serves only the library it was built with. To add Perl code, grant it in a folder of its own and name that folder with `-I` or `PERL5LIB`.
- **Why a passed set is not checked here:** using it unchecked is spec 004's choice, and wasi-python's `run(artifacts=...)` is unchecked too. Changing it would change the API beside wasi-python's.

## Checks (written first, seen failing; they count WASI calls)

1. **The five-module program** `use strict; use warnings; use POSIX; use JSON::PP; use List::Util; 1` makes no failed `path_filestat_get` under the library: 0, against 118 today. A counting probe linker records every WASI call and its errno.
2. **`-MPOSIX -e 1`** makes 0 failed stats under the library, against 73 today.
3. **Answers are unchanged:**
   - `require Not::There` dies with the native message, listing the same `@INC`;
   - `-e "/usr/local/lib/perl5/5.42/strict.pm"` is true;
   - `-e ".../5.42/nope.pm"` is false, with `$!{ENOENT}`;
   - `-d ".../5.42/File"` is true;
   - `opendir` of a library folder lists what it lists today;
   - each row of Decision 3's table gives its errno, and the probe linker shows each row's call reaching the host or not, as the table says;
   - `tests/escapes/test_escapes.py` passes unchanged, `lib-new` and `create-in-lib` included.
4. **The lookup, at the C level** (`tests/build/test_lib_index.py`, built as `test_poll_buffer.py` builds its probe).
   - **The probe.** It is linked with `wasi-cc`, the recipe's descriptor table, and an index that `lib_index.sh` makes from a test folder holding `5.42/A.pm` and `5.42/A.pmc`. The table's calls to the host's `path_filestat_get` and `path_open` are renamed to counting functions that forward them. It runs under the wasmtime CLI, with the test folder preopened at `/usr/local/lib/perl5`.
   - A stat of `5.42/A.pmc` reaches the host once, and succeeds.
   - A stat of `5.42/B.pmc` answers `ENOENT` with no host call.
   - A read-only open of `5.42/A.pm/x` answers `ENOTDIR` with no host call.
   - An open of `5.42/B.pm` with `CREAT` reaches the host.
   - The same probe linked with the empty index sends every stat to the host.

   So a `.pmc` the index lists is asked of the host. Perl's preference for it, which this specification does not touch, then works as natively. No second `perl.wasm` is built.
5. **The index matches `MANIFEST`** (a build check):
   - the index's files equal `MANIFEST`'s `lib/` entries, with `lib/perl5/` taken off;
   - its folders are exactly those entries' ancestors, not counting `/usr/local/lib/perl5` itself;
   - the shipped `perl.wasm` holds the marker `wasi-perl-lib-index-sha256:` once, followed by the SHA-256 of that list.
6. **A passed set is not indexed again.** A copy of the built set with one added file, `lib/perl5/5.42/Added.pm`, is passed as `artifacts=`. `require Added` dies "Can't locate Added.pm in @INC". The same file in a grant named with `-I` loads. This pins Decision 6's documented rule.
7. **The build's order** (a build check, from the set, the logs and the stamps):
   - `SHA256SUMS` lists the `perl.wasm` in `build/artifacts/` with its hash, and is not older than any file it lists;
   - the log of step 5's `make` holds one link command and no compile;
   - a touched `lib_index.sh` reruns `40_link.sh` alone; a touched `30_library.sh` reruns 30 and 40.

## Acceptance

1. The checks pass (EXECUTED).
2. The one-liner oracle, the escape tests and the whole of Perl's `t/` suite pass, with no new exclusion.
3. A memo records the fuel and wall time of the module-loading programs before and after.
4. `CHANGELOG.md` under "Unreleased".
5. A fresh agent's review lands.

## Landing notes

Built 2026-11-24 by the implementer (Claude Opus 5.5) on `spec/019-the-library-index`, from dev at `8897c22`, Linux x86-64, 4 vCPUs, CPython 3.13, wasmtime-py 51.0.0, as root. **Not yet reviewed.** Every claim below was run (EXECUTED) unless it says otherwise. Every build and every timed or fuel run held `flock /home/user/wasi-perl-wt/machine.lock`.

### What was built

- **Decision 1.** The index; `@INC`, the layout and `PERL_DISABLE_PMC` are unchanged. The guest's `@INC` is `/usr/local/lib/perl5/5.42/site_perl/wasm32-wasi /usr/local/lib/perl5/5.42/site_perl /usr/local/lib/perl5/5.42/wasm32-wasi /usr/local/lib/perl5/5.42`, as on dev (check 3 pins it).
- **Decision 2.** `scripts/lib_index.sh <folder> <out.c>` writes the names (`find`, then sorted by their ASCII-lowercased bytes with `LC_ALL=C sort`; round 1), their count and the marker `wasi-perl-lib-index-sha256:<hex>`, the SHA-256 of the names one per line. The structure is declared once, in a new `recipes/include/wasi-perl-lib-index.h`, which the table, the empty index and the generated source include: `count`, a pointer to the names and a pointer to the marker. `recipes/wasi-lib-index-empty.c` has a count of 0 and the marker of no names (the SHA-256 of nothing). `20_target.sh` compiles it and adds it to `libwasi-perl-fd-table.a` beside the table. The built index has 1,728 names (1,461 files and 267 folders), and `perl.wasm` grows by 54,726 bytes to 9,442,629 (round 1's build).
- **Decision 3.** `recipes/wasi-fd-table.c`: `start()` finds the grant named `/usr/local/lib/perl5` as it finds `/dev` (one `is_named` for both). `index_answer()` reads a path through the library's real descriptor: it drops `.` components, gives up (asks the host) on an empty or `..` component, a path of more than 1,023 bytes, the grant itself, or a count of 0; then walks the path's folders by binary search, as amended in round 1 (below). A name is a folder when the first name not below `name/` starts with `name/`. It is asked by `path_filestat_get` and by `path_open` when `asks_only_to_read()` holds: none of `CREAT`, `TRUNC`, `EXCL`; none of `FD_WRITE`, `FD_DATASYNC`, `FD_ALLOCATE`, `FD_FILESTAT_SET_SIZE`; none of `APPEND`, `DSYNC`, `RSYNC`, `SYNC`.
- **Decision 4.** Only the library's grant is judged; `/dev`, grants and every other call are untouched.
- **Decision 5.** As the table says. Steps 4 and 6 are empty: step 6 is a copy from `build/link/perl.wasm`. The relink is `make perl "libs=build/index/lib-index.o <libs>"`, with `<libs>` read from the tree's substituted `config.sh`, and no `-j`. `.link.stamp` is `stamp_of` over `40_link.sh`, `lib_index.sh`, `lib.sh` and `.library.stamp`. `listed` moved to `lib.sh` unchanged; `MANIFEST` is byte for byte dev's, and `SHA256SUMS` has the same 1,464 lines with the new `perl.wasm` hash.
- **Decision 6.** The `Artifacts` docstring and a new "Adding Perl code" section of `docs/modules.md`.

### Choices the specification left to the implementer

- **A shared header.** The specification names the table, the empty index and the generator; the structure they share is declared in `recipes/include/wasi-perl-lib-index.h`, which `wasi-cc`'s `-I` already reaches, so its three users cannot disagree on it. INFERRED: a recipe file, the smallest way to keep one definition.
- **What `lib_index.sh` refuses.** Anything that is neither a file nor a folder; an empty folder, since a folder is known only by the names below it, so an empty one would read as a file; a name holding a blank, a control character, a quote or a backslash, which `30_library.sh` already refuses; and a library folder that holds nothing. `30_library.sh` deletes empty folders, so the built library has none.
- **Where the index is judged on a real descriptor.** The table compares the real descriptor with the library grant's number, which is the same as its virtual number and never freed. So a duplicate of the grant's number, which names the same host descriptor, is judged too. INFERRED: it is the library's grant.
- **The checks' homes.** Check 4, 5 and 7 are in `tests/build/test_lib_index.py`; checks 1, 2, 3 and 6 in `tests/build/test_library_lookups.py`. The counts come from the wasmtime command line's trace, as `test_reads_and_writes.py` counts calls, since a Python host function cannot pass a WASI call on. That command line grants every folder writable, so the traced runs use a copy of the library; the errors a program sees are read under wasmtime-py with the library read-only. Check 7's "touched script" bullet is checked from the stamps (`.link.stamp` is recomputed from its four inputs; neither earlier step reads `lib_index.sh`), and was run once by hand: a line added to `lib_index.sh` gave "30_library: up to date, 40_link: ok"; a line added to `30_library.sh` gave "30_library: ok, 40_link: ok"; with both reverted and rebuilt, a further `build.sh` gave every step "up to date" (`build/logs/touch-lib-index.out`, `touch-library.out`, `up-to-date.out`).
- **CI.** The build job's gate that fails on a skipped CPAN check now counts the two new files too. Locally: a full run, "71 checks … (at least 40), 0 skipped", exit 0; a run with no `perl.wasm`, 38 skipped, exit 1.
- **One existing check's stand-in changed.** `tests/build/test_cpan_unpack.py`'s scratch checkouts stood in `build/artifacts/perl.wasm` for 30_library.sh's precondition; they now stand in `build/target/perl`, which Decision 5 makes the precondition. What the checks assert is unchanged. The review should see it.

### Findings

- **A host whose file names ignore case** (round 0's H1, fixed in round 1). Round 0's index answered ENOENT for `5.42/Strict.pm`, which macOS and Windows find. Round 1 sends it to the host; see below.
- **`recipes/config.sh`'s `libs`** is at line 940, not 935 as the specification cites (READ). Nothing depends on the line.
- **Fuel rises 1.2–1.3%** for the module-loading programs, from the lookups in WebAssembly, while their wall time falls 15–16% (`../research/2026-11-24-the-library-index-measured.md`).

### The checks

| check | before the code (dev's set) | after |
|---|---|---|
| 1: five modules, failed stats under the library | failed: 118 | passed: 0 (36 found, unchanged) |
| 2: `-MPOSIX -e 1`, failed stats | failed: 73 | passed: 0 (22 found, unchanged) |
| 3: `require Not::There` as natively, same `@INC` | passed (a pin) | passed |
| 3: `-e`, `-d`, `-f`, `$!{ENOENT}`, `opendir` listing | passed (a pin) | passed |
| 3: each row's errno (10 cases) | passed (a pin) | passed |
| 3: each row reaches the host or not (10 cases) | the four "from the index" rows failed; the six host rows passed | passed |
| 3: `tests/escapes` unchanged | passed | passed |
| 4: the C-level probe (6 cases) and `lib_index.sh`'s order and refusals (2) | failed: no `lib_index.sh` | passed |
| 5: the index is `MANIFEST`'s library; the shipped marker once, with its hash | failed (marker); skipped (no `build/index`) | passed |
| 6: a passed set with an added file | failed: `require Added` loaded it | passed: "Can't locate Added.pm in @INC"; from a `-I` grant it loads |
| 7: `SHA256SUMS` last and pins `perl.wasm` | passed: dev's `30_library.sh` wrote it last too | passed |
| 7: one link and no compile in the relink's log; the link's stamp | skipped: no `40_link.sh` outputs | passed |

### The acceptance runs

| command | result |
|---|---|
| `flock …machine.lock scripts/build.sh` from an empty `build/` | 00–40 ok; 1,462 files in `MANIFEST`, 1,464 in `SHA256SUMS`; a second run: every step "up to date" |
| `uv run pytest tests/build -ra` | 509 passed, 0 skipped |
| `WASI_PERL_ORACLE=build/native-install/bin/perl uv run pytest tests/runner tests/escapes tests/oneliners -ra` | 701 passed, 2 skipped ("root can write every folder"; "macOS, run in CI"), as on dev; the same against the set `assemble_artifacts.py` copies into the package |
| `uv run python tests/perl_t/run_suite.py --quiet --list-failures --jobs 4` | 616 run, 439 passed, 177 excluded, 0 failed, 0 stale exclusions, 0 regressed (19,052 guarded oks); `tests/perl_t/exclusions.toml` unchanged |
| `uv run pytest -m "not slow"` | 496 passed, 2 skipped, 245 deselected |
| `uv run ruff check`; `uv run ruff format --check` | all checks passed; 40 files already formatted |

### Round 1 of the review

The review (`../reviews/019-the-library-index-pr-2026-11-24.md`) found H1, M1 and L1–L4. All are fixed in one commit; Decision 3's amendment records the owner's choice for H1.

- **H1.** `lib_index.sh` sorts the names by their lowercased bytes and refuses two that differ only in case. `index_answer()` searches with A to Z as a to z, narrowing the range at each folder so each comparison starts at the folder's end. ENOENT only when a name is in the index in no case, ENOTDIR only below a file reached by its exact names, anything else the host's. A component ending in `.` or a blank, or holding `~`, `\` or `:`, goes to the host. `tests/runner/test_library_case.py` runs `-e`, `open` and `require Strict` on every CI leg and asserts what a plain open of the host's own `Strict.pm` does; the wheel legs' gate counts it, so it cannot skip there.
- **M1.** A component over 255 bytes, or any byte of 0x80 or above, goes to the host: ENAMETOOLONG and EILSEQ are back.
- **L1.** `lib_index.sh` and `30_library.sh` refuse a name holding a newline, looked for with `find -name`, not `grep`.
- **L2.** `40_link.sh` is up to date only while `build/artifacts/perl.wasm` exists.
- **L3.** The index is out of `look()`; the counts are unchanged (118 → 0, 73 → 0). `docs/modules.md` and the `Artifacts` docstring say the library must be mounted read-only. The C probe's create check now expects the look's stat to reach the host (1 stat, 1 open), as on dev.
- **L4.** The test comment now says what a row is, in words.
- **Windows device names** (asked after round 1, by the same rule). A component whose stem is `CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`, `CONIN$` or `CONOUT$`, in any case, sends the path to the host. Five C-probe rows (`CON`, `con.pm`, `LPT1.x`, `Nul.txt/x`, `conout$`) and one host-call row (`stat` of `5.42/con.pm`) failed on the build before it (6 failed) and pass after. Fuel: the five modules 123,227,113, `-MPOSIX` 72,010,878, `-e 1` 1,600,492 (EXECUTED; +1.25% and +1.3% on dev's).
- **CI's first run on round 1** (`d4db0ab`, run 45484104559) failed on macOS and Windows in `test_require_in_another_case_loads_as_the_host_finds_it`: the host found `Strict.pm`, and `strict.pm` itself then dies "Incorrect use of pragma 'strict' at /usr/local/lib/perl5/5.42/Strict.pm line 41", as it would on dev. The check was wrong about Perl, not the index (EXECUTED: both job logs). The check now loads `TEXT::ABBREV`, which has no such guard, and a second check asserts that `require Strict` dies in the host's `Strict.pm`, not "Can't locate", wherever a plain open of the host's `Strict.pm` succeeds. `tests/build/test_library_lookups.py` gains a check that exercises the found branch on Linux too: a library copy holding `5.42/Strict.pm` and `5.42/TEXT/ABBREV.pm` answers as a case-insensitive host would, and the guest loads the one and dies in the other.

The new checks, each seen failing on round 0's build before the code changed (EXECUTED: 37 failed, 44 passed):

| check | on round 0's code | after |
|---|---|---|
| `lib_index.sh`: folded order; refuses a newline, two files and two folders by case (4) | failed | passed |
| C probe: 14 paths the host may answer otherwise reach the host | failed: answered by the index | passed |
| C probe: a create's look stat reaches the host | failed: 0 stats | passed |
| C probe: names in no case answered without the host (4) | passed | passed |
| row errnos: two ENAMETOOLONG, two EILSEQ | failed: ENOENT, ENOTDIR | passed |
| row host calls: other case (4), trailing `.`, `~`, long (2), not UTF-8 (2) | failed: 0 host calls | passed: 1 |
| `require Strict` asks the host once, for `5.42/Strict.pm` | failed: 0 | passed |
| `30_library.sh` refuses a newline | failed: "cp: cannot stat 'x'" | passed |
| `40_link.sh` not up to date without `perl.wasm` | failed: "up to date" | passed |
| `tests/runner/test_library_case.py` (2) | passed on Linux, as on dev; INFERRED to fail on round 0 on a case-insensitive host | passed |
| the built index and marker, sorted by the new order | failed | passed |

Round 1's runs (EXECUTED):

| command | result |
|---|---|
| `flock …machine.lock scripts/build.sh` | 20–40 ok; `perl.wasm` `ade0a15c…616a`, 9,442,629 bytes |
| `uv run pytest tests/build -ra` | 551 passed, 0 skipped |
| `WASI_PERL_ORACLE=build/native-install/bin/perl uv run pytest tests/runner tests/escapes tests/oneliners -ra` | 703 passed, 2 skipped, as before; the wheel legs' gate script on this run's JUnit: "259 guest checks (at least 249 … 2 library-case checks), 0 skipped" |
| `uv run python tests/perl_t/run_suite.py --quiet --list-failures --jobs 4` | 616 run, 439 passed, 177 excluded, 0 failed, 0 regressed; exclusions unchanged |
| `uv run pytest -m "not slow"` | 496 passed, 2 skipped, 247 deselected |
| `uv run ruff check`; `uv run ruff format --check` | clean; 41 files already formatted |
| `count_calls.sh`, both programs | 22 ok / 0 failed and 36 ok / 0 failed stats; opens 11 and 18, as round 0 |
