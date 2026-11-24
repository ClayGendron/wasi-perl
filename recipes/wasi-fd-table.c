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
 * descriptors as before, and the module imports nothing new.
 *
 * Three more answers are the guest's own, each where the host's would differ from a POSIX
 * system's: an open of a special file is refused before the host could wait in it
 * (path_open), select reads Perl's bit vectors (select), and a rename over a file the
 * program holds open succeeds where the host refuses it (path_rename). */
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <unistd.h>
#include <wasi/api.h>

/* The most virtual numbers at once, which is also the most host descriptors one call can
 * hold: 256, macOS's default open-file limit, so that a few calls at once cannot exhaust the
 * embedding process's descriptors. The host's own descriptors, the grants among them, count
 * against it. */
#define FD_LIMIT 256

/* The real number of an open file whose host descriptor is gone: a reopen after a rename over
 * it failed. Every call through it answers EBADF, as the host would; its close succeeds. */
#define GONE ((__wasi_fd_t)-1)

struct file {
    __wasi_fd_t real;
    uint32_t refs; /* 0: this entry is free */
    /* How path_open opened it, to open it again after a rename over it (path_rename). */
    __wasi_oflags_t oflags;
    __wasi_fdflags_t fdflags;
    __wasi_rights_t base, inheriting;
    uint8_t reopenable;
};

/* Each open file has at least one virtual number, so there are never more files than numbers.
 * slot[v] is 1 + the index in files of the open file v refers to, or 0 when v is closed. */
static struct file files[FD_LIMIT];
static uint16_t slot[FD_LIMIT];
/* FD_CLOEXEC of each virtual number. Nothing can be executed, so it only has to read back. */
static uint8_t cloexec[FD_LIMIT];
/* The grants' numbers. wasi-libc finds each preopened folder once, keeps its number, and
 * resolves every path under that folder through it for the rest of the run. So a grant's
 * number always names its grant: closing it succeeds and leaves the grant open, and nothing
 * may be put on it (dup2, dup3, fd_renumber answer EBADF). Since it is never freed, no open,
 * dup or F_DUPFD is given it, and a path under one grant can never land in another. */
static uint8_t grant[FD_LIMIT];
static int started;

__typeof__(__wasi_fd_fdstat_get) __real___wasi_fd_fdstat_get;
__typeof__(__wasi_fd_prestat_get) __real___wasi_fd_prestat_get;
__typeof__(__wasi_fd_close) __real___wasi_fd_close;
__typeof__(__wasi_path_open) __real___wasi_path_open;
__typeof__(__wasi_path_rename) __real___wasi_path_rename;
static __wasi_errno_t look(__wasi_fd_t realdir, __wasi_lookupflags_t dirflags, const char *path,
                           __wasi_oflags_t oflags);

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
            files[i].reopenable = 0;
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
        __wasi_prestat_t pre;
        bind(fd, new_file((__wasi_fd_t)fd), fd > 2);
        grant[fd] = fd > 2 && __real___wasi_fd_prestat_get((__wasi_fd_t)fd, &pre) == 0 &&
                    pre.tag == __WASI_PREOPENTYPE_DIR;
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
    if (--f->refs != 0 || f->real == GONE)
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

/* Setting a file's times through a link it follows makes wasmtime open the file to call
 * futimens on it, for writing and then for reading, and a FIFO's open waits for the other
 * end, past every limit (the reason open looks first). Without following, wasmtime sets the
 * times on the name through its parent folder (utimensat with AT_SYMLINK_NOFOLLOW) and opens
 * nothing. So a following call is looked at first. A folder or a regular file goes on as it
 * is. A special file that is the name itself, not a link, goes on without following, which
 * sets the same times without opening it, as natively. A link to a special file is refused
 * with ENXIO, as an open of it is. Every other host call that takes a path was checked in
 * wasmtime 51 to open nothing at the name: stat and the lookups use O_PATH or fstatat;
 * readlink, link, rename, unlink, mkdir, rmdir and symlink act through the parent folder; a
 * component in the middle of a path is opened as a folder, which a FIFO refuses at once. */
