# 001 — The toolchain, Configure and miniperl, and hello under wasmtime

- **Status:** agreed by the owner 2026-11-24; building.
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

8. **The exit status is kept for the embedder** (decided by the orchestrator while building, 2026-11-24). wasmtime 51 refuses a WASI exit status of 126 or more ("exit with invalid exit status outside of [0..126)"), on the command line and in wasmtime-py alike, so an uncaught `die` (255) or `exit 200` comes back as a host error, not an exit code. The final link wraps wasi-libc's `__wasi_proc_exit` (`-Wl,--wrap=__wasi_proc_exit`, in `ldflags`); the wrapper in `recipes/wasi-stubs.c` stores the status, then calls the real function with it unchanged. The module exports `wasi_perl_exit_status`, which returns the stored status, or -1 if the program has not exited. An export adds no import. The runner (specification 004) reads it after a refused exit and reports the real code. On the plain wasmtime command line a status of 126 or more stays an error. `test_die_uncaught` checks the refusal, the export's 255, and that `END` sees `$?` of 255 as natively.

9. **Not in this specification.** The Python package and the module cache (004), the core XS modules and the laid-out library (002), the process and signal refusals (003), Perl's own suite (007) and CI (008). The build runs on Linux x86-64 here; the release build host is 008's choice.

## Acceptance

1. `scripts/build.sh` from a clean `build/` produces `build/artifacts/perl.wasm` and its library, from inputs that all verify against `downloads/SHA256SUMS`. EXECUTED, with the command and the artifact's SHA-256 and size in the landing notes.
2. Every check in decision 7 passes under `uv run pytest`, EXECUTED, output pasted in the landing notes.
3. Any patch to Perl's source is listed with its reason; zero is the goal.
4. The review file is written by a fresh agent and the verdict begins "Lands".

## Landing notes

Built 2026-11-24 on branch `spec/001-build`, in this container (Linux x86-64, 4 cores).

### Commands and the artifact

    rm -rf build && WASI_PERL_DOWNLOAD_CACHE=/home/user/wasi-perl/downloads scripts/build.sh
    # 00_download: ok / 10_native: ok / 20_target: ok; real 2m1.6s                     EXECUTED
    sha256sum build/artifacts/perl.wasm; stat -c %s build/artifacts/perl.wasm
    # c544325d3989c8456b19d42248e2630c6d6531f03c2eb9c555e6ca69be9d6116, 4029132 bytes   EXECUTED

- `scripts/build.sh` run again right after skips every step ("up to date"). EXECUTED.
- `WASI_PERL_DOWNLOAD_CACHE` names a folder to copy a pinned file from instead of fetching it; the copy is verified against `downloads/SHA256SUMS` exactly as a download is. EXECUTED.
- The library under `build/artifacts/lib/perl5/5.42/` is 1622 files, 36996122 bytes (`find … -type f | wc -l`, `du -sb`). EXECUTED. `build/artifacts/dev/null` is an empty file.
- **The module is not byte-reproducible yet.** Two target builds from the same inputs gave different hashes (`e9db31c8…` from the clean build, then `c544325d…` from `20_target.sh` alone, same size). EXECUTED. The cause is INFERRED: `perl.c` puts `__DATE__`/`__TIME__` into `perl -V` ("Compiled at"). Release reproducibility is 008's concern; the pinned answer is `-DPERL_BUILD_DATE` or a fixed `SOURCE_DATE_EPOCH`.
- The native oracle is `build/native-install/bin/perl` (5.42.3, from the same tarball). `scripts/10_native.sh` runs standalone and is idempotent. EXECUTED.

### The checks

    uv run --no-project --with pytest --with wasmtime==51.0.0 pytest -v tests/build      EXECUTED
    36 passed in 1.45s

test_hello, test_hello_on_the_wasmtime_command_line, test_die_in_eval, test_die_in_eval_nested_object_and_sort_match_native, test_die_uncaught, test_exit_status_export[0, 1, 7, 125, 126, 200, 255], test_exit_status_export_before_exit, test_exit_code, test_version, test_imports, test_exceptions_are_required, test_library_is_read, test_locales[unset, LC_ALL=C, mixed, LANG-and-numeric], test_dev_null, test_pwd_sets_the_working_directory, test_matches_native[0..11]: all PASSED. `ruff check` and `ruff format --check` on `tests/build` are clean. EXECUTED.

