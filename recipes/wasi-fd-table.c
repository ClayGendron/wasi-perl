/* Descriptors the program can duplicate, over WASI's, which it cannot.
 *
 * POSIX lets a process hold many numbers for one open file (dup, dup2, fcntl F_DUPFD), and Perl
 * relies on that to save and restore STDOUT and STDERR, for open(">&"), and in Test::More.
 * WASI preview1 gives one number per open file and no call to make a second. So the guest
 * numbers its own descriptors: a table maps each number the program sees (virtual) to an open
 * file, and each open file holds WASI's number for it (real) and a count of the virtual
 * numbers that refer to it. The real descriptor is closed when the last of them is.
 *
 * Every wasi-libc call that passes a descriptor to the host goes through one of the
 * __wasi_* functions of wasi-libc's bottom half, each defined in one object and called from
 * others. The link passes -Wl,--wrap=<function> for each (recipes/wasi-cc), so every call
 * reaches __wrap_<function> here, which translates the virtual number to the real one and
 * calls __real_<function>, wasi-libc's own. A call that gives back a new descriptor
 * (path_open, sock_accept) records it under the lowest free virtual number, as POSIX
 * requires of open(). fd_close and fd_renumber become operations on the table. dup, dup2,
 * dup3 and fcntl are wrapped the same way, because wasi-libc defines them to fail.
 *
 * The table fills itself on first use: every real descriptor the host opened before the
 * program started (stdio and the preopened folders, numbered from 0 with no gap, as wasi-libc
 * assumes when it looks for preopens) gets the same virtual number. Nothing runs before
 * that, so the order of constructors does not matter. The host sees the same real
 * descriptors as before, and the module imports nothing new. */
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <wasi/api.h>

/* The most virtual numbers at once: the runner's default open-file limit. */
#define FD_LIMIT 1024

struct file {
    __wasi_fd_t real;
    uint32_t refs; /* 0: this entry is free */
};

/* Each open file has at least one virtual number, so there are never more files than numbers.
 * slot[v] is 1 + the index in files of the open file v refers to, or 0 when v is closed. */
static struct file files[FD_LIMIT];
static uint16_t slot[FD_LIMIT];
/* FD_CLOEXEC of each virtual number. Nothing can be executed, so it only has to read back. */
static uint8_t cloexec[FD_LIMIT];
static int started;

__typeof__(__wasi_fd_fdstat_get) __real___wasi_fd_fdstat_get;
__typeof__(__wasi_fd_close) __real___wasi_fd_close;

static void bind(int v, int file, int close_on_exec)
{
    slot[v] = (uint16_t)(file + 1);
    files[file].refs++;
    cloexec[v] = (uint8_t)close_on_exec;
}

static int new_file(__wasi_fd_t real)
{
    for (int i = 0; i < FD_LIMIT; i++) {
        if (files[i].refs == 0) {
            files[i].real = real;
            return i;
        }
    }
    abort(); /* cannot happen: there are fewer open files than numbers in use */
}

/* The descriptors the host opened before the program started, each under its own number.
 * 0, 1 and 2 may be missing; from 3, the first number the host does not know ends the scan. */
static void start(void)
{
    started = 1;
    for (int fd = 0; fd < FD_LIMIT; fd++) {
        __wasi_fdstat_t st;
        if (__real___wasi_fd_fdstat_get((__wasi_fd_t)fd, &st) == __WASI_ERRNO_BADF) {
            if (fd > 2)
                break;
            continue;
        }
        bind(fd, new_file((__wasi_fd_t)fd), fd > 2);
    }
}

static int is_open(int v)
{
    if (!started)
        start();
    return v >= 0 && v < FD_LIMIT && slot[v] != 0;
}

static int lowest_free(int from)
{
    for (int v = from; v < FD_LIMIT; v++) {
        if (slot[v] == 0)
            return v;
    }
    return -1;
}