__typeof__(__wasi_path_filestat_set_times) __real___wasi_path_filestat_set_times,
    __wrap___wasi_path_filestat_set_times;
__wasi_errno_t __wrap___wasi_path_filestat_set_times(__wasi_fd_t fd, __wasi_lookupflags_t flags,
                                                     const char *path, __wasi_timestamp_t atim,
                                                     __wasi_timestamp_t mtim,
                                                     __wasi_fstflags_t fst_flags)
{
    __wasi_filestat_t target, name;
    TO_REAL(fd);
    if ((flags & __WASI_LOOKUPFLAGS_SYMLINK_FOLLOW) &&
        __real___wasi_path_filestat_get(fd, flags, path, &target) == __WASI_ERRNO_SUCCESS &&
        target.filetype != __WASI_FILETYPE_DIRECTORY &&
        target.filetype != __WASI_FILETYPE_REGULAR_FILE) {
        if (__real___wasi_path_filestat_get(fd, 0, path, &name) != __WASI_ERRNO_SUCCESS ||
            name.filetype == __WASI_FILETYPE_SYMBOLIC_LINK)
            return __WASI_ERRNO_NXIO;
        flags &= ~__WASI_LOOKUPFLAGS_SYMLINK_FOLLOW;
    }
    return __real___wasi_path_filestat_set_times(fd, flags, path, atim, mtim, fst_flags);
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

/* Perl's in-place edit (-i) renames its work file over the file it is reading, which ARGV
 * still holds open. Linux and macOS allow that. Windows refuses to replace a file any process
 * holds open, and wasmtime reports its ERROR_ACCESS_DENIED and ERROR_SHARING_VIOLATION as
 * EACCES (EPERM for a refusal it cannot name). So when a rename fails with either, and the
 * program holds the destination open, the guest closes the real descriptors on it, renames
 * once more, and opens the destination again in each one's place, as it was opened (without
 * creating or truncating), at the same offset or the new file's end if that is shorter. The
 * virtual numbers do not change. Where the first rename succeeds, as on Linux and macOS,
 * nothing here runs. The one difference from POSIX on Windows: after such a rename, a read
 * through the old handle reads the new file, not the old one. If the retry fails, the old
 * file is opened again where it can be and the first error is returned. */
#ifdef WASI_PERL_TEST_HELD_RENAME
/* A test build only, never the shipped module: the host refuses, as Windows does, a rename
 * over a file the program holds open (tests/build/test_rename_over_open.py). */
static int holds(__wasi_fd_t realdir, const char *path);
static __wasi_errno_t host_rename(__wasi_fd_t fd, const char *old_path, __wasi_fd_t new_fd,
                                  const char *new_path)
{
    if (holds(new_fd, new_path))
        return __WASI_ERRNO_ACCES;
    return __real___wasi_path_rename(fd, old_path, new_fd, new_path);
}
#else
#define host_rename __real___wasi_path_rename
#endif

/* The open files the program holds on the regular file at this name, by device and inode. */
static int held[FD_LIMIT];
static __wasi_filesize_t held_at[FD_LIMIT];

static int find_held(__wasi_fd_t realdir, const char *path)
{
    __wasi_filestat_t target, st;
    int n = 0;
    if (__real___wasi_path_filestat_get(realdir, 0, path, &target) != __WASI_ERRNO_SUCCESS ||
        target.filetype != __WASI_FILETYPE_REGULAR_FILE)
        return 0;
    for (int i = 0; i < FD_LIMIT; i++) {
        if (files[i].refs == 0 || !files[i].reopenable)
            continue;
        if (__real___wasi_fd_filestat_get(files[i].real, &st) == __WASI_ERRNO_SUCCESS &&
            st.filetype == __WASI_FILETYPE_REGULAR_FILE && st.dev == target.dev &&
            st.ino == target.ino)
            held[n++] = i;
    }
    return n;
}

#ifdef WASI_PERL_TEST_HELD_RENAME
static int holds(__wasi_fd_t realdir, const char *path)
{
    return find_held(realdir, path) > 0;
}
#endif

__typeof__(__wasi_path_rename) __real___wasi_path_rename, __wrap___wasi_path_rename;
__wasi_errno_t __wrap___wasi_path_rename(__wasi_fd_t fd, const char *old_path, __wasi_fd_t new_fd,
                                         const char *new_path)
{
    __wasi_errno_t error, retry;
    int n;
    TO_REAL(fd);
    TO_REAL(new_fd);
    error = host_rename(fd, old_path, new_fd, new_path);
    if (error != __WASI_ERRNO_ACCES && error != __WASI_ERRNO_PERM)
        return error;
    n = find_held(new_fd, new_path);
    if (n == 0)
        return error;
    /* Each held file is opened again at the destination, so first make sure it can be: with
     * the flags it has now (fcntl may have changed them since the open) and rights no wider
     * than it has. A file that cannot be opened again is left alone, and so is the rename. */
    for (int k = 0; k < n; k++) {
        struct file *f = &files[held[k]];
        __wasi_fdstat_t now;
        __wasi_fd_t probe;
        if (__real___wasi_fd_fdstat_get(f->real, &now) != __WASI_ERRNO_SUCCESS)
            return error;
        f->fdflags = now.fs_flags;
        f->base &= now.fs_rights_base;
        f->inheriting &= now.fs_rights_inheriting;
        __wasi_oflags_t oflags =
            f->oflags & ~(__WASI_OFLAGS_CREAT | __WASI_OFLAGS_EXCL | __WASI_OFLAGS_TRUNC);
        if (__real___wasi_path_open(new_fd, 0, new_path, oflags, f->base, f->inheriting,
                                    f->fdflags, &probe) != __WASI_ERRNO_SUCCESS)
            return error;
        (void)__real___wasi_fd_close(probe);
    }
    for (int k = 0; k < n; k++) {
        struct file *f = &files[held[k]];
        if (__real___wasi_fd_tell(f->real, &held_at[k]) != __WASI_ERRNO_SUCCESS)
            held_at[k] = 0;
        (void)__real___wasi_fd_close(f->real);
        f->real = GONE;
    }
    retry = host_rename(fd, old_path, new_fd, new_path);
    /* Between the rename and the reopen another process on the host could put something else
     * at the name: the same race the look before an open leaves. So the reopen is looked at
     * first, as every open is, and what it opened must be a regular file; anything else is
     * closed again and the number answers EBADF. */
    for (int k = 0; k < n; k++) {
        struct file *f = &files[held[k]];
        __wasi_filestat_t st;
        __wasi_filesize_t pos;
        __wasi_fd_t real;
        __wasi_oflags_t oflags =
            f->oflags & ~(__WASI_OFLAGS_CREAT | __WASI_OFLAGS_EXCL | __WASI_OFLAGS_TRUNC);
        if (look(new_fd, 0, new_path, oflags) != __WASI_ERRNO_SUCCESS ||
            __real___wasi_path_open(new_fd, 0, new_path, oflags, f->base, f->inheriting,
                                    f->fdflags, &real) != __WASI_ERRNO_SUCCESS)
            continue;
        if (__real___wasi_fd_filestat_get(real, &st) != __WASI_ERRNO_SUCCESS ||
            st.filetype != __WASI_FILETYPE_REGULAR_FILE) {
            (void)__real___wasi_fd_close(real);
            continue;
        }
        f->real = real;
        pos = held_at[k] > st.size ? st.size : held_at[k];
        (void)__real___wasi_fd_seek(real, (__wasi_filedelta_t)pos, __WASI_WHENCE_SET, &pos);
    }
    return retry == __WASI_ERRNO_SUCCESS ? retry : error;
}

__typeof__(__wasi_path_symlink) __real___wasi_path_symlink, __wrap___wasi_path_symlink;
__wasi_errno_t __wrap___wasi_path_symlink(const char *old_path, __wasi_fd_t fd,
                                          const char *new_path)
{
    TO_REAL(fd);
    return __real___wasi_path_symlink(old_path, fd, new_path);
}

/* A host wait cannot be stopped: wasmtime's poll_oneoff waits in the host, where the runner's
 * epoch deadline and fuel do not reach, so `sleep 2**31` would hold the caller for good. So no
 * clock wait goes to the host longer than SLICE_NS. While the earliest clock is further away,
 * the host is asked about the descriptors and one slice of the monotonic clock, and the loop
 * runs again in WebAssembly, where the deadline and fuel stop it. Each clock's own time is
 * kept, absolute or relative, on its own clock. The last wait, at most one slice, is the
 * subscriptions as given with each clock made relative to what remains, so the events returned
 * are the host's for exactly those subscriptions; a slice's own event never is.
 *
 * The host writes into a buffer of this function's own, one event per subscription it is
 * sent, and at most `cap` events are copied to `out`, so the host can never write past the
 * caller's buffer whatever is added here. Events of the clock this file adds (userdata
 * OWN_CLOCK) are dropped. */
#define SLICE_NS 10000000ULL
#define OWN_CLOCK UINT64_MAX

__typeof__(__wasi_poll_oneoff) __real___wasi_poll_oneoff;

static __wasi_errno_t sliced_poll(const __wasi_subscription_t *subs, __wasi_size_t n,
                                  __wasi_event_t *out, __wasi_size_t cap,
                                  __wasi_size_t *nevents)
{
    __wasi_timestamp_t *due = malloc(n * sizeof *due);
    __wasi_subscription_t *work = malloc((n + 1) * sizeof *work);
    __wasi_event_t *ev = malloc((n + 1) * sizeof *ev);
    __wasi_errno_t error = __WASI_ERRNO_SUCCESS;
    __wasi_size_t got = 0, kept = 0;
    int clocks = 0, sliced = 1;
    if (due == NULL || work == NULL || ev == NULL) {
        error = __WASI_ERRNO_NOMEM;
        goto done;
    }
    for (__wasi_size_t i = 0; i < n; i++) {
        const __wasi_subscription_clock_t *c = &subs[i].u.u.clock;
        __wasi_timestamp_t now;
        if (subs[i].u.tag != __WASI_EVENTTYPE_CLOCK)
            continue;
        clocks++;
        if (__wasi_clock_time_get(c->id, 1, &now) != __WASI_ERRNO_SUCCESS) {
            sliced = 0; /* a clock the host cannot read: the host's own answer for the call */
            continue;
        }
        if (c->flags & __WASI_SUBCLOCKFLAGS_SUBSCRIPTION_CLOCK_ABSTIME)
            due[i] = c->timeout;
        else
            due[i] = c->timeout > UINT64_MAX - now ? UINT64_MAX : now + c->timeout;
    }
    if (clocks == 0 || !sliced) {
        memcpy(work, subs, n * sizeof *work);
        error = __real___wasi_poll_oneoff(work, ev, n, &got);
        goto done;
    }
    for (;;) {
        __wasi_timestamp_t soonest = UINT64_MAX;
        __wasi_size_t m = 0;
        for (__wasi_size_t i = 0; i < n; i++) {
            __wasi_timestamp_t now = 0, left;
            work[i] = subs[i];
            if (subs[i].u.tag != __WASI_EVENTTYPE_CLOCK)
                continue;
            (void)__wasi_clock_time_get(subs[i].u.u.clock.id, 1, &now);
            left = due[i] > now ? due[i] - now : 0;
            work[i].u.u.clock.timeout = left;
            work[i].u.u.clock.flags &= ~__WASI_SUBCLOCKFLAGS_SUBSCRIPTION_CLOCK_ABSTIME;
            if (left < soonest)
                soonest = left;
        }
        if (soonest <= SLICE_NS) {
            error = __real___wasi_poll_oneoff(work, ev, n, &got);
            break;
        }
        for (__wasi_size_t i = 0; i < n; i++) {
            if (subs[i].u.tag != __WASI_EVENTTYPE_CLOCK)
                work[m++] = subs[i];
        }
        memset(&work[m], 0, sizeof work[m]);
        work[m].userdata = OWN_CLOCK;
        work[m].u.tag = __WASI_EVENTTYPE_CLOCK;
        work[m].u.u.clock.id = __WASI_CLOCKID_MONOTONIC;
        work[m].u.u.clock.timeout = SLICE_NS;
        m++;
        error = __real___wasi_poll_oneoff(work, ev, m, &got);
        if (error != __WASI_ERRNO_SUCCESS)
            break;
        /* The slice is the only clock in this call, so any clock event is its own. */
        for (__wasi_size_t k = 0; k < got && k < m; k++) {
            if (ev[k].type != __WASI_EVENTTYPE_CLOCK)
                ev[kept++] = ev[k];
        }
        got = kept;
        kept = 0;
        if (got > 0)
            break;
    }
done:
    if (error == __WASI_ERRNO_SUCCESS) {
        for (__wasi_size_t k = 0; k < got && k < n + 1 && kept < cap; k++) {
            if (ev[k].userdata == OWN_CLOCK && ev[k].type == __WASI_EVENTTYPE_CLOCK)
                continue;
            out[kept++] = ev[k];
        }
        *nevents = kept;
    }
    free(due);
    free(work);
    free(ev);
    return error;
}

/* poll_oneoff names descriptors inside its subscriptions. Two kinds are answered here and not
 * sent to the host. A subscription on a closed number gets its own event, EBADF with its
 * userdata, which wasi-libc's poll turns into POLLNVAL for that entry alone, as natively; the
 * host would fail the whole call. A regular file is always ready to read and to write, as
 * POSIX says of select and poll; the host would report it only once a read it starts in the
 * background is done, which a zero timeout can outrun. A third kind is not sent at all: a
 * read on a descriptor without the right to read, or a write on one without the right to
 * write (a copy of STDOUT asked whether it is readable). The host fails the whole call for
 * those; natively that entry is simply never ready. The host's events follow these in `out`. */
__typeof__(__wasi_poll_oneoff) __real___wasi_poll_oneoff, __wrap___wasi_poll_oneoff;
__wasi_errno_t __wrap___wasi_poll_oneoff(const __wasi_subscription_t *in, __wasi_event_t *out,
                                         __wasi_size_t nsubscriptions, __wasi_size_t *nevents)
{
    __wasi_subscription_t *copy;
    __wasi_errno_t error = __WASI_ERRNO_SUCCESS;
    __wasi_size_t nlocal = 0, nsent = 0, nhost = 0;
    int has_fd = 0;
    for (__wasi_size_t i = 0; i < nsubscriptions; i++)
        has_fd |= in[i].u.tag != __WASI_EVENTTYPE_CLOCK;
    if (!has_fd)
        return sliced_poll(in, nsubscriptions, out, nsubscriptions, nevents);
    copy = malloc(nsubscriptions * sizeof *copy);
    if (copy == NULL)
        return __WASI_ERRNO_NOMEM;
    for (__wasi_size_t i = 0; i < nsubscriptions; i++) {
        /* fd_read and fd_write have the same layout. */
        __wasi_fd_t fd = in[i].u.u.fd_read.file_descriptor;
        __wasi_fdstat_t st;
        int open = in[i].u.tag != __WASI_EVENTTYPE_CLOCK && is_open((int)fd) &&
                   files[slot[fd] - 1].real != GONE;
        int known = open && __real___wasi_fd_fdstat_get(files[slot[fd] - 1].real, &st) == 0;
        if (known && st.fs_filetype != __WASI_FILETYPE_REGULAR_FILE &&
            !(st.fs_rights_base & (in[i].u.tag == __WASI_EVENTTYPE_FD_READ
                                       ? __WASI_RIGHTS_FD_READ
                                       : __WASI_RIGHTS_FD_WRITE)))
            continue;
        if (in[i].u.tag != __WASI_EVENTTYPE_CLOCK &&
            (!open || (known && st.fs_filetype == __WASI_FILETYPE_REGULAR_FILE))) {
            memset(&out[nlocal], 0, sizeof out[nlocal]);
            out[nlocal].userdata = in[i].userdata;
            out[nlocal].error = open ? __WASI_ERRNO_SUCCESS : __WASI_ERRNO_BADF;
            out[nlocal].type = in[i].u.tag;
            out[nlocal].fd_readwrite.nbytes = 1;
            nlocal++;
            continue;
        }
        copy[nsent] = in[i];
        if (in[i].u.tag != __WASI_EVENTTYPE_CLOCK)
            copy[nsent].u.u.fd_read.file_descriptor = files[slot[fd] - 1].real;
        nsent++;
    }
    /* With an event answered here there is something to report already, so the host is asked
     * whether anything else is ready without waiting: a clock subscription would make it
     * wait. */
    if (nlocal > 0) {
        __wasi_size_t keep = 0;
        for (__wasi_size_t i = 0; i < nsent; i++) {
            if (copy[i].u.tag != __WASI_EVENTTYPE_CLOCK)
                copy[keep++] = copy[i];
        }
        nsent = keep;
        if (nsent > 0) {
            __wasi_subscription_t *now = &copy[nsent++];
            memset(now, 0, sizeof *now);
            now->userdata = OWN_CLOCK;
            now->u.tag = __WASI_EVENTTYPE_CLOCK;
            now->u.u.clock.id = __WASI_CLOCKID_MONOTONIC;
        }
    }
    if (nsent > 0)
        error = sliced_poll(copy, nsent, out + nlocal, nsubscriptions - nlocal, &nhost);
    free(copy);
    if (error != __WASI_ERRNO_SUCCESS)
        return error;
    *nevents = nlocal + nhost;
    return __WASI_ERRNO_SUCCESS;
}

/* A host's open of a FIFO waits in the kernel for the other end, where neither the runner's
 * deadline nor its fuel can stop it: they stop WebAssembly code only. So before the host is
 * asked to open a name, the guest asks what the name is, with the same directory, lookup flags
 * and path, so it resolves as the open will (following links exactly when the open does).
 * Anything but a folder, a regular file, or a link the open will not follow is refused:
 * ENOTDIR when the open must find a folder (what the host answers for any other non-folder),
 * ENXIO otherwise (what a host gives for a device it will not open). A name that does not
 * exist, or that the host will not describe, is left to the open, so creating files works
 * and every other answer stays the host's. An exclusive create needs no look: the host refuses
 * an existing name before it opens anything. The guest's only code is Perl, which cannot call
 * a WASI import directly, so no program can skip the look. What it cannot close is another
 * process on the host swapping the name between the look and the open. */
static __wasi_errno_t look(__wasi_fd_t realdir, __wasi_lookupflags_t dirflags, const char *path,
                           __wasi_oflags_t oflags)
{
    __wasi_filestat_t st;
    if ((oflags & __WASI_OFLAGS_CREAT) && (oflags & __WASI_OFLAGS_EXCL))
        return __WASI_ERRNO_SUCCESS;
    if (__real___wasi_path_filestat_get(realdir, dirflags, path, &st) != __WASI_ERRNO_SUCCESS)
        return __WASI_ERRNO_SUCCESS;
    switch (st.filetype) {
    case __WASI_FILETYPE_DIRECTORY:
    case __WASI_FILETYPE_REGULAR_FILE:
    case __WASI_FILETYPE_SYMBOLIC_LINK:
        return __WASI_ERRNO_SUCCESS;
    default:
        return (oflags & __WASI_OFLAGS_DIRECTORY) ? __WASI_ERRNO_NOTDIR : __WASI_ERRNO_NXIO;
    }
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
    int v, f;
    TO_REAL(fd);
    v = lowest_free(0);
    if (v < 0)
        return __WASI_ERRNO_MFILE;
    error = look(fd, dirflags, path, oflags);
    if (error != __WASI_ERRNO_SUCCESS)
        return error;
    error = __real___wasi_path_open(fd, dirflags, path, oflags, base, inheriting, fdflags, &real);
    if (error != __WASI_ERRNO_SUCCESS)
        return error;
    f = new_file(real);
    files[f].oflags = oflags;
    files[f].fdflags = fdflags;
    files[f].base = base;
    files[f].inheriting = inheriting;
    files[f].reopenable = 1;
    bind(v, f, 0);
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
    if (grant[fd])
        return __WASI_ERRNO_SUCCESS;
    return release((int)fd);
}

/* Moves one open number onto another, closing the other first (wasi-libc's freopen uses
 * this). It is a table operation; the host is told only if a real descriptor closes. A grant's
 * number is neither moved nor replaced. */
__typeof__(__wasi_fd_renumber) __wrap___wasi_fd_renumber;
__wasi_errno_t __wrap___wasi_fd_renumber(__wasi_fd_t fd, __wasi_fd_t to)
{
    if (!is_open((int)fd) || !is_open((int)to) || grant[fd] || grant[to])
        return __WASI_ERRNO_BADF; /* as WASI's own, which needs both open */
    if (fd == to)
        return __WASI_ERRNO_SUCCESS;
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
    if (!is_open(fd) || newfd < 0 || newfd >= FD_LIMIT || (grant[newfd] && fd != newfd)) {
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

/* Four-argument select. Perl hands select the strings vec() builds, one bit per descriptor, as
 * its fd_set buffers (pp_sselect, the only caller of select in the link), and pads each to
 * whole words past nfds / 8 bytes. wasi-libc's fd_set is a count and a list of descriptors. So
 * the bits are read into wasi-libc's sets, its select runs, and the descriptors it reports are
 * written back as bits. As on Linux, which reads no further than its descriptor table, bits at
 * or past the table's size are not looked at and are left as they were. On an error the
 * vectors are left as they were, as POSIX says. */
__typeof__(select) __real_select, __wrap_select;
int __wrap_select(int nfds, fd_set *restrict readfds, fd_set *restrict writefds,
                  fd_set *restrict errorfds, struct timeval *restrict timeout)
{
    static fd_set sets[3];
    unsigned char *bits[3] = {(unsigned char *)readfds, (unsigned char *)writefds,
                              (unsigned char *)errorfds};
    fd_set *args[3];
    int n, found;
    if (nfds < 0) {
        errno = EINVAL;
        return -1;
    }
    n = nfds < FD_LIMIT ? nfds : FD_LIMIT;
    /* No descriptor here has an exceptional condition to report, and wasi-libc answers
     * ENOSYS for an error set with nothing else to watch, so the error set is never passed:
     * on success its bits come back clear, as natively. */
    for (int k = 0; k < 3; k++) {
        args[k] = NULL;
        if (bits[k] == NULL || k == 2)
            continue;
        args[k] = &sets[k];
        FD_ZERO(args[k]);
        for (int fd = 0; fd < n; fd++) {
            if (bits[k][fd / 8] & (1u << (fd % 8)))
                FD_SET(fd, args[k]);
        }
    }
    found = __real_select(n, args[0], args[1], args[2], timeout);
    if (found < 0)
        return found;
    for (int k = 0; k < 3; k++) {
        if (bits[k] == NULL)
            continue;
        for (int fd = 0; fd < n; fd++) {
            if (args[k] != NULL && FD_ISSET(fd, args[k]))
                bits[k][fd / 8] |= (unsigned char)(1u << (fd % 8));
            else
                bits[k][fd / 8] &= (unsigned char)~(1u << (fd % 8));
        }
    }
    return found;
}
