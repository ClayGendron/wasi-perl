# Notes on zeroperl (github.com/uswriting/zeroperl, MIT) — what it teaches a wasm32-wasip1 Perl 5.42 build

Scope: study notes only, written in our own words. No zeroperl code is reproduced. Paths are relative to the zeroperl checkout (`/home/user/zeroperl`, one shallow commit `c28db5d`). Where I checked behaviour against upstream perl-5.42.0 sources, the perl file is named. Anything marked **(inferred)** is my reading; the repo doesn't document it.

---

## 1. Versions, toolchain, setjmp/longjmp strategy

- **Perl**: 5.42.0 by default (`Dockerfile` build arg `PERL_VERSION`, `pipeline/build-wasi-perl.sh`). An older Emscripten workflow (`.github/workflows/nodefs.yml`) targets 5.40.0 with emsdk 3.1.73. It's the project's ancestor (webperl-style) and is no longer the main path.
- **wasi-sdk 27**, **binaryen 124**, Debian trixie base. Host clang/lld come from apt and are used for host-side compiles (`Dockerfile`).
- **Target triple**: `--target=wasm32-wasi` (the old alias) with `--sysroot=$WASI_SDK/share/wasi-sysroot`. It uses the multi-target sysroot layout (`include/wasm32-wasi`, `lib/wasm32-wasi`). See `wasi-bin/wasic` and `pipeline/build-wasi-libs.sh`.
- **Compiler wrappers**: `wasi-bin/wasic` and `wasic++` are Python wrappers that always add sysroot, target and `-w`, which silences *all* warnings. `wasi-bin/wasiconfigure` and `wasi-bin/wasimake` set CC/AR/NM/RANLIB/LD to the SDK tools and HOST_CC to `/usr/bin/clang`. They also add `-D_WASI_EMULATED_SIGNAL` / `-lwasi-emulated-signal` to CFLAGS/LDFLAGS.
- **Optimisation**: `-O3` for libperl (hint `optimize`). The final glue objects use `-O3 -flto`. The final link uses `-flto -g`, then `--strip-all`.
- **Final link** (`pipeline/build-wasm.sh`): reactor model (`-mexec-model=reactor`, `--no-entry`), `--stack-first`, stack 8 MiB (`STACK_SIZE`), initial memory 32 MiB, and `--export-dynamic`. It exports `malloc`/`free`/`__stack_pointer` for the JS host. libperl.a and its own stub archive are linked `--whole-archive`.
- **setjmp/longjmp uses Asyncify, not wasm exceptions.** The `Dockerfile` *deletes every `setjmp.h` from the wasi-sysroot*. That way `#include <setjmp.h>` resolves to `stubs/setjmp.h`, a userland jmp_buf built on Binaryen Asyncify (ruby.wasm "asyncjmp" lineage, per the header comment in `stubs/setjmp.c`). The pieces:
  - `stubs/setjmp.c` / `setjmp_core.S`: setjmp unwinds to the root frame and immediately rewinds to capture the context. longjmp unwinds to the root and rewinds into the saved setjmp. The assembly saves and restores `__stack_pointer`.
  - `stubs/runtime.c`: a root "trampoline" loop that re-enters the callback after each unwind. **Every exported entry point** in `stubs/zeroperl.c` (init, eval, run_file, call, reset) is routed through it.
  - Each jmp_buf carries a **32 KiB** Asyncify buffer (`stubs/setjmp.h`). Perl puts a JMPENV on the C stack for every eval/require/sort/etc., which partly explains the 8 MiB stack.
  - `stubs/machine.c` / `machine_core.S`: stack-pointer accessors and a scan of locals in the style of a conservative GC (a ruby leftover).
  - Post-link, `wasm-opt --asyncify` runs. In async mode (`asyncify-imports@wasi_snapshot_preview1.fd_read,env.call_host_function`) the JS host can suspend on stdin reads and host callbacks. With `ASYNCIFY=false` the pass still runs, with `asyncify-ignore-imports`, because setjmp needs it. The README calls `zeroperl_reactor.wasm` "without asyncify", but it's really the pre-pass link and can't longjmp.
  - **Fake wasm-opt** (`tools/wasm-opt`): during the perl build and the final link they swap in a no-op `wasm-opt`. The wasi-sdk clang driver runs `wasm-opt` automatically after an optimised link if it finds one on PATH, and they want to control that pass themselves (`Dockerfile`, `pipeline/build-wasm.sh`).
  - After wasm-opt they strip a trailing empty `name` custom section because `wamrc` (WAMR AOT) can't parse it (`pipeline/build-wasm.sh`).
  - Perl must not use `sigsetjmp`. The repo leaves this implicit, but the old Emscripten hints in `nodefs.yml` set `d_sigsetjmp='undef'`, so Perl's `Sigjmp_buf/Sigsetjmp` macros fall back to plain `jmp_buf/setjmp` (perl `config_h.SH`).

