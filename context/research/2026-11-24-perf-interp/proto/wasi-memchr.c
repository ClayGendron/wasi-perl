/* memchr for wasm32, in place of wasi-libc's, which compares four bytes a step (musl's word
 * loop on a 32-bit size_t). Perl calls memchr once per line read (sv_gets looks for the
 * separator) and once per candidate in its substring search (fbm_instr), so it is the hottest
 * libc function in line-oriented one-liners.
 *
 * With SIMD (-msimd128) it compares sixteen bytes a step. Each load is of an aligned 16-byte
 * block holding at least one byte of the range, so it never reaches past the end of linear
 * memory (whose size is a multiple of 64 KiB); bytes outside the range are masked off and never
 * returned. Without SIMD it compares eight bytes a step in a 64-bit word. */
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#ifdef __wasm_simd128__
#include <wasm_simd128.h>

void *memchr(const void *s, int c, size_t n)
{
    if (n == 0)
        return NULL;
    uintptr_t at = (uintptr_t)s;
    uintptr_t block = at & ~(uintptr_t)15;
    size_t skip = at - block;
    /* Bytes from the block's start to the range's end; a range that reaches the top of the
     * address space is cut there, where memory ends anyway. */
    size_t left = n > SIZE_MAX - 16 ? SIZE_MAX - 16 : n + skip;
    v128_t want = wasm_i8x16_splat((int8_t)c);
    uint32_t hits = wasm_i8x16_bitmask(wasm_i8x16_eq(wasm_v128_load((const void *)block), want));
    hits &= ~0u << skip;
    for (;;) {
        if (hits) {
            size_t i = (size_t)__builtin_ctz(hits);
            return i < left ? (void *)(block + i) : NULL;
        }
        if (left <= 16)
            return NULL;
        left -= 16;
        block += 16;
        hits = wasm_i8x16_bitmask(wasm_i8x16_eq(wasm_v128_load((const void *)block), want));
    }
}

#else

#define ONES 0x0101010101010101ull
#define HIGHS 0x8080808080808080ull

void *memchr(const void *s, int c, size_t n)
{
    const unsigned char *p = s;
    unsigned char b = (unsigned char)c;
    for (; ((uintptr_t)p & 7) && n; p++, n--) {
        if (*p == b)
            return (void *)p;
    }
    uint64_t want = ONES * b;
    for (; n >= 8; p += 8, n -= 8) {
        uint64_t w;
        memcpy(&w, p, 8);
        w ^= want;
        if ((w - ONES) & ~w & HIGHS)
            break;
    }
    for (; n; p++, n--) {
        if (*p == b)
            return (void *)p;
    }
    return NULL;
}

#endif
