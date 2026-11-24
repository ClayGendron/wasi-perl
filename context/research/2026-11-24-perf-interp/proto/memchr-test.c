/* Checks memchr against a byte loop at every start offset, length and target position. */
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
    return 0;
}