**For us**: we use `-fwasm-exceptions -mllvm -wasm-enable-sjlj` instead, so we must **keep** the sysroot `setjmp.h`. Every TU that can call setjmp/longjmp (all of libperl and any XS that uses JMPENV) needs the same flags, and so does the link. The SDK's setjmp support library and an EH-capable runtime are also required. That part comes from my own knowledge of wasi-sdk, not from zeroperl.

---

## 2. Every breakage they hit and how they worked around it

### 2a. The LC_ALL / locale startup panic

The fix is in `pipeline/build-wasi-perl.sh`, five `sed` edits on `config.sh` after Configure, then `Configure -S`. Intended values are also in `pipeline/hints-wasi.sh` under "Preprocessor workarounds".

- **Root cause**: Perl 5.42's Configure has a run-time probe, "Checking the syntax of LC_ALL when categories are set to different locales" (perl `Configure` ~line 17486). It finds out what `setlocale(LC_ALL, NULL)` returns when categories differ and sets `d_perl_lc_all_uses_name_value_pairs`, `d_perl_lc_all_separator`/`perl_lc_all_separator`, and `d_perl_lc_all_category_positions_init`/`perl_lc_all_category_positions_init`. This probe **ignores hint values**: it assigns all of them unconditionally whenever `d_setlocale` is defined.
- zeroperl's Configure probes run on the **host** (see §3), so the probe saw **glibc**, which reports mixed locales as `LC_CTYPE=…;LC_NUMERIC=…` name=value pairs. wasi-libc uses **musl's** locale code, which reports them **positionally**: six locale names joined by `;`, in numeric category order (CTYPE=0, NUMERIC=1, TIME=2, COLLATE=3, MONETARY=4, MESSAGES=5; LC_ALL=6).
- **What crashes**: in perl `locale.c`, `S_parse_LC_ALL_string` is compiled, under `PERL_LC_ALL_USES_NAME_VALUE_PAIRS`, to split only on `;` and expect `name=value`. Interpreter construction (`perl_construct` calls `init_i18nl10n`, perl `perl.c`) queries LC_ALL. Once any category differs from the others, the string comes back as something like `C.UTF-8;C;C;C;C;C`, for example when Perl keeps LC_NUMERIC at "C" while LC_CTYPE follows the environment. The parser then fails with "needs an '=' to split name=value". On the call paths with panic-on-error it reaches `locale_panic_via_`, so the interpreter panics at startup **(inferred trigger; the repo only says "Fix locale settings that Configure overrides")**.
- **Fix approach**: force positional notation. Set uses-name-value-pairs to undef, separator defined as `";"`, and category-positions init defined as the six-entry array {0,1,2,3,4,5}. Then run `Configure -S` to regenerate config.h from the edited config.sh.
- The six-entry count matters. perl `locale.c` has a static assertion that the positions array length equals `LC_ALL_INDEX_`, the number of non-LC_ALL categories wasi-libc's `<locale.h>` defines.
- The hint also sets `d_setlocale='undef'`. With `-de`, Configure's `inlibc` probe overrides a hinted function value when the symbol is found, so this was probably ineffective, and the LC_ALL probe did run **(inferred)**.

### 2b. File::Glob and the password database (`patches/glob.patch`, `stubs/pwd.h`, `stubs/grp.h`)

- wasi-libc has no `<pwd.h>`/`<grp.h>`. Perl's `unixish.h` still defines `HAS_PASSWD`/`HAS_GROUP` unconditionally, and `pp_sys.c` and `ext/File-Glob/bsd_glob.c` use `struct passwd` fields under `HAS_PASSWD`.
- They put stub `pwd.h`/`grp.h` on the include path via `-I stubs` in ccflags. The headers define the structs and NULL-returning `getpwnam/getpwuid/getgrnam/getgrgid`. They're *definitions in a header*, not declarations. Hints set `i_pwd`/`i_grp` = define.
- Because the header defines functions, including it in two TUs would give duplicate symbols. The patch therefore removes `bsd_glob.c`'s pwd include and its `getpwuid(getuid())` / `getpwnam()` tilde-expansion paths. `~` now expands only from `$HOME`, and `~user` is returned literally.
- The patch also contains an `#elif DOSISH` → `#ifdef DOSISH` adjustment.

