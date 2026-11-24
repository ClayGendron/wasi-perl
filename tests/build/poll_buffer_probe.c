/* A probe for the descriptor table's poll_oneoff: the host must never write past the caller's
 * event buffer.
 *
 * tests/build/test_poll_buffer.py compiles the table with __real___wasi_poll_oneoff renamed to
 * fake_host_poll, below, and links it with this program. The fake host answers every
 * subscription it is sent as ready, the most events a host may write. The program asks about a
 * regular file (answered by the table itself) and STDOUT (sent to the host) with no timeout,
 * which once made the table send one subscription more than the caller's buffer had room for.
 * A canary after the buffer must come back whole. */
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <wasi/api.h>

__wasi_errno_t fake_host_poll(const __wasi_subscription_t *in, __wasi_event_t *out,
                              __wasi_size_t n, __wasi_size_t *nevents);
__wasi_errno_t fake_host_poll(const __wasi_subscription_t *in, __wasi_event_t *out,
                              __wasi_size_t n, __wasi_size_t *nevents)
{
    for (__wasi_size_t i = 0; i < n; i++) {
        memset(&out[i], 0, sizeof out[i]);
        out[i].userdata = in[i].userdata;
        out[i].type = in[i].u.tag;
    }
    *nevents = n;
    return __WASI_ERRNO_SUCCESS;
}

static struct {
    __wasi_event_t events[2];
    unsigned char canary[64];
} buf;

int main(int argc, char **argv)
{
    __wasi_subscription_t subs[2];
    __wasi_size_t got = 0;
    __wasi_errno_t error;
    int fd = open(argc > 1 ? argv[1] : "/g/file", O_RDONLY);
    if (fd < 0) {
        perror("open");
        return 2;
    }
    memset(subs, 0, sizeof subs);
    subs[0].userdata = 10;
    subs[0].u.tag = __WASI_EVENTTYPE_FD_READ;
    subs[0].u.u.fd_read.file_descriptor = (__wasi_fd_t)fd;
    subs[1].userdata = 11;
    subs[1].u.tag = __WASI_EVENTTYPE_FD_WRITE;
    subs[1].u.u.fd_write.file_descriptor = 1;
    memset(buf.canary, 0xa5, sizeof buf.canary);
    error = __wasi_poll_oneoff(subs, buf.events, 2, &got);
    for (size_t i = 0; i < sizeof buf.canary; i++) {
        if (buf.canary[i] != 0xa5) {
            printf("canary broken at %zu\n", i);
            return 1;
        }
    }
    printf("error %d, %u events:", (int)error, (unsigned)got);
    for (__wasi_size_t i = 0; i < got; i++)
        printf(" %llu", (unsigned long long)buf.events[i].userdata);
    printf("\ncanary whole\n");
    return 0;
}
