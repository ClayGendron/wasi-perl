# 020 — Docs for agents: the fast way to write common programs

- **Status:** proposed, not yet agreed by the owner. Written by the orchestrator, 2026-11-24, for synthesis item 8. The owner agreed to build the ten items that day: "You can do all 10 actually, but do not change the API around perl to be different than wasi python, unless wasi python could adopt the same changes." That agrees the item, not this text. Its choices are the page's six items and the 20% margin on each ratio. They are to be asked with AskUserQuestion before the build starts. Amended the same day for the review `../reviews/specs-017-022-pr-2026-11-24.md` (M6; round 2: N6).
- **Owner:** Clay Gendron
- **Kind:** `docs/` (a new `docs/fast-perl.md`, linked from the README and `docs/modules.md`), with its examples run as checks in `tests/oneliners/`.
- **Depends on:** 018, whose modules it names.
- **Research it stands on:** `../research/2026-11-24-perf-libs.md` (study F) §6 and §7.3; `../research/2026-11-24-perf-startup.md` (study D) §1.2 and §8 rank 3.

## Intent

Agents write the Perl they have seen. Some common forms cost 4–6× more than an equal one in this sandbox. After this specification, one page says which form to use, with the measured numbers, and every example on it is a check.

## Decisions

1. **The page says the fact and the number, and stops**, as `AGENTS.md` asks of `docs/`. Each item:
   - **Whole-file regex work:** `perl -0777 -ne` or `-0777 -pi -e` instead of a line loop, when the pattern does not depend on lines. It runs 4.8–5.6× fewer instructions (F §6).
   - **Keyed sorts:** a Schwartzian transform instead of `split` inside the `sort` block: 5× (F §6).
   - **JSON:** `use JSON::MaybeXS` or `use Cpanel::JSON::XS`, not `JSON::PP`: 29× on decode (spec 018).
   - **CSV:** `use Text::CSV`, which uses the XS parser.
   - **Imports:** `use POSIX ()` or `use POSIX qw(floor ceil)` instead of a bare `use POSIX`. Its default import of 587 names costs 16 M instructions (D §1.2).
   - **`File::Find` and anything that calls `chdir`:** give `run()` a `cwd`. Without one, `File::Find` dies "Can't cd to /" (F §7.3).
2. **Numbers are counts.** The page quotes instruction ratios (fuel), not milliseconds, so it holds on any machine.
3. **Every example is a check.** Each one is a case in `tests/oneliners/`, compared byte for byte with native perl, as spec 006's cases are.
   - **The module cases.** The JSON and CSV examples use spec 018's modules, which 018 installs for native perl in `build/native-cpan`, outside the oracle's own perl. So these cases form their own group, and the oracle runs native perl for them with `PERL5LIB=build/native-cpan/lib/perl5`, as 018's check 1 does. Every other case runs as today, with no `PERL5LIB`.
   - **Another oracle.** An oracle named by `WASI_PERL_ORACLE` must have the same five distributions at 018's versions, on its own `@INC` or through `PERL5LIB`. The group first asks the oracle for the five `$VERSION`s, and skips with that reason when one differs or is missing. It never passes against a different version. Each instruction ratio the page claims is a check, with a margin of 20%. It counts fuel, never time.

## Acceptance

1. The page exists and is linked from the README and `docs/modules.md`.
2. Its examples pass as oracle cases, and its ratios pass as fuel checks (EXECUTED).
3. `CHANGELOG.md` under "Unreleased".
4. A fresh agent's review lands.