### 2c. Missing libc functions (`stubs/stubs.c`, linked only in the final link)

- Identity calls always return 0, so the program "is root": `getuid/geteuid/getgid/getegid/setuid/setgid`. Perl calls `PerlProc_getuid` etc. unconditionally in `perl.c`, regardless of `d_getuid`.
- `kill` returns 0 (claims success). `dup` returns -1. `umask` returns 0. `execvp/execv/execl` return -1. `wait` returns -1. `tzset` is a no-op.
- `do_aspawn`/`do_spawn` return -1. Without fork, perl `pp_sys.c` `pp_system` takes the "!FORK" branch, which calls these. On Unix they're otherwise only defined for Win32/OS2/VMS, so they must be supplied.
- `PerlProc_pipe_cloexec` returns -1, so pipes, backticks and piped opens fail.
- Several stubs use made-up signatures ("Adjusted signature" comments; `execl` with a fixed argument count). On wasm a signature mismatch becomes a trapping call, so these are fragile.
- `stubs/stubs.h` is an inline duplicate that nothing includes.
- `stubs/syslog.h` provides no-op `openlog/syslog/closelog`. Sys::Syslog is disabled anyway, so it's probably a leftover **(inferred)**.

### 2d. Other compile-level workarounds (hint `ccflags`/`cppflags`, `pipeline/hints-wasi.sh`)

