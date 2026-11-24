# JSON, CSV and diff for agents, measured

- **Status:** research, 2026-11-24. The measurement that specification 018 asks for, on its branch `spec/018-json-csv-and-diff`.
- **Researcher:** the agent that built 018 (Claude Opus 5.5).
- **Stands on:** study F, `2026-11-24-perf-libs.md`, whose corpus (`2026-11-24-perf-libs/corpus.py`) is used here unchanged.
- **Evidence:** `2026-11-24-json-csv-and-diff-measured/`. `measure.sh` makes every number in `results/`; `table.py` prints the table below from them; `edge.py` and `deep.py` make the rest.

Labels: EXECUTED means run and seen. READ means read from a file. INFERRED means my reasoning.

Terms, once:

- **Fuel** is the guest's executed WebAssembly instructions. It is a count, the same on any machine, and it is the primary evidence.
- **Wall time** is the median of one warm call, in one process, with a warm module cache. It is secondary: the machine has 4 shared vCPUs.

## 1. The answer

| workload | before | after | gain in fuel | gain in time |
|---|---|---|---:|---:|
| JSON decode of 1.4 MB (`decode_json`, sum a field) | JSON::PP: 11,156 M, 1,250 ms | Cpanel::JSON::XS: 370 M, 45 ms | **30.2×** | 28× |
| the same through `use JSON::MaybeXS` | falls back to JSON::PP: 11,179 M, 1,264 ms | picks Cpanel: 412 M, 60 ms | 27.1× | 21× |
| JSON encode of 20,000 rows (canonical) | JSON::PP: 3,914 M, 421 ms | Cpanel: 576 M, 58 ms | 6.8× | 7.3× |
| a jq-like select over 1.4 MB | JSON::PP: 11,162 M, 1,266 ms | Cpanel: 376 M, 43 ms | 29.7× | 30× |
| JSON decode of 2 KB | JSON::PP: 102 M, 23 ms | Cpanel: 35 M, 12 ms | 2.9× | 1.9× |
| CSV parse of 50,000 quoted rows | Text::ParseWords: 18,215 M, 1,342 ms | Text::CSV_XS: 1,511 M, 133 ms | **12.1×** | 10× |
| the same with a hand regex as the before | 3,527 M, 325 ms | Text::CSV_XS | 2.3× | 2.5× |
| the same through `use Text::CSV` | pure Perl from `/ext`: 32,416 M, 3,637 ms | through Text::CSV_XS: 1,513 M, 129 ms | 21.4× | 28× |
| a diff of two 5,000-line files | Algorithm::Diff from `/ext`: 211 M, 27 ms | from the library: 211 M, 28 ms | none | none |

"Before" is the dev build at `f44e0fd`; "after" is this branch. Both are EXECUTED, from `results/fuel-*.json` and `results/wall-*.json`, printed by `table.py`. Every answer, before and after, equals native perl 5.42.3's with the same five modules, byte for byte (the `answer` column below).

So the specification's claims hold: Cpanel decodes study F's file 28× faster than JSON::PP (the specification says about 29×, from F) and in a thirtieth of its instructions, and Text::CSV parses through Text::CSV_XS, 10× faster than Text::ParseWords in time and 12× in fuel. Algorithm::Diff costs what it cost from `/ext`: the module is the same, only its place changed.

The cost (EXECUTED, `results/sets.txt`, `stat -c %s` and `sha256sum`):

| | before | after | change |
|---|---:|---:|---:|
| `perl.wasm` | 9,261,691 bytes, `d70858dc…a391ca821` | 9,387,903 bytes, `064dfdbb…5b96c25a6` | +126,212 (+1.4%) |
| library files | 1,453 | 1,462 | +9 files, +645,959 bytes |
| imports | 33, all `wasi_snapshot_preview1` | the same 33 | none |

The `perl.wasm` growth equals study F's set A exactly (126,212 bytes, F §4). The nine library files are Cpanel's three `.pm` files (117,848 bytes) and Text::CSV_XS's one (156,629), as F found, and the five of the pure distributions (371,482). One existing library file changed: `wasm32-wasi/Config_heavy.pl`, whose `static_ext` and `extensions` now name the two (EXECUTED: `join` of the two `MANIFEST` files on path).

## 2. Every workload

Fuel in millions of instructions. ms is the median of 3 to 9 warm calls. "native ms" is native perl 5.42.3 with `PERL5LIB=build/native-cpan/lib/perl5`, as a subprocess. A program with no "before" needs a module the before set lacked.

