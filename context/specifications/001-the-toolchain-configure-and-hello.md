# 001 — The toolchain, Configure and miniperl, and hello under wasmtime

- **Status:** proposed 2026-11-24, for the owner's agreement before it is built.
- **Owner:** Clay Gendron
- **Kind:** the build recipe (new `downloads/SHA256SUMS`, `scripts/`, `recipes/config.sh`, `recipes/wasi-cc`), the first checks in `tests/`, the repository's working agreement (`AGENTS.md`, `context/constitution.md`, `context/roadmap.md`).
- **Depends on:** nothing.
- **Research it stands on:**
  - `../research/2026-11-24-what-zeroperl-teaches.md`: where Perl's build and runtime break under WASI, studied from zeroperl (MIT) without copying.
  - `../research/2026-11-24-the-toolchain-probe.md`: what wasi-sdk, wasmtime and wasmtime-py give us here, EXECUTED in this container.
  - Perl's own `INSTALL`, section "Cross-compilation" (READ): Configure has two cross routes; the second "doesn't need access to the target system, but requires you to provide a config.sh". `Makefile.SH` (READ) supports a host miniperl through `hostperl` (it links `$(HOST_PERL)` in as `miniperl`) and host-built header generators through `hostgenerate`.

## The problem

Nothing exists yet. Everything later (the XS modules, the Python runner, the sandbox tests, Perl's own suite) needs one thing first: a `perl.wasm` built from pinned sources by a repeatable script, that starts under wasmtime, reads its library, prints, and survives `die` inside `eval`. That last point is the riskiest part of the port. Perl's `die` unwinds with `longjmp`, and we lower setjmp/longjmp to WebAssembly exception handling instead of Asyncify. If that does not work, nothing else does.

## Decisions

1. **The pins.** Each is a URL and a SHA-256 in `downloads/SHA256SUMS`; `scripts/00_download.sh` fetches into `downloads/`, verifies every hash, and refuses to unpack on a mismatch.

   | Input | Version | Why this one |
   |---|---|---|
   | Perl source | `perl-5.42.3.tar.xz` from `www.cpan.org/src/5.0/` (its hash matches CPAN's published `.sha256.txt`) | the owner's version; the newest 5.42 point release carries its fixes |
   | wasi-sdk | `wasi-sdk-34` (clang 23.1.0), x86_64-linux | the newest wasi-sdk; its setjmp/longjmp runs on the standard exception-handling encoding, which wasmtime 51 loads |
   | wasmtime CLI | v51.0.0, used by the tests only | the same engine as `wasmtime>=51,<52`, which the wheel depends on |

   The hashes are in `downloads/SHA256SUMS` on this branch.

2. **The host build comes first, from the same tarball.** `scripts/10_native.sh` unpacks a fresh tree at `build/native/`, runs Perl's own `Configure -des` for the host with `-Dprefix=$PWD/build/native-install`, builds, runs no tests, and installs. It gives us two things:
   - **the host tools:** `miniperl` and `generate_uudmap`, which the cross build runs on the host;
   - **the oracle:** a native perl 5.42, the same version as the guest, that later specifications compare one-liners against. No system perl is ever the oracle.

3. **The target is configured by a hand-written `config.sh`, and Configure probes nothing.** `recipes/config.sh` is a complete `config.sh` for `wasm32-wasip1`, written by hand. Each `d_*` symbol is set from what wasi-libc in the pinned wasi-sdk declares and implements, not from a probe. The build copies it into a fresh tree at `build/target/` and runs `sh ./Configure -S`, which only substitutes variables into the `.SH` files (`config.h`, `Makefile`, `cflags`, `makedepend`) and runs no compiler. Fixed choices it records:
   - `osname='wasi'`, `archname='wasm32-wasi'`, `usecrosscompile='define'`, `hostperl` and `hostgenerate` pointing at the host build's `miniperl` and `generate_uudmap`.
   - `usedl='undef'` (no dynamic loading; DynaLoader is the static stand-in Perl builds without it), `usethreads='undef'`, `useithreads='undef'`, `usemultiplicity='undef'`.
   - 64-bit integers on a 32-bit pointer: `use64bitint='define'`, `ivsize='8'`, `uvsize='8'`, `ptrsize='4'`, `longsize='4'`, `nvsize='8'`, `byteorder='12345678'`. Perl's integers then behave as on a 64-bit native build, which the one-liner oracle needs.
   - No processes and no signals in the C library sense: `d_fork`, `d_vfork`, `d_waitpid`, `d_wait4`, `d_execv*`-dependent features, `d_pipe`, `d_sigaction`, `d_sigprocmask`, `d_alarm`, `d_kill`, `d_killpg` and the user/group database calls (`d_getpwnam` and kin) are `undef`. Perl then reports these as unimplemented on this platform rather than calling a missing function. The clear "not available in the wasi-perl sandbox" messages are specification 003's.
   - `d_sigsetjmp='undef'`, so Perl's `JMPENV` uses plain `setjmp`/`longjmp`, which the compiler lowers to exception handling (decision 4).
   - **The LC_ALL notation is wasi-libc's, written by hand.** The zeroperl study found its start-up crash: a Configure probe run on the host answered that `setlocale(LC_ALL)` reports a mixed locale as `name=value` pairs, but wasi-libc reports it positionally (`C.UTF-8;C;C;C;C;C`), and Perl's `S_parse_LC_ALL_string` panics at start-up looking for an `=`. Our `config.sh` says what wasi-libc does: `d_perl_lc_all_uses_name_value_pairs='undef'`, `d_perl_lc_all_separator='define'` with `perl_lc_all_separator='";"'`, and `d_perl_lc_all_category_positions_init='define'` with a positions list of exactly as many entries as wasi-libc has categories (`locale.c` checks the count with a static assertion). A check (decision 7) starts Perl with `LC_ALL` unset, with `LC_ALL=C`, and with `LC_CTYPE=C.UTF-8 LC_COLLATE=C`, and each must run.
   - **No user or group database.** `unixish.h` turns on `HAS_PASSWD` and `HAS_GROUP` whatever `config.sh` says, and wasi-libc has no `pwd.h` or `grp.h`. `recipes/include/pwd.h` and `grp.h` declare the structures and functions, and `recipes/wasi-stubs.c` defines them to find nothing (`getpwnam` returns NULL with `errno` set to `ENOENT`). So `getpwnam` in Perl returns the empty list, as on a system that does not know the user, and `~` in `glob` comes from `$HOME`.
   - **Stubs have exact prototypes and refuse.** A function Perl's objects call that wasi-libc lacks (the user and group calls above, `getuid` and kin answering 0 as a single-user system does, `dup` and the process calls Perl names even with `d_fork` undefined, such as `do_aspawn` and `do_spawn` behind `system`) is defined in `recipes/wasi-stubs.c` with the exact prototype of the header Perl includes (a wrong signature traps on WebAssembly, the study found), and a refusal sets `errno` to `ENOSYS` and returns failure. It is linked into both `miniperl`-sized test links and the final `perl`, so Perl's own make finds it at every link. The clear messages built on these refusals are specification 003's.
   - `prefix='/usr/local'`, `privlib`, `archlib` and their `site*` kin all under `/usr/local/lib/perl5/5.42`, the one read-only folder the runner will grant (specification 002 lays it out).
   - `alignbytes='8'`: wasm32 aligns `double` and 64-bit integers at 8; the study notes a 4 there would be wrong.

4. **One compiler wrapper.** `recipes/wasi-cc` calls the pinned wasi-sdk's `clang --target=wasm32-wasip1 --sysroot=…` with:
   - `-mllvm -wasm-enable-sjlj -mllvm -wasm-use-legacy-eh=false`, and links `-lsetjmp`: the flags wasi-sdk documents, proved in the toolchain probe. The legacy encoding is refused by wasmtime 51, so it is never used. No Asyncify, no `wasm-opt --asyncify`.
   - wasi-libc's emulation libraries only where Perl needs a declaration that is otherwise absent and the function is harmless (`-D_WASI_EMULATED_SIGNAL` with `-lwasi-emulated-signal`, `_WASI_EMULATED_PROCESS_CLOCKS`, `_WASI_EMULATED_GETPID`, `_WASI_EMULATED_MMAN`); each one used is listed in the landing notes with the symbol that needed it.
   - a linear-memory stack of 8 MiB (`-Wl,-z,stack-size=8388608`): Perl's recursion (deep regexes, deep data in `Data::Dumper`) is on the C stack.
   Every object, Perl's own and the extensions', is compiled through the wrapper, so all carry the same exception flags; a mix links but fails at run time. clang runs `wasm-opt` after an optimised link when it finds one on `PATH`; the build sets a `PATH` with none, so the module is exactly what `wasm-ld` wrote. The module is a command (`_start`), not a reactor: one call runs one program, as `perl` does.
   The resulting module imports only `wasi_snapshot_preview1` functions. A check reads the import section and fails on any other module name.

5. **The extensions in this first build are only those Perl cannot start without.** `static_ext` holds the minimum the core build insists on; `nonxs_ext` builds as Perl's Makefile does. Every other XS module is specification 002's. This keeps the first failure, if there is one, about the toolchain and not about POSIX.

6. **The scripts.** `scripts/build.sh` runs `00_download.sh`, `10_native.sh` and `20_target.sh` in order and is idempotent: a step whose output exists and whose inputs are unchanged is skipped. `20_target.sh` unpacks, copies `recipes/config.sh`, runs `Configure -S`, then `make` with the host tools, and copies the result to `build/artifacts/perl.wasm` and the built library to `build/artifacts/lib/perl5/5.42/`. Logs go to `build/logs/<step>.log`; a failure prints one line and the log's path. Any source change Perl needs is a patch under `recipes/patches/perl-5.42.x/`, applied after the hash check; the goal for this specification is none.

7. **The checks.** `pytest` under `tests/`, run with `uv run pytest`. They drive the wasmtime CLI (the pinned v51.0.0) with exceptions turned on and the library granted read-only at `/usr/local/lib/perl5`; a test needing an artifact that is not built is skipped with a message naming the script that builds it. Each was seen failing before it passed.
   - `test_hello`: `perl.wasm -e 'print "hello, WebAssembly\n"'` exits 0 with exactly that stdout and empty stderr.
   - `test_die_in_eval`: `-e 'eval { die "boom\n" }; print "caught: $@"; print "after\n"'` prints `caught: boom` then `after` and exits 0. A nested case (`eval` inside `eval`, `die` with an object, `die` inside a sort block) prints the same as the native oracle.
   - `test_die_uncaught`: `-e 'die "bye\n"'` exits 255 (Perl's code for an uncaught `die`) with `bye` on stderr, and no trap.
   - `test_exit_code`: `-e 'exit 7'` exits 7.
   - `test_version`: `-v` names 5.42 and `-V:osname` prints `osname='wasi';`.
   - `test_imports`: the module imports nothing but `wasi_snapshot_preview1`.
   - `test_exceptions_are_required`: the same `perl.wasm` with exceptions turned off in wasmtime fails to load, with wasmtime's error. This records the host requirement as a fact.
   - `test_library_is_read`: `-e 'use strict; use warnings; print "ok\n"'` exits 0, so the granted library is found at the configured path.
   - `test_locales`: hello runs with `LC_ALL` unset, with `LC_ALL=C`, and with mixed categories (`LC_CTYPE=C.UTF-8 LC_COLLATE=C`).
   - `test_dev_null`: for `-e`, Perl opens `BIT_BUCKET`, `/dev/null` (`perl.c`, READ), as its empty script file. The host's `/dev/null` is never granted. Instead the artifact set carries a folder `dev/` holding one empty file `null`, granted read-only at `/dev` beside the library. `-e` then runs, reading `/dev/null` gives end of file as natively, and writing it is refused as a read-only grant refuses any write (a documented difference). The check runs `-e` with exactly those two grants, and checks a write to `/dev/null` fails without a trap.
   - `test_matches_native`: the outputs above equal the native oracle's for the same `-e` program, byte for byte.

8. **Not in this specification.** The Python package and the module cache (004), the core XS modules and the laid-out library (002), the process and signal refusals (003), Perl's own suite (007) and CI (008). The build runs on Linux x86-64 here; the release build host is 008's choice.

## Acceptance

1. `scripts/build.sh` from a clean `build/` produces `build/artifacts/perl.wasm` and its library, from inputs that all verify against `downloads/SHA256SUMS`. EXECUTED, with the command and the artifact's SHA-256 and size in the landing notes.
2. Every check in decision 7 passes under `uv run pytest`, EXECUTED, output pasted in the landing notes.
3. Any patch to Perl's source is listed with its reason; zero is the goal.
4. The review file is written by a fresh agent and the verdict begins "Lands".

## Landing notes

(Written when built.)
