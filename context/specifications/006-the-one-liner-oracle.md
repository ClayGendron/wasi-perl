# 006 — The one-liner oracle

- **Status:** pre-agreed by the owner 2026-11-24 with the roadmap; building.
- **Owner:** Clay Gendron
- **Kind:** checks only (new `tests/oneliners/`: `cases.toml`, `known_differences.toml`, `oneliner_oracle.py`, `test_oneliners.py`, `record_oracle.py`, `conftest.py`). No build change, no runner change.
- **Depends on:** 001 for `perl.wasm` and the native perl 5.42.3 oracle; 004 for `wasi_perl.run()`; 002 for the XS modules the corpus loads. The corpus and its native checks stand alone and run today; the guest comparison runs once 001 and 004 have landed, and passes in full only after 002.
- **Research it stands on:**
  - The constitution, goal 4: "A set of agent-style one-liners (`-ne`, `-pi -e`, `-lane`, `-0777`, over granted files) gives the same stdout, stderr, exit code and file contents as native perl 5.42."
  - Specification 004, decisions 2 to 6 (READ, branch `spec/004-runner`): `run(argv, *, grants, env, stdin, cwd)` takes the arguments after `perl`; `env` is exactly what the guest sees plus `PWD`, and `PWD` itself is refused; `cwd` is a guest path inside a grant; `Completed.exit_code` is `None` when the guest trapped.
  - wasi-sdk 34's `include/wasm32-wasip1/__errno_values.h` (READ): `ENOENT` is 44 and `ENOTTY` is 59, where Linux has 2 and 25. Perl's `die` exits with `$!` when it is non-zero (`perlfunc`, `die`), so the exit code of `open ... or die` follows the C library's numbering.
  - `perlrun` (READ) for every switch the corpus uses; `perlsec`, "Algorithmic Complexity Attacks", for `PERL_HASH_SEED` and `PERL_PERTURB_KEYS`.

## The problem

wasi-perl is for the Perl agents write: `perl -ne`, `-pi -e`, `-lane`, `-0777`, run over a few files. The promise is that such a command does in the sandbox exactly what it does on a normal machine. Without a test of that promise, "works" means "printed something". Small differences hide well: a `$!` text from another C library, an exit code, a backup file `-i.bak` did not write, a `-CSD` length, a 64-bit integer printed as a float, an XS module that is missing and falls back or dies.

Hand-written expected outputs would encode our guesses about Perl. Native perl 5.42.3, the same version built from the same tarball, knows the answer. So it is the oracle, consulted at test time.

## Decisions