| program | fuel M, before | fuel M, after | ms, before | ms, after | native ms | answer |
|---|---:|---:|---:|---:|---:|---|
| json_decode | 11,156 | 11,156 | 1,250.4 | 1,314.2 | 536.8 | same |
| json_decode+maybexs | 11,179 | 412 | 1,263.8 | 59.8 | 26.9 | same |
| json_small | 102 | 102 | 22.7 | 28.8 | 10.3 | same |
| json_encode | 3,914 | 3,916 | 420.8 | 423.6 | 194.2 | same |
| jq_select | 11,162 | 11,163 | 1,265.6 | 1,267.3 | 524.6 | same |
| csv_parsewords | 18,215 | 18,193 | 1,341.8 | 1,440.5 | 443.3 | same |
| csv_regex | 3,527 | 3,531 | 325.2 | 324.0 | 137.7 | same |
| csv_pp+textcsv | 32,416 | 1,513 | 3,636.5 | 128.6 | 55.2 | same |
| diff_lcs+algdiff | 211 | 211 | 26.6 | 27.8 | 13.3 | same |
| load_jsonpp | 87 | 86 | 20.0 | 20.7 | 9.9 | same |
| load_none | 2 | 2 | 1.6 | 1.8 | 1.4 | same |
| json_decode+cpanel |  | 370 |  | 44.9 | 21.6 | same |
| json_small+cpanel |  | 35 |  | 12.1 | 4.7 | same |
| json_encode+cpanel |  | 576 |  | 58.0 | 38.2 | same |
| jq_select+cpanel |  | 376 |  | 42.6 | 22.5 | same |
| csv_xs+csv_xs |  | 1,511 |  | 132.5 | 55.9 | same |
| load_cpanel+cpanel |  | 35 |  | 10.8 | 4.7 | same |
| load_textcsv_xs+csv_xs |  | 89 |  | 19.5 | 10.0 | same |

EXECUTED: `uv run python -I table.py results`. The programs are study F's, named as F names them (F §3.1); `corpus.py` holds their text.

What the table shows:

- **Programs that use no new module run the same instructions before and after**, to within 0.5% (`load_none`, 1,605,996 against 1,613,692; the rest within 0.2%). Their wall times moved by up to 27% (`json_small`, 22.7 against 28.8 ms, for the same 102 M instructions). That is the machine's noise, as F saw (§3.1); prefer fuel.
- **The selectors work as F found.** `use JSON::MaybeXS` now gets Cpanel by itself; it loads 42 M instructions more than `use Cpanel::JSON::XS`. `use Text::CSV` gets Text::CSV_XS, and runs within 0.2% of it.
- **Agreement with F.** Every fuel count is within 1.5% of F's set A (F §3.1: 372 M, 580 M, 376 M, 1,525 M, 1,527 M; the diff, 214 M against 211 M, is the largest gap). F's numbers were taken on an older `dev` (`32dfb73`), before specification 016 changed PerlIO's buffer and `memchr` (INFERRED as the reason for the small differences).

## 3. The checks the numbers back

- **Check 4, the fuel ratio.** JSON::PP's decode of the 1.4 MB file takes 11,156,440,042 instructions and Cpanel's 369,988,764: 30.2×, more than the 10× the check asks (EXECUTED: `results/fuel-after.json`; `test_the_linked_decoder_takes_under_a_tenth_of_json_pps_instructions` makes the same comparison under its own engine).
- **Check 2, hostile input.** The guest's output equals native perl's, line for line, and no case traps (EXECUTED: `uv run python -I edge.py results`; `results/edge-guest.txt` against `results/edge-native.txt`, which differ only in the guest's added `trapped None`).
- **Check 3, imports.** `results/imports-before.txt` and `imports-after.txt` are identical: 33 functions, all `wasi_snapshot_preview1` (EXECUTED: `diff`).

## 4. A limit found on the way

Cpanel::JSON::XS's decoder recurses in C once per nesting level. With its default `max_depth` of 512, deeper input is refused with a Perl error, which check 2 covers. A program that raises `max_depth` can go deeper:

- the guest decodes 1,719 levels; at 1,720 its stack runs out and the call ends as trapped, with the reason "stack overflow";
- native perl decodes 10,000 levels and dies of a segmentation fault at 100,000.

EXECUTED: `uv run python -I deep.py`, `results/deep.txt`. The trap is wasmtime's stack limit, so the guest ends cleanly and the host is not touched (INFERRED: a trap is how the runner reports any guest that exhausts its stack). The default is safe, and `docs/modules.md` says to keep it.

## 5. How it was run

```
# the before set: the dev build at f44e0fd, built in this worktree, then copied
flock /home/user/wasi-perl-wt/machine.lock scripts/build.sh
cp -a build/artifacts build/before/artifacts
# the after set: this branch
flock /home/user/wasi-perl-wt/machine.lock scripts/build.sh
# the numbers
flock /home/user/wasi-perl-wt/machine.lock sh context/research/2026-11-24-json-csv-and-diff-measured/measure.sh
uv run python -I context/research/2026-11-24-json-csv-and-diff-measured/table.py context/research/2026-11-24-json-csv-and-diff-measured/results
uv run python -I context/research/2026-11-24-json-csv-and-diff-measured/edge.py context/research/2026-11-24-json-csv-and-diff-measured/results
uv run python -I context/research/2026-11-24-json-csv-and-diff-measured/deep.py
```

- Every wall-clock run held `flock /home/user/wasi-perl-wt/machine.lock`. `measure.sh` took 1 min 59 s (EXECUTED).
- **Before**, the three pure-Perl modules are not in the library, so `measure.sh` grants them read-only at `/ext` on `PERL5LIB`, as F did: a folder holding the `lib/` trees of the same pinned tarballs, from `build/cpan-pure`. The two XS modules cannot be granted, so their programs have no "before".
- **After**, nothing is granted beyond `/data`.
- The inputs are F's, from `corpus.py data`: deterministic, with no clock and no randomness.
