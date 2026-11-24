# 002 — The core XS modules, linked statically, and the library as one folder

- **Status:** pre-agreed by the owner 2026-11-24 (the roadmap); written by the orchestrator.
- **Owner:** Clay Gendron
- **Kind:** the build recipe (`recipes/config.sh`'s extension lists, `scripts/20_target.sh`, a new `scripts/30_library.sh`), checks in `tests/build/`.
- **Depends on:** 001.
- **Research it stands on:** `../research/2026-11-24-what-zeroperl-teaches.md` §4 (which extensions zeroperl had to leave out, and that its hand-kept lists drifted); Perl's `INSTALL`, "Building a static perl" (READ: with `usedl` undefined every XS extension in `static_ext` is linked into `perl` and registered by the generated `perlmain.c`).

## The problem

Specification 001 gives a `perl.wasm` with only the extensions Perl cannot start without. Agents' one-liners reach for `List::Util`, `POSIX`, `Data::Dumper`, `JSON::PP`, `Digest::*`, `Encode` and `Storable` all the time. Without dynamic loading, each XS module must be linked into the one binary, and its Perl half must be in the library the runner grants.

## Decisions

1. **Every core XS extension is static, unless it cannot work under WASI, and each one left out is named with its reason.** `static_ext` in `recipes/config.sh` lists them; Perl's generated `perlmain.c` registers them (no hand-written `xs_init`, so the list cannot drift from what is linked). At least these must be in and work: POSIX, List::Util and Scalar::Util (`cpan/Scalar-List-Utils`), Data::Dumper, Storable, Encode (with its tables: `Encode::Byte`, `CN`, `JP`, `KR`, `TW`, `Symbol`, `Unicode`), Digest::MD5, Digest::SHA, MIME::Base64, Time::HiRes, Cwd and File::Spec (`dist/PathTools`), plus what they and the core need (Fcntl, IO, File::Glob, Hash::Util, mro, re, attributes, PerlIO::scalar, PerlIO::encoding, PerlIO::via, Opcode, B, Sys::Hostname, I18N::Langinfo, Unicode::Normalize, Unicode::Collate, Math::BigInt::FastCalc, Compress::Raw::Zlib, Compress::Raw::Bzip2, Time::Piece, Socket if it builds). JSON::PP and File::Spec are pure Perl and come with the library.
   - **Left out, each with its reason in `context/specifications/002-…` landing notes and in `docs/modules.md`:** threads and threads::shared (no threads), IPC::SysV (no System V IPC in WASI), Sys::Syslog (no syslog), GDBM_File, NDBM_File, ODBM_File and DB_File (their C libraries are not built), and any other the build proves cannot work, with the error that proved it.
   - An extension's `Makefile.PL` that runs a probe program for the target (Time::HiRes, Storable's stack measure) is answered by the hints its cross-compile path already reads, or by a recipe hints file under `recipes/hints/`, never by running a target binary on the host.
2. **The library is one read-only folder.** `scripts/30_library.sh` runs Perl's own `installperl` with the host perl and `--destdir=build/stage`, then copies the installed `privlib` and `archlib` into `build/artifacts/lib/perl5/5.42/`, the folder the runner grants at `/usr/local/lib/perl5`. It leaves out what a sandboxed run never reads: `pod/` and `*.pod` files, `CORE/` headers, static archives (`*.a`), `.packlist`, `*.bs` and man pages. `build/artifacts/MANIFEST` lists every file with its SHA-256; `SHA256SUMS` hashes `perl.wasm` and `MANIFEST`. The size of the folder is recorded in the landing notes.
3. **Checks** (`tests/build/test_modules.py`), each compared with the native oracle's output for the same program:
   - every module in decision 1's list loads (`-M<Module> -e 1`), and `%Config` agrees with the list;
   - each does one real thing: `List::Util::sum/max/first/uniq`, `Scalar::Util::blessed/reftype/looks_like_number`, `POSIX::floor/ceil/strftime` on a fixed `gmtime`, `POSIX::_exit` exit code, `Data::Dumper` with `Sortkeys`, `Storable::freeze/thaw` and `dclone` of nested data, `Encode::encode/decode` UTF-8 and Latin-1 and Shift_JIS, `Digest::MD5::md5_hex`, `Digest::SHA::sha256_hex`, `MIME::Base64` round trip, `Time::HiRes::time` increasing, `Cwd::getcwd` under a granted `PWD`, `File::Spec->catfile/rel2abs`, `JSON::PP->new->canonical->encode` and `decode`;
   - `die` and `eval` through XS: `Storable::thaw` of corrupt data dies and is caught; `POSIX::strtol` of junk; a `die` from a `sort` block called through `List::Util::first`;
   - no file under `build/artifacts/lib` is a `.pod`, `.a` or `CORE` header.

