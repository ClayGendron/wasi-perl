# 018 — JSON, CSV and diff for agents

- **Status:** written by the orchestrator, 2026-11-24, under the owner's agreement of the speed work ("You can do all 10 actually", 2026-11-24).
- **Owner:** Clay Gendron
- **Kind:** the build recipe (new `recipes/cpan-xs` and `recipes/cpan-pure` lists, `recipes/config.sh`), `scripts/00_download.sh`, `scripts/20_target.sh`, `scripts/30_library.sh`, `downloads/SHA256SUMS`, checks in `tests/build/`, `docs/modules.md`.
- **Depends on:** 002; it builds after 016, whose recipe changes it meets.
- **Research it stands on:** `../research/2026-11-24-perf-libs.md` (study F), its §4, §8 and §9, and the two prototype diffs in its evidence folder.

## Intent

Agents over vfs read and write JSON and CSV more than anything but plain text. Today:
- JSON::PP decodes 1.4 MB in 1,350 ms, against 61 ms for wasi-python's `json`;
- the core has no fast CSV parser;
- the core has no diff module.

After this specification, `use Cpanel::JSON::XS` decodes the same file in about 46 ms, 29× faster. `use Text::CSV` parses through Text::CSV_XS, 9× faster than `Text::ParseWords`. `use Algorithm::Diff` gives a real diff. All of it comes from pinned CPAN releases, built statically with no change to their source.

wasi-python's API is untouched. This is the guest's library only.

## Decisions

1. **Two XS distributions, linked statically:**
   - Cpanel-JSON-XS 4.83;
   - Text-CSV_XS 1.65.

   Both are under Perl's own terms, and both built for wasm32-wasip1 unchanged on the first try (F §4). Together they add about 124 KB to `perl.wasm`, and no import (F §8). They are listed in `recipes/cpan-xs`, one line per distribution: the tarball's name and the folder under `cpan/` it unpacks as. `config.sh` appends their module names to `static_ext`, `extensions` and `known_extensions`, so Perl's own `make_ext.pl` builds them as spec 002 builds the core's.
2. **Three pure-Perl distributions, in the library:**
   - JSON-MaybeXS 1.004008, which picks Cpanel::JSON::XS by itself;
   - Text-CSV 2.06, which uses Text::CSV_XS when it is linked;
   - Algorithm-Diff 1.201.

   They are listed in `recipes/cpan-pure`. `30_library.sh` copies each distribution's `lib/` tree into `lib/perl5/5.42`, refusing to overwrite a file Perl installed, and `MANIFEST` covers them like any other library file.
3. **Every tarball is pinned and checked.**
   - Each tarball has its CPAN URL and SHA-256 in `downloads/SHA256SUMS`, as F §8 recorded them, and `00_download.sh` fetches it.
   - The unpack step refuses a tarball that is unpinned, does not match its pin, unpacks to more than one top folder, or would replace a folder already in the tree.
   - The stamp of `20_target.sh` and of `30_library.sh` includes each listed tarball's SHA-256, not only the list.
4. **Not in this specification, with the reason for each:**
   - **YAML::XS:** the largest addition (+158 KB), and a C parser of a complex format; vfs's catalog asks for JSON and CSV far more. A later specification if the owner wants YAML.
   - **Sort::Key:** the docs of spec 020 get most of its gain.
   - **JSON::XS:** needs Canary::Stability at configure time, and is no faster than its fork Cpanel.
   - **JSON.pm:** it keeps JSON::PP unless `PERL_JSON_BACKEND` names Cpanel. Shipping it would make the slow path the default for `use JSON`, so it is left out, and the docs say to use JSON::MaybeXS.
   - **List::MoreUtils::XS and Unicode::UTF8:** their configure steps fail under miniperl (F §4).
5. **The POD stays in the shipped `.pm` files.** The library is as `installperl` and the distributions lay it out; stripping is a separate choice for all files at once.
6. **One documented difference from JSON::PP:** Cpanel::JSON::XS encodes a whole-number float as `5.0` (F §1). `docs/modules.md` says so.

## Checks (written first, seen failing)

1. **Each module loads and does one real thing in the guest, equal to native perl 5.42.3 with the same versions installed:**
   - a Cpanel decode and encode round trip;
   - a JSON::MaybeXS call that reports `Cpanel::JSON::XS` as its backend;
   - Text::CSV parsing quoted fields with embedded newlines, reporting `Text::CSV_XS` as its backend;
   - an `Algorithm::Diff` hunk list.
2. **Hostile input ends as natively** (F's `edge.pl`): JSON nested 100,000 deep, truncated, with a NUL or trailing garbage; a 5,000-digit number; invalid UTF-8; CSV with broken quotes, a NUL field or a 1 MB field. Each is a catchable Perl error or a result, never a trap.
3. **The import list of `perl.wasm` is unchanged:** only `wasi_snapshot_preview1`, with the same names.
4. **Fuel** for the 1.4 MB decode through Cpanel is under 1/10 of JSON::PP's. This is a count.
5. **A tampered tarball fails the build** at the unpack step: a one-byte change against its pin.

## Acceptance

1. The checks pass (EXECUTED).
2. The one-liner oracle, the escape tests and the whole of Perl's `t/` suite pass, with no new exclusion.
3. `docs/modules.md` gains a "From CPAN" section with each distribution's version, licence and purpose.
4. `CHANGELOG.md` under "Unreleased".
5. A fresh agent's review lands.
