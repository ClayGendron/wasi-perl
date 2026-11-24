# Review: 001, the toolchain, Configure and hello (branch `spec/001-build`)

Reviewer: an agent (Claude), a fresh sub-agent of https://claude.ai/code/session_01ThzDEUwftdmy8zWBJK1gWa; not the author

- **Date:** 2026-11-24
- **Scope:** `git diff origin/dev...spec/001-build` (4 commits, 15 files), read against `AGENTS.md`, `context/constitution.md`, the specification with its landing notes, and the zeroperl memo.
- **Not done:** a clean rebuild. The artifacts in `build/` were used. A parallel agent shares the 4 cores.

## What I ran

| Command | Result | Label |
|---|---|---|
| `uv run --no-project --with pytest --with wasmtime==51.0.0 pytest tests/build -q` | `36 passed in 0.77s` | EXECUTED |
| `sha256sum build/artifacts/perl.wasm` | `c544325d…6d6116`, the same hash the landing notes give | EXECUTED |
| `sha256sum -c` over `downloads/SHA256SUMS` | 3 OK. The Perl hash also matches `www.cpan.org/src/5.0/perl-5.42.3.tar.xz.sha256.txt` | EXECUTED |
| `uvx ruff check tests/build`, `uvx ruff format --check tests/build` | clean | EXECUTED |
| `_Static_assert` file compiled with `recipes/wasi-cc -std=c99 -D_GNU_SOURCE` | every size in config.sh holds: int/long 4, long long 8, pointer and size_t 4, off_t 8, fpos_t 16, dev_t and ino_t 8, uid_t and gid_t 4 (unsigned), time_t 8, long double 16, `_Alignof(double)` and `_Alignof(long long)` 8 | EXECUTED |
| `llvm-objcopy --dump-section=target_features` on every `.o` in `build/target` and on `build/stubs/wasi-stubs.o` | 54 of 54, plus the stubs object, carry `exception-handling` | EXECUTED |
| The `producers` section of `perl.wasm` | clang 23.1.0 only. No binaryen, so wasm-opt did not run | EXECUTED |
| The GLOBAL and DATA sections of `perl.wasm` | `__stack_pointer` starts at 8388608 and data starts at 8388608, so the stack comes first and an overflow traps | EXECUTED |
| `grep warning: build/logs/20_target.log` | 94 warnings, none of them an implicit declaration or an incompatible pointer | EXECUTED |
| Guest probes under wasmtime-py 51 (scratch scripts; no file in the repo) | outputs quoted in the findings below | EXECUTED |

## Checked and found sound

- **Pins.** `scripts/00_download.sh` verifies every file before the `mv` out of `.part`, cached copies included. `10_native.sh` and `20_target.sh` re-check the tarball hash before they unpack it.
- **No Configure probes on the target.** `Configure -S` only substitutes. `cflags.SH` compiles a flag test with `wasi-cc` (see L5), but nothing runs on the target.
- **config.sh.**
  - The variable list matches the native `config.sh` exactly.
  - The LC_ALL values match wasi-libc's `<locale.h>` (`LC_CTYPE` 0 to `LC_MESSAGES` 5).
  - `sig_name` matches wasi-libc's `bits/signal.h` numbering.
  - `alignbytes='8'` is correct.
  - Every `d_*` set to define that names a function was checked with `llvm-nm` against libc.a, libm, the emulation libraries and the stubs. Each is present, or is not a function at all (attributes, struct members, macros).
- **Stubs.**
  - The prototypes match musl's own declarations in the wasi-libc headers.
  - They also match Perl's call sites: the non-Win32 `do_aspawn(…, (void **)MARK, (void **)SP)` in `pp_sys.c`, and the unprefixed `PerlProc_pipe_cloexec` when `HAS_PIPE` is undefined.
  - Each refusal sets `ENOSYS`. Only the getuid family and `umask` succeed (see L6).
  - Probes (EXECUTED): `system` gives -1 with "Function not implemented". `exec`, `wait`, backticks, pipe opens and `kill` all fail with ENOSYS. `getpwnam` gives the empty list with ENOENT.
- **proc_exit wrapper.**
  - `__wrap___wasi_proc_exit` stores the status and passes it on unchanged.
  - The export returns -1 before an exit. This is checked by a test.
  - A refused `exit 255` shows the backtrace `__wasi_proc_exit` ← `__wrap___wasi_proc_exit` ← `_Exit` ← `exit`.