## Acceptance

1. `scripts/build.sh` builds `perl.wasm` with every extension of decision 1, EXECUTED, with the binary's size and hash.
2. Every check passes; every extension left out is listed with its reason.
3. A fresh agent's review lands.

## Landing notes

Built 2026-11-24 on branch `spec/002-xs-modules`, in this container (Linux x86-64, 4 shared cores, `WASI_PERL_JOBS=3`).

### Commands and the artifact

    rm -f build/.target.stamp && WASI_PERL_DOWNLOAD_CACHE=/home/user/wasi-perl/downloads WASI_PERL_JOBS=3 scripts/build.sh
    # 00_download: ok / 10_native: up to date / 20_target: ok / 30_library: ok; real 0m56.7s   EXECUTED
    sha256sum build/artifacts/perl.wasm; stat -c %s build/artifacts/perl.wasm
    # 6689b409e75cf6f966062754d8f9c6949f2731805379f970be377d1ee11b274a, 9251730 bytes         EXECUTED

(These are the numbers after the review fixes; the first build, before Devel::PPPort was added, was 9102529 bytes.)

- 001's binary was 4029132 bytes; the extensions add 5222598 bytes. EXECUTED (`stat`).
- The module is still not byte-reproducible (`__DATE__`/`__TIME__` in `perl -V`, as 001 found); 008's concern. INFERRED from 001's finding, not re-run.
- The library: `find build/artifacts/lib -type f | wc -l` gives 1452 files, 22612718 bytes (`find … -printf '%s\n'`, summed). EXECUTED. 001's untrimmed library was 1622 files, 36996122 bytes.
- `MANIFEST` lists 1453 files (the library and `dev/null`), sha256 `638af04f7cef5db1d5ef2d19943e7f3c626f1cb0df1171b91bf0d14dd6b7b371`. `SHA256SUMS` lists 1455 (every file but itself). EXECUTED.
- The build warnings: 332 lines with `warning:` in `build/logs/20_target.log`; no implicit declaration and no `-Wint-conversion`, which `ccflags` now makes an error everywhere. EXECUTED (`grep`).

### The modules

**Linked statically** (`static_ext`, registered by the generated `perlmain.c`), 44 entries: B, Compress/Raw/Bzip2, Compress/Raw/Zlib, Cwd, Data/Dumper, Devel/PPPort, Devel/Peek, Digest/MD5, Digest/SHA, Encode, Encode/Byte, Encode/CN, Encode/EBCDIC, Encode/JP, Encode/KR, Encode/Symbol, Encode/TW, Encode/Unicode, Fcntl, File/DosGlob, File/Glob, Filter/Util/Call, Hash/Util, Hash/Util/FieldHash, I18N/Langinfo, IO, List/Util, MIME/Base64, Math/BigInt/FastCalc, Opcode, POSIX, PerlIO/encoding, PerlIO/via, SDBM_File, Socket, Storable, Sys/Hostname, Time/HiRes, Time/Piece, Unicode/Collate, Unicode/Normalize, attributes, mro, re. PerlIO::scalar is pure Perl in 5.42 (its layer is in the core), so it is in `nonxs_ext`. READ (`ext/PerlIO-scalar` holds only `scalar.pm`). Every one of them loads and works in the guest. EXECUTED (the checks below).

**Left out**, each with its reason (also in `docs/modules.md`):

