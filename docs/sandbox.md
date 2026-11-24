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
| a store into `%SIG` for a signal, e.g. `$SIG{INT} = 'IGNORE'` | `signal handler for SIGINT` |

About `%SIG`:

- A store of any value for a real signal dies, `'IGNORE'`, `'DEFAULT'` and `undef` included. A script that relies on a handler must not run on believing it has one.
- `local $SIG{ALRM}` dies where it is written. When its scope ends, the old value is put back quietly.
- Reading `%SIG` works, and so do names that are not signals (Perl warns about those, as natively).
- `$SIG{__WARN__}` and `$SIG{__DIE__}` are hooks, not signals. They work exactly as natively.

`system("true") == 0 or die` dies with the `system` refusal: `system` never returns a status to compare.

## What still works

- `$$`. It is always 42, wasi-libc's stand-in process ID.
- `getppid`. It returns 0: the parent is the host, outside the sandbox, as for a Linux process whose parent is in another PID namespace.
- `times`, `sleep`, and `select(undef, undef, undef, $seconds)`. They use WASI's clock.
- `exit`, `END` blocks, `die` and `warn`.
- Reopening a standard handle onto a file: `open STDOUT, '>', 'out.txt'`, `open STDERR, '>>', 'log'`, `open STDIN, '<', 'in'`.

## Exit statuses

The exit status is the one native perl on Linux gives.

- `exit N` ends with the low eight bits of N: `exit 256` ends with 0, `exit -1` with 255.
- A `die` takes its status from `$!`, then from `$? >> 8`, then 255, as natively. The `$!` part uses Linux's errno numbers: `open my $f, '<', 'nope' or die "$!\n"` ends with 2 (ENOENT), as on Linux.
- `$!` as a number stays WASI's own. `$! + 0` after a missing file is 44, not 2. `$!` as text is the usual message ("No such file or directory").
- WASI's ENOTCAPABLE ("the sandbox did not grant this") has no Linux number. Its exit status is 13 (EACCES).

wasmtime refuses an exit status of 126 or more and reports an error instead. The module keeps the real status in its `wasi_perl_exit_status` export, which the runner reads.

## Differences from native that remain

- **No descriptor duplication.** WASI preview1 cannot copy a file descriptor. `open(STDERR, '>&', \*STDOUT)`, `open(my $save, '>&STDOUT')` and `>&=` fail, with `$!` "Not supported". Reopening a standard handle onto a file works: see above.
- **The old descriptor of a `dup2` is gone.** wasi-perl's `dup2` moves the descriptor (WASI's `fd_renumber`). This is what Perl's own reopen of STDIN, STDOUT and STDERR needs. Code that calls `dup2` directly and keeps using the old descriptor gets EBADF.
- **Every identity is root.** `getuid` and its kin answer 0, so file tests such as `-w` answer from root's view; the grant still decides what a write may do.