- **Import check.** The LEB128 parser handles all five import kinds correctly. It also fails on an empty import list.
- **Read-only grants.**
  - Every read-only grant in the tests goes through `WasiConfig.preopen_dir(path, guest, fs_mutable=False)`. Its signature was checked in wasmtime-py 51.
  - A guest append to a granted `strict.pm` is refused with EPERM (EXECUTED).
  - `wasmtime run --help` confirms that the v51 CLI's `--dir` has no read-only form. **Nothing relies on the CLI for read-only.** The one CLI test that grants folders grants them writable (M4).
- **Code comments** under `recipes/`, `scripts/` and `tests/` name no specification, decision or review.
- **zeroperl.**
  - I compared `recipes/include/pwd.h`, `grp.h` and `recipes/wasi-stubs.c` with zeroperl's `stubs/pwd.h`, `grp.h` and `stubs.c`. Nothing was copied.
  - Ours declare in headers and define in one C file. Theirs define functions in the headers.
  - Our field order is musl's. Our prototypes are exact where zeroperl's are "adjusted".
  - Our refusals set `ENOSYS` where zeroperl's `kill` and `setuid` claim success.
  - The function set overlaps because the same link errors drive it.

## Findings

| # | Rank | Where | Finding | Suggested fix |
|---|---|---|---|---|
| M1 | Medium | `recipes/wasi-cc:13-17` | `: "${WASI_SDK:=…}"` honours an ambient `WASI_SDK`, a variable wasi developers often export. `20_target.sh` checks the pinned `$SDK/bin/clang` but never sets `WASI_SDK`, so on such a host every object is compiled by an unpinned clang and sysroot. Meanwhile `ar`, `nm` and `sysroot` in config.sh still name the pinned SDK. The step stamp does not see the variable either. This defeats constitution 4.2 without any message. | Have `20_target.sh` export `WASI_SDK="$SDK"`, or rename the override (e.g. `WASI_PERL_SDK`) and make `wasi-cc` refuse a path other than the pinned one unless that variable is set. Put the SDK path in the stamp. |
| M2 | Medium | `recipes/wasi-cc:17` | "PATH holds no wasm-opt" is only true on this container. The wrapper's `PATH` keeps `/usr/bin` and `/bin`, where an apt-installed binaryen puts `wasm-opt`. On such a host clang would run it after the `-O2` link, against decision 4. clang 23 has a switch for this: `--no-wasm-opt` (EXECUTED, `clang --help-hidden`). | Add `--no-wasm-opt` to the link flags. Add a check that the `producers` section of `perl.wasm` names no `binaryen`. |
| M3 | Medium | `recipes/config.sh:706`, `:99` | Two values were copied from the native x86-64 build rather than set for wasm32. This contradicts the landing note "Every value was then set for wasm32-wasip1". (a) `extensions` lists POSIX, Socket, Storable, threads, GDBM_File, Fcntl and the rest, though `static_ext` and `dynamic_ext` are empty. The guest's `$Config{extensions}` claims POSIX while `require POSIX` fails (EXECUTED). Perl's own `t/` suite (007) and modules use this list to decide what to test or load. (b) `cppsymbols` holds the host's gcc 13 and glibc macros: `__x86_64__=1`, `__linux__=1`, `__GLIBC__=2`, `__LP64__=1`, `__SIZEOF_LONG__=8`, `__SIZEOF_POINTER__=8`. | Set `extensions` to `$dynamic_ext $static_ext $nonxs_ext`, as Configure does, and let 002 extend it. Generate `cppsymbols` from `recipes/wasi-cc -dM -E - </dev/null`, or leave it empty. |
| M4 | Medium | `tests/build/test_perl_wasm.py:30-48` | `test_hello_on_the_wasmtime_command_line` grants `build/artifacts/lib/perl5` and `build/artifacts/dev` to the guest writable, because the v51 CLI cannot grant read-only. That puts the runtime's own files in a writable grant, which constitution 4.5 forbids. It is harmless for this fixed program, but it is a bad pattern to copy. | `print` needs no library. Grant only a `tmp_path` copy of `dev/` (one empty `null`) on the CLI, and no artifact folder. |
| L1 | Low | `recipes/config.sh:68`, `:1000-1001`, `:1136` | The shipped `Config.pm` and `Config_heavy.pl` carry build-host absolute paths: 1 and 19 of them (`cc`, `ld`, `ar`, `sysroot`, `libs`, …). `$Config{perl}` is the native perl's path, where native has `'perl'` (EXECUTED in the guest). The library is not reproducible across build hosts, and it leaks the build path. | Leave it to 002 or 008: after `installperl`, rewrite `Config.pm` and `Config_heavy.pl` to neutral values (`cc='wasi-cc'`, `perl='perl'`, no host paths). Record this in 008's reproducibility work. |
| L2 | Low | `recipes/wasi-stubs.c:29-32` | `getuid` returning 0 makes Perl treat the program as root: `cando()` answers yes. So `-w` and `-r` are true for any file. `-w` on a read-only-granted `strict.pm` gives 1, while the append is refused with EPERM (EXECUTED). The specification allows uid 0, but agent one-liners that test `-w` before writing will be misled. | Record it as a documented difference in 003 or 005. Or decide there to answer a non-zero uid such as 1, so that `cando()` falls back to the mode bits. |
| L3 | Low | `tests/build/test_perl_wasm.py:201-213` | With no locale variables set, the guest's default is C.UTF-8 (`${^UTF8LOCALE}` 1), where native gives 0 (EXECUTED). The `C.UTF-8;C;…` mapping itself is right: `LC_CTYPE=C.UTF-8 LC_COLLATE=C` gives 1 and `LC_NUMERIC=C.UTF-8 LC_CTYPE=C` gives 0, both as natively. But `test_locales` only checks that hello runs. A swapped positions list would still pass. | Compare `${^UTF8LOCALE}` with the native oracle for each environment, and mark the "unset" difference as known. Note the default for the one-liner oracle (005). |
| L4 | Low | specification decision 8 (`:68`); `recipes/wasi-stubs.c:51-56` | Two choices were taken while building and not agreed by the owner. Decision 8 (the exit-status export) was added by the orchestrator. The `PWD` constructor appears only in the landing notes, not in any decision, yet 004 depends on it. `AGENTS.md` says nothing is built that no specification describes. | Make the `PWD` working directory a numbered decision. Ask the owner, through AskUserQuestion, to accept decisions 8 and the new `PWD` decision. |
| L5 | Low | specification decision 3 (`:32`) | "`Configure -S` … runs no compiler" is not quite true. `cflags.SH` compiles and links `_cflags.c` with `wasi-cc` to test warning flags ("cflags.SH: Adding -std=c99" in `build/logs/20_target.log`). It is compile-only on the host, so no rule is broken. | Correct the wording in the landing notes. |
| L6 | Low | `recipes/wasi-stubs.c:39` | `umask` reports 022 and changes nothing. That is a silent success outside the getuid family. It is harmless: `d_umask` is undef, so Perl's `umask` returns undef (EXECUTED), and only `doio.c:1192` and `perlio.c:5466` call it, around `mkstemp`. | Keep it. Say in the comment why it is safe (only the temp-file code calls it, and Perl's `umask` reports unimplemented). |
| L7 | Low | `scripts/20_target.sh:73` | `perl.wasm` ships the DWARF sections of wasi-libc and compiler-rt (`.debug_info`, `.debug_line` and others, about 0.2 MB), as well as the `name` section. | Strip them in 002 or 008 (`llvm-strip --strip-debug`, keeping `name` if backtraces are wanted). Record the size in MANIFEST. |
| L8 | Low | (repository root) | There is no `CHANGELOG.md`. `AGENTS.md` step 4 asks for lines under "Unreleased". | Add `CHANGELOG.md` with an Unreleased entry: perl.wasm builds from pinned sources; no source patches. |
| L9 | Low | `scripts/00_download.sh:38-45` | The `.unpacked` stamp for the tools is keyed by name only. A re-pinned file with the same name would leave the old tree in use. | Write the file's SHA-256 into `.unpacked`, and unpack again when it differs. |
| L10 | Low | `tests/build/test_perl_wasm.py:69` | The no-trap check for an uncaught `die` is `"unreachable" not in r.error`. Other traps would pass it. | Also assert that the first backtrace frame is `__wasi_proc_exit`. Or assert that `r.status == 255` came from the wrapper and that the error is exactly the exit-status refusal. |
| L11 | Low | specification decision 3 (`:40`) | The decision lists `dup` among the stubs. wasi-libc defines `dup` and `dup2`, so no stub is needed, and the code is right not to add one. But `open my $d, ">&", \*STDOUT` fails with "Not supported" (EXECUTED), which matters for one-liners. | Correct the decision text. Add `>&` duplication to the documented differences in 003 or 005. |

## Verdict

Lands: there are no High findings. All 36 checks pass on the recorded artifact, the pins verify, config.sh is right for wasm32 where it counts (sizes, alignment, LC_ALL, signals, the `d_*` functions), and every object carries the same exception flags. Before the merge, fix the four Medium findings in one commit (M1 and M2 in `wasi-cc`, M3 in `config.sh`, M4 in the CLI test), or answer them in writing.
