# The numbers of docs/fast-perl.md, measured

- **Status:** research, 2026-11-24. The measurement that specification 020 asks for, on its branch `spec/020-docs-for-agents`.
- **Researcher:** the agent that built 020 (Claude Opus 5.5).
- **Stands on:** study F, `2026-11-24-perf-libs.md` §6 and §7.3; study D, `2026-11-24-perf-startup.md` §1.2; `2026-11-24-json-csv-and-diff-measured.md`.
- **Evidence:** `2026-11-24-fast-perl-measured/`. `measure.py` prints every number below; `results/` holds its two runs.

Labels: EXECUTED means run and seen. READ means read from a file. INFERRED means my reasoning.

**Fuel** is the guest's executed WebAssembly instructions. It is a count, the same on any machine. It moves by about 0.2% between runs, because Perl seeds its hash order from the host's randomness (READ: the JSON and CSV memo above).

## 1. The answer

Every ratio the page quotes holds within the specification's 20%, on today's `dev` build (EXECUTED, `results/dev.md`):

| item | slow form | fast form | slow fuel | fast fuel | ratio | page |
|---|---|---|---:|---:|---:|---:|
| count matches | `-ne '$n++ while /ERROR/g; END {…}'` | `-0777 -ne '$n++ while /ERROR/g; …'` | 587,243,136 | 64,757,494 | 9.07× | 9× |
| substitute | `-pi -e 's/ERROR/WARN/g'` | `-0777 -pi -e 's/ERROR/WARN/g'` | 733,300,579 | 97,166,932 | 7.55× | 7.5× |
| keyed sort | `split` inside the `sort` block | Schwartzian transform | 29,514,543,011 | 5,354,833,117 | 5.51× | 5× |
| JSON | `-MJSON::PP` | `-MJSON::MaybeXS` | 13,341,791,904 | 507,311,171 | 26.30× | 29× |
| JSON | `-MJSON::PP` | `-MCpanel::JSON::XS` | 13,341,791,904 | 465,328,535 | 28.67× | 29× |
| CSV | `Text::ParseWords::parse_line` | `Text::CSV` | 15,313,473,477 | 1,161,441,020 | 13.18× | 12× |

- **POSIX:** `-MPOSIX` costs 71,083,395 instructions and `-mPOSIX` (`use POSIX ()`) 54,799,158, so the default import costs 16.3 M (page: 16 M). `scalar @POSIX::EXPORT` is 587 (EXECUTED).
- **File::Find without a `cwd`:** over `/work` it dies "Can't cd to /: No such file or directory", exit 2. Over `.` it exits 0 and finds nothing. With `cwd="/work"` it finds the files (EXECUTED, `test_guest_file_find_needs_a_working_folder`).

The forms are the page's own examples, which are one-liner cases (`fast-*` in `tests/oneliners/cases.toml`). Each pair gives the same stdout and files on the large inputs, or `measure` fails.

The inputs are the checks' own (`write_inputs` in `tests/oneliners/test_fast_perl.py`), laid out under the examples' file names. They are deterministic:
- `app.log` and `data.csv` are study F's `t.log` and `t.csv`: 200,000 lines each, 10,214,747 and 4,577,629 bytes;
- `users.json` is F's 20,000 rows plus an `active` field: 1,688,893 bytes;
- `people.csv` has 50,000 rows with quoted names that hold commas, and doubled quotes in every fourth note: 2,059,919 bytes.

## 2. The -0777 figure moved

The specification quoted F §6: 4.8× for the count and 5.6× for the substitution. Today they are 9.07× (7.05× in F's own form, `$n++ if /ERROR/`) and 7.55×. Both are more than 20% above the old figures.

The cause is specification 016, which landed after study F. The same pairs on F's base set reproduce F's figures (EXECUTED, `results/f-base.md`). That set is the `perf-base` build of `dev` at `32dfb73`: `perl.wasm` of 9,260,141 bytes, sha256 `38c80f9c…6599ab30`.

| pair | F's base: slow / fast | ratio | dev: slow / fast | ratio |
|---|---:|---:|---:|---:|
| count, F's form `$n++ if /ERROR/` | 565.0 M / 116.8 M | 4.84× | 456.7 M / 64.8 M | 7.05× |
| count, the page's form `while /ERROR/g` | 697.1 M / 116.8 M | 5.97× | 587.2 M / 64.8 M | 9.07× |
| substitute, `-pi -e` | 842.9 M / 149.5 M | 5.64× | 733.3 M / 97.2 M | 7.55× |
| keyed sort | 29,546 M / 5,389 M | 5.48× | 29,515 M / 5,355 M | 5.51× |

So the whole-file side fell by 52 M instructions, from 116.8 M to 64.8 M, a 45% cut. The line loop fell by about 110 M, but that is only 13–19% of its count, so the ratio rose (EXECUTED, both tables). Spec 016 made PerlIO read 64 KiB at a time instead of 8 KiB, and replaced `memchr` (READ: `CHANGELOG.md`, "Unreleased"). Which part of 016 cut the whole-file read is INFERRED: probably the larger buffer, since a slurp grows its one string once per buffer fill. The keyed sort did not move.

The page quotes 9× and 7.5×, which are the measured values rounded, and its checks centre on them. The specification records the change with a dated line under Decision 1. The POSIX cost was 16.3 M on both sets.

## 3. The checks

`tests/oneliners/test_fast_perl.py`:
- `test_guest_ratio_is_the_pages` runs each pair and requires the ratio to lie within 20% of the page's figure, either way.
- `test_guest_posix_default_import_costs_what_the_page_says` does the same for the 16 M, and checks the 587.
- `test_guest_file_find_needs_a_working_folder` checks the three outcomes above.
- Three checks need no perl. Every example on the page must be a case word for word, every measured case must be on the page, and the page must quote every checked figure.

Before the page's -0777 figures were updated, its two checks failed with 9.07× and 7.55× against 4.8× and 5.6× (EXECUTED). One run of the whole file takes 13 s on this machine.

## 4. How it was run

```
# today's dev build (the same perl.wasm as 018's after set: 9,387,903 bytes, 064dfdbb…5b96c25a6)
flock /home/user/wasi-perl-wt/machine.lock uv run python -I context/research/2026-11-24-fast-perl-measured/measure.py > results/dev.md
# study F's base set
WASI_PERL_ARTIFACTS=/home/user/wasi-perl-wt/perf-base/build/artifacts flock /home/user/wasi-perl-wt/machine.lock \
  uv run python -I context/research/2026-11-24-fast-perl-measured/measure.py \
  "count matches in a whole file" "substitute in a whole file" "keyed sort" "count, F's form" posix > results/f-base.md
```

The base set has no Cpanel::JSON::XS or Text::CSV, so it has no JSON or CSV rows.

## 5. Limits

- Specification 019, built at the same time, changes `perl.wasm`. Its fuel may move these counts a little; the 20% margin is meant to cover that. If a check fails once 019 is on `dev`, the ratio is measured again, and the margin stays (INFERRED).
- The ratios depend on input size, since every run also pays Perl's start (about 2 M instructions, 55 M with POSIX). On a 10 KB file the -0777 gain is smaller (INFERRED).
