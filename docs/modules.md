# Modules

`perl.wasm` is one static binary. It cannot load a compiled module at run time, so every XS (C) module it offers is linked in. Their Perl halves, and the whole pure-Perl core library, are in the library folder the runner grants read-only at `/usr/local/lib/perl5`.

## Compiled in

B, Compress::Raw::Bzip2, Compress::Raw::Zlib, Cwd, Data::Dumper, Devel::Peek, Devel::PPPort, Digest::MD5, Digest::SHA, Encode with all its tables (Encode::Byte, CN, EBCDIC, JP, KR, Symbol, TW, Unicode), Fcntl, File::DosGlob, File::Glob, Filter::Util::Call, Hash::Util, Hash::Util::FieldHash, I18N::Langinfo, IO, List::Util and Scalar::Util, MIME::Base64, Math::BigInt::FastCalc, Opcode (and so Safe), POSIX, PerlIO::encoding, PerlIO::via, SDBM_File, Socket, Storable, Sys::Hostname, Time::HiRes, Time::Piece, Unicode::Collate, Unicode::Normalize, attributes, mro and re.

`$Config{static_ext}` lists them. `$Config{extensions}` lists them with every pure-Perl module set in the library, such as JSON::PP, File::Spec, File::Temp and Getopt::Long.

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
- **Copying a file handle.** WASI cannot duplicate a descriptor, so `open my $fh, ">&", STDOUT` fails with "Not supported". Test::More (through Test2), CPAN and Term::Table copy STDOUT when they load, so they do not load.
- **`use diagnostics`** fails: it reads `perldiag.pod`, and the library ships no pod.
- **`umask`** is not implemented; Archive::Tar warns about it when it loads, and works.
