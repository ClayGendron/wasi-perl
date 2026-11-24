# The sandbox: what is refused and what works

wasi-perl runs Perl inside WebAssembly with WASI preview1. WASI preview1 has no processes and no signals. So the operations that would start a process or handle a signal are refused. A refusal is an ordinary Perl `die`, never a crash.

## The refusal

Every refusal dies with one message:

    <what> is not available in the wasi-perl sandbox at FILE line N.

- `eval` catches it, and `$@` holds the message.
- Uncaught, it ends the program with exit status 255 and the message on stderr.
- `$!` is 0 after a refusal.

## Refused operations

| Perl code | `<what>` in the message |
|---|---|
| `fork` | `fork` |
| `exec` (any form) | `exec` |
| `system` (any form) | `system` |
| `` `…` ``, `qx//`, `readpipe` | `backticks` |
| `open` with `\|`, `-\|`, `\|-`, or a list form | `pipe open` |
| `pipe` | `pipe` |
| `wait` | `wait` |
| `waitpid` | `waitpid` |
| `kill` | `kill` |
| `alarm` | `alarm` |
| `CORE::dump` | `dump` |
| a store into `%SIG` for a signal, e.g. `$SIG{INT} = 'IGNORE'` | `signal handler for SIGINT` |

About `%SIG`:

- A store of any value for a real signal, made by the program's own code, dies, `'IGNORE'`, `'DEFAULT'` and `undef` included. A script that relies on a handler must not run on believing it has one.
- `local $SIG{ALRM}` dies where it is written. When its scope ends, the old value is put back quietly.
- Reading `%SIG` works, and so do names that are not signals (Perl warns about those, as natively).
- `delete $SIG{INT}` works: it puts back the default, which installs nothing.
- A file of the runtime's own library may store a handler while it is being loaded. Core modules such as Math::Complex and Math::Trig do so (`local $SIG{FPE}`), and they work. The handler is kept and never runs: the sandbox delivers no signals. The store must be made by that file's own code, while `require` or `use` has that file open from `/usr/local/lib/perl5/5.42/` (not `site_perl`). Everything else is the program's own code and is refused: any other module, granted or not; a store after the load, such as `use sigtrap`'s handlers; and code that names a library file in `#line`, in `%INC`, or through an `@INC` hook.
- `$SIG{__WARN__}` and `$SIG{__DIE__}` are hooks, not signals. They work exactly as natively.

`system("true") == 0 or die` dies with the `system` refusal: `system` never returns a status to compare.

## What still works

- `$$`. It is always 42, wasi-libc's stand-in process ID.
- `getppid`. It returns 0: the parent is the host, outside the sandbox, as for a Linux process whose parent is in another PID namespace.
- `times`, `sleep`, `Time::HiRes::sleep` and `select(undef, undef, undef, $seconds)`. They use WASI's clock, and they sleep their full time. A sleep still ends at the runner's deadline, like any other code: the guest waits in slices of at most 10 ms. A sleeping guest spends little fuel (about 20,000 a second), so a fuel cap alone bounds a sleep only loosely; the deadline is what bounds it.
- `exit`, `END` blocks, `die` and `warn`.
- Reopening a standard handle onto a file: `open STDOUT, '>', 'out.txt'`, `open STDERR, '>>', 'log'`, `open STDIN, '<', 'in'`.
- Four-argument `select` over the bit vectors `vec` builds, `IO::Select` and `IO::Poll`. A regular file is always ready to read and to write, as on POSIX.
- In-place edits (`-i`) on every host. A Windows host refuses to rename a file over one the program holds open, which `-i` does; the guest then closes its own descriptors on that file, renames again and opens the new file in their place.
- Copying a descriptor, as on POSIX: `open my $save, '>&', STDOUT`, `open STDERR, '>&STDOUT'`, the `&=` forms, `POSIX::dup`, `POSIX::dup2` and `fcntl(F_DUPFD)`. The copies share one open file and its offset; the file closes with the last of them. Test::More loads and works.

## Exit statuses

The exit status is the one native perl on Linux gives.

- `exit N` ends with the low eight bits of N: `exit 256` ends with 0, `exit -1` with 255.
- A `die` takes its status from `$!`, then from `$? >> 8`, then 255, as natively. The `$!` part uses Linux's errno numbers: `open my $f, '<', 'nope' or die "$!\n"` ends with 2 (ENOENT), as on Linux.
- `$!` as a number stays WASI's own. `$! + 0` after a missing file is 44, not 2. `$!` as text is the usual message ("No such file or directory").
- WASI's ENOTCAPABLE ("the sandbox did not grant this") has no Linux number. Its exit status is 13 (EACCES).
- A number the script puts into `$!` is read as a WASI errno too: `$! = 2; die` ends with 13, because WASI's 2 is EACCES. Natively it ends with 2.

wasmtime refuses an exit status of 126 or more and reports an error instead. The module keeps the real status in its `wasi_perl_exit_status` export, which the runner reads.

## Differences from native that remain

- **Descriptor numbers start higher.** Each granted folder holds a descriptor from 3 up, so the first file a program opens gets a number above them, not 3. New descriptors still take the lowest free number.
- **A granted folder's number always names that folder.** `open my $fh, '<&=', 3` succeeds on it, where natively it gives EBADF. Closing it succeeds and leaves the folder granted. `dup2` or `dup3` onto it fails with EBADF, and no `open` or `dup` is given it.
- **At most 256 open descriptors.** The standard streams and the granted folders count against them. Past that, `open`, `dup` and their kin fail with EMFILE ("No file descriptors available").
- **Special files are refused at open.** Opening a FIFO, a device or a socket file in a granted folder fails with ENXIO ("No such device or address"), or ENOTDIR for `opendir`, before the host is asked: a host's open of a FIFO would wait for the other end, past every limit. So does a link to one. `utime` on a FIFO sets its times, as natively, but through a link to one it fails with ENXIO. A name that does not exist is created as usual. `-e` sees a FIFO, but `-p` is false: WASI has no file type for a FIFO.
- **After a rename over an open file on Windows, the old handle reads the new file.** Natively it goes on reading the old one. For `-i` the handle is already at its end, so nothing changes. Linux and macOS hosts allow the rename, and the handle reads the old file as natively.
- **Every identity is root.** `getuid` and its kin answer 0, so file tests such as `-w` answer from root's view; the grant still decides what a write may do.

## The artifact set is checked in every new process

Before a process's first call, the runner hashes every file of the artifact set: `perl.wasm`, `MANIFEST` and each file of the library. Each must match `SHA256SUMS`, and no file may be missing or added. This happens in every new process, on every system.

The runner keeps no record of what it hashed across processes. Such a record would trust a file whose size and times had not moved. A write through a shared memory mapping can change a file's bytes without moving its times, so a changed library would be trusted.