/* Lets go of a virtual number, and closes the real descriptor if it was the last one. */
static __wasi_errno_t release(int v)
{
    struct file *f = &files[slot[v] - 1];
    slot[v] = 0;
    cloexec[v] = 0;
    if (--f->refs != 0)
        return __WASI_ERRNO_SUCCESS;
    return __real___wasi_fd_close(f->real);
}

/* Replaces the virtual number fd, a parameter of the calling wrapper, by its real one, or
 * returns EBADF from the wrapper. */
#define TO_REAL(fd)                                                                              \
    do {                                                                                         \
        if (!is_open((int)(fd)))                                                                 \
            return __WASI_ERRNO_BADF;                                                            \
        (fd) = files[slot[fd] - 1].real;                                                         \
    } while (0)

/* A wrapper that translates the descriptor in its first argument and passes the call on. */
#define PASS_FD(name, params, args)                                                              \
    __typeof__(__wasi_##name) __real___wasi_##name, __wrap___wasi_##name;                       \
    __wasi_errno_t __wrap___wasi_##name params                                                   \
    {                                                                                            \
        TO_REAL(fd);                                                                             \
        return __real___wasi_##name args;                                                        \
    }

PASS_FD(fd_advise, (__wasi_fd_t fd, __wasi_filesize_t offset, __wasi_filesize_t len,
                    __wasi_advice_t advice), (fd, offset, len, advice))
PASS_FD(fd_allocate, (__wasi_fd_t fd, __wasi_filesize_t offset, __wasi_filesize_t len),
        (fd, offset, len))
PASS_FD(fd_datasync, (__wasi_fd_t fd), (fd))
PASS_FD(fd_fdstat_set_flags, (__wasi_fd_t fd, __wasi_fdflags_t flags), (fd, flags))
PASS_FD(fd_fdstat_set_rights, (__wasi_fd_t fd, __wasi_rights_t base, __wasi_rights_t inheriting),
        (fd, base, inheriting))
PASS_FD(fd_filestat_get, (__wasi_fd_t fd, __wasi_filestat_t *buf), (fd, buf))
PASS_FD(fd_filestat_set_size, (__wasi_fd_t fd, __wasi_filesize_t size), (fd, size))
PASS_FD(fd_filestat_set_times, (__wasi_fd_t fd, __wasi_timestamp_t atim, __wasi_timestamp_t mtim,
                                __wasi_fstflags_t flags), (fd, atim, mtim, flags))
PASS_FD(fd_pread, (__wasi_fd_t fd, const __wasi_iovec_t *iovs, size_t iovs_len,
                   __wasi_filesize_t offset, __wasi_size_t *nread), (fd, iovs, iovs_len, offset, nread))
PASS_FD(fd_prestat_get, (__wasi_fd_t fd, __wasi_prestat_t *buf), (fd, buf))
PASS_FD(fd_prestat_dir_name, (__wasi_fd_t fd, uint8_t *path, __wasi_size_t path_len),
        (fd, path, path_len))
PASS_FD(fd_pwrite, (__wasi_fd_t fd, const __wasi_ciovec_t *iovs, size_t iovs_len,
                    __wasi_filesize_t offset, __wasi_size_t *nwritten),
        (fd, iovs, iovs_len, offset, nwritten))
PASS_FD(fd_read, (__wasi_fd_t fd, const __wasi_iovec_t *iovs, size_t iovs_len, __wasi_size_t *nread),
        (fd, iovs, iovs_len, nread))
PASS_FD(fd_readdir, (__wasi_fd_t fd, uint8_t *buf, __wasi_size_t buf_len, __wasi_dircookie_t cookie,
                     __wasi_size_t *used), (fd, buf, buf_len, cookie, used))
PASS_FD(fd_seek, (__wasi_fd_t fd, __wasi_filedelta_t offset, __wasi_whence_t whence,
                  __wasi_filesize_t *newoffset), (fd, offset, whence, newoffset))
PASS_FD(fd_sync, (__wasi_fd_t fd), (fd))
PASS_FD(fd_tell, (__wasi_fd_t fd, __wasi_filesize_t *offset), (fd, offset))
PASS_FD(fd_write, (__wasi_fd_t fd, const __wasi_ciovec_t *iovs, size_t iovs_len,
                   __wasi_size_t *nwritten), (fd, iovs, iovs_len, nwritten))
PASS_FD(path_create_directory, (__wasi_fd_t fd, const char *path), (fd, path))
PASS_FD(path_filestat_get, (__wasi_fd_t fd, __wasi_lookupflags_t flags, const char *path,
                            __wasi_filestat_t *buf), (fd, flags, path, buf))
PASS_FD(path_filestat_set_times, (__wasi_fd_t fd, __wasi_lookupflags_t flags, const char *path,
                                  __wasi_timestamp_t atim, __wasi_timestamp_t mtim,
                                  __wasi_fstflags_t fst_flags),
        (fd, flags, path, atim, mtim, fst_flags))
PASS_FD(path_readlink, (__wasi_fd_t fd, const char *path, uint8_t *buf, __wasi_size_t buf_len,
                        __wasi_size_t *used), (fd, path, buf, buf_len, used))
PASS_FD(path_remove_directory, (__wasi_fd_t fd, const char *path), (fd, path))
PASS_FD(path_unlink_file, (__wasi_fd_t fd, const char *path), (fd, path))
PASS_FD(sock_recv, (__wasi_fd_t fd, const __wasi_iovec_t *ri_data, size_t ri_data_len,
                    __wasi_riflags_t ri_flags, __wasi_size_t *ro_datalen, __wasi_roflags_t *ro_flags),
        (fd, ri_data, ri_data_len, ri_flags, ro_datalen, ro_flags))
PASS_FD(sock_send, (__wasi_fd_t fd, const __wasi_ciovec_t *si_data, size_t si_data_len,
                    __wasi_siflags_t si_flags, __wasi_size_t *so_datalen),
        (fd, si_data, si_data_len, si_flags, so_datalen))
PASS_FD(sock_shutdown, (__wasi_fd_t fd, __wasi_sdflags_t how), (fd, how))

/* The calls with a descriptor in another place, or two. */
__typeof__(__wasi_fd_fdstat_get) __wrap___wasi_fd_fdstat_get;
__wasi_errno_t __wrap___wasi_fd_fdstat_get(__wasi_fd_t fd, __wasi_fdstat_t *buf)
{
    TO_REAL(fd);
    return __real___wasi_fd_fdstat_get(fd, buf);
}

__typeof__(__wasi_path_link) __real___wasi_path_link, __wrap___wasi_path_link;
__wasi_errno_t __wrap___wasi_path_link(__wasi_fd_t fd, __wasi_lookupflags_t old_flags,
                                       const char *old_path, __wasi_fd_t new_fd,
                                       const char *new_path)
{
    TO_REAL(fd);
    TO_REAL(new_fd);
    return __real___wasi_path_link(fd, old_flags, old_path, new_fd, new_path);
}

__typeof__(__wasi_path_rename) __real___wasi_path_rename, __wrap___wasi_path_rename;
__wasi_errno_t __wrap___wasi_path_rename(__wasi_fd_t fd, const char *old_path, __wasi_fd_t new_fd,
                                         const char *new_path)
{
    TO_REAL(fd);
    TO_REAL(new_fd);
    return __real___wasi_path_rename(fd, old_path, new_fd, new_path);
}

__typeof__(__wasi_path_symlink) __real___wasi_path_symlink, __wrap___wasi_path_symlink;
__wasi_errno_t __wrap___wasi_path_symlink(const char *old_path, __wasi_fd_t fd,
                                          const char *new_path)
{
    TO_REAL(fd);
    return __real___wasi_path_symlink(old_path, fd, new_path);
}

/* poll_oneoff names descriptors inside its subscriptions. A closed number is passed on as a
 * number no host gives out, so the host reports EBADF for that subscription, as it would. */
__typeof__(__wasi_poll_oneoff) __real___wasi_poll_oneoff, __wrap___wasi_poll_oneoff;
__wasi_errno_t __wrap___wasi_poll_oneoff(const __wasi_subscription_t *in, __wasi_event_t *out,
                                         __wasi_size_t nsubscriptions, __wasi_size_t *nevents)
{
    __wasi_subscription_t *copy;
    __wasi_errno_t error;
    int has_fd = 0;
    for (__wasi_size_t i = 0; i < nsubscriptions; i++)
        has_fd |= in[i].u.tag != __WASI_EVENTTYPE_CLOCK;
    if (!has_fd)
        return __real___wasi_poll_oneoff(in, out, nsubscriptions, nevents);
    copy = malloc(nsubscriptions * sizeof *copy);
    if (copy == NULL)
        return __WASI_ERRNO_NOMEM;
    memcpy(copy, in, nsubscriptions * sizeof *copy);
    for (__wasi_size_t i = 0; i < nsubscriptions; i++) {
        /* fd_read and fd_write have the same layout. */
        __wasi_fd_t *fd = &copy[i].u.u.fd_read.file_descriptor;
        if (copy[i].u.tag == __WASI_EVENTTYPE_CLOCK)
            continue;
        *fd = is_open((int)*fd) ? files[slot[*fd] - 1].real : UINT32_MAX;
    }
    error = __real___wasi_poll_oneoff(copy, out, nsubscriptions, nevents);
    free(copy);
    return error;
}

/* The calls that make a new descriptor. A free number is found first, so a full table refuses
 * the call without opening anything. */
__typeof__(__wasi_path_open) __real___wasi_path_open, __wrap___wasi_path_open;
__wasi_errno_t __wrap___wasi_path_open(__wasi_fd_t fd, __wasi_lookupflags_t dirflags,
                                       const char *path, __wasi_oflags_t oflags,
                                       __wasi_rights_t base, __wasi_rights_t inheriting,
                                       __wasi_fdflags_t fdflags, __wasi_fd_t *opened)
{
    __wasi_fd_t real;
    __wasi_errno_t error;
    int v;
    TO_REAL(fd);
    v = lowest_free(0);
    if (v < 0)
        return __WASI_ERRNO_MFILE;
    error = __real___wasi_path_open(fd, dirflags, path, oflags, base, inheriting, fdflags, &real);
    if (error != __WASI_ERRNO_SUCCESS)
        return error;
    bind(v, new_file(real), 0);
    *opened = (__wasi_fd_t)v;
    return __WASI_ERRNO_SUCCESS;
}

__typeof__(__wasi_sock_accept) __real___wasi_sock_accept, __wrap___wasi_sock_accept;
__wasi_errno_t __wrap___wasi_sock_accept(__wasi_fd_t fd, __wasi_fdflags_t flags,
                                         __wasi_fd_t *accepted)
{
    __wasi_fd_t real;
    __wasi_errno_t error;
    int v;
    TO_REAL(fd);
    v = lowest_free(0);
    if (v < 0)
        return __WASI_ERRNO_MFILE;
    error = __real___wasi_sock_accept(fd, flags, &real);
    if (error != __WASI_ERRNO_SUCCESS)
        return error;
    bind(v, new_file(real), 0);
    *accepted = (__wasi_fd_t)v;
    return __WASI_ERRNO_SUCCESS;
}

/* close() reaches here. */
__typeof__(__wasi_fd_close) __wrap___wasi_fd_close;
__wasi_errno_t __wrap___wasi_fd_close(__wasi_fd_t fd)
{
    if (!is_open((int)fd))
        return __WASI_ERRNO_BADF;
    return release((int)fd);
}

/* Moves one number onto another, closing the other first if it is open (wasi-libc's freopen
 * uses this). It is a table operation; the host is told only if a real descriptor closes. */
__typeof__(__wasi_fd_renumber) __wrap___wasi_fd_renumber;
__wasi_errno_t __wrap___wasi_fd_renumber(__wasi_fd_t fd, __wasi_fd_t to)
{
    if (!is_open((int)fd) || to >= FD_LIMIT)
        return __WASI_ERRNO_BADF;
    if (fd == to)
        return __WASI_ERRNO_SUCCESS;
    if (slot[to] != 0)
        (void)release((int)to);
    slot[to] = slot[fd];
    cloexec[to] = cloexec[fd];
    slot[fd] = 0;
    cloexec[fd] = 0;
    return __WASI_ERRNO_SUCCESS;
}

/* dup, dup2, dup3 and fcntl(F_DUPFD, F_DUPFD_CLOEXEC): a new number for the same open file. */
static int dup_from(int fd, int from, int close_on_exec)
{
    int v;
    if (!is_open(fd)) {
        errno = EBADF;
        return -1;
    }
    v = lowest_free(from);
    if (v < 0) {
        errno = EMFILE;
        return -1;
    }
    bind(v, slot[fd] - 1, close_on_exec);
    return v;
}

int __wrap_dup(int fd);
int __wrap_dup(int fd)
{
    return dup_from(fd, 0, 0);
}

/* As POSIX says: a no-op when the numbers are equal, and otherwise newfd is closed first,
 * quietly. */
static int dup_onto(int fd, int newfd, int close_on_exec)
{
    if (!is_open(fd) || newfd < 0 || newfd >= FD_LIMIT) {
        errno = EBADF;
        return -1;
    }
    if (fd == newfd)
        return newfd;
    if (slot[newfd] != 0)
        (void)release(newfd);
    bind(newfd, slot[fd] - 1, close_on_exec);
    return newfd;
}

int __wrap_dup2(int fd, int newfd);
int __wrap_dup2(int fd, int newfd)
{
    return dup_onto(fd, newfd, 0);
}

/* wasi-libc's O_CLOEXEC is 0, so no flag can be asked for, and any other flag is invalid. */
int __wrap_dup3(int fd, int newfd, int flags);
int __wrap_dup3(int fd, int newfd, int flags)
{
    if (fd == newfd || (flags & ~O_CLOEXEC) != 0) {
        errno = EINVAL;
        return -1;
    }
    return dup_onto(fd, newfd, (flags & O_CLOEXEC) != 0);
}

/* The third argument is read only for the commands that take one. wasi-libc's fcntl acts as
 * if FD_CLOEXEC were always set and does not look at the descriptor; here F_GETFD and
 * F_SETFD use the table, so a closed number fails with EBADF and the flag reads back as set.
 * Every other command is wasi-libc's. */
int __real_fcntl(int fd, int cmd, ...);
int __wrap_fcntl(int fd, int cmd, ...);
int __wrap_fcntl(int fd, int cmd, ...)
{
    va_list ap;
    int arg = 0;
    if (cmd == F_DUPFD || cmd == F_DUPFD_CLOEXEC || cmd == F_SETFD || cmd == F_SETFL) {
        va_start(ap, cmd);
        arg = va_arg(ap, int);
        va_end(ap);
    }
    switch (cmd) {
    case F_DUPFD:
    case F_DUPFD_CLOEXEC:
        if (arg < 0 || arg >= FD_LIMIT) {
            errno = EINVAL;
            return -1;
        }
        return dup_from(fd, arg, cmd == F_DUPFD_CLOEXEC);
    case F_GETFD:
    case F_SETFD:
        if (!is_open(fd)) {
            errno = EBADF;
            return -1;
        }
        if (cmd == F_GETFD)
            return cloexec[fd] ? FD_CLOEXEC : 0;
        cloexec[fd] = (arg & FD_CLOEXEC) != 0;
        return 0;
    case F_SETFL:
        return __real_fcntl(fd, cmd, arg);
    default:
        return __real_fcntl(fd, cmd);
    }
}
