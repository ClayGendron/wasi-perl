# Preload images: start calls with their modules loaded

A *preload image* is the guest's memory after your code ran once: the modules it loaded, compiled, and the subroutines and package variables it made. You make one at install time or when your service starts. Every call you give it is still a fresh, sealed instance, but it starts with that memory, so it skips the time its modules take to compile.

The API is wasi-python's: `Snapshot`, `SnapshotOrigin`, `SnapshotFailed`, `SnapshotMismatch`, `make_snapshot`, `make_package_snapshot` and `run(..., snapshot=...)`.

wasi-perl ships no image. An image is made only by your own call, with a hash seed drawn from your host's randomness then.

## Make one and use it

```python
from wasi_perl import make_package_snapshot, run

snap = make_package_snapshot()  # once, at install or service start
done = run(["-e", "use POSIX; print floor(2.5)"], snapshot=snap)
```

`make_package_snapshot()` loads eight modules with `require`, so none is imported into `main`: `strict`, `warnings`, `POSIX`, `List::Util`, `JSON::PP`, `Data::Dumper`, `Storable` and `Encode`. Give it names to load others: a CPAN distribution (`JSON-PP`, in any case), or a module name (`Text::Wrap`).

`make_snapshot(code)` runs any code once and keeps what it left:

```python
from wasi_perl import Grant, make_snapshot, run

grants = [Grant("data", "/data")]
snap = make_snapshot(
    'use JSON::PP; open my $f, "<", "/data/schema.json" or die; local $/; '
    "our $schema = decode_json(<$f>); close $f;",
    grants=grants,
)
done = run(["-e", "print scalar keys %$schema"], snapshot=snap)
```

The code runs as a `-e` program in package `main`. A call from the image runs its own program after it, as if both were one script. The code's END blocks run at the end of every call, after the call's own.

`make_snapshot` takes `run`'s keywords and checks them by the same rules. Its deadline is 600 seconds, since a making is a setup step. It takes no `argv` and no `stdin`: the code is the program.

`snapshot=True`, the default, and `snapshot=False` both run `perl.wasm`. A `Snapshot` starts the call from the image.

## Pass it only to calls that load modules

A call from an image pays for the pages of the image it touches. That is less than compiling a module, and more than nothing (on Linux, measured in [the memo](../context/research/2026-11-24-the-preload-image-measured.md)):

| program | perl.wasm | from the package image |
|---|---|---|
| `use strict; use warnings; use POSIX; use JSON::PP; use List::Util` | about 23 ms | about 5 ms |
| `-MPOSIX -e 1` | about 13 ms | about 4.5 ms |

A bare `-e 1` costs 0.64–0.76 ms more from the image than from `perl.wasm` ([the memo](../context/research/2026-11-24-the-preload-image-measured.md), §1). So pass the image to calls that load modules, and not to bare one-liners. The runner never guesses from the program's text.

## Save it and load it in another process

```python
from wasi_perl import Snapshot, SnapshotMismatch, make_package_snapshot

try:
    snap = Snapshot.load(path)
    snap.check()
except (FileNotFoundError, SnapshotMismatch):
    snap = make_package_snapshot()
    snap.save(path)
```

- `save` writes the file at mode 0600. `load` reads only a file only you can read or write, with one name, in a folder no other user can change. On Windows it checks only that the file is no link and has one name, so keep it in a folder only you can write.
- `load` checks the file's header, size and hash. It compiles nothing.
- An image binds to the artifact set it was made with: its `perl-reactor.wasm` and its library. `check()` raises `SnapshotMismatch` when the set is another, as a call would.
- Once this process has saved or loaded an image in a folder, a call refuses a writable grant at or above that folder, and `save` and `load` refuse a folder a running call may write.

## The image is as trusted as its code

A making cannot escape the sandbox. But it decides what every later call's Perl is: it can redefine a core function through `CORE::GLOBAL::`, set `$SIG{__DIE__}` or `$SIG{__WARN__}`, queue an END block, or leave any package variable set. Every call from the image runs what it left.

So give `make_snapshot` only code you would run in every call yourself. Never make an image from a program written by the party the sandbox guards against.

## Keep the image as private as a key

- The image's hash seed is drawn once, at the making, and every call from that image shares it. A program running from the image can read it with `Hash::Util::hash_seed()`. When code from the same image may be hostile, run a program that hashes untrusted keys with `snapshot=False`. To change the seed, make the image again, at each install or service start.
- The saved file holds the seed, and so does the module cache: an image's compiled `.cwasm` carries its data. A cache shared between embedders shares their images' seeds. With `cache_dir=False` nothing is written.
- Freed is not erased. Anything the code read can stay in the image, its saved file and its `.cwasm`, even after the code dropped it. The values of `env` stay in `%ENV`. Pass no secret in `env` or in the code.

