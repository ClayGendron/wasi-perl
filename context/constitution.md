# Constitution

- **Status:** proposed 2026-11-24, for the owner's agreement with specification 001.
- **Owner:** Clay Gendron

## 1. Mission

wasi-perl exists so that **any program that can host WebAssembly can run real Perl 5 inside a sandbox it fully controls**, above all the one-liners agents write to search and edit files, without a kernel feature, a container or a package install step.

One artifact. One wall. Every platform.

## 2. The goal, stated so it can be checked

Release 0.1.0 is ready when all of these are true:

1. A statically linked `perl.wasm` for `wasm32-wasip1`, Perl 5.42 with the core XS modules compiled in (at least POSIX, List::Util, Scalar::Util, Data::Dumper, Storable, Encode, Digest::MD5, Digest::SHA, MIME::Base64, Time::HiRes, Cwd, File::Spec and JSON::PP) is built from pinned sources, and the `wasi-perl` wheel carries it with the pure-Perl core library.
2. `pip install wasi-perl` gives a working `run()` and `wasi-perl run` on Linux (x86-64 and arm64), macOS (arm64) and Windows (x86-64), from the same bytes.
3. Perl's own `t/` suite passes under the runtime, apart from a listed set of exclusions, each with a reason.
4. A set of agent-style one-liners (`-ne`, `-pi -e`, `-lane`, `-0777`, over granted files) gives the same stdout, stderr, exit code and file contents as native perl 5.42.
5. wasi-python's sandbox escape tests, ported to Perl, pass: reads outside a grant, `../` climbs, links out of a grant, and writes to a read-only grant are all refused.

## 3. What this project is not

- Not a development environment. No CPAN client, no `local::lib`. A release is one pinned Perl with one fixed set of modules.
- Not a general wasm toolchain. It builds one artifact well.
- Not an application. A thin `wasi-perl run` that prints the result as JSON is part of the runner; it adds no power `run()` lacks.

## 4. Hard constraints

1. **Upstream patches are few, recorded and temporary** (AGENTS.md).
2. **Every input is pinned and hashed** in `downloads/SHA256SUMS`.
3. **One target: `wasm32-wasip1`.** setjmp/longjmp through WebAssembly exception handling; no Asyncify; no dynamic loading (`-Uusedl`); no threads.
4. **The guest reaches the host only through WASI preview1.**
5. **Safe by default.** Every limit has a default (memory, time, output). No host folder, no network and nothing writable unless the embedder grants it. The runtime's own files are never in a writable grant.
6. **Processes and signals are refused, clearly.** `fork`, `exec`, `system`, backticks, pipe opens and `%SIG` handlers fail with "not available in the wasi-perl sandbox".
7. **Prior art is studied, never copied.**
8. **Downloads are untrusted.**
9. **Tests count, benchmarks time.**
10. **Claims carry their evidence.**
11. **Licence compliance ships with the artifact.** Perl is under the Artistic License or GPL-1.0-or-later; its notice ships inside the wheel. The project's own code is Apache-2.0.

## 5. How work is done

Study → specify → build → review, through pull requests into `dev` (AGENTS.md). When a choice is not covered here or in a specification: keep the wall whole; prefer a recipe file over a patch; prefer the plain WASI form; prefer the smallest thing that meets the specification; and if it is still open, it is the owner's.

## 6. One line

Build one sealed, portable Perl 5.42 from pinned sources, prove every claim by running it, and make the one-liners agents write work inside it exactly as they do natively.
