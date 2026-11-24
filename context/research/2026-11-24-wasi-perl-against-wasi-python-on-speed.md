# wasi-perl against wasi-python on speed, with and without snapshots

- **Status:** research, 2026-11-24. A measurement the owner asked for. It changes no code. Section 6 names one cost in the runner that is worth a specification.
- **Owner:** Clay Gendron
- **Researcher:** an agent (Claude Opus 5.5).
- **The question, in the owner's words:** "Can you do a performance test of wasi-python and wasi-perl? With and without snapshots. I want to know if perl being a smaller binary is faster to use than python."
- **Evidence:** the harness and every raw result are in `2026-11-24-wasi-perl-against-wasi-python-on-speed/`. Every number below is EXECUTED from those files unless it is labelled otherwise.

Labels: EXECUTED means run and seen. READ means read from a file. INFERRED means the researcher's reasoning.

## 1. What was measured

**The two tools:**

- **wasi-perl 0.1.0.** Built from `dev` at `aac5d4d` in a clean worktree with `scripts/build.sh`, then `scripts/assemble_artifacts.py` and `uv build --wheel`.
- **wasi-python 0.1.6**, the wheel from PyPI.

Both ran in one venv: host CPython 3.11.18 and wasmtime-py 51.0.0. So the engine was the same for both.

**The machine:** 4 vCPU Intel Xeon @ 2.30 GHz, 15 GiB, Linux 6.18. It was otherwise idle: the build had finished before any timing began.

**The modes:**

| mode | what starts each call |
|---|---|
| `perl/none` | perl.wasm's `_start`. wasi-perl has no snapshot. |
| `python/plain` | `snapshot=False`: the plain reactor, which starts CPython from nothing. |
| `python/snapshot` | `snapshot=True`, the default: `python-snapshot.wasm`, with the interpreter already started at build time. |
| `python/image` | `snapshot=make_snapshot("import json, re")`: an embedder's image. The call starts from its memory. |

**The workloads.** The same task is written once in each language (`bench.py`, `PERL` and `PY`). Both languages printed the same answer for every workload, in every mode.

The inputs:

- `t.csv`: a 4.6 MB CSV of 200,000 lines.
- `t.log`: a 10.2 MB log of 200,000 lines.
- `t.json`: a 1.4 MB JSON array of 20,000 objects.

| workload | Perl | Python |
|---|---|---|
| hello | `-e 'print "hello\n"'` | `print("hello")` |
| sum_csv | `-F, -lane '$s += $F[2]; END {…}'` | a `for` loop over `split(',')` |
| grep_log | `-ne '$n++ if /ERROR \d+/; …'` | `re.compile(...).search` per line |
| inplace | `-pi -e 's/ERROR/WARN/g'` on a writable grant | read the whole file, `re.sub`, write it back |
| json | `-MJSON::PP -0777 -ne 'decode_json'` | `json.load` |
| loop | `$s += $_ * $_ for 1 .. 1_000_000` | `for i in range(1, 1000001)` |

**Timing:**

- Wall time is `time.perf_counter()` around `run()`.
- Per-call numbers are the median of 5 to 30 calls, after two warm-up calls, in one process with a warm module cache.
- **Fuel** is the guest's executed WebAssembly instructions. It is a count, the same on any machine. It was measured in a separate metered pass, because metering changes the compiled code.

## 2. Size, and what it buys at the start

### 2.1 Size on disk and in memory

| | wasi-perl | wasi-python | ratio |
|---|---|---|---|
| wheel | 9.9 MB | 123.0 MB (PyPI) | 12× |
| main binary | 9.3 MB `perl.wasm` | 109.6 MB `python.wasm`, 116.4 MB `python-snapshot.wasm` | 12× |
| library | 27 MB folder | 73 MB zip | |
| compiled module in the cache | 14.9 MB | 304 MB plain, 318 MB snapshot | 20× |
| peak RSS of a process that compiles | 232 MiB | 2.5–2.6 GiB | 11× |

Sources: `results/firsts.json`, `ls -la` of both installed packages, and the PyPI JSON API for the wheel size.

### 2.2 A fresh host process: import, then one hello call

Each line is the median of 3 to 5 fresh subprocesses (`bench.py firsts`). The timed call is the first call in a new process:

- **cold:** the cache is empty, so the module is compiled and stored.
- **warm:** the cache holds the compiled module.
- **off:** `cache_dir=False`, so the module is compiled every time.

| | perl | python plain | python snapshot |
|---|---|---|---|
| first call, cold cache | **1.05 s** | 24.6 s | 25.2 s |
| first call, no cache | 1.06 s | 24.2 s | 25.1 s |
| first call, warm cache | **0.122 s** | 0.404 s | 0.358 s |
| whole process, warm cache | **0.19 s** | 0.57 s | 0.46 s |
| second call in that process | 5 ms | 33 ms | 16 ms |

