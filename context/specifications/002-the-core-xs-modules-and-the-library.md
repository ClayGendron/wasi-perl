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

(Written when built.)
