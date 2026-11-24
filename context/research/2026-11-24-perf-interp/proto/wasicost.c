/* The host's cost of one WASI preview1 call of each kind, timed inside the guest with the
 * monotonic clock around N calls. Built with plain wasi-sdk (no descriptor table), run by
 * wasicost.py under wasmtime-py with the runner's preopens: fd 3 is the library, 4 /dev,
 * 5 /data. Prints one "name ns_per_call" line per kind. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <wasi/api.h>

static double now_ns(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1e9 + t.tv_nsec;
}

#define TIME(name, n, body)                                                                     \
    do {                                                                                       \
        double t0 = now_ns();                                                                  \
        for (int i = 0; i < (n); i++) {                                                         \
            body;                                                                              \
        }                                                                                      \
        printf("%s %.0f\n", name, (now_ns() - t0) / (n));                                      \
    } while (0)

int main(int argc, char **argv)
{
    int n = argc > 1 ? atoi(argv[1]) : 5000;
    const char *file = "t.log", *missing = "no-such.pm";
    __wasi_fd_t f;
    __wasi_rights_t all = __WASI_RIGHTS_FD_READ | __WASI_RIGHTS_FD_SEEK | __WASI_RIGHTS_FD_TELL |
                          __WASI_RIGHTS_FD_FILESTAT_GET | __WASI_RIGHTS_FD_FDSTAT_SET_FLAGS;
    if (__wasi_path_open(5, 0, file, 0, all, all, 0, &f) != 0)
        return 1;
    static char buf[262144];
    __wasi_iovec_t iov1 = {(uint8_t *)buf, 1}, iov8 = {(uint8_t *)buf, 8192},
                   iov64 = {(uint8_t *)buf, 65536}, iov256 = {(uint8_t *)buf, 262144};
    __wasi_ciovec_t ov = {(const uint8_t *)buf, 1};
    __wasi_size_t got;
    __wasi_filesize_t pos;
    __wasi_fdstat_t fst;
    __wasi_filestat_t st;
    __wasi_prestat_t pre;
    TIME("fd_prestat_get", n, __wasi_fd_prestat_get(3, &pre));
    TIME("fd_fdstat_get(dir)", n, __wasi_fd_fdstat_get(5, &fst));
    TIME("fd_fdstat_get(file)", n, __wasi_fd_fdstat_get(f, &fst));
    TIME("fd_fdstat_get(stdout)", n, __wasi_fd_fdstat_get(1, &fst));
    TIME("fd_tell", n, __wasi_fd_tell(f, &pos));
    TIME("fd_seek", n, __wasi_fd_seek(f, 0, __WASI_WHENCE_SET, &pos));
    TIME("fd_filestat_get", n, __wasi_fd_filestat_get(f, &st));
    TIME("path_filestat_get(hit)", n, __wasi_path_filestat_get(5, 0, file, &st));
    TIME("path_filestat_get(miss)", n, __wasi_path_filestat_get(3, 0, missing, &st));
    TIME("fd_read(1 B)", n, __wasi_fd_read(f, &iov1, 1, &got));
    TIME("fd_read(8 KiB)", n / 4, (__wasi_fd_seek(f, 0, __WASI_WHENCE_SET, &pos), __wasi_fd_read(f, &iov8, 1, &got)));
    TIME("fd_read(64 KiB)", n / 4, (__wasi_fd_seek(f, 0, __WASI_WHENCE_SET, &pos), __wasi_fd_read(f, &iov64, 1, &got)));
    TIME("fd_read(256 KiB)", n / 8, (__wasi_fd_seek(f, 0, __WASI_WHENCE_SET, &pos), __wasi_fd_read(f, &iov256, 1, &got)));
    TIME("fd_read(at EOF)", n, (__wasi_fd_seek(f, 0, __WASI_WHENCE_END, &pos), __wasi_fd_read(f, &iov1, 1, &got)));
    TIME("open+close", n / 4, {
        __wasi_fd_t g;
        __wasi_path_open(5, 0, file, 0, all, all, 0, &g);
        __wasi_fd_close(g);
    });
    TIME("fd_write(stdout, 1 B)", n, __wasi_fd_write(1, &ov, 1, &got));
    TIME("clock_time_get", n, now_ns());
    return 0;
}