1. **The oracle is native perl 5.42.3, run at test time; no expected output is stored in the repository.** A case is an input (argv, files, stdin, environment), never an answer. Native perl runs it, the guest runs it, and the two results are compared. This removes goldens that drift, and it makes a new case one block of TOML.
   - The oracle is found as `WASI_PERL_ORACLE`, else `build/native-install/bin/perl` (spec 001's host build). It is refused unless `perl -e 'print $^V'` prints exactly `v5.42.3`. The system perl (5.38 here) is never used, not even as a fallback: an oracle of another version would turn real differences into noise.
   - A host without native perl (CI on macOS and Windows tests the installed wheel there) uses a recorded oracle, decision 7.

2. **The corpus is one TOML file, `tests/oneliners/cases.toml`.** TOML because Python reads it with the standard library (`tomllib`), and its literal strings (`'...'`) hold Perl code exactly as an agent types it between shell single quotes, with no escaping. Each `[[case]]` has `id`, `category`, `argv` (a list: the arguments after `perl`, as a shell would split them), and optionally `fixtures` (names of shared file sets under `[fixtures.*]`), `files` (its own files), `stdin` and `env`. A file's content is a string (written as UTF-8) or `{ base64 = "..." }` for bytes; a name ending in `/` is an empty folder. Script files (`perl report.pl data.csv`) are files of the case like any other.

3. **What the corpus covers.** 195 cases in 13 categories, chosen from what coding agents run (EXECUTED: `grep -c '^\[\[case\]\]' tests/oneliners/cases.toml` → 195):

   | Category | Cases | What it holds |
   |---|---|---|
   | `grep` | 14 | `-ne 'print if /re/'`, `-i` matching, invert, count, `$.`, captures, all matches, files-with-matches with `close ARGV`, stdin, flip-flop ranges, head with `exit` |
   | `multifile` | 10 | `$.` continuing across files and reset by `close ARGV if eof`, `eof()` for the last file, per-file counts, `-` for stdin between files, `END` totals, `reverse <>`, tail |
   | `fields` | 15 | `-lane` columns, sums and maxima, `-F,` CSV filters and group sums, `-F:` passwd-style, `-F'\t'` TSV, `-F/regex/`, `split` with a limit, `printf` tables |
   | `inplace` | 13 | `-pi -e 's///g'` over several files, `-pi.bak`, `-i'orig_*'`, `-ni` deletion, `-i` with `print ARGVOUT`, `-0777 -pi`, `$^I` in `BEGIN`, CRLF stripping, a missing file, a directory, `eof` appends, `-lpi` |
   | `records` | 12 | `-0777` counting, multi-line `s///gs`, binary slurp, `-00` paragraphs, `local $/`, custom `$/`, `-0` NUL records, fixed-size records |
   | `counting` | 9 | `!$seen{$_}++`, counts by frequency, word frequency, numeric and multi-key sorts, `sort keys` uniq, `scalar(%h)` |
   | `text` | 17 | `tr///` (count, `c`, `d`, `s`, `r`), `sprintf`/`printf` formats and rounding, `%*d`, `%vd`, commify, `s///e` and `s///r`, `$,` `$\` `$"`, `pos` with `/g`, a long regex |
   | `numbers` | 13 | 64-bit integer limits, `use integer`, float printing, libm results, `Inf`/`NaN`, `hex`/`oct`, `pack`/`unpack` with `q` and `d>`, `Math::BigInt` and `bigint` |
   | `modules` | 33 | List::Util, Scalar::Util, Data::Dumper (`Sortkeys`, `Indent=1`, `Terse`, `Useqq`), JSON::PP (canonical, pretty, decode, `-i` rewrite), POSIX (`floor`, `ceil`, `fmod`, `strtol`, `strftime` over `gmtime`), Time::Local, Time::Piece, Digest::MD5, Digest::SHA, MIME::Base64, File::Basename, File::Spec, Cwd (relative results only), Text::Wrap, Text::ParseWords, Storable, Encode, File::Path, File::Copy, File::Find (sorted), Term::ANSIColor, Unicode::Normalize, Config, a missing module |
   | `unicode` | 11 | `-CSD` lengths, `uc`, code points, sorting and `\w`; byte lengths without `-C`; `-Mutf8` literals, `fc`, `\p{Han}`; the "Wide character" warning; `:encoding(UTF-8)` layers and `-Mopen` |
   | `files` | 11 | writing and appending, `glob`, sorted `readdir`, file tests, `rename`/`unlink`, `mkdir`/`rmdir` errors, `:raw`, `+<` with `seek`/`tell`, `stat`, `$!` texts and `%!` |
   | `errors` | 21 | `die` with and without a newline, `die` mid-stream, `die` after a failed `open`, `exit N`, `$?` in `END`, `warn`, a missing input file, `-c` good and bad, an unterminated string, `use strict` errors, `-w` warnings (uninitialized, numeric, odd hash, missing printf argument, deep recursion), fatal warnings, `eval`, `croak`, `try`/`catch` |
   | `switches` | 16 | `-E`/`say`, `-nE`, `BEGIN`/`END`, `-l`, several `-e`, `@ARGV` and `$0`, `$]` and `$^V`, `-V:ivsize`, `next`/`last` in `-p`/`-n`, signatures and postfix dereference, a multi-statement script, script files with `#!` switches, `-c` on a script, `die` in a script |

   A missing XS module is not a reason to drop a case: until spec 002 links them, those cases fail in the guest and say so.

4. **What is compared, byte for byte:** stdout, stderr, the exit code, whether the guest trapped (a trap is always a difference), and the final state of the work directory: every file's path and content and every folder's path, including the backups `-i.bak` writes and the files a case creates, renames or deletes. Not compared: mode bits and times, because WASI preview1 has no `chmod` and times are not repeatable.

5. **The runs are made alike.**
   - **Separate, fresh work directories.** The native run and the guest run each get their own copy of the case's files in a new temporary directory. Native perl runs with that directory as its working directory and relative file names in argv. The guest gets its directory granted writable at `/work` with `cwd="/work"`; the runner sets `PWD`.
   - **The same environment.** Both get exactly `PERL_HASH_SEED=0`, `PERL_PERTURB_KEYS=0`, `LC_ALL=C`, plus the case's own `env`; the native run also gets `PWD` set to its directory, as the runner sets the guest's. Nothing of the host's environment reaches either: no `PERL5LIB`, `PERL5OPT`, `HOME` or `PATH`. Stdin is the case's bytes, or empty; never the terminal.
   - **No hash order in any output.** The fixed seed makes one build repeatable, but it cannot make two builds agree: the wasm build has 32-bit pointers and may choose a different hash function. So every case sorts keys before printing them. Decision 6 checks this.
   - **No time, pid, random number, `%ENV` dump, `$^O` or memory address in any output.** Times come from fixed epochs through `gmtime`; `localtime` is not used. `$^O` (`linux` against `wasi`) and `$Config{ptrsize}` differ by design and are out of the corpus.

6. **Path normalisation is one function, applied the same way in every mode.** A native result can name its work directory (Cwd, an error with a path). `normalise` replaces the host path of the work directory, in both its given and its `realpath` spelling, by `/work` in stdout, stderr and every file's content. It is applied inside `run_native`, which is the only way any mode gets a native result, live or recorded; guest results are never rewritten. The corpus still prints relative results where it can (Cwd cases print the part after the starting directory).

7. **Two oracle modes.**
   - **Live:** the native oracle runs each case beside the guest.
   - **Recorded:** `uv run python tests/oneliners/record_oracle.py [--perl PATH] --out PATH` runs every case on the native oracle (`--perl`, else `WASI_PERL_ORACLE`, else `build/native-install/bin/perl`, refused unless v5.42.3) and writes one JSON object: `{"format": 1, "perl": "5.42.3", "corpus_sha256": "<sha256 of cases.toml>", "cases": {"<id>": {"exit_code": int, "stdout": "<base64>", "stderr": "<base64>", "files": {"<relpath>": "<base64>"}}}}`. A folder is its path with a trailing `/` and an empty value. Base64 keeps every byte exact whatever the encoding.
   - `WASI_PERL_ORACLE_JSON=<path>` makes the guest tests compare against such a file. A native perl, when one is found, wins over it. A file whose `format`, `perl` or `corpus_sha256` does not match, or that lacks any case, **fails** every guest test; it never skips, since a stale oracle would let a regression pass. With neither a native perl nor a file, the guest tests skip and say how to get one.

8. **Determinism is checked natively before the guest is trusted to be compared.** `test_native_is_deterministic` runs every case twice on the native oracle: once with seed 0 in one directory, once with `PERL_HASH_SEED=12345 PERL_PERTURB_KEYS=2` in a directory with a longer name. The two results must be equal, and no output may contain the oracle's install prefix or the temporary directory's path. A case that fails this depends on hash order, time, or the host's layout, and would make the guest comparison unfair; it is fixed in the corpus, never allow-listed. It needs only native perl, so it runs now.

9. **Known differences are listed, never silent.** `tests/oneliners/known_differences.toml` holds entries `{ id, fields, kind, reason }`: `fields` are the compared fields that may differ; `kind` is `accepted` (a platform difference the project documents) or `gap` (something agents would hit, to be fixed by the build). A listed case that differs only in its listed fields is an expected failure (`xfail`, with the reason), never a pass. A listed case that matches fails until its entry is removed, so the list cannot go stale. A difference in an unlisted field is a plain failure. Entries are added only from an observed guest run, each in its own commit. A case is rewritten to avoid a difference only when agents would not meet that difference; otherwise it stays as a gap.

   **Gaps predicted now (INFERRED, to be confirmed by the first guest run):** `err-die-after-failed-open` and `err-die-mid-stream` exit with `$!`, which natively is 2 (`ENOENT`) and 25 (`ENOTTY`, left by perl's start-up `isatty` probe) and in wasi-libc would be 44 and 59. Agents meet this whenever they write `open ... or die`. It is a gap for the build (mapping errno to Linux numbering for the exit status, or for `$!` as a whole), not an accepted difference.

10. **The test layout and markers.** `oneliner_oracle.py` holds the machinery (load, lay out, run, snapshot, normalise, record, compare) so the test file and `record_oracle.py` share it. `test_corpus_is_well_formed` always runs (unique ids, known categories, relative file names, valid known-difference entries). `test_native_is_deterministic[<id>]` runs when the oracle exists. `test_guest_matches_native[<id>]` is marked `slow`, as it starts `perl.wasm` 195 times; it skips when `wasi_perl` cannot be imported or `WASI_PERL_ARTIFACTS` names a folder without `perl.wasm`. The only timeout is a 120-second hang guard on a native run; no test asserts on time.

11. **Not in this specification:** the sandbox walls (005), the process refusals (003; no case calls `system`, backticks or `fork`), Perl's own suite (007) and speed.

## Acceptance

1. `cases.toml` holds at least 80 cases across the categories of decision 3. EXECUTED: 195.
2. `uv run --with pytest pytest tests/oneliners` passes the corpus check and the native determinism check for every case, EXECUTED, output in the landing notes.
3. `record_oracle.py` writes the file of decision 7; with `WASI_PERL_ORACLE_JSON` set and no native perl, the guest tests use it; a changed corpus makes them fail; with neither oracle they skip. EXECUTED with a stand-in runner, landing notes.
4. Once `perl.wasm` (001) and `wasi_perl` (004) exist: every guest case passes, or is in `known_differences.toml` with its reason; the failures are listed in the landing notes by cause. Missing XS modules are expected until 002 and recorded, not dropped.
5. `ruff check` and `ruff format --check` are clean on `tests/oneliners`.
6. The review file is written by a fresh agent and the verdict begins "Lands".

## Landing notes

Built 2026-11-24 on branch `spec/006-oneliners`, Linux x86-64.

- **The oracle.** Native perl 5.42.3 built in this worktree from the pinned tarball (sha256 `c9387e14…0ddc`, verified): `sh ./Configure -des -Dprefix=$PWD/build/native-install && make -j3 && make install` under `build/perl-5.42.3`. `build/native-install/bin/perl -e 'print $^V'` → `v5.42.3`. EXECUTED. `/usr/bin/perl` (v5.38.2) is refused: `record_oracle.py --perl /usr/bin/perl` → `refused: /usr/bin/perl is perl v5.38.2, the oracle must be v5.42.3`. EXECUTED.
- **The corpus.** 195 cases (`grep -c '^\[\[case\]\]' tests/oneliners/cases.toml`), by category: modules 33, errors 21, text 17, switches 16, fields 15, grep 14, inplace 13, numbers 13, records 12, unicode 11, files 11, multifile 10, counting 9. `cases.toml` sha256 `8f8c8d0f8ffcee98ae6e57d3f0d02693d39173a73345fbb0ab50334a837b017f`. EXECUTED.
- **Native determinism.** `uv run --with pytest pytest tests/oneliners` → `196 passed, 195 skipped` (1 corpus check, 195 determinism checks under two hash seeds and two directory names; the 195 guest tests skipped: no runner or perl.wasm yet). EXECUTED. Every native output was read once by hand for intent; one case was fixed before commit (`fc` needs `-Mfeature=fc` outside `use v5.16`). READ.
- **Native results worth knowing** (from `build/oracle.json`, EXECUTED): `err-die-after-failed-open` exits 2 and `err-die-mid-stream` exits 25, both from `$!` (decision 9); `err-die-newline` exits 255; `err-end-sets-status` exits 4.
- **Recorded mode.** `uv run python tests/oneliners/record_oracle.py --perl build/native-install/bin/perl --out build/oracle.json` → `recorded 195 cases … (corpus 8f8c8d0f8ffc)`. With a copy of `tests/oneliners` in a folder that has no `build/native-install`, and a stand-in `wasi_perl` that runs native perl inside the grant (scratch only, never committed): `WASI_PERL_ORACLE_JSON=build/oracle.json` → `196 passed, 195 skipped` (guest cases compared against the file; determinism skipped); after appending a comment to the copy's `cases.toml` → every guest test fails with "was recorded from another cases.toml"; with neither oracle → the guest tests skip with the message of decision 7. EXECUTED.
- **The known-differences logic.** With the same copy and a stand-in that changes `foo` to `FOO` in stdout and exit code 2 to 3: a listed exit-code difference is `XFAIL … known gap`, a listed case that matches fails "now matches; remove it", an unlisted stdout difference fails showing both byte strings. EXECUTED.
- `ruff check` (004's rule set: E, F, I, UP, B, TID252, line length 100) and `ruff format --check` clean on `tests/oneliners`. EXECUTED.
