# 018 — JSON, CSV and diff for agents

- **Status:** proposed, not yet agreed by the owner. Written by the orchestrator, 2026-11-24, for synthesis item 6. The owner agreed to build the ten items that day: "You can do all 10 actually, but do not change the API around perl to be different than wasi python, unless wasi python could adopt the same changes." That agrees the item, not this text. These choices are new here and await the owner: the five distributions and their versions, leaving out YAML::XS and JSON.pm, and installing the five into a native folder for check 1. They are to be asked with AskUserQuestion before the build starts. Amended the same day for the review `../reviews/specs-017-022-pr-2026-11-24.md` (M6, M7).
- **Owner:** Clay Gendron
- **Kind:** the build recipe (new `recipes/cpan-xs` and `recipes/cpan-pure` lists, `recipes/config.sh`), `scripts/00_download.sh`, `scripts/10_native.sh`, `scripts/20_target.sh`, `scripts/30_library.sh`, `downloads/SHA256SUMS`, checks in `tests/build/`, `docs/modules.md`.
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

   Both are under Perl's own terms, and both built for wasm32-wasip1 unchanged on the first try (F §4). Together they add about 124 KB to `perl.wasm`, and no import (F §4, its costs). They are listed in `recipes/cpan-xs`, one line per distribution: the tarball's name and the folder under `cpan/` it unpacks as. `config.sh` appends their module names to `static_ext`, `extensions` and `known_extensions`, so Perl's own `make_ext.pl` builds them as spec 002 builds the core's.
2. **Three pure-Perl distributions, in the library:**
   - JSON-MaybeXS 1.004008, which picks Cpanel::JSON::XS by itself;
   - Text-CSV 2.06, which uses Text::CSV_XS when it is linked;
   - Algorithm-Diff 1.201.

   They are listed in `recipes/cpan-pure`. `30_library.sh` copies each distribution's `lib/` tree into `lib/perl5/5.42`, refusing to overwrite a file Perl installed, and `MANIFEST` covers them like any other library file.
3. **Every tarball is pinned and checked.**
   - Each tarball has a line in `downloads/SHA256SUMS`, in its three-field format (`<sha256>  <name>  <url>`), and `00_download.sh` fetches and verifies it as every other input. The five lines:

     | tarball | SHA-256 | URL |
     |---|---|---|
     | `Cpanel-JSON-XS-4.83.tar.gz` | `daabf6ad0376ee4613e671c521f5d05650c42205e403cac077e79d8594da3ade` | `https://www.cpan.org/authors/id/R/RU/RURBAN/Cpanel-JSON-XS-4.83.tar.gz` |
     | `Text-CSV_XS-1.65.tgz` | `4f0c16457c87def295a994e9c4ba397a8baafad96cefaf2d61483125f90ff974` | `https://www.cpan.org/authors/id/H/HM/HMBRAND/Text-CSV_XS-1.65.tgz` |
     | `JSON-MaybeXS-1.004008.tar.gz` | `cd3937afa78831f80a2ad5abab6c51b9e82fca4c31e5856ea208d598db5dc867` | `https://www.cpan.org/authors/id/E/ET/ETHER/JSON-MaybeXS-1.004008.tar.gz` |
     | `Text-CSV-2.06.tar.gz` | `dfcaec925a788b0ba41e51bc6d16e21b0e98b4c7af9b79395090add75f5e506f` | `https://www.cpan.org/authors/id/I/IS/ISHIGAKI/Text-CSV-2.06.tar.gz` |
     | `Algorithm-Diff-1.201.tar.gz` | `0022da5982645d9ef0207f3eb9ef63e70e9713ed2340ed7b3850779b0d842a7d` | `https://www.cpan.org/authors/id/R/RJ/RJBS/Algorithm-Diff-1.201.tar.gz` |

   - **The evidence.** The hashes are F §4's table (`perf-libs.md:180-200`, READ). Each also matches the `sha256` its author folder's PAUSE `CHECKSUMS` file gives. The author folder is the one whose `CHECKSUMS` lists the tarball (EXECUTED: `sha256sum` of study F's tarballs under `/home/user/wasi-perl-downloads/cpan/`, against `CHECKSUMS.R_RU_RURBAN`, `.H_HM_HMBRAND`, `.E_ET_ETHER`, `.I_IS_ISHIGAKI` and `.R_RJ_RJBS`, 2026-11-24; all five match). The URL is the author folder's path on `www.cpan.org`, where F fetched them (F §4, :180).
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

   **How native perl gets the same versions.** `10_native.sh` builds the five from the same pinned tarballs, after native perl: `perl Makefile.PL INSTALL_BASE=build/native-cpan`, then `make` and `make install`, with the native compiler. They go into a folder of their own, so the oracle's native perl (spec 006) sees no new module. The check runs native perl with `PERL5LIB=build/native-cpan/lib/perl5`. The stamp of `10_native.sh` includes the five SHA-256s.
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
