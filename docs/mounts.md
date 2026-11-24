# Mounts: files served from your own code

A *mount* puts a *provider*, an object you write, at a guest path. The guest opens, reads, lists and stats files there with ordinary Perl (`open`, `opendir`, `-e`, `stat`, `-pi`), and your provider answers each one. The files can come from a database, an object store or a permission layer of your own. A mount is read-only unless you pass `writable=True`.

The API, the provider protocol, the limits and the record are wasi-python's, name for name. A provider written for one runtime works unchanged with the other.

## Write a read-only provider

A read-only provider has three methods. This one serves files from a dict:

```python
from wasi_perl import Errno, Mount, Stat, run


class Files:  # paths are absolute within the mount: "/" is the mount itself
    def __init__(self, tree):  # {"/a.log": b"...", "/d": None (a folder)}
        self.tree = tree

    def stat(self, path):
        if path == "/" or self.tree.get(path, b"") is None:
            return Stat("dir")
        return Stat("file", len(self.tree[path])) if path in self.tree else Errno.ENOENT

    def list(self, path):
        prefix = path.rstrip("/") + "/"
        return [
            (p[len(prefix) :], "dir" if v is None else "file")
            for p, v in self.tree.items()
            if p.startswith(prefix) and "/" not in p[len(prefix) :]
        ]

    def read(self, path):  # the whole file
        if path == "/" or self.tree.get(path, b"") is None:
            return Errno.EISDIR
        return self.tree.get(path, Errno.ENOENT)


done = run(
    ["-ne", "print if /ERROR/", "/work/a.log"],
    mounts=[Mount(Files({"/a.log": b"ok\nERROR one\n"}), "/work")],
)
done.stdout  # b'ERROR one\n'
done.files.calls  # {'stat': 1, 'list': 0, 'read': 1, 'write': 0, 'mkdir': 0, ...}
```

Each method gets a normalized absolute path, where `/` is the mount itself. Each answers a value or an `Errno`:

- `stat(path)` returns `Stat(kind, size=0, mtime_ns=0)`. `kind` is `"file"` or `"dir"`, and the `Stat` for `/` must be a folder.
- `list(path)` returns an iterable of `(name, kind)` pairs. A name is one path component: not empty, `.` or `..`, with no `/` or NUL, at most 255 bytes of UTF-8, and no repeats.
- `read(path)` returns the whole file as `bytes`, `bytearray` or `memoryview`. It returns `Errno.EISDIR` when the path is a folder, and `Errno.ENOTDIR` when a parent is a file.

`Errno` holds WASI's own error numbers. The guest sees each as `$!` of that number. The runner knows nothing about permissions: it calls your provider for every access and passes its answer through. Nothing is cached between opens; your provider may cache.

## What a Perl program costs a provider

Perl's guest makes calls a Python guest does not, so the counts are Perl's. Every open but an exclusive create is looked at first, with a `stat`, so a special file is never opened. Measured on the built `perl.wasm`, a mount at `/work`, each row alone (`tests/mounts/test_mount_perl.py::test_each_perl_operation_makes_its_provider_calls`):

| Perl | provider calls |
|---|---|
| `perl -e 1` | none |
| `open "<"`, read to the end, `close` | `stat` 1, `read` 1 |
| `-e`, `-f`, `-s`, `stat` or `lstat` of a file | `stat` 1 |
| `opendir`, `readdir`, `closedir` of a folder | `stat` 2, `list` 1 |
| the same of the mount's own folder | `stat` 1, `list` 1 |
| `glob("/work/d/*")` over N files | `stat` 2 + N, `list` 1 |
| `open ">"` of a new file, `print`, `close` | `stat` 1, `write` 2 (`b""`, then the bytes) |
| `open ">>"` of a file, `print`, `close` | `stat` 1, `read` 1, `write` 1 |
| `open "+<"`, no write, `close` | `stat` 1, `read` 1 |
| `sysopen` with `O_CREAT \| O_EXCL` | `stat` 1, `write` 1 |
| `mkdir`, `rmdir`, `rename` | that method, 1 |
| `unlink` | `stat` 1, `remove` 1 |
| `perl -pi -e ... file` | `stat` 2, `read` 1, `write` 2, `rename` 1 |
| the same with `cwd` at the mount's own path | and `stat` 2, `list` 1 |
| the same with `cwd` below it | and `stat` 3, `list` 1 |

`-i` costs more with `cwd` in a mount: Perl lists the working folder once for each file it edits, and the guest's start-up stats `cwd` once.

A module the guest loads from a mount, with `-I`, `use lib` or `do`, is your provider's code to the guest. Serve only what you would run.

## Raise an OSError instead

A method may raise an `OSError` instead of returning an `Errno`, and the guest gets the same answer. The errno is matched by name, never by number, since numbers differ between hosts. With no errno at all, the class answers: `raise FileNotFoundError(path)` is `ENOENT`, and so on for `FileExistsError`, `IsADirectoryError`, `NotADirectoryError`, `PermissionError` and `TimeoutError`.

An errno outside the `Errno` set becomes `EIO`. The guest never sees the exception's message, only its own C library's text for the errno.

Any other exception, or an answer outside the protocol, gives the guest `EIO` and adds a `FileFault` to `done.files.faults`; the run goes on. A `BaseException` that is not an `Exception`, such as `KeyboardInterrupt`, stops the call and is raised from `run`.

## Treat a path as a name, not a host path