Where Perl's warm first call goes (`first_split.py`, median of 5):

- 66 ms: the once-per-process check of the library against `SHA256SUMS`, which hashes 1,455 files.
- 51 ms: loading the 15 MB compiled module.
- 8 ms: the call itself.

**Finding 1.** For anything that starts a new process per program, the smaller binary wins clearly:

- **No usable cache** (a CI job, a fresh container, a serverless cold start): Perl is ready in about 1 s, Python in about 25 s. That is 23× faster, in a tenth of the memory.
- **Warm cache:** a Perl process finishes in 0.19 s, Python's in 0.46 s or more.

## 3. One warm process: the cost of each call

The median in ms per call (`results/calls.json`). A read-only grant is used, plus a writable grant for `inplace` only.

| mode | hello | sum_csv | grep_log | inplace | json | loop |
|---|---|---|---|---|---|---|
| perl/none | **4.4** | **145** | **107** | 216 | 1,287 | **71** |
| python/plain | 31.5 | 227 | 180 | 106 | 123 | 323 |
| python/snapshot | 13.8 | 204 | 147 | **72** | **67** | 304 |
| python/image | 31.4 | 220 | 165 | 82 | 105 | 350 |
| native perl 5.42.3 (subprocess) | 1.8 | 58 | 26 | 41 | 546 | 24 |
| native CPython 3.13 (subprocess) | 7.5 | 46 | 41 | 52 | 36 | 71 |

The native rows come from `native.py`, which runs each workload as a subprocess. The guest is CPython 3.15.2, so the native Python is a different version. Those rows give scale only.

Guest instructions, from `results/fuel.json`:

| mode | hello | sum_csv | grep_log | inplace | json | loop |
|---|---|---|---|---|---|---|
| perl/none | **1.6 M** | **1.27 B** | **0.64 B** | 0.84 B | 11.1 B | **0.60 B** |
| python/plain | 138 M | 2.10 B | 1.29 B | 0.61 B | 0.68 B | 4.00 B |
| python/snapshot | 37 M | 1.95 B | 1.09 B | 0.41 B | 0.46 B | 3.82 B |
| python/image | 0.77 M | 1.90 B | 1.05 B | **0.38 B** | **0.43 B** | 4.42 B |

**Finding 2.** For a bare call, Perl is 3× faster than Python's best mode: 4.4 ms against 13.8 ms. It runs 23× fewer instructions than Python's snapshot to start: 1.6 M against 37 M.

**Finding 3.** Once a program does work, the binary's size stops mattering. What matters is the language's own interpreter:

- **Line-oriented text and plain loops: Perl is faster.** On these one-liners it is 1.4× faster than Python's snapshot, and 4.3× faster on the arithmetic loop. That holds in wall time and in instructions.
- **In-place editing: Python is 3× faster.** The two programs differ, though (INFERRED from the programs): `-pi` reads, edits and writes 200,000 lines one at a time and renames the file at the end, while the Python version reads the file once and calls one C-coded `re.sub`. Natively the two are close, 41 ms against 52 ms.
- **JSON: Python is 19× faster.** `JSON::PP` is pure Perl and Python's `json` is C. Natively the gap is also 15×, so the cause is Perl's library, not WebAssembly. wasi-perl ships no XS JSON module (READ: `docs/modules.md`).

**Overhead against native:** on the text one-liners and the loop, the guest runs 2.5 to 4× slower than native for Perl and 3.5 to 4.5× slower for Python.

## 4. Snapshots

### 4.1 Python: the shipped snapshot, and an embedder's image

- **The shipped `python-snapshot.wasm`** is the right default. Against `plain`, it:
  - halves a bare call (31.5 → 13.8 ms);
  - cuts startup instructions by 3.7× (138 M → 37 M);
  - saves 10 to 50 ms on every workload above.

  It costs nothing extra to compile: 25.2 s against 24.6 s.
- **An embedder's image of light imports** (`import json, re`) *loses* to the shipped snapshot: a hello call takes 31.4 ms against 13.8 ms. It runs almost no instructions (0.77 M), so the time goes to restoring the image's memory into a new instance. That is about 30 ms per call (INFERRED from 31.4 ms of wall time for under 1 M instructions).
- **An image of heavy imports** is where images pay (`heavy_probe.py`, `results/heavy.json`). Take a program that imports pandas and sums a three-row frame:

  | start | ms per call | instructions |
  |---|---|---|
  | plain | 1,432 | 5.07 B |
  | shipped snapshot | 1,273 | 4.80 B |
  | image of `import pandas` | **50** | **4.7 M** |

  That is 25× faster.

### 4.2 Perl: what a snapshot could save

wasi-perl has none. Three measurements bound what one could save (`results/floor.json`, `results/heavy.json`):

