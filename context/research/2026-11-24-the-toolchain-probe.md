# The toolchain probe

- **Date:** 2026-11-24. **Box:** 30 minutes. **Where:** this cloud container, Linux x86-64, 4 cores, 15 GiB.

## What was found

- **Perl 5.42.3** is the newest 5.42 release on www.cpan.org (5.42.0 to 5.42.3 exist). Its SHA-256 matches CPAN's published `.sha256.txt`. EXECUTED.
- **wasi-sdk-34** is the newest wasi-sdk (35 and 34.1 return 404). It ships clang 23.1.0 and a `wasm32-wasip1` sysroot with `libsetjmp.a` and the `libwasi-emulated-{signal,mman,getpid,process-clocks}` libraries. EXECUTED.
- wasi-sdk's `SetjmpLongjmp.md` (READ) names the flags: `-mllvm -wasm-enable-sjlj -lsetjmp -mllvm -wasm-use-legacy-eh=false`. Without the first flag, `setjmp.h` stops the build with `#error`. `-fwasm-exceptions` is optional for C.
- A test that `longjmp`s out of ten levels of recursion, built with those flags, runs correctly under the **wasmtime 51.0.0 CLI** and under **wasmtime-py 51.0.0**. EXECUTED.
  - The CLI has exceptions on by default in 51; `-W exceptions=n` refuses the module ("exceptions proposal not enabled").
  - wasmtime-py's switch is `Config.wasm_exceptions`; `False` refuses the module.
  - A build with the legacy exception encoding (`-wasm-use-legacy-eh=true`) fails to load ("legacy_exceptions feature required"), so the standard encoding is the one we use.
- wasmtime-py 51.0.0 matches wasmtime 51.0.0; 51.0.0 is the only 51 release.
- The system perl is 5.38.2, so the native oracle is built from the pinned 5.42.3 tarball.

## The commands

    clang --target=wasm32-wasip1 -O2 -o sjlj.wasm sjlj.c -mllvm -wasm-enable-sjlj -lsetjmp -mllvm -wasm-use-legacy-eh=false
    wasmtime run sjlj.wasm            # "longjmp returned 42 after depth 10 / count=1 ok", exit 0
    wasmtime run -W exceptions=n sjlj.wasm   # refused

Hashes are in `downloads/SHA256SUMS`.
