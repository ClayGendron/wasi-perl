/* A probe of the library index at the C level: one stat or open of a path under the library,
 * through wasi-libc and the descriptor table, and how many times the table asked the host.
 *
 * The table is compiled from the recipe with its two calls to the host's path_filestat_get and
 * path_open renamed to the counting functions here, which count and pass the call on to
 * wasi-libc's own (the link's --wrap makes __real___wasi_<name> that one). The shipped module is
 * not built this way.
 *
 *   lib_index_probe stat <path>          stat("/usr/local/lib/perl5/<path>")
 *   lib_index_probe read <path>          open(..., O_RDONLY)
 *   lib_index_probe create <path>        open(..., O_WRONLY | O_CREAT)
 *
 * In <path>, "%" and two hex digits stand for that byte, so a path can hold one the command line
 * would not pass. It prints one line: the answer (ok, ENOENT, ENOTDIR, or errno's number), then
 * the host's path_filestat_get and path_open calls. */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <wasi/api.h>

__typeof__(__wasi_path_filestat_get) __real___wasi_path_filestat_get;
__typeof__(__wasi_path_open) __real___wasi_path_open;

static int stats, opens;

__wasi_errno_t counted_stat(__wasi_fd_t fd, __wasi_lookupflags_t flags, const char *path,
                            __wasi_filestat_t *buf)
{
    stats++;
    return __real___wasi_path_filestat_get(fd, flags, path, buf);
}

__wasi_errno_t counted_open(__wasi_fd_t fd, __wasi_lookupflags_t dirflags, const char *path,
                            __wasi_oflags_t oflags, __wasi_rights_t base,
                            __wasi_rights_t inheriting, __wasi_fdflags_t fdflags,
                            __wasi_fd_t *opened)
{
    opens++;
    return __real___wasi_path_open(fd, dirflags, path, oflags, base, inheriting, fdflags, opened);
}

static const char *answer(int failed)
{
    static char number[16];
    if (!failed)
        return "ok";
    if (errno == ENOENT)
        return "ENOENT";
    if (errno == ENOTDIR)
        return "ENOTDIR";
    snprintf(number, sizeof number, "%d", errno);
    return number;
}

int main(int argc, char **argv)
{
    char path[512];
    struct stat st;
    int failed, fd = -1;
    if (argc != 3)
        return 2;
    size_t n = (size_t)snprintf(path, sizeof path, "/usr/local/lib/perl5/");
    for (const char *p = argv[2]; *p != '\0' && n + 1 < sizeof path; p++) {
        char hex[3] = {0};
        if (p[0] == '%' && p[1] != '\0' && p[2] != '\0') {
            hex[0] = p[1];
            hex[1] = p[2];
            path[n++] = (char)strtol(hex, NULL, 16);
            p += 2;
        } else {
            path[n++] = *p;
        }
    }
    path[n] = '\0';
    if (strcmp(argv[1], "stat") == 0) {
        failed = stat(path, &st) != 0;
    } else if (strcmp(argv[1], "read") == 0) {
        failed = (fd = open(path, O_RDONLY)) < 0;
    } else if (strcmp(argv[1], "create") == 0) {
        failed = (fd = open(path, O_WRONLY | O_CREAT, 0644)) < 0;
    } else {
        return 2;
    }
    printf("%s, %d stats, %d opens\n", answer(failed), stats, opens);
    if (fd >= 0)
        close(fd);
    return 0;
}