| | ms per call | instructions |
|---|---|---|
| instantiating perl.wasm without running `main` | 0.07 | |
| a whole `perl -e 1` call through `run()` | 4.0–4.4 | 1.6 M |
| `-MPOSIX -e 1` | 21.9 | |
| `-MJSON::PP -e 1` | 28.8 | |
| `-MPOSIX -MList::Util -MData::Dumper -MJSON::PP -MStorable -MEncode -e 1` | 48.7 | 174 M |

**Finding 4.** A snapshot of a started perl, the counterpart of `python-snapshot.wasm`, would save at most a fraction of a millisecond of a 4 ms call (INFERRED). Perl's own startup is 1.6 M instructions. At this guest's speed, about 8.5 B instructions per second (`loop`: 0.60 B in 71 ms), that is about 0.2 ms. The rest is host work that a snapshot does not remove: the store, the WASI context, the preopens and the runner's checks.

Python's image restore costs about 30 ms. A restore of similar cost would make a bare Perl call slower, not faster (INFERRED).

An image with modules preloaded could save up to about 45 ms on a program that loads six heavy modules. Even then, the restore would have to cost under about 45 ms to help (INFERRED). wasi-perl's main use, `-ne` and `-pi` one-liners that load no module, would gain nothing.

## 5. The answer

Yes: in starting, the smaller binary is much faster.

- It compiles 23× faster (1 s against 25 s) in a tenth of the memory.
- Its compiled module is 20× smaller.
- A fresh process's first call is 3× faster with a warm cache.
- A bare call in a warm process is 3× faster than Python with its snapshot, and 7× faster than Python without one.

For a host that starts a process per program, or cannot keep a cache, this is the difference that matters.

Once the program does work, the binary's size no longer matters; each language's interpreter and libraries do:

- Perl is faster on line-oriented text and on loops, its main use, by 1.4× to 4×.
- Python is faster where its standard library runs C and Perl's core runs Perl: JSON by 19×, and whole-file regex substitution.

Snapshots change Python's picture a lot, and Perl's hardly at all:

- Python's shipped snapshot is worth keeping on: it halves a bare call.
- An embedder's image pays only for heavy imports: 25× for pandas, a loss for `json`.
- Perl starts in 1.6 M instructions, so a snapshot would buy it little.

## 6. A cost found in wasi-perl's runner

**A writable grant adds 28 ms to every Perl call.** `grants_probe.py`, `results/grants.json`, median of 30 hello calls:

| | no grant | read-only grant | read-only and writable grants |
|---|---|---|---|
| perl | 3.4 ms | 3.5 ms | **31.4 ms** |
| python/snapshot | 17.7 ms | 18.1 ms | 19.1 ms |

The cause (READ, `src/wasi_perl/runner.py` `_runtime_files` and `_grants.py` `_refuse_second_names`):

- On every call that has a writable grant, the runner walks the wasmtime-py and `wasi_perl` package trees with `rglob` and `lstat`s every file it finds. That is about 3,000 files, because the library lies under the package and so is counted again. The point is to rule out a hard link from a writable grant to a runtime file.
- uv installs by hard link, so several runtime files have more than one name. The writable grants are then walked as well.

wasi-python makes the same check for 1.5 ms.

Without the change, the in-place workload above would read 216 ms rather than about 188 ms (INFERRED: 216 − 28). The first pass of `bench.py calls`, with a writable grant on every workload (`results/calls-writable-everywhere.json`), shows the same 28 to 36 ms added on each.

**Recommendation (INFERRED, for a specification):**

- Compute the runtime file list, and its set of multiply-linked identities, once per process, or once per stamp of the trees, as `_artifacts.checked()` already does for the library hash.
- Each call would then `lstat` only the few files known to be multiply linked, then walk the writable grants as today.
- This keeps the protection and removes most of the 28 ms.

## 7. Limits of this measurement

- One x86-64 Linux machine. Neither macOS, arm64 nor Windows was timed.
- `inplace` and `json` compare different algorithms, as section 3 says.
- Fuel was measured with the writable grant on every workload. A grant does not change the guest's instructions beyond its preopen (INFERRED).
- The pandas image's 25.9 s to make includes compiling the module into a fresh cache. Making it from a warm cache was not timed.

To reproduce, from this folder: make a venv with `uv venv` and install both wheels into it with `uv pip install`. Then run each line below with that venv's `python`:

```
python bench.py data data
python bench.py floor data results/floor.json
python bench.py calls data results/calls.json
python bench.py fuel data results/fuel.json
python bench.py firsts data results/firsts.json
python grants_probe.py data results/grants.json
python heavy_probe.py results/heavy.json
python native.py data <native perl 5.42.3> results/native.json
```

Run `first_split.py <a warm cache folder>` once per fresh process.