Seen failing first (EXECUTED):
- The exit-status checks failed (9 failures: `status` None, `KeyError: 'wasi_perl_exit_status'`) on the module built before the wrapper, then passed after it.
- A module built from a `config.sh` with glibc's name=value LC_ALL notation, and with the `PWD` constructor removed, failed 25 checks: every run panicked in `locale.c` with exit 70 except under `LC_ALL=C`, and the `PWD` check failed. This is zeroperl's start-up crash reproduced, and the reason the hand-written LC_ALL values are what they are. (Only `test_imports`, `test_exceptions_are_required` and the `LC_ALL=C` hello passed on it.)
- The module built before `NO_ENV_ARRAY_IN_MAIN` trapped at `undefined_weak:main`; the one with `/dev` not granted printed "Can't open /dev/null".

### Emulation libraries and the symbol that needs each

Found by relinking `perl` without each library (EXECUTED):

| Library | Define | Needed by |
|---|---|---|
| `-lwasi-emulated-signal` | `_WASI_EMULATED_SIGNAL` | `signal`, `__SIG_IGN`, `__SIG_ERR` (`perl.o`, `mg.o`, `util.o`) |
| `-lwasi-emulated-process-clocks` | `_WASI_EMULATED_PROCESS_CLOCKS` | `times` (`pp_sys.o`) |
| `-lwasi-emulated-getpid` | `_WASI_EMULATED_GETPID` | `getpid` (`perl.o`, `mg.o`, `doio.o`, `util.o`) |

`_WASI_EMULATED_MMAN` is not used: nothing in this build calls `mmap` (`d_mmap` is undef). PerlIO::mmap in 002 would need it.

### What the stubs supply

`recipes/wasi-stubs.c` (prototypes in `recipes/include/pwd.h`, `grp.h`, and `wasi-perl-libc.h`, which `wasi-cc` includes ahead of every file because wasi-libc declares none of these): `getpw{nam,uid,ent}`, `{set,end}pwent`, `getgr{nam,gid,ent}`, `{set,end}grent` find nothing (`ENOENT`); `get{,e}uid`, `get{,e}gid` return 0; `setuid`, `setgid`, `kill`, `execv`, `execvp`, `execl`, `wait`, `do_aspawn`, `do_spawn` and `PerlProc_pipe_cloexec` fail with `ENOSYS`; `umask` returns 022 (it cannot fail). `PerlProc_pipe_cloexec` is there because `util.c` calls it for piped opens even when `HAS_PIPE` is undefined. Each was found by a compile or link error. EXECUTED. A constructor `chdir`s to `$PWD` when it is absolute; `test_pwd_sets_the_working_directory` checks a relative `open` and `readdir(".")` under `PWD=/work`, and that no `PWD` or a relative one leaves the directory at `/`. EXECUTED.

### Patches

None. `recipes/patches/perl-5.42.3/` does not exist; `20_target.sh` applies any `*.patch` there after the hash check.

### How config.sh was written

