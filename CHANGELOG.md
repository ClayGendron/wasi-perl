# Changelog

## Unreleased

- A call's fixed cost on the host is smaller: a bare warm call 3.9–4.5 ms → 1.4–1.8 ms, and a fresh process's first call about 135–145 ms → 106 ms, with 31 MiB less peak memory, on one Linux VM. wasmtime-py's version is read once per process, from its dist-info; `SHA256SUMS` is parsed once per stamp of the file; a rule's inputs are found only when a grant can break the rule; each compiled module is linked once; the checked `.cwasm` is mapped by its descriptor's own name (`/proc/self/fd/N` on Linux, `/dev/fd/N` on macOS) instead of being read into memory, and no later grant may reach a folder a module was mapped from; and the engine states the four wasmtime defaults the speed depends on. Every rule is unchanged. Specification 014; the timings are in `context/research/2026-11-24-the-runners-fixed-cost-measured.md`.

## 0.1.0 — 2026-11-24

The first release. One pinned set: Perl 5.42.3, built with wasi-sdk 34 for `wasm32-wasip1`, run with wasmtime 51.

### The runtime

- `perl.wasm`: Perl 5.42.3 as one static WebAssembly module. It imports only `wasi_snapshot_preview1` and needs a host with exception handling (`die` and `eval` unwind through WebAssembly exceptions, not Asyncify). It is built from pinned sources with a hand-written `config.sh`, a host miniperl and no patch to Perl.
- The core XS modules are linked in statically, among them POSIX, List::Util, Scalar::Util, Data::Dumper, Storable, Encode and its tables, Digest::MD5, Digest::SHA, MIME::Base64, Time::HiRes, Cwd, File::Glob, IO, Fcntl, Socket, Compress::Raw::Zlib and ::Bzip2, Unicode::Normalize, Unicode::Collate, Time::Piece, B and Opcode. JSON::PP, File::Spec and the rest of the pure-Perl core library ship as one read-only folder. `docs/modules.md` lists what is left out and why: threads, IPC::SysV, Sys::Syslog, the DB_File family and PerlIO::mmap.
- Processes and signals are refused, clearly. `fork`, `exec`, `system`, backticks, pipe opens, `pipe`, `wait`, `waitpid`, `kill`, `alarm`, `dump` and a `%SIG` handler each die with "… is not available in the wasi-perl sandbox", which `eval` can catch. `$SIG{__WARN__}` and `$SIG{__DIE__}` work. Core modules that set a handler while they load, such as Math::Complex, still load.
- Exit statuses match native Perl on Linux. An uncaught `die` exits 255. An exit taken from `$!` uses Linux's errno numbers. Statuses are masked to 0–255. wasmtime 51 refuses statuses of 126 and above, so the module exports `wasi_perl_exit_status` for the host to read them.
- Descriptors behave as on POSIX. The guest's own table gives `dup`, `dup2`, `F_DUPFD`, `>&STDOUT` and the `&=` forms, numbered lowest-free, so Test::More works. Four-argument `select` reads Perl's bit vectors.
- In-place editing works on every host, Windows included: when the host refuses to rename over a file the guest holds open, the guest releases its own handles on it and retries.
- Special files are refused at open (`ENXIO`), so a FIFO in a grant cannot hold the host. Every clock wait is cut into 10 ms slices, so a sleeping guest keeps its deadline.

### The Python package

- `run(argv, *, grants=[Grant(host, guest, writable=False)], env, stdin, cwd, fuel, deadline_s, memory_bytes, output_bytes, cache_dir, artifacts)` returns `Completed(exit_code, stdout, stderr, trapped, fuel_used, truncated)`. `wasi-perl run [OPTIONS] -- ARG...` prints the same as one JSON line.
- Safe by default. No host folder, no network and nothing writable unless granted. Every call has a deadline (60 s), a memory cap (1 GiB) and an output cap per stream (16 MiB). The guest's environment is only what the caller passes.
- Grants are checked before any work. Guest paths are normalised. A grant may not overlap the runtime's own library, a module cache, this package, wasmtime or the host Python's folders. Within one process, a call's grants may not overlap another live call's writable grants.
- The compiled module is cached as wasi-python caches it. The folder must be 0700 and owned by the user, files are 0600, and a module is loaded through the descriptor that was checked and compiled from bytes hashed once. A folder any call of the process granted writable is never used as a cache.
- The artifact set is checked against its `SHA256SUMS` before use.

### Judged by

- Perl's own `t/` suite, run one file per guest: 616 files, 439 pass and 0 fail; the other 177 are listed in `tests/perl_t/exclusions.toml`, each with its reason (143 start a child process, 11 need signals, 2 are refused calls, 6 check Perl's own source tree, 6 are WASI differences, 6 are for other systems, 3 are known bugs), a failure signature and a floor of passing results that may not fall (19,052 in all).
- 209 agent-style one-liners (`-ne`, `-pi -e`, `-lane`, `-F`, `-0777`, `-00`, modules, errors, Unicode, paths), byte for byte against native perl 5.42.3, on Linux x86-64 and arm64, macOS arm64 and Windows x86-64. Two cases differ on a Windows host, each allowed with its reason: NTFS gives a directory a size of 0, and writing to a directory there fails with EINVAL, not EISDIR.
- wasi-python's sandbox escape tests, ported to Perl: reads outside a grant, `../` and absolute climbs, links made by the host or the guest, every kind of write to a read-only grant, FIFOs, a guest that sleeps forever, and planted code.
