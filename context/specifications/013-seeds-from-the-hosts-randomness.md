# 013 — Seeds from the host's randomness

- **Status:** a draft by the orchestrator, 2026-11-24, for the owner to agree. The owner chose specifications 013 to 016 to come first ("1–4 first", 2026-11-24).
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
2. **What the device is.**
   - **Which names are virtual.** A `path_open` relative to the runner's `/dev` preopen whose path, after `.` components are dropped, is exactly `urandom` or `random` opens a descriptor in the guest's own table. The host never sees it.
   - **Reads.** `fd_read` and `fd_pread` fill the buffers from `__wasi_random_get`, without bound and never short, as native `/dev/urandom` does.
   - **Everything else on the descriptor:**
     - `fd_seek` and `fd_tell` answer `ESPIPE`;
     - `fd_write` answers `EBADF`;
     - an open for writing, truncating or creating answers `EACCES`, as a write open of the read-only `/dev` preopen does;
     - `fd_close`, `dup`, `dup2` and `fcntl` work as for any table descriptor.
   - **Stat.** `fd_fdstat_get`, `fd_filestat_get` and `path_filestat_get` answer a character device, size 0, so `-c` and `-e` are true.
   - **What is not virtual.** Listing `/dev` still shows only `null`: the folder listing is the host's. That is a documented difference from native.
3. **The special-file refusal is unchanged for the host's files.** The virtual device never reaches the host, so it is not a host special file. The `ENXIO` refusal of FIFOs, sockets and devices inside grants keeps every check of spec 010.
4. **The artifact set is unchanged.** `dev/` still holds exactly one empty `null`, and the runner's check of that stays.
5. **Perl's own controls keep working, as natively:**
   - `PERL_HASH_SEED` (including `0`), `PERL_PERTURB_KEYS` and `PERL_INTERNAL_RAND_SEED` (where Perl's rules allow it);
   - `srand(N)`.

## Checks (written first, seen failing)

1. **The seeds come from `random_get`.**
   - A probe builds its own wasmtime-py linker: `define_wasi`, then `random_get` shadowed to return a fixed byte pattern.
   - Two runs of `print rand(), " ", Hash::Util::hash_seed(), " ", join(",", keys %{{map {$_ => 1} "a".."p"}})` print the same line.
   - Today they differ, because the clock seeds them.
2. **Under the real runner, 20 calls give 20 different hash seeds** and 20 different `rand()` values.
3. **The device behaves as section 2 says.**
   - `open my $f, "<", "/dev/urandom"` and `read $f, $b, 32` gives 32 bytes, and two reads differ.
   - `-c "/dev/urandom"` is true.
   - Opening it for writing fails.
   - `sysseek` fails.
   - `/dev/random` works the same way.
4. **Nothing else became openable.**
   - `/dev/zero`, `/dev/../dev/urandom` written through a grant, and a FIFO named `urandom` inside a grant all behave as today.
   - The FIFO is refused with `ENXIO`.
5. **`PERL_HASH_SEED=0`** gives the same key order on every call, as natively.

## Acceptance

1. The checks pass (EXECUTED).
2. The one-liner oracle, the escape tests and the whole of Perl's `t/` suite pass. Any exclusion that existed only because `/dev/urandom` was missing is removed, in its own commit, with its reason.
3. `docs/sandbox.md` says where the guest's randomness comes from.
4. `CHANGELOG.md` names the fix under "Unreleased" as a security fix.
5. A fresh agent's review lands.