READ: the native build's generated `config.sh` gave the variable list and format. Every value was then set for wasm32-wasip1:
- For the 343 function symbols Configure checks with `inlibc` and the 59 headers it checks with `inhdr`, the value says whether wasi-sdk 34 declares and links the symbol: a one-line C file per symbol, compiled and linked with `recipes/wasi-cc -std=c99 -D_GNU_SOURCE` (the flags Perl's objects use), and one `#include` per header. EXECUTED. This reads wasi-libc's headers and `libc.a`; it is not a Configure probe and nothing runs on the target. The drafting script is not committed; the file is.
- Processes, signals, users and groups, sockets, SysV IPC, dbm and dynamic loading are undef whatever wasi-libc declares; the `*_r` reentrant forms are undef as in any unthreaded build.
- Sizes (EXECUTED, `sizeof` compiled for the target): int 4, long 4, long long 8, pointer and size_t 4, off_t 8, fpos_t 16, dev_t and ino_t 8, uid_t and gid_t 4 (unsigned), time_t 8, double 8, long double 16 (IEEE binary128, `longdblkind=1`), `_Alignof(double)` and `_Alignof(long long)` 8. IV is `long long`, so the 64-bit formats are `"lld"` and kin.
- The LC_ALL values are those of decision 3; wasi-libc's `<locale.h>` has six categories (`LC_CTYPE` 0 to `LC_MESSAGES` 5, `LC_ALL` 6). READ.
- `static_ext` and `dynamic_ext` are empty; DynaLoader is the static `dl_none` stand-in. The core build insisted on nothing more. `nonxs_ext` is Configure's native list, built as Perl's Makefile does.

### Deviations from the decisions, and why

1. **The checks run the guest under wasmtime-py 51, not the wasmtime CLI.** The wasmtime 51 command line has no read-only grant: `--dir HOST::GUEST` is always writable, and a written suffix such as `::readonly` is silently ignored (a write to `/dev/null` through it reached the host file). EXECUTED. wasmtime-py's `WasiConfig.preopen_dir(path, guest, fs_mutable=False)` grants read-only, and it is the engine the wheel uses. The CLI is still exercised where its behaviour is the fact: `test_hello_on_the_wasmtime_command_line` (with writable grants) and `test_exceptions_are_required` (`-W exceptions=n` fails with "exceptions proposal not enabled"; wasmtime-py with `wasm_exceptions = False` fails the same way). The checks need `--with wasmtime==51.0.0` until 004's `pyproject.toml` lands.
2. **An uncaught `die` does not exit 255 on the host**, because wasmtime refuses it; decision 8 (added while building) keeps the status in an export instead.
3. **The guest's library is granted from `build/artifacts/lib/perl5` at `/usr/local/lib/perl5`**, so `5.42/` inside it is the configured `privlib`.
4. **The library comes from `installperl`, not `make install`.** `make install` runs `installperl` under the host miniperl with `@INC` cut to `lib/`, where Cwd (an XS module not built here) is missing. `20_target.sh` runs `./miniperl -Ilib -MCwd -MFile::Spec installperl --destdir=build/target-install`, loading Cwd's pure-Perl fallback from the source tree first, and copies `usr/local/lib/perl5/5.42` from there. The pods are installed too; 002 lays out and trims the library.
5. **Build-mechanics recipes** that the decisions did not name: `-D_GNU_SOURCE` (Perl's `-std=c99` sets `__STRICT_ANSI__`, which hides most of wasi-libc's POSIX and BSD declarations); `-DNO_ENV_ARRAY_IN_MAIN` (wasi-libc's start-up calls `main(argc, argv)`; Perl's three-argument `main` was left unlinked and trapped); `i_fcntl` define (wasi-libc's `<sys/file.h>` does not pull in `<fcntl.h>`); `d_dup2` define (wasi-libc has it, and Perl's own fallback collided with it); the utilities are extracted by the host miniperl before the main `make`, because their Makefile runs `../perl`; `hostgenerate` points at a copy of `generate_uudmap` made after `generate_uudmap.o`, because with the original two parallel makes raced to recreate the link.
6. `-Wl,-z,stack-size=8388608` is passed at every link by `wasi-cc`; the `-mllvm` flags are passed at links too, where clang notes them unused.

### For the next specifications

- **002 (XS modules):** add each module to `static_ext`; every object already goes through `wasi-cc`. `libs`/`perllibs` hold the stubs object by absolute path (`@BUILD@/stubs/wasi-stubs.o`), which ExtUtils::Liblist may not like for static extension links. Time::HiRes may want `clock_gettime` (present) rather than process clocks; PerlIO::mmap needs `_WASI_EMULATED_MMAN`. Storable's build runs the target perl, which cannot run on the host. A change to `config.h` needs a fresh tree (incremental make did not rebuild `locale.o`; EXECUTED).
- **003 (refusals):** today `system` calls the `do_aspawn`/`do_spawn` stubs (`ENOSYS`), pipe opens and backticks fail through `PerlProc_pipe_cloexec`, `fork` is unimplemented (`d_fork` undef), `kill` fails with `ENOSYS`. Errno values are WASI's, not Linux's: `ENOENT` is 44, so `-e 'use Nonexistent'` ends with status 44 where native perl ends with 2 (EXECUTED). Exit statuses taken from `$!` differ from the oracle; this matters for the one-liner comparisons.
- **004 (runner):** read `wasi_perl_exit_status` after a refused exit (decision 8); grant `dev/` at `/dev` and the library at `/usr/local/lib/perl5`, both read-only; set `PWD` to the guest path of the working directory.

