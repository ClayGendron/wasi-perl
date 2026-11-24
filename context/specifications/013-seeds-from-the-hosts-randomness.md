# 013 — Seeds from the host's randomness

- **Status:** agreed by the owner 2026-11-24 ("Agree all four (Recommended)"). Written by the orchestrator; amended the same day for the review `../reviews/perf-research-and-specs-013-016-pr-2026-11-24.md` (H1, M1, M2, L6).
- **Owner:** Clay Gendron
- **Kind:** the build recipe (`recipes/wasi-fd-table.c`), checks in `tests/build/` and `tests/escapes/`, and `docs/sandbox.md`.
- **Depends on:** 009, 010.
- **Research it stands on:** `../research/2026-11-24-perf-startup.md` §6, and `../research/2026-11-24-perf-synthesis.md` §1.

## Intent

Today the guest has no `/dev/urandom`. So Perl seeds its hash randomisation, its internal random state and `srand` from the time of day, a constant process id and two stack addresses. Those can be guessed from a call's start time. That weakens Perl's defence against hash flooding, an attack where crafted keys make every insert into a hash slow.

After this specification, Perl reads its seeds from WASI's `random_get`, as native Perl reads `/dev/urandom`. Nothing else in the sandbox changes.

Evidence:
- `-e "/dev/urandom"` is false in the guest (EXECUTED, by the orchestrator and by study D).
- `Perl_seed` opens `/dev/urandom` first, and uses the clock only when that fails (READ: `util.c` 4683–4750 in 5.42.3).

## Decisions

1. **A guest-side `/dev/urandom` and `/dev/random`, served by the descriptor table, rather than a linker wrap of `Perl_seed`.**

   Why not a wrap:
   - `Perl_seed` has three callers that matter:
     - `perl.c` 302 seeds the internal random state, from which the hash seed is drawn;
     - `pp.c` 3217 and 3283 seed `srand` and `rand`;
     - `util.c` 4839 seeds the hash's order bits.
   - The last is in the same file as `Perl_seed`. A linker `--wrap` may not redirect a call inside one object file.

   Why the device instead:
   - Every caller already tries `/dev/urandom` first, so all three get real randomness with no patch to Perl.
   - Perl programs and modules that read `/dev/urandom` themselves work as they do natively.
2. **What the device is.** It is a descriptor of the guest's own table that holds no host descriptor. Native Linux is the model, and every difference is listed in `docs/sandbox.md`.
   - **Which paths are virtual.** A `path_open` whose folder descriptor is the runner's `/dev` preopen, and whose path, with `.` components dropped, is exactly `urandom` or `random`. So `/dev/urandom` and `/dev/./urandom` are virtual. A path through a grant, such as `<grant>/../dev/urandom`, is not: it goes to the host and gets today's answer.
   - **Its slot.** The table entry's host descriptor is a named sentinel, `VIRTUAL_RANDOM`, beside today's `GONE`. No wrapper may hand it to the host. `release()` skips the host for it, as it does for `GONE`.
   - **The open.**
     - With `O_DIRECTORY`, or a trailing `/` (`urandom/`), it answers `ENOTDIR`.
     - With `O_CREAT` and `O_EXCL`, it answers `EEXIST`.
     - Otherwise it opens, for reading, writing or both, as rights asked. `O_TRUNC` and `O_APPEND` change nothing, as on a Linux character device.
   - **Reads.** `fd_read` and `fd_pread` fill every buffer from `__wasi_random_get`, never short.
   - **Writes.** `fd_write` and `fd_pwrite` accept every byte and discard it, returning the count. That is what Linux does for a user who is not root, who may write to `/dev/urandom` (mode 0666).
   - **Seeks.** `fd_seek` and `fd_tell` answer offset 0, as Linux's `noop_llseek` does from a new descriptor.
   - **Stat.**
     - `fd_fdstat_get` answers a character device whose base rights include `FD_READ`, `FD_WRITE`, `FD_SEEK`, `FD_TELL`, `FD_FDSTAT_SET_FLAGS`, `FD_FILESTAT_GET` and `POLL_FD_READWRITE`. wasi-libc's `isatty` calls a character device without `FD_SEEK` and `FD_TELL` a terminal, so these rights make `-t` false, as natively.
     - `fd_filestat_get` and `path_filestat_get` answer a character device of size 0. Its `dev` is the `/dev` preopen's own, its `ino` a fixed nonzero constant, and its times those of the `/dev` folder, read from the host at the stat.
   - **Waits.** `poll_oneoff` and four-argument `select` treat the descriptor as ready for reading and writing at once. The wrapper answers its subscriptions itself and sends the others to the host with a zero timeout, so the events of both come back together.
   - **Every other `fd_*` call** on it:
     - `fd_fdstat_set_flags` succeeds and changes nothing;
     - `fd_close`, `dup`, `dup2`, `dup3` and `fcntl` work as for any table descriptor;
     - `fd_readdir` answers `ENOTDIR`;
     - everything else answers `EINVAL`: `fd_advise`, `fd_allocate`, `fd_sync`, `fd_datasync`, `fd_filestat_set_size`, `fd_filestat_set_times` and `fd_renumber` onto a host descriptor.
     - A `path_*` call that uses it as its folder answers `ENOTDIR`.
   - **What is not virtual.** Listing `/dev` still shows only `null`, because the folder listing is the host's. That is a documented difference from native.