- `-include <sysroot>/include/wasm32-wasi/fcntl.h` is forced into every TU, presumably so `O_*`/`AT_*` constants are visible everywhere **(inferred)**.
- `-Wno-implicit-function-declaration` and `-Wno-int-conversion` cover calls to functions wasi-libc doesn't declare (clang 16+ treats these as errors). `-Wno-null-pointer-arithmetic` and `-Wno-incomplete-setjmp-declaration` are also passed.
- `-D_WASI_EMULATED_SIGNAL`, `_PROCESS_CLOCKS` and `_GETPID` are defined, plus the matching `-lwasi-emulated-*` libs and `-lwasi-emulated-mman` (for PerlIO::mmap's mmap).
- `-D_GNU_SOURCE -D_POSIX_C_SOURCE`.
- `-DNO_MATHOMS` drops the mathoms compatibility functions.
- `-DBIG_TIME` makes `time()` and the stat times NVs (perl `pp_sys.c`, `doio.c`).
- `-DSTANDARD_C -DPERL_USE_SAFE_PUTENV` (inherited from the Emscripten hints).

### 2e. Runtime quirks (`stubs/zeroperl.c`, `README.md`)

- **/dev/null**: with `-e`, perl opens `BIT_BUCKET` (`/dev/null`; perl `perl.c`). The README warns hosts may need to preopen `/dev/null`. zeroperl's default init argv is effectively `-e 0`.
- **Interpreter reset**: after `perl_destruct` + `perl_construct`, they zero `PL_opfreehook`. In a non-MULTIPLICITY build, File::Glob's static `MY_CXT` keeps the old hook, so re-booting File::Glob would chain the hook to itself.
- `PL_perl_destruct_level=0` and clearing `PERL_EXIT_DESTRUCT_END` keep END blocks from running on destruct.
- argv[0] must be `zeroperl` (README).

---

## 3. Configure and config.sh

- **Configure does run**: `wasiconfigure sh ./Configure -sde -Dhintfile=wasi` (`pipeline/build-wasi-perl.sh`).
- It is **not** run in cross mode: no `-Dusecrosscompile` and no `Cross/` machinery. Instead they use a "compile for WASI, run on host" trick in `wasi-bin/wasic`.
  - Whenever the args contain `try.c`, the wrapper first compiles it for WASI. Failure means "feature absent".
  - It then recompiles the same file with host clang, so Configure can execute `./try`.
  - Both compiles get **only** `try.c -o try`, without Configure's ccflags/libs.
  - Consequences:
    - Symbol presence reflects wasi-libc, but without the emulated-libs flags, so signal/getpid/clock/mmap tend to probe as absent.
    - Every *run-time* answer (sizes, LC_ALL syntax, numeric formatting) reflects **x86_64 glibc**.
- **Hints file**: `pipeline/hints-wasi.sh` is turned into a template, with paths filled in by `sed`, and copied to `hints/wasi.sh`. Size hints survive because Configure's size probes only run when the variable is empty (perl `Configure` intsize/ptrsize blocks).
- **Post-Configure surgery**: the five LC_ALL `sed`s (§2a), then `Configure -S`.
- **Variables set in the hints, by group, and why**:
  - **Identity/arch**:
    - `osname=wasi`, `archname=wasm32-wasi`, so the arch dir is `lib/5.42.0/wasm32-wasi`.
    - `prefix=/zeroperl`, which is also the embedded-FS prefix.
    - `inc_version_list=none`, `man1dir/man3dir=none`.
    - `hostperl` and `hostgenerate` point at the native build.
    - `cc/ld=wasic`, SDK `ar/ranlib`.
  - **No dynamic loading**:
    - `dlsrc=none`, so DynaLoader builds from `dl_none`.
    - `d_dlopen` undef; `loclibpth`/`glibpth` empty.
    - `usenm` undef (nm on wasm objects is useless).
  - **Malloc/threads**:
    - `usemymalloc=n`, `usemallocwrap=define`.
    - `usethreads` and `usemultiplicity` undef.
    - `i_pthread` and the pthread_* functions undef.
  - **Processes**:
    - `d_wait`, `d_waitpid`, `d_wait3`, `d_wait4`, `i_syswait`, `d_vfork`, `d_pseudofork`, `d_killpg`, `d_pause` and `d_syscall` are undef.
    - `d_procselfexe` is undef, so there's no `/proc/self/exe` for `$^X`.
  - **Users/groups**: every `d_getpw*`, `d_getgr*`, `d_setpwent`/`d_setgrent`/`d_endgrent` (and `_r`), `d_getspnam`, `d_getprpwnam`, `d_get[e]uid`/`gid` and `d_setr[ug]id` is undef. `i_pwd`/`i_grp` are define, for the stub headers.
  - **SysV IPC**: msg*/sem*/shm* undef.
  - **Misc**: `d_setproctitle`, `d_malloc_size`, `d_malloc_good_size`, `d_clearenv`, `d_cuserid` and `d_eaccess` undef.
  - **Files**:
    - `useperlio`, `d_stat`, `d_fstat`, `d_lstat`, `d_lseek`, `d_fseeko` and `d_ftello` are define.
    - `d_statblks`, `d_fdclose`, `d_dirnamlen` and `d_llseek` are undef.
    - Every `*64` variant (`d_off64_t`, `d_stat64`, `d_fstat64`, `d_lseek64`, `d_fseeko64`, `d_ftello64`, `d_readdir64_r`) is undef, because WASI's off_t is natively 64-bit.
    - `uselargefiles`, `lseeksize=8`, `fpossize=16`.
  - **Types (wasm32)**:
    - char 1, short 2, int 4, long 4, long long 8, ptr/size_t/ssize_t 4.
    - double 8; `longdblsize=16` (wasm32 long double is binary128).
    - `alignbytes=4`.
    - `use64bitint` define with IV/UV = (unsigned) long long, `ivsize=uvsize=8`, quad = long long (`quadkind=3`).
    - Explicit i8..u64 types and sizes.
    - NV = double, `nv_preserves_uv_bits=53`, `d_nv_preserves_uv` undef.
    - `uselongdouble` and `usequadmath` undef.
  - **Locale**: §2a.

---

## 4. XS extensions

- **Built static** (hint `static_ext`): mro, Time::HiRes, File::Glob, Sys::Hostname, PerlIO::via/mmap/encoding, attributes, Unicode::Normalize, Unicode::Collate, re, Digest::MD5/SHA, Math::BigInt::FastCalc, Data::Dumper, I18N::Langinfo, Time::Piece, IO, Hash::Util(::FieldHash), Filter::Util::Call, Encode plus all of its sub-encodings (Unicode, JP, KR, CN, TW, EBCDIC, Symbol, Byte), Compress::Raw::Zlib/Bzip2, MIME::Base64, Cwd, List::Util, Fcntl, Opcode.
- **Disabled** (hint `noextensions`): **POSIX, Socket, Storable**, Devel::Peek, Sys::Syslog, B, threads, threads::shared, IPC::SysV, SDBM_File, File::DosGlob. No reasons are documented. My guesses **(inferred)**:
  - POSIX references many functions wasi-libc lacks (signals/sigaction, termios, process groups, tmpnam, etc.).
  - Storable's build runs the freshly built target perl to probe stack limits, which isn't possible when cross-compiling.
  - Socket fails because wasip1 has no BSD sockets.
- **Registration is hand-written**: they don't use the generated `perlmain.c`. `stubs/zeroperl.c` has its own `xs_init` that registers `DynaLoader::boot_DynaLoader` and `Pkg::bootstrap` for each static ext, so `XSLoader::load` finds the boot function. `pipeline/build-wasm.sh` lists each `lib/auto/**.a` explicitly, plus `$(cat ext.libs)`.
- These three lists (hint, link line, xs_init) must be kept in sync by hand. They already drift: `mro` is in `static_ext`, but its archive isn't linked and its boot isn't registered.
- **zlib 1.3.1 / bzip2 1.0.8** are cross-built into the sysroot (`pipeline/build-wasi-libs.sh`) and linked with `-lz -lbz2`. Compress-Raw-* bundle their own sources by default, so this may be belt-and-braces.
- **Time::HiRes** needs the process-clocks emulation. Its boot prototype is written differently from the others, which suggests it was added later.
- **PerlIO::mmap** needs `-lwasi-emulated-mman`.

---

## 5. fork / exec / system / signals / users

- **fork**: absent in wasi-libc, so `d_fork` is undef by probe. `fork` dies with "Unsupported function" (perl `pp_sys.c`).
- **system/exec**: `system` goes through the stubbed `do_aspawn`/`do_spawn` and returns -1. `exec*` are stubbed to -1. `pipe` is stubbed to -1. `wait*` are undef or stubbed.
- **Signals**: only wasi-libc's emulated `signal()`/`raise()`. `kill` is stubbed to "success"; `killpg` and `pause` are undef.
- ExifTool's `$SIG{INT}` and `$SIG{CONT}` assignments are deleted with `sed` (`pipeline/build-exiftool.sh`).
- **Users**: every getpw*/getgr* is undef, and uid/gid stubs return 0 (§2b, §2c).
- `umask` returns 0. `dup` fails, so dup-mode opens like `>&STDOUT` will fail **(inferred)**.

---

## 6. Library bundling and host tools

- **Native perl first** (`pipeline/build-native-perl.sh`): the same 5.42.0, built with `-Dusedevel` and a big static list. It provides:
  - `miniperl` and `generate_uudmap` for the cross build;
  - `cpanm` → Perl::Tidy, used for minifying;
  - an ExifTool install whose modules get copied into the wasm arch dir (`pipeline/prepare-prefix.sh`).
- **Host tools in the WASI build**:
  - With `hostperl` set, perl's `Makefile.SH` turns `miniperl` into a **symlink to the host miniperl** and runs `installperl`/`installman` with it.
  - With `hostgenerate` set, `generate_uudmap` becomes a symlink to the host binary. They also symlink it by hand.
  - The build is `make utilities PERL=<host miniperl>`, then `make RUN_PERL="<host miniperl> -Ilib -I."`, then `make install` (`pipeline/build-wasi-perl.sh`).
  - The host miniperl loads the *WASI* `lib/Config.pm` via `-Ilib`, so extension Makefile.PLs see the WASI config.
  - The version must match exactly.
- **Install and trim** (`pipeline/prepare-prefix.sh`):
  - Install into `/zeroperl`.
  - Delete `bin/`, `.so/.a/.ld/.pod/.h` files and executables.
  - Delete ~1270 paths from `tools/delete.txt`: CPAN, Test2/TAP/Test, ExtUtils, Pod, most of `unicore` (including `unicore/lib` and `unicore/To`), Unicode::Collate data, IPC::Open2/Open3/Cmd, User::pwent/grent, B::Deparse, perl5db, Net, IO::Socket::IP and HTTP bits.
  - Run `perltidy` to strip comments and POD from every .pm/.pl.
- **Embedding** (`tools/sfs.js`, `stubs/zeroperl.c`):
  - A Node script flattens the tree into one blob, which is pulled in with C23 `#embed` (the data TU is compiled `-O0 -std=c23`), plus a table of path/start/end entries keyed by `/zeroperl/...`.
  - At link time they use `--wrap` on `fopen/open/close/read/lseek/stat/fstat`. Paths under the prefix are served from memory through `fmemopen`, and everything else goes to the real WASI calls.
  - Wrappers for `fileno`/`access` exist but aren't in the `--wrap` list, so they're dead.
  - Limitations:
    - Fake fds start at 3 and are tracked in a 32-entry table. Real WASI preopens also start at fd 3 and are never marked as used, so collisions are possible **(inferred)**.
    - There's no opendir/readdir on the blob.
    - Paths are capped at 256 bytes.
    - stat reports only size and `S_IFREG`.
- **Host API**: ~60 `export_name` functions (init/eval/call, value/array/hash marshalling) and one import, `env.call_host_function`, wired through an XS dispatcher (`stubs/zeroperl.c`).

---

## 7. Pitfalls to plan for (ranked)

1. **LC_ALL notation in config.sh.** Our hand-written config.sh must say positional notation: `d_perl_lc_all_uses_name_value_pairs` undef, separator define `";"`, positions define `{ 0, 1, 2, 3, 4, 5 }`. Otherwise expect a startup locale panic. Check the count against wasi-libc's `<locale.h>`, because of the static assertion in perl `locale.c` (§2a).
2. **sjlj via wasm EH.**
   - Keep the sysroot `setjmp.h` (zeroperl deletes it).
   - Set `d_sigsetjmp` undef so Perl uses `jmp_buf`/`setjmp`.
   - Compile *all* of libperl and every XS archive with identical EH/sjlj flags, and link the SDK's setjmp runtime.
   - Make sure the runtime supports the EH proposal (legacy vs exnref encoding).
   - Clang auto-runs `wasm-opt` if one is on PATH; make sure it gets EH feature flags or isn't found. zeroperl hides it (`tools/wasm-opt`).
3. **Undefined symbols at link.** Provide one stub TU with *exact* prototypes for:
   - get[e]uid/get[e]gid (and set*), kill, dup (and maybe dup2), umask, exec*, wait, tzset, pipe;
   - `do_aspawn`/`do_spawn` (needed by `pp_system` without fork).
   
   Don't copy zeroperl's mismatched signatures: on wasm a signature mismatch is a trap. Don't blanket-silence `-Wimplicit-function-declaration`, or these bugs hide.
4. **pwd/grp.** `unixish.h` forces `HAS_PASSWD`/`HAS_GROUP` on, so `pp_sys.c` and `bsd_glob.c` need `struct passwd`/`struct group`. Ship declaration-only stub headers plus a single .c with NULL-returning getpw*/getgr*. That avoids zeroperl's header-definition plus source-patch approach.
5. **Hand-written config.sh correctness.**
   - Use the wasm32 type sizes from §3; Configure's defaults would come from the host.
   - Check `alignbytes`: zeroperl uses 4, but wasm32 doubles and long long align to 8, and `max_align_t` is 16. I'd use 8.
   - Set `byteorder` (`1234` / `12345678` for the 8-byte IV).
   - Set `d_fork` undef.
   - Set the emulated-feature `d_*` values to match the `-D_WASI_EMULATED_*` flags you link.
6. **Emulated libs.** Each `_WASI_EMULATED_*` define needs its `-lwasi-emulated-*` at *every* link: the in-tree `perl` link as well as our final link.
7. **Static ext bookkeeping.** The `static_ext` list, the archives linked and the boot registrations must agree; consider generating perlmain.c rather than hand-writing it. Decide up front about POSIX (high user value, big porting effort), Storable (its build runs the target perl), Socket (no), B/Devel::Peek (probably fine).
8. **Host miniperl.** It must be the exact same version, used via `hostperl`/`hostgenerate`, and overriding `PERL`/`RUN_PERL` for `utilities` and the main build. `installperl` runs under the host perl.
9. **/dev/null.** Make sure `-e` doesn't fail when `/dev/null` isn't preopened (README). Options: run with a preopen, or `FAKE_BIT_BUCKET`.
10. **Library delivery.** If you embed, don't collide with WASI preopen fds, and remember there's no directory listing. Trimming `unicore/lib` breaks less common `\p{}` lookups. A preopened directory with `-I` paths is simpler.
11. **Non-MULTIPLICITY reset.** Static XS state (File::Glob's `opfreehook`) survives `perl_destruct`/`perl_construct` and needs clearing.
12. **Semantics users will notice.** `$<` and `$>` are 0. `system`, backticks, pipes and fork fail. `kill` can't actually signal anything; zeroperl even returns success. `~user` globbing doesn't work. `$^X` can't come from `/proc/self/exe`.
13. **Stack and memory.** zeroperl needed 8 MiB of stack, partly because of its 32 KiB Asyncify jmp_bufs. EH-based sjlj should need less, but deep regex/recursion still needs a generous `-z stack-size`.