## What is frozen in the image

- A module the image loaded is usable without `use`, and is in `%INC`: `POSIX::floor(2.5)` works with no `use POSIX`, and `defined &POSIX::floor` is true.
- `hash_seed()` is the same in every call. The hash *order* still varies from call to call.
- The code's subroutines, package variables and END blocks.
- What a module the image loaded read from the environment as it loaded. Encode reads `PERL_ENCODE_DEBUG` then, so that name is among those below that send a call to `perl.wasm`.

## What is fresh in every call

- `$0`, `@ARGV` and the switches; `%ENV`, exactly the call's; the working folder (`/` unless the call gives `cwd`); the grants and mounts; standard input.
- `PERL5OPT`, `PERL5LIB`, `PERLLIB`, `PERL_USE_UNSAFE_INC`, `PERL5DB`, `PERL_SIGNALS`, `PERL_DESTRUCT_LEVEL`, `PERL_HASH_SEED_DEBUG`, `PATH` and `TMPDIR`, which Perl reads in each call's own parse, end or use.
- The clock: `$^T` and `time` are the call's.
- `rand` and the per-call hash-order bits, drawn from the call's own randomness. `PERL_RAND_SEED` is honoured.
- The special variables with a built-in meaning that `perl.wasm` starts with a fixed value: `$_`, `$,`, `$\`, `$;`, `$^I`, `$.`, `$|` and the format variables among them.

Perl reads some variables only once, as it starts, or at the first open of a handle, and an image cannot honour a call's own value of those. A call whose `env` names any of them runs `perl.wasm` instead, and a making may not set them, so an image is made without them:

- the hash seed's: `PERL_HASH_SEED` and `PERL_PERTURB_KEYS`;
- the locale's: `LC_ALL`, `LC_CTYPE`, `LC_NUMERIC`, `LC_COLLATE`, `LC_TIME`, `LC_MESSAGES`, `LC_MONETARY`, `LANG`, `LANGUAGE`, `PERL_BADLANG`, `PERL_SKIP_LOCALE_INIT` and `PERL_DEBUG_LOCALE_INIT`;
- `PERL_UNICODE`, `PERLIO` and `PERLIO_DEBUG`;
- `PERL_JSON_PP_USE_B`, `PERL_ENCODE_DEBUG` and `PERL_RE_COLORS`.

A call that ran `perl.wasm` this way has `Completed.image_sha256` `None`; a call that started from the image has the image's hash there. So a call that sets `LC_ALL=C` gets `perl.wasm`'s speed, not the image's.

## Avoid these in the image's code

- **Leaving a file open**, a module's `__DATA__` handle included. A call cannot reopen it, so the making fails with `SnapshotFailed` and the reason `"open file"`.
- **Waiting for time to pass.** The clock does not move while the code runs, so a loop on the time runs to its deadline.
- **`my` variables a call expects to see.** A call is a separate compilation unit. Use `our`.
- **Secrets in `env` or the code**, as above.

A making that exits with a status other than 0, or dies, fails with the reason `"exit"`; one that traps, runs out of fuel or passes its deadline, with `"trapped"`. `exit 0` makes an image.

## What it costs

- **The making** runs its code once: about 30 ms and 151 million instructions for the package image. Then the runner writes the image into a module and compiles it, about 1 s, once per image, and caches it. A process's first making also compiles `perl-reactor.wasm`, about 1 s, cached as `perl.wasm` is.
- **The cache** holds about 17.6 MB per image and flavour, about 37 MB for both.
- **Each call, on Linux**, maps the image copy-on-write: the instance starts in microseconds and holds only the pages it writes.
- **Each call, on macOS and Windows**, writes the image's 8 MB of data into fresh memory, since wasmtime has no file there to map. That costs a few milliseconds a call (not measured), so there the image pays only for calls that load modules.
- **Fuel** counts the call's own work: the image's compile is not in it. "five" is 18 million instructions from the image against 120 million from `perl.wasm`.
- **Memory**: a call needs `memory_bytes` at least the image's size, 16 MB for the package image.

In one line: make an image once with `make_package_snapshot`, pass it as `snapshot=` to the calls that load modules, and keep it as private as a key.