3. **The special-file refusal is unchanged for the host's files.** The virtual device never reaches the host, so it is not a host special file. The `ENXIO` refusal of FIFOs, sockets and devices inside grants keeps every check of spec 010.
4. **The artifact set is unchanged.** `dev/` still holds exactly one empty `null`, and the runner's check of that stays.
5. **The module gains one import, `random_get`.** It is WASI preview1, and every preview1 host defines it, so the rule that the guest reaches the host only through WASI preview1 holds.
   - The descriptor table's header comment, which says the module imports nothing new, is corrected.
   - perl.wasm imports 32 preview1 functions today, without `random_get` (the review, EXECUTED).
6. **Perl's own controls keep working, as natively:**
   - `PERL_HASH_SEED` (including `0`), `PERL_PERTURB_KEYS` and `PERL_INTERNAL_RAND_SEED` (where Perl's rules allow it);
   - `srand(N)`.

## Checks (written first, seen failing)

1. **The seeds come from `random_get`.**
   - A probe builds its own wasmtime-py linker: `define_wasi`, then `random_get` shadowed to return a fixed byte pattern.
   - Two runs of `print rand(), " ", Hash::Util::hash_seed(), " ", join(",", keys %{{map {$_ => 1} "a".."p"}})` print the same line.
   - Today they differ, because the clock seeds them.
2. **Under the real runner, 20 calls give 20 different hash seeds** and 20 different `rand()` values.
3. **The device behaves as decision 2 says.**
   - `read $f, $b, 32` gives 32 bytes, and two reads differ.
   - `-c` is true, and `-t $f` is false.
   - `sysseek` gives "0 but true".
   - A write of 10 bytes returns 10.
   - `open` with `O_DIRECTORY`, or of `urandom/`, fails with ENOTDIR, and `O_CREAT|O_EXCL` fails with EEXIST.
   - `select` reports it readable at once.
   - `close` makes no host call: a counting probe records none.
   - `/dev/random` and `/dev/./urandom` behave the same way.
4. **Nothing else became openable.**
   - `/dev/zero` behaves as today.
   - `<grant>/../dev/urandom` gets today's answer.
   - A FIFO named `urandom` inside a grant is refused with `ENXIO`.
   - The import list is the 32 functions of today plus `random_get`, all `wasi_snapshot_preview1`.
5. **`PERL_HASH_SEED=0`** gives the same key order on every call, as natively.

## Acceptance

1. The checks pass (EXECUTED).
2. The one-liner oracle, the escape tests and the whole of Perl's `t/` suite pass. Any exclusion that existed only because `/dev/urandom` was missing is removed, in its own commit, with its reason.
3. `docs/sandbox.md` says where the guest's randomness comes from, and lists the device's differences from native: the `/dev` listing, and `EINVAL` for the calls decision 2 names.
4. `CHANGELOG.md` names the fix under "Unreleased" as a security fix.
5. A fresh agent's review lands.
