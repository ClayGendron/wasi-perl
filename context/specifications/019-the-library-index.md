# 019 — The library index: module searches that ask the host only for files that exist

- **Status:** proposed, not yet agreed by the owner. Written by the orchestrator, 2026-11-24, for synthesis item 7, a leaner `@INC`. The owner agreed to build the ten items that day: "You can do all 10 actually, but do not change the API around perl to be different than wasi python, unless wasi python could adopt the same changes." That agrees the item, not this text. These choices are new here and await the owner: an index rather than a new layout, the build order of Decision 5 with its new `scripts/40_link.sh`, and the rule for a set passed with `artifacts=`. They are to be asked with AskUserQuestion before the build starts. Amended the same day for the review `../reviews/specs-017-022-pr-2026-11-24.md` (H2, H3, M5, M6; round 2: N2, N4).
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
