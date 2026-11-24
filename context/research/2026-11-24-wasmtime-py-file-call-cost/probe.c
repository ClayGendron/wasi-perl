/* Times N calls of one WASI preview1 file-system function on a file in a
 * preopened folder, with the guest's own clock, and prints nanoseconds per
 * call. The clock call itself is answered from memory and costs well under a
 * microsecond, so it does not blur the number.
 *
 *     probe.wasm <kind> <n> [file]
 *
 * kind: read (fd_read of 1 byte, the file must hold n bytes), eof (fd_read
 * at end of file), fstat (fd_filestat_get), fdstat (fd_fdstat_get on the
 * file), pstat (path_filestat_get), open (path_open and fd_close), clock
 * (clock_time_get, the floor). The file defaults to "f.txt" in the first
 * preopen.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wasi/api.h>

static __wasi_timestamp_t now(void) {
    __wasi_timestamp_t t = 0;
    (void)__wasi_clock_time_get(__WASI_CLOCKID_MONOTONIC, 1, &t);
    return t;
}

static __wasi_fd_t first_preopen(void) {
    for (__wasi_fd_t fd = 3; fd < 64; fd++) {
        __wasi_prestat_t p;
        if (__wasi_fd_prestat_get(fd, &p) == 0) return fd;
    }
    return (__wasi_fd_t)-1;
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: probe <kind> <n> [file]\n");
        return 2;
    }
    const char *kind = argv[1];
    long n = atol(argv[2]);
    const char *name = argc > 3 ? argv[3] : "f.txt";
    __wasi_fd_t dir = first_preopen();
    __wasi_fd_t fd;
    __wasi_errno_t e = __wasi_path_open(dir, 0, name, 0,
                                        __WASI_RIGHTS_FD_READ | __WASI_RIGHTS_FD_SEEK |
                                            __WASI_RIGHTS_FD_FILESTAT_GET,
                                        0, 0, &fd);
    if (e) {
        fprintf(stderr, "open failed: %d\n", e);
        return 1;
    }
    char byte;
    __wasi_iovec_t iov = {(uint8_t *)&byte, 1};
    if (strcmp(kind, "eof") == 0) {
        __wasi_filesize_t pos;
        (void)__wasi_fd_seek(fd, 0, __WASI_WHENCE_END, &pos);
    }
    __wasi_errno_t bad = 0;
    __wasi_timestamp_t t0 = now();
    for (long i = 0; i < n; i++) {
        __wasi_size_t got;
        __wasi_filestat_t st;
        __wasi_fdstat_t fs;
        __wasi_fd_t f2;
        __wasi_timestamp_t t;
        if (strcmp(kind, "read") == 0) {
            e = __wasi_fd_read(fd, &iov, 1, &got);
            if (!e && got != 1) e = 255;
        } else if (strcmp(kind, "eof") == 0) {
            e = __wasi_fd_read(fd, &iov, 1, &got);
            if (!e && got != 0) e = 255;
        } else if (strcmp(kind, "fstat") == 0) {
            e = __wasi_fd_filestat_get(fd, &st);
        } else if (strcmp(kind, "fdstat") == 0) {
            e = __wasi_fd_fdstat_get(fd, &fs);
        } else if (strcmp(kind, "pstat") == 0) {
            e = __wasi_path_filestat_get(dir, 0, name, &st);
        } else if (strcmp(kind, "open") == 0) {
            e = __wasi_path_open(dir, 0, name, 0, __WASI_RIGHTS_FD_READ, 0, 0, &f2);
            if (!e) e = __wasi_fd_close(f2);
        } else if (strcmp(kind, "clock") == 0) {
            e = __wasi_clock_time_get(__WASI_CLOCKID_MONOTONIC, 1, &t);
        } else {
            fprintf(stderr, "unknown kind %s\n", kind);
            return 2;
        }
        if (e) bad = e;
    }
    __wasi_timestamp_t t1 = now();
    if (bad) {
        fprintf(stderr, "call failed: %d\n", bad);
        return 1;
    }
    printf("%s n=%ld ns_per_call=%.1f\n", kind, n, n ? (double)(t1 - t0) / n : 0.0);
    return 0;
}
