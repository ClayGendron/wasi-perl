# Modules

`perl.wasm` is one static binary. It cannot load a compiled module at run time, so every XS (C) module it offers is linked in. Their Perl halves, and the whole pure-Perl core library, are in the library folder the runner grants read-only at `/usr/local/lib/perl5`.

## Compiled in

B, Compress::Raw::Bzip2, Compress::Raw::Zlib, Cwd, Data::Dumper, Devel::Peek, Devel::PPPort, Digest::MD5, Digest::SHA, Encode with all its tables (Encode::Byte, CN, EBCDIC, JP, KR, Symbol, TW, Unicode), Fcntl, File::DosGlob, File::Glob, Filter::Util::Call, Hash::Util, Hash::Util::FieldHash, I18N::Langinfo, IO, List::Util and Scalar::Util, MIME::Base64, Math::BigInt::FastCalc, Opcode (and so Safe), POSIX, PerlIO::encoding, PerlIO::via, SDBM_File, Socket, Storable, Sys::Hostname, Time::HiRes, Time::Piece, Unicode::Collate, Unicode::Normalize, attributes, mro and re.

`$Config{static_ext}` lists them, with the two compiled-in modules from CPAN below. `$Config{extensions}` lists all of these with every pure-Perl module set in the library, such as JSON::PP, File::Spec, File::Temp and Getopt::Long.

## From CPAN

Five distributions that are not in Perl's own tarball are built in, each from a pinned CPAN release with no change to its source. All five are under Perl's own terms (the Artistic License or the GPL).

| Distribution | Version | Where | What it is for |
|---|---|---|---|
| Cpanel-JSON-XS | 4.83 | compiled in | `Cpanel::JSON::XS`: JSON decode and encode in C. It decodes a 1.4 MB file in about a thirtieth of JSON::PP's instructions. |
| Text-CSV_XS | 1.65 | compiled in | `Text::CSV_XS`: CSV in C, with quoted fields, embedded newlines and doubled quotes. |
| JSON-MaybeXS | 1.004008 | library | `JSON::MaybeXS`: picks the fastest JSON module there is, here Cpanel::JSON::XS. |
| Text-CSV | 2.06 | library | `Text::CSV`: the usual CSV front end. It uses Text::CSV_XS. |
| Algorithm-Diff | 1.201 | library | `Algorithm::Diff`: diffs and longest common subsequences of two lists, such as two files' lines. |

- **For JSON, use `JSON::MaybeXS` or `Cpanel::JSON::XS`.** JSON::PP, in the core library, is pure Perl. `JSON` (JSON.pm) is not shipped: it would pick JSON::PP.
- **One difference from JSON::PP.** Cpanel::JSON::XS writes a number held as a float with a whole value as `5.0`; JSON::PP writes `5`. Python's `json` writes `5.0` too.
- Their booleans are `JSON::PP::Boolean` objects, as JSON::PP's are.
- Hostile input ends in an error the program can catch: JSON nested deeper than 512 levels, truncated, or with trailing garbage; CSV with a broken quote.
- **Keep Cpanel::JSON::XS's `max_depth` at its default of 512.** Its decoder and its encoder recurse in C once per level. Raised, they run out of the guest's stack at about 1,700 and 1,900 levels, and the call ends as trapped. A trap ends only that call; the next one runs as usual. Native perl gets further: its decoder crashes at 100,000.

## Adding Perl code

The library is fixed. `perl.wasm` holds an index of the library it was built with, and answers "no such file" itself for a name under `/usr/local/lib/perl5` that it holds in no case. So a file added to the library folder is not found, even in a set passed with `run(artifacts=...)`.

To add Perl code, grant it in a folder of its own and name that folder with `-I` or `PERL5LIB`:

```python
run(["-I/code", "-MMy::Tool", "-e", "My::Tool::main()"], grants=[Grant("my-code", "/code")])
```

`@INC` is as Perl configures it, and a `.pmc` beside a `.pm` is still preferred. A name the guest answers itself is one no host could find, so every other lookup is still the host's: a name in another case, such as `Strict.pm`, is found where the host's file names ignore case (macOS and Windows), as before.

The index assumes the library is mounted read-only, as the runner always mounts it. An embedder that mounts it writable sees a file the program makes there in `readdir`, but not with `-e` or `require`.

## Left out

| Module | Why |
|---|---|
| threads, threads::shared | WASI preview1 has no threads. |
| Thread::Queue, Thread::Semaphore | Both load threads::shared. |
| IPC::SysV | WASI has no System V IPC. |
| Sys::Syslog | WASI has no syslog. |
| DB_File, GDBM_File, NDBM_File, ODBM_File | Their C libraries are not built. SDBM_File, which carries its own, is in. |
| PerlIO::mmap | WASI has no `mmap`; wasi-libc's stand-in only copies the file into memory, which the default `:perlio` layer already does. |
| XS::APItest, XS::Typemap | Test scaffolding of Perl's own build; `make install` never installs them. |

`use` of a left-out module fails with "Can't locate …".

## What behaves differently in the sandbox

- **Signals.** A `POSIX::SigSet` holds signals 1 to 8 only (wasi-libc's set is one byte): adding a larger one fails with "Invalid argument", `POSIX::SigSet->new(SIGTERM)` dies, and no larger signal is a member. Nothing can be delivered anyway. `POSIX::sigaction`, `sigprocmask`, `sigpending`, `sigsuspend` and `pause` fail with "Function not implemented".
- **Sockets.** Socket's constants and its address packing work. Creating a socket dies with `Unsupported socket function "socket" called`, so `IO::Socket::INET->new` dies too.
- **Clocks.** Time::HiRes's clock ids are Linux's: `CLOCK_REALTIME` 0, `CLOCK_MONOTONIC` 1, `CLOCK_PROCESS_CPUTIME_ID` 2, `CLOCK_THREAD_CPUTIME_ID` 3. The two CPU clocks give the time since the program began. Any other id, such as Linux's 4 (`CLOCK_MONOTONIC_RAW`), fails with "Invalid argument".
- **Error numbers** are WASI's, not Linux's: `ENOENT` is 44 and `EACCES` 2. `$!` gives the right message, and `Errno` and `POSIX` agree, but a test such as `$! == 2` means something else.
- **32-bit C types.** `long` is 32 bits: `POSIX::LONG_MAX` is 2147483647 and `POSIX::strtoul("4294967296")` stops at 4294967295. Perl's own integers are 64 bits.
- **Time zones.** There are no zone tables: local time is UTC, `POSIX::tzset` does nothing, and `POSIX::tzname` gives two empty names (natively `GMT`).
- **Terminals and special files.** `POSIX::ttyname` returns undef; `POSIX::mkfifo` fails with "Function not implemented".
- **Host name.** `Sys::Hostname::hostname` returns `(none)`.
- **`use diagnostics`** fails: it reads `perldiag.pod`, and the library ships no pod.
- **`umask`** is not implemented; Archive::Tar warns about it when it loads, and works.