| Extension | Reason | Label |
|---|---|---|
| threads, threads/shared | No threads (decision 1). | READ |
| Thread/Queue, Thread/Semaphore | Pure Perl, but both `use threads::shared`; taken out of `nonxs_ext` so `%Config` claims nothing that cannot load. | EXECUTED (`Can't locate threads/shared.pm`) |
| IPC/SysV, Sys/Syslog | No System V IPC, no syslog (decision 1). | READ |
| DB_File, GDBM_File, NDBM_File, ODBM_File | Their C libraries are not built (decision 1). | READ |
| PerlIO/mmap | wasi-libc's `mmap` exists only as an emulation (`-lwasi-emulated-mman`) that reads the file into memory, which the default `:perlio` layer already does; it would also need `d_mmap` and a link flag in `wasi-cc`. Nothing gained. | READ (wasi-libc headers, 001's notes) |
| XS/APItest, XS/Typemap | Test scaffolding; the native `make install` installs neither (no `.pm` or `.so` of theirs under `build/native-install`). | EXECUTED (`find`) |

### What the build needed

Found by building with `make -k` and reading each error. EXECUTED.

- **POSIX** includes `<sys/wait.h>` on every Unix-like system and calls the signal-set functions, `sigaction`, `sigprocmask`, `sigpending`, `sigsuspend`, `pause`, `mknod` (its `mkfifo` fallback), `ttyname` and `tzset` unconditionally. wasi-libc hides all of them ("WASI has no signals", "no ttyname", "no timezone tables"). Answered by recipe files, not patches: `recipes/include/sys/wait.h` (the W* status macros only, in Perl's `$?` layout), `recipes/include/wasi-perl-posix.h` (declarations, included through `wasi-perl-libc.h`) and `recipes/wasi-posix-stubs.c` (signal sets are bit sets as wide as wasi-libc's one-byte `sigset_t`, so they hold signals 1 to 8: a larger number fails with `EINVAL` and is never a member; the signal calls, `pause` and `mknod` fail with `ENOSYS`; `ttyname` gives NULL with `ENOTTY`; `tzset` does nothing, as local time is UTC). The core never takes these paths: `HAS_SIGACTION` and kin stay undefined, and the core's only `sigaction` uses are under `HAS_SIGACTION`. READ (`grep` of `*.c *.h`).
- **Time::Piece** calls `tzset`: the same stub.
- **Socket**: wasi-libc's `struct sockaddr_un` has no `sun_path`, so `i_sysun` is now undef. `IO::Socket::INET` names `SOCK_RAW` when it loads, which wasi-libc does not define (only `SOCK_STREAM` 6 and `SOCK_DGRAM` 5); the header gives it Linux's value 3. Without it `use IO` and `use IO::Socket` died at compile time with "Your vendor has not defined Socket macro SOCK_RAW". EXECUTED. Every socket call still dies with `Unsupported socket function "socket" called` (`HAS_SOCKET` undefined).
- **Time::HiRes**: its `Makefile.PL` believes `$Config{d_clock_gettime}`, `d_clock_getres`, `d_clock_nanosleep` and `d_clock` when they exist, instead of running a probe program ("possibly set for cross-compilation"). They are set in `config.sh`; `d_nanosleep` and `d_usleep` were already there. READ. wasi-libc's `clockid_t` is a pointer to one of two clock objects, and Time::HiRes takes clock ids from Perl as integers (typemap `T_IV`). The first build hid that behind `-Wno-error=int-conversion`, and the review found `clock_gettime(-1)` trapping (review H1). Now, when Time::HiRes's own `-DTIME_HIRES_CLOCK_GETTIME` is on the compile line, `wasi-perl-posix.h` gives it Linux's integer ids (`CLOCK_REALTIME` 0, `CLOCK_MONOTONIC` 1, `CLOCK_PROCESS_CPUTIME_ID` 2, `CLOCK_THREAD_CPUTIME_ID` 3) and routes `clock_gettime`, `clock_getres` and `clock_nanosleep` to shims in `wasi-posix-stubs.c`. They map 0 and 1 to wasi-libc's clocks, give 2 and 3 the time since the program began (wasi-libc's `clock()` emulation; the host refuses the WASI CPU-time clock ids with a trap, "Invalid enum value Clockid", EXECUTED), and fail any other id with `EINVAL`. `ccflags` now carries `-Werror=int-conversion`. EXECUTED: the constants print 0 1 2 3 as natively; `clock_gettime`/`clock_getres` of -1, 99 and 2**31 give -1 with "Invalid argument" as natively; `clock_nanosleep(-1, 1000)` and `(99, 1000)` give 1000 as natively. Linux's other ids (4 `CLOCK_MONOTONIC_RAW` and up) fail with `EINVAL` where Linux answers; `docs/modules.md` says so.
- **Storable**: 5.42's `Makefile.PL` probes nothing and runs no target binary; the recursion limits are defaults in `Storable.pm` (`$recursion_limit = 512`, `_hash = 256`). Nothing was needed. READ.
- **Encode**: listed with its eight sub-extensions in `static_ext`, as Configure's own code does for a static Encode (`Configure` lines 24735–24747). READ.
- **The stubs object by absolute path in `libs`** caused no trouble: static extensions link only into `perl`, whose rule uses `$(libs)`. EXECUTED (the build).
- **`make install` and `installperl`**: with Cwd built, `lib/Cwd.pm` exists and `./miniperl installperl --destdir=build/stage` runs with no `-MCwd` preload. That is decision 2 as written, so `make install` was not revisited. EXECUTED.

### The library and the lists

`scripts/30_library.sh` (new) runs `installperl` into `build/stage`, copies `usr/local/lib/perl5/5.42` and removes `pod/`, `CORE/`, `*.pod`, `*.a`, `.packlist`, `*.bs` and the 43 `extralibs.ld` files (lists of libraries for a later static link, never read at run time). `scripts/20_target.sh` now stops at `perl.wasm`. After the review, `30_library.sh` also removes what only builds XS code: C headers (`Encode/encode.h`), Encode's six `*.e2x` templates, `ExtUtils/xsubpp` and `ExtUtils/typemap` (review L5).

- `MANIFEST` is `<sha256>  <path>` for every file under `lib/` and `dev/`. `SHA256SUMS` is the same for every file of the set but itself (`perl.wasm`, `MANIFEST`, `lib/…`, `dev/null`), not only for `perl.wasm` and `MANIFEST` as decision 2 says: the runner (004) checks the library tree against `SHA256SUMS`, so the build writes the list it reads. Both are sorted by path component, as Python sorts paths. The output is byte-identical to 004's `scripts/write_sums.py` (`sums(Path("build/artifacts")) == SHA256SUMS`, EXECUTED), and 004's runner accepts the set as found: `WASI_PERL_ARTIFACTS=build/artifacts uv run --with-editable ../004-runner python -c 'import wasi_perl; print(wasi_perl.run([...]))'` gives `Completed(exit_code=0, …)`. EXECUTED.
- `%Config` named the build's tools by the checkout's absolute path (`cc`, `ld`, `ar`, `sysroot`, `libs`, `perl`, …: 19 in `Config_heavy.pl`, 1 in `Config.pm`). The shipped copies have that prefix replaced by `/wasi-perl-build`, so the library's bytes do not depend on where it was built; the script fails if the path remains anywhere in the library. EXECUTED. This is 001 review L1 in part: `$Config{perl}` still names the native perl under that prefix, not `perl`.
- 001 review M3: `extensions` is now `static_ext` plus `nonxs_ext`, `dynamic_ext` is empty, and `cppsymbols` is what the pinned clang predefines for wasm32-wasip1 with the `-D` flags every compile gets (`clang --target=wasm32-wasip1 --sysroot=… -O2 -D_WASI_EMULATED_SIGNAL -D_WASI_EMULATED_PROCESS_CLOCKS -D_WASI_EMULATED_GETPID -D_GNU_SOURCE -DNO_ENV_ARRAY_IN_MAIN -fwrapv -fno-strict-aliasing -dM -E -x c /dev/null`, 371 object-like macros as `NAME=value`, spaces escaped as Configure does, sorted). EXECUTED. `known_extensions` keeps its Configure meaning, every extension the source tree has (plus Encode's sub-extensions, as Configure adds them).

### The checks

    uv run --no-project --with pytest --with wasmtime==51.0.0 pytest -q tests/build          EXECUTED
    156 passed in 11.13s                                                                   (after the review fixes)

`tests/build/test_modules.py` has 120 of them; 001's 36 stay green. `ruff check` and `ruff format --check` on `tests/build` are clean. EXECUTED.

- `test_module_loads` (66 modules, each compared with native), `test_config_agrees_with_the_list`, `test_config_describes_wasm32` (`cppsymbols` has `__wasm32__`, `__wasi__`, `_ILP32` and no `__x86_64__` or `__linux__`; no `%Config` value names the checkout), `test_every_listed_extension_loads_as_natively` (every entry of `$Config{extensions}` loads as it does natively, but three named below), `test_left_out_module_does_not_load` (all 13 left out), `test_posix_signal_sets_hold_signals_one_to_eight`.
- `test_same_as_native` (27 programs; after the review, `time-hires-clocks` and the W* macros over eight statuses including 0x7f, 0xff and 0xffff were added, stdout, stderr and status equal to native's): List::Util, Scalar::Util, POSIX (`floor`/`ceil`/`fmod`/`strftime` on a fixed `gmtime`, `strtol`, `WEXITSTATUS`), `POSIX::strtol` of junk, Data::Dumper with `Sortkeys`, Storable (`freeze`/`thaw`/`dclone`, `nfreeze` bytes with `$Storable::canonical`), corrupt `thaw` dies and is caught, Encode (UTF-8, Latin-1, Shift_JIS, eight more tables), the `:encoding` layer, Digest::MD5 and Digest::SHA, MIME::Base64 round trip, File::Spec, JSON::PP, a `die` from a `first` block and from a `sort` inside it, Hash::Util, mro/re/attributes, PerlIO layers with `:via(QuotedPrint)`, Safe/Opcode/B/B::Deparse, Unicode::Normalize/Collate, Math::BigInt with FastCalc, Zlib/gzip/Bzip2, Time::Piece, Fcntl/IO, FieldHash/Devel::Peek, Socket's address packing, Time::HiRes.
- `test_posix_exit[0, 3, 42]`, `test_cwd_under_a_granted_pwd` (`getcwd`, `cwd`, `abs_path`, `rel2abs` under `PWD=/work`), `test_glob`, `test_sdbm_on_a_writable_grant`, `test_library_holds_no_build_only_files`, `test_manifest_lists_every_library_file`, `test_sha256sums_covers_every_file_of_the_set`.

Seen failing first (EXECUTED): against 001's artifact (`WASI_PERL_ARTIFACTS=../001-build/build/artifacts`), 98 failed, 11 passed (the pure-Perl loads and the left-out checks), 2 skipped (no `MANIFEST`/`SHA256SUMS`). `test_library_holds_no_build_only_files` failed on the 43 `extralibs.ld` files before the script removed them. On the first POSIX build, `-MIO` failed with the `SOCK_RAW` error above.

### Found while checking, for other specifications

- **Copying a descriptor fails** (`open my $fh, ">&", STDOUT`: "Not supported", 001 review L11). Test::More (through Test2: "Can't dup fileno 1"), CPAN ("dup failed") and Term::Table ("Could not clone STDOUT") therefore do not load. EXECUTED. This matters to 007 (much of `lib/` and `cpan/` testing uses Test::More) and to 003/005 as a documented difference. The check lists them as failing in the sandbox, so a fix shows.
- **`use diagnostics` fails**: it reads `pod/perldiag.pod`, which decision 2 leaves out. EXECUTED.
- **Archive::Tar warns on load** ("Use of uninitialized value in 1's complement" at `Archive/Tar/Constant.pm` line 55: `umask` is unimplemented) and works. EXECUTED.
- `Sys::Hostname::hostname` returns `(none)`; `I18N::Langinfo::langinfo(CODESET)` gives `UTF-8` with no locale set. EXECUTED.
- 003: `POSIX::sigaction` now exists and fails with `ENOSYS` ("Function not implemented"); `IO::Socket::INET->new` dies with `Unsupported socket function "socket" called`, where native returns undef. Both may want the sandbox's refusal wording. The stubs are in `recipes/wasi-posix-stubs.c`, not `wasi-stubs.c`; `libs` and `perllibs` in `config.sh` gained `@BUILD@/stubs/wasi-posix-stubs.o`. `recipes/wasi-cc` was not changed.

### Patches

None. `recipes/patches/perl-5.42.3/` does not exist. EXECUTED (`ls`).

### Deviations from the decisions, and why

1. `SHA256SUMS` hashes every file of the set, not only `perl.wasm` and `MANIFEST`, so the runner can check the library with one list (above).
2. Thread::Queue and Thread::Semaphore are left out with threads, which decision 1 did not name.
3. `extralibs.ld` is removed from the library besides the kinds decision 2 names.
4. The left-out list adds PerlIO::mmap, XS::APItest and XS::Typemap, each with its reason above.
5. `extralibs.ld`, C headers, `*.e2x`, `xsubpp` and its `typemap` are removed from the library besides the kinds decision 2 names.

### Author's answers to the review

The review is `context/reviews/002-the-core-xs-modules-and-the-library-pr-2026-11-24.md`; every finding was answered in one commit.

- **H1, M2 (fixed).** Integer clock ids through recipe shims, as above; `-Wno-error=int-conversion` is gone and `-Werror=int-conversion` is in `ccflags`, so a pointer and integer mix-up anywhere fails the build. The new `time-hires-clocks` check compares the constants, `clock_gettime`/`clock_getres` for -1, 0 to 3, 99 and 2**31, monotonic order and `clock_nanosleep` with native. Seen failing on the first artifact (constants printed as addresses), then passing. EXECUTED.
- **M1 (fixed, the honest way).** The sets stay wasi-libc's one byte. `sigismember` gives 0, not -1, for a number the set cannot hold; `sigfillset` fills 1 to 8; the comments and `docs/modules.md` say signals 1 to 8. A wider set would mean redefining `sigset_t` ahead of `<signal.h>` for every file, for sets nothing can deliver to (signals are refused, 003). `test_posix_signal_sets_hold_signals_one_to_eight` failed first (`ismember(15)` gave -1), then passed. EXECUTED.
- **M3 (fixed).** Devel::PPPort is linked in: it loads natively, so it now loads in the guest too (`PPPort.pm` and its 149 KB XS half). EXECUTED.
- **L1 (fixed).** `WIFSIGNALED` excludes 0x7f in the low seven bits, as glibc does; checked against native over eight statuses.
- **L2 (fixed).** The left-out check runs over every `LEFT_OUT` entry.
- **L3 (fixed).** `30_library.sh` refuses a file name with a blank, a quote or a backslash, and `xargs -d '\n'` splits on lines only.
- **L4 (fixed).** The checkout path is escaped before it goes into `sed`.
- **L5 (fixed).** The build-only files are removed (above), and the check looks for them.
- **L6 (answered).** Decision 2's text is the owner's; the deviation is recorded above (deviation 1). The set as built: `SHA256SUMS` lists every file of the set but itself, and `MANIFEST` lists the library and `dev/null`. 004's docstring ("every pinned source and recipe hash") and 008's acceptance should follow that.
- **L7 (left out).** `CHANGELOG.md` is 008's to start.
- **L8 (fixed).** `docs/modules.md` lists WASI's errno numbers, `POSIX::tzname`'s empty names, the 32-bit `long`, and the clock ids Linux has and the guest refuses.
- **L9 (for 003).** `POSIX::raise(15)` returns 0 and the program goes on (wasi-libc's emulated `raise`), and `POSIX::abort()` ends in a wasm trap. Neither is this branch's code; both are for 003's list.
