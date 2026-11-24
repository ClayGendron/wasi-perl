# Modules

`perl.wasm` is one static binary. It cannot load a compiled module at run time, so every XS (C) module it offers is linked in. Their Perl halves, and the whole pure-Perl core library, are in the library folder the runner grants read-only at `/usr/local/lib/perl5`.

## Compiled in

B, Compress::Raw::Bzip2, Compress::Raw::Zlib, Cwd, Data::Dumper, Devel::Peek, Digest::MD5, Digest::SHA, Encode with all its tables (Encode::Byte, CN, EBCDIC, JP, KR, Symbol, TW, Unicode), Fcntl, File::DosGlob, File::Glob, Filter::Util::Call, Hash::Util, Hash::Util::FieldHash, I18N::Langinfo, IO, List::Util and Scalar::Util, MIME::Base64, Math::BigInt::FastCalc, Opcode (and so Safe), POSIX, PerlIO::encoding, PerlIO::via, SDBM_File, Socket, Storable, Sys::Hostname, Time::HiRes, Time::Piece, Unicode::Collate, Unicode::Normalize, attributes, mro and re.

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
| Devel::PPPort, XS::APItest, XS::Typemap | Test scaffolding of Perl's own build; `make install` never installs them. |

`use` of a left-out module fails with "Can't locate …".

## What behaves differently in the sandbox

- **Signals.** POSIX's signal sets (`POSIX::SigSet`) work. `POSIX::sigaction`, `sigprocmask`, `sigpending`, `sigsuspend` and `pause` fail with "Function not implemented".
- **Sockets.** Socket's constants and its address packing work. Creating a socket dies with `Unsupported socket function "socket" called`, so `IO::Socket::INET->new` dies too.
- **Time zones.** There are no zone tables: local time is UTC, and `POSIX::tzset` does nothing.
- **Terminals and special files.** `POSIX::ttyname` returns undef; `POSIX::mkfifo` fails with "Function not implemented".
- **Host name.** `Sys::Hostname::hostname` returns `(none)`.
- **Copying a file handle.** WASI cannot duplicate a descriptor, so `open my $fh, ">&", STDOUT` fails with "Not supported". Test::More (through Test2), CPAN and Term::Table copy STDOUT when they load, so they do not load.
- **`use diagnostics`** fails: it reads `perldiag.pod`, and the library ships no pod.
- **`umask`** is not implemented; Archive::Tar warns about it when it loads, and works.