Each path your provider gets is `/` and then components joined by `/`. A component is never empty, `.` or `..`, and never holds `/`, `\`, `:` or NUL. The runner answers `EINVAL` itself for a component with `\` or `:`, before your provider is called. A provider that stores on a host folder joins the components one at a time, and never hands the whole path to a host path function.

## Choose where to mount

`Mount(provider, guest)` takes an absolute, normalized guest path with no `\`, or `/`. A mount at `/` makes your provider the guest's whole filesystem, except the library at `/usr/local/lib/perl5`, `/dev`, and what a more specific grant or mount owns. Relative paths then reach it when `cwd` is `None`. A mount at or under `/usr/local/lib/perl5` or `/dev` is refused; one above them, such as `/usr`, is not, and every path into the runtime's folders spelled through it is `EPERM`.

Mounts and grants nest either way, and no two share a guest path. A path spelled through an outer mount into an inner grant or mount is `EPERM`, with no provider call.

`cwd` may name a guest path in a mount. The runner checks it by its text and calls no provider before the guest runs. The guest's start-up then asks your `stat`. If you do not answer a folder, the guest starts at `/`, and relative paths fail.

## Know the limits

- **Provider calls.** `max_file_calls=` caps every provider call, whatever it answers. The default is `defaults().file_calls`, 10,000. The call that would pass it is not made: the run stops, and `trapped` is `"out of file calls"`, with `exit_code` and `fuel_used` `None`.
- **Open files.** The guest's own descriptor table holds 256 numbers. An open past it answers `EMFILE` before your provider is called. A mount holds at most 1,024 open files of its own whatever the guest.
- **Staged bytes.** The bytes of every open file plus every kept listing are at most `memory_bytes`, or 4 GiB with `memory_bytes=None`. A `read` or a growth that would pass it answers `EFBIG`. Check a file's size in `stat` before you read it.

## Know how a mount differs from a grant

A read-only mount answers as a read-only grant does: every write-type call is `EPERM`, before your provider is called. It differs in a few ways:

- A whole file is staged at open, so a 1-byte read stages the file.
- `stat` of an open handle shows its kind and size but no times. There are no links: `readlink` answers `EINVAL`.
- `..` is resolved by text, so `missing/../a.txt` opens `a.txt`. A climb above the folder the call starts from is `EPERM`, as on a grant.
- `select` reports a mount file ready, as it does a grant's.
- A path over 4,096 bytes is `ENAMETOOLONG`.

## Mind time and threads

A provider runs synchronously on the thread that called `run`. Before each provider call the runner checks the call's deadline; once it has passed, the provider is not called, and the run ends `"deadline"`. Your provider's own time is outside the deadline and cannot be interrupted: bound its calls yourself.

One provider may serve several calls at once, from each call's thread, so a shared provider must be thread-safe. An `async def` method is refused. A provider may itself call `run`; its record is its own.

Ctrl-C during a provider call is deferred until the provider returns. Then the guest stops, nothing is written back, and `run` raises `KeyboardInterrupt`.

## Read the record

`done.files` is a `FileRecord`, or `None` for a call without mounts. It holds:

- `calls`, each method's count, zeros included;
- `faults`, the first 1,000 `FileFault(method, path, error)`;
- for writable mounts, `refused` and `refused_total` (below).

## Make a mount writable

`Mount(provider, guest, writable=True)` lets the guest create, write, append, truncate, make and remove folders, and rename within the mount. `writable` is keyword-only and must be `True` or `False`. A read-only mount never calls the write half.

A writable provider has five more methods, each answering `None` for success or an `Errno`: `write(path, data)` (the whole file; create it if missing), `mkdir(path)`, `remove(path)` (a file; `EISDIR` for a folder), `rmdir(path)` (an empty folder; `ENOTEMPTY` otherwise) and `rename(src, dst)` (POSIX's replace rule, within the mount).

**A file exists from its open.** A create or a truncation reaches you at open as `write(path, b"")`. A create you refuse fails the guest's `open`.

**The bytes are handed over whole.** What the guest writes goes into a buffer the runner holds. The whole file reaches you as one `write(path, data)` at its close, at `fsync`, and at the call's ending, whatever ends it: `exit`, `POSIX::_exit`, an uncaught `die`, the fuel cap or the deadline. A refused write shows at the close: Perl's `close` returns false with `$!` set. Nothing is written back when `run` itself raises.

**The record.** `done.files.refused` holds the first 1,000 `RefusedWrite` entries: every write-half call that did not succeed, and every write-back owed but not made. Each names the `mount`, `method`, `paths`, `errno`, `at` (`"open"`, `"call"`, `"close"`, `"sync"` or `"exit"`) and `cause` (`"refused"`, `"fault"` or `"limit"`). A rename the provider refuses over a file the guest holds open is tried twice, so it is recorded twice.

A writable mount differs from a writable grant in a few more ways:

- It writes no host file. Every write goes to the runner's buffer or to your provider.
- Two handles on one path have two buffers, and the later write-back wins.
- A rename between a mount and another writable folder is `EXDEV`; `File::Copy`'s `move` then copies and removes.
- `link` and `symlink` are `ENOTSUP`. `utime` of a path that is there succeeds and changes nothing.

**State across runs is yours.** A provider that keeps its state holds what a later run meets, modules it could load included. Give each untrusted program its own writable namespace.

In one line: a mount is a provider you write, answering whole-file `stat`, `list` and `read` (and, when writable, five more methods), with every call counted and bounded.
