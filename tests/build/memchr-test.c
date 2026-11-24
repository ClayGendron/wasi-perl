/* Checks memchr against a byte loop at every start offset, length and target position, and
 * over ranges that end at the last byte of linear memory, where an aligned load that went one
 * block too far would trap. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static void *ref(const void *s, int c, size_t n)
{
    const unsigned char *p = s;
    for (size_t i = 0; i < n; i++)
        if (p[i] == (unsigned char)c)
            return (void *)(p + i);
    return NULL;
}

int main(void)
{
    static unsigned char buf[256] __attribute__((aligned(16)));
    long checks = 0;
    for (int off = 0; off < 32; off++)
        for (size_t n = 0; n < 80; n++)
            for (int pos = -1; pos < (int)n + 2; pos++) {
                memset(buf, 'a', sizeof buf);
                if (pos >= 0 && off + pos < (int)sizeof buf)
                    buf[off + pos] = '\n';
                buf[off + n + 3] = '\n'; /* a hit past the range must not be returned */
                if (off > 0)
                    buf[off - 1] = '\n'; /* nor one before it */
                volatile size_t vn = n;
                if (memchr(buf + off, '\n', vn) != ref(buf + off, '\n', n) ||
                    memchr(buf + off, 'a' + 256, vn) != ref(buf + off, 'a', n)) {
                    printf("FAIL off=%d n=%zu pos=%d\n", off, n, pos);
                    return 1;
                }
                checks++;
            }
    printf("ok %ld\n", checks);

    /* A page of our own at the top of memory, so the ranges below end where memory ends. */
    if (__builtin_wasm_memory_grow(0, 1) == (size_t)-1) {
        printf("FAIL memory.grow\n");
        return 1;
    }
    uintptr_t end = (uintptr_t)__builtin_wasm_memory_size(0) * 65536;
    unsigned char *top = (unsigned char *)(end - 4096);
    long at_end = 0;
    memset(top, 'a', 4096);
    for (size_t n = 0; n <= 80; n++) {
        unsigned char *s = (unsigned char *)(end - n);
        volatile size_t vn = n;
        int ok = memchr(s, '\n', vn) == ref(s, '\n', n);
        at_end++;
        for (size_t t = 0; t < n; t++) {
            volatile size_t vt = t;
            s[t] = '\n';
            ok &= memchr(s, '\n', vn) == ref(s, '\n', n);
            ok &= memchr(s, '\n' + 256, vn) == ref(s, '\n', n);
            ok &= memchr(s, '\n', vt) == NULL; /* the range stops just before the target */
            s[t] = 'a';
            at_end += 3;
        }
        if (n > 0) {
            s[-1] = '\n'; /* a hit just before the range must not be returned */
            ok &= memchr(s, '\n', vn) == NULL;
            s[-1] = 'a';
            at_end++;
        }
        if (!ok) {
            printf("FAIL at the end of memory n=%zu\n", n);
            return 1;
        }
    }
    /* The last byte of memory as the only match, from up to a page away. */
    top[4095] = '\n';
    for (size_t k = 1; k <= 4096; k++, at_end++) {
        volatile size_t vk = k;
        if (memchr(top + 4096 - k, '\n', vk) != top + 4095) {
            printf("FAIL the last byte from %zu away\n", k);
            return 1;
        }
    }
    /* An empty range loads nothing, so a pointer at or past the end of memory is safe. */
    volatile size_t zero = 0;
    if (memchr((void *)end, '\n', zero) != NULL || memchr((void *)(end + 65536), 'a', zero) != NULL) {
        printf("FAIL an empty range past the end of memory\n");
        return 1;
    }
    at_end += 2;
    printf("end of memory ok %ld\n", at_end);
    return 0;
}
