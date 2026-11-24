# 019 — The library index: module searches that ask the host only for files that exist

- **Status:** written by the orchestrator, 2026-11-24, under the owner's agreement of the speed work ("You can do all 10 actually", 2026-11-24).
- **Owner:** Clay Gendron
- **Kind:** the build recipe (`recipes/wasi-fd-table.c`, a generated index source), `scripts/30_library.sh` and a relink step, checks in `tests/build/`.
- **Depends on:** 013, 016, 018. It meets their changes to the descriptor table and the library.
- **Research it stands on:** `../research/2026-11-24-perf-startup.md` (study D) §1.3 and §8 rank 2; `../research/2026-11-24-perf-interp.md` §4.4 (`PERL_DISABLE_PMC`).

## Intent

A program that loads five common modules makes 155 stats, and 118 of them fail. The reasons:
- `@INC` names two `site_perl` folders that the library does not have;
- every `require` tries `X.pmc` before `X.pm` in each folder.

A failed stat costs 28.7 µs on the host, so this is about 3.4 ms of a 25 ms call (D §1.3, INFERRED). Under vfs, a library served by a provider would make each failed stat a provider call.

After this specification, the guest answers "no such file" for any path under the library that the library does not hold, without asking the host. `@INC`, `.pmc` handling and every other behaviour stay as they are.

## Decisions

1. **An index rather than a new layout.** Study D measured three ways to remove the failed stats:
   - dropping the absent folders from `@INC`: 72 stats;
   - merging the arch folder into `5.42`: 100;
   - adding `-DPERL_DISABLE_PMC` as well: 118.

   An index removes all 118 and changes nothing the program can see: `@INC` prints as natively configured, and a `.pmc` would still be preferred. So the index is chosen, and the layout and `PERL_DISABLE_PMC` are not changed.
2. **The index is compiled into `perl.wasm`.**
   - `30_library.sh` writes a sorted list of every file and folder under the library, relative to `/usr/local/lib/perl5`, as a generated C source of one sorted string array.
   - The build then links `perl.wasm` a second time with that object. The first link exists only because `installperl` needs a perl.
   - `SHA256SUMS` pins `perl.wasm` and the library together. So the index and the files cannot disagree in any set the runner accepts: its check refuses a library whose files are not exactly those listed (spec 012).
3. **Where the index answers.**
   - The descriptor table's `path_filestat_get` and `path_open`, for a path relative to the library's preopen, look the normalized path up by binary search.
   - A path not in the index answers `ENOENT` with no host call.
   - A path in the index goes to the host as today, and the special-file look and every other check still run.
   - A path that cannot be normalized within the library goes to the host unchanged, so its answer is the host's: `..` above the preopen, for example.
4. **Only the library is indexed.** Grants and `/dev` are untouched, and `/dev/urandom` is spec 013's.

## Checks (written first, seen failing; they count WASI calls)

1. **The five-module program** `use strict; use warnings; use POSIX; use JSON::PP; use List::Util; 1` makes no failed `path_filestat_get` under the library: 0, against 118 today. A counting probe linker records every WASI call and its errno.
2. **`-MPOSIX -e 1`** makes 0 failed stats under the library, against 73 today.
3. **Answers are unchanged:**
   - `require Not::There` dies with the native message, listing the same `@INC`;
   - `-e "/usr/local/lib/perl5/5.42/strict.pm"` is true;
   - `-e ".../5.42/nope.pm"` is false;
   - `-d ".../5.42/File"` is true;
   - `opendir` of a library folder lists what it lists today.
4. **A `.pmc` placed beside a `.pm`** in a test-only artifact set whose index lists it is preferred, as natively.
5. **The index matches `MANIFEST` exactly:** a build check.

## Acceptance

1. The checks pass (EXECUTED).
2. The one-liner oracle, the escape tests and the whole of Perl's `t/` suite pass, with no new exclusion.
3. A memo records the fuel and wall time of the module-loading programs before and after.
4. `CHANGELOG.md` under "Unreleased".
5. A fresh agent's review lands.
