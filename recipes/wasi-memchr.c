/* memchr for wasm32, in place of wasi-libc's, which is musl's word loop and so compares four
 * bytes a step on a 32-bit size_t. Perl calls memchr once per line it reads (sv_gets looks for
 * the separator in its buffer) and once per candidate in a substring search (fbm_instr), so in
 * line-oriented one-liners it is the hottest libc function.
 *
 * This file alone is compiled for WebAssembly SIMD128, and compares sixteen bytes a step. Every
 * load is of an aligned 16-byte block that holds at least one byte of the range. Linear memory
 * is a whole number of 64 KiB pages, so such a block lies wholly inside it whenever that byte
 * does: the load can read bytes before or after the range, never past the end of memory. The
 * bytes outside the range are masked off and never returned.
 *
 * It is linked as an object ahead of wasi-libc, so the archive's memchr is never pulled in, and
 * wasi-libc's own callers get this one too. */
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#ifndef __wasm_simd128__
#error "wasi-memchr.c is built with -msimd128"
#endif
#include <wasm_simd128.h>

static uint32_t matches(uintptr_t block, v128_t want)
{
    return wasm_i8x16_bitmask(wasm_i8x16_eq(wasm_v128_load((const void *)block), want));
}

void *memchr(const void *s, int c, size_t n)
{
    if (n == 0)
        return NULL;
    uintptr_t block = (uintptr_t)s & ~(uintptr_t)15;
    size_t skip = (uintptr_t)s - block;
    /* The bytes from the block's start to the range's end. A range that claims to run past the
     * top of the address space is cut short there; memory ends before it anyway. */
    size_t left = n > SIZE_MAX - 16 ? SIZE_MAX - 16 : n + skip;
    v128_t want = wasm_i8x16_splat((int8_t)(unsigned char)c);
    uint32_t hits = matches(block, want) & (~0u << skip);
    for (;;) {
        if (hits) {
            size_t i = (size_t)__builtin_ctz(hits);
            return i < left ? (void *)(block + i) : NULL;
        }
        if (left <= 16)
            return NULL;
        left -= 16;
        block += 16;
        hits = matches(block, want);
    }
}
